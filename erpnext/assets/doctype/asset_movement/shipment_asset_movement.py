# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import now_datetime


def make_transit_receipt(asset_movement_name, shipment_name):
	"""Move shipped Assets from the configured transit Location to their final Locations."""
	source = frappe.get_doc("Asset Movement", asset_movement_name)
	existing = frappe.db.get_value(
		"Asset Movement", {"shipment_source_movement": source.name, "docstatus": 1}, "name"
	)
	if existing:
		return existing
	if source.docstatus != 1 or not source.get("shipment_transit_leg"):
		frappe.throw(_("The Asset Movement must be submitted to Transit before receipt."))

	receipt = frappe.new_doc("Asset Movement")
	receipt.company = source.company
	# A return shipment starts as an Asset Movement Receipt at the Transit
	# Location, then completes as a Receipt at the original company Location.
	receipt.purpose = "Receipt" if source.purpose == "Receipt" else "Transfer"
	receipt.transaction_date = now_datetime()
	receipt.customer = source.customer
	receipt.customer_contact = source.customer_contact
	receipt.customer_address = source.customer_address
	receipt.customer_address_display = source.customer_address_display
	receipt.customer_contact_display = source.customer_contact_display
	receipt.customer_contact_email = source.customer_contact_email
	receipt.expected_return_date = source.expected_return_date
	receipt.reference_doctype = source.reference_doctype
	receipt.reference_name = source.reference_name
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
	receipt.shipment = shipment_name
	receipt.shipment_source_movement = source.name
	for row in source.assets:
		if not row.shipment_destination_location:
			frappe.throw(_("Final Target Location is missing for Asset {0}.").format(row.asset))
		receipt.append(
			"assets",
			{
				"asset": row.asset,
				"source_location": row.target_location,
				"target_location": row.shipment_destination_location,
				"request_item": row.request_item,
				"asset_item_code": row.asset_item_code,
				"demo_asset_required": row.demo_asset_required,
				"custody_loan_item": row.custody_loan_item,
				"to_employee": row.to_employee,
			},
		)
	receipt.flags.ignore_permissions = True
	receipt.insert(ignore_permissions=True)
	receipt.submit()
	return receipt.name
