# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def prepare_stock_entry_for_shipment(doc, method=None):
	if not doc.get("requires_shipment"):
		return

	if doc.purpose == "Material Receipt" and doc.get("is_custody_loan_transaction"):
		if doc.get("custody_loan_source_doctype") != "Custody Loan Return":
			frappe.throw(_("Only a Custody Loan Return can receive replacement stock through a Shipment."))
		destinations = {row.t_warehouse for row in doc.items if row.t_warehouse}
		destination = doc.shipment_destination_warehouse or doc.to_warehouse or next(iter(destinations), None)
		if not destination or destinations != {destination}:
			frappe.throw(_("A shipped Custody Loan replacement receipt must use one final Destination Warehouse."))
		warehouse = frappe.db.get_value(
			"Warehouse", destination, ["company", "is_group", "warehouse_type"], as_dict=True
		)
		if not warehouse or warehouse.company != doc.company or warehouse.is_group or warehouse.warehouse_type == "Transit":
			frappe.throw(_("The final Destination Warehouse must be a non-Transit leaf warehouse in the same company."))
		doc.shipment_destination_warehouse = destination
		return

	if doc.purpose != "Material Transfer":
		frappe.throw(_("Shipment tracking is available for Material Transfer and Custody Loan replacement receipts only."))

	if not doc.shipment_destination_warehouse:
		doc.shipment_destination_warehouse = doc.to_warehouse

	if not doc.shipment_destination_warehouse:
		frappe.throw(_("Select the final Destination Warehouse before creating a Shipment."))
	destination = frappe.db.get_value(
		"Warehouse",
		doc.shipment_destination_warehouse,
		["company", "is_group", "warehouse_type"],
	)
	if not destination or destination[0] != doc.company or destination[1] or destination[2] == "Transit":
		frappe.throw(_("The final Destination Warehouse must be a non-Transit leaf warehouse in the same company."))

	source_warehouse = doc.from_warehouse or next(
		(row.s_warehouse for row in doc.items if row.s_warehouse), None
	)
	if not source_warehouse:
		frappe.throw(_("Select a Source Warehouse before creating a Shipment."))
	doc.from_warehouse = source_warehouse

	transit_warehouse = frappe.db.get_value("Warehouse", source_warehouse, "default_in_transit_warehouse")
	if not transit_warehouse:
		transit_warehouse = frappe.db.get_value("Company", doc.company, "default_in_transit_warehouse")
	if not transit_warehouse:
		transit_warehouses = frappe.get_all(
			"Warehouse",
			filters={"company": doc.company, "warehouse_type": "Transit", "is_group": 0},
			pluck="name",
		)
		if len(transit_warehouses) == 1:
			transit_warehouse = transit_warehouses[0]

	if not transit_warehouse:
		frappe.throw(
			_("Set a Default In-Transit Warehouse on the Source Warehouse or Company before using Shipment.")
		)

	transit = frappe.db.get_value("Warehouse", transit_warehouse, ["company", "warehouse_type", "is_group"])
	if not transit or transit[0] != doc.company or transit[1] != "Transit" or transit[2]:
		frappe.throw(
			_("The configured In-Transit Warehouse must be a non-group Transit warehouse in the same company.")
		)

	if transit_warehouse == doc.shipment_destination_warehouse:
		frappe.throw(_("The final Destination Warehouse cannot be the In-Transit Warehouse."))

	doc.add_to_transit = 1
	doc.to_warehouse = transit_warehouse
	for row in doc.items:
		row.t_warehouse = transit_warehouse


def validate_stock_entry_shipment(doc, method=None):
	if not doc.get("requires_shipment") or doc._action != "submit":
		return

	if not doc.shipment:
		frappe.throw(_("Create and submit the linked Shipment before submitting this Stock Entry."))

	if frappe.db.get_value("Shipment", doc.shipment, "docstatus") != 1:
		frappe.throw(_("The linked Shipment must be submitted before this Stock Entry can be submitted."))

	if not frappe.db.exists(
		"Shipment Document",
		{
			"parent": doc.shipment,
			"reference_doctype": "Stock Entry",
			"reference_name": doc.name,
		},
	):
		frappe.throw(_("This Stock Entry is not linked to the selected Shipment."))
	if doc.purpose == "Material Receipt" and frappe.db.get_value(
		"Shipment", doc.shipment, "tracking_status"
	) != "Delivered":
		frappe.throw(_("A Custody Loan replacement receipt can only be submitted after the Shipment is delivered."))


def validate_stock_entry_shipment_cancel(doc, method=None):
	if not doc.get("requires_shipment") or not doc.shipment:
		return
	if frappe.db.get_value("Shipment", doc.shipment, "docstatus") == 1:
		frappe.throw(_("Cancel the Shipment before cancelling its Stock Entry."))


def make_transit_receipt(stock_entry_name, shipment_name):
	"""Create the standard incoming leg for a Stock Entry that has reached its Shipment."""
	from erpnext.stock.doctype.stock_entry.stock_entry import make_stock_in_entry

	source = frappe.get_doc("Stock Entry", stock_entry_name)
	if source.docstatus != 1 or not source.add_to_transit:
		frappe.throw(_("The outgoing Stock Entry must be submitted to the Transit Warehouse first."))

	if not source.shipment_destination_warehouse:
		frappe.throw(_("The Stock Entry has no final Destination Warehouse."))

	existing = frappe.db.get_value(
		"Stock Entry",
		{"outgoing_stock_entry": source.name, "docstatus": 1},
		"name",
	)
	if existing:
		return existing

	receipt = make_stock_in_entry(source.name)
	if not receipt or not receipt.get("items"):
		frappe.throw(
			_("No remaining quantity is available to receive from Transit for Stock Entry {0}.").format(source.name)
		)

	receipt.requires_shipment = 0
	receipt.shipment = shipment_name
	receipt.add_to_transit = 0
	receipt.to_warehouse = source.shipment_destination_warehouse
	for field in (
		"is_system_generated",
		"is_custody_loan_transaction",
		"custody_loan",
		"custody_loan_source_doctype",
		"custody_loan_source_name",
	):
		if source.get(field):
			setattr(receipt, field, source.get(field))
	if source.get("is_custody_loan_transaction"):
		receipt.flags.custody_loan_generated = True
	for field in (
		"customer_address",
		"customer_address_display",
		"customer_contact",
		"customer_contact_display",
		"customer_contact_email",
	):
		if source.get(field):
			setattr(receipt, field, source.get(field))
	for row in receipt.items:
		row.t_warehouse = source.shipment_destination_warehouse
		if row.ste_detail:
			row.material_request = frappe.db.get_value("Stock Entry Detail", row.ste_detail, "material_request")
			row.material_request_item = frappe.db.get_value(
				"Stock Entry Detail", row.ste_detail, "material_request_item"
			)
			row.custody_loan_item = frappe.db.get_value(
				"Stock Entry Detail", row.ste_detail, "custody_loan_item"
			)

	receipt.flags.ignore_permissions = True
	receipt.insert(ignore_permissions=True)
	receipt.submit()
	return receipt.name
