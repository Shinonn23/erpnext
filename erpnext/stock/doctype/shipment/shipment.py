# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.contacts.doctype.contact.contact import get_default_contact
from frappe.model.document import Document
from frappe.utils import flt, get_time, now_datetime

from erpnext.accounts.party import get_party_shipping_address


class Shipment(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.stock.doctype.shipment_delivery_note.shipment_delivery_note import ShipmentDeliveryNote
		from erpnext.stock.doctype.shipment_document.shipment_document import ShipmentDocument
		from erpnext.stock.doctype.shipment_parcel.shipment_parcel import ShipmentParcel

		amended_from: DF.Link | None
		material_request: DF.Link | None
		material_request_direction: DF.Literal["Delivery", "Pickup"] | None
		custody_loan: DF.Link | None
		custody_loan_direction: DF.Literal["Delivery", "Pickup"] | None
		awb_number: DF.Data | None
		carrier: DF.Data | None
		carrier_service: DF.Data | None
		delivery_address: DF.TextEditor | None
		delivery_address_name: DF.Link
		delivery_company: DF.Link | None
		delivery_contact: DF.TextEditor | None
		delivery_contact_email: DF.Data | None
		delivery_contact_name: DF.Link | None
		delivery_customer: DF.Link | None
		delivery_supplier: DF.Link | None
		delivery_to: DF.Data | None
		delivery_to_type: DF.Literal["Company", "Customer", "Supplier"]
		description_of_content: DF.SmallText
		incoterm: DF.Link | None
		pallets: DF.Literal["No", "Yes"]
		parcel_template: DF.Link | None
		pickup: DF.Data | None
		pickup_address: DF.TextEditor | None
		pickup_address_name: DF.Link
		pickup_company: DF.Link | None
		pickup_contact: DF.TextEditor | None
		pickup_contact_email: DF.Data | None
		pickup_contact_name: DF.Link | None
		pickup_contact_person: DF.Link | None
		pickup_customer: DF.Link | None
		pickup_date: DF.Date
		pickup_from: DF.Time
		pickup_from_type: DF.Literal["Company", "Customer", "Supplier"]
		pickup_supplier: DF.Link | None
		pickup_to: DF.Time
		pickup_type: DF.Literal["Pickup", "Self delivery"]
		service_provider: DF.Data | None
		shipment_amount: DF.Currency
		shipment_delivery_note: DF.Table[ShipmentDeliveryNote]
		shipment_documents: DF.Table[ShipmentDocument]
		received_by: DF.Data | None
		received_on: DF.Datetime | None
		delivery_signature: DF.Data | None
		delivery_notes: DF.SmallText | None
		shipment_id: DF.Data | None
		shipment_parcel: DF.Table[ShipmentParcel]
		shipment_type: DF.Literal["Goods", "Documents"]
		status: DF.Literal["Draft", "Submitted", "Booked", "Cancelled", "Completed"]
		total_weight: DF.Float
		tracking_status: DF.Literal["", "In Progress", "Delivered", "Returned", "Lost"]
		tracking_status_info: DF.Data | None
		tracking_url: DF.SmallText | None
		value_of_goods: DF.Currency
	# end: auto-generated types

	def on_discard(self):
		self.db_set("status", "Cancelled")

	def validate(self):
		self.sync_material_request_snapshots()
		self.validate_weight()
		self.validate_pickup_time()
		self.validate_shipment_documents()
		self.set_value_of_goods()
		self.set_total_weight()
		if self.tracking_status == "Delivered":
			if not self.received_on:
				self.received_on = now_datetime()
			if not self.received_by or not self.delivery_signature:
				frappe.throw(_("Record the receiver, receipt time, and signature to mark this Shipment as Delivered."))
		if self.docstatus == 0:
			self.status = "Draft"

	def sync_material_request_snapshots(self):
		sources = []
		custody_loan_sources = []
		asset_transfer_target_locations = set()
		for row in self.get("shipment_documents"):
			if not row.reference_name:
				continue
			if row.reference_doctype == "Asset Movement":
				movement = frappe.get_doc("Asset Movement", row.reference_name)
				if movement.get("is_custody_loan_transaction"):
					loan = frappe.get_doc("Custody Loan", movement.custody_loan)
					direction = "Pickup" if movement.purpose == "Receipt" else "Delivery"
					custody_loan_sources.append((loan.name, direction, movement))
					continue
				if movement.reference_doctype == "Material Request":
					request = frappe.get_doc("Material Request", movement.reference_name)
					if request.material_request_type in ("Customer Demo", "Customer Borrow / Loan"):
						direction = "Pickup" if movement.purpose == "Receipt" else "Delivery"
						sources.append((movement.reference_name, direction, movement))
					elif request.material_request_type == "Asset Transfer":
						if self.material_request and self.material_request != request.name:
							frappe.throw(_("A Shipment cannot combine different Material Requests"))
						self.material_request = request.name
						asset_transfer_target_locations.update(
							asset.shipment_destination_location or asset.target_location
							for asset in movement.assets
						)
			elif row.reference_doctype == "Stock Entry":
				stock_entry = frappe.get_doc("Stock Entry", row.reference_name)
				if stock_entry.get("outgoing_stock_entry"):
					continue
				if stock_entry.get("is_custody_loan_transaction"):
					loan = frappe.get_doc("Custody Loan", stock_entry.custody_loan)
					custody_loan_sources.append(
						(loan.name, get_custody_loan_stock_direction(stock_entry, loan), stock_entry)
					)
					continue
				request_names = get_stock_entry_material_requests(stock_entry)
				if len(request_names) > 1:
					frappe.throw(_("A Shipment Stock Entry must reference one Material Request"))
				if request_names:
					request_name = next(iter(request_names))
					request = frappe.get_doc("Material Request", request_name)
					if request.material_request_type != "Customer Borrow / Loan":
						continue
					loan_warehouse = request.customer_loan_warehouse
					directions = set()
					for item in stock_entry.items:
						target = (
							stock_entry.shipment_destination_warehouse
							if stock_entry.requires_shipment and stock_entry.shipment_destination_warehouse
							else item.t_warehouse
						)
						if target == loan_warehouse:
							directions.add("Delivery")
						elif item.s_warehouse == loan_warehouse:
							directions.add("Pickup")
					if len(directions) != 1:
						frappe.throw(_("A Customer Loan Stock Entry Shipment must move in one direction."))
					sources.append((request_name, directions.pop(), stock_entry))
		if len(asset_transfer_target_locations) > 1:
			frappe.throw(_("Ship Assets to one Target Location per Shipment"))
		if custody_loan_sources:
			if sources:
				frappe.throw(_("A Shipment cannot combine Custody Loan transactions with other source documents"))
			loan_names = {source[0] for source in custody_loan_sources}
			directions = {source[1] for source in custody_loan_sources}
			if len(loan_names) != 1 or len(directions) != 1:
				frappe.throw(_("A Shipment can contain documents for one Custody Loan and one direction"))
			loan_name, direction, source = custody_loan_sources[0]
			loan = frappe.get_doc("Custody Loan", loan_name)
			set_custody_loan_shipment_party(self, loan, direction, source)
			return
		if not sources:
			return

		request_names = {source[0] for source in sources}
		directions = {source[1] for source in sources}
		if self.material_request and request_names and self.material_request not in request_names:
			frappe.throw(_("A Shipment cannot combine different Material Requests"))
		if len(request_names) > 1 or len(directions) > 1:
			frappe.throw(_("A Shipment can contain documents for one Customer Demo / Loan Material Request and one direction."))
		request_name, direction, source = sources[0]
		from erpnext.assets.doctype.material_request.material_request import (
			get_customer_delivery_snapshot,
		)

		snapshot = get_customer_delivery_snapshot(
			frappe.db.get_value("Material Request", request_name, "customer"),
			source.customer_address,
			source.customer_address_display,
			source.customer_contact,
			source.customer_contact_display,
			source.customer_contact_email,
		)
		if not snapshot.customer_address or not snapshot.customer_address_display:
			frappe.throw(_("Select a Customer Address on the Material Request before creating a Shipment."))
		request = frappe.get_doc("Material Request", request_name)
		self.material_request = request_name
		self.material_request_direction = direction
		if direction == "Delivery":
			self.delivery_to_type = "Customer"
			self.delivery_customer = request.customer
			self.delivery_company = None
			self.delivery_supplier = None
			self.delivery_to = request.customer
			self.delivery_address_name = snapshot.customer_address
			self.delivery_address = snapshot.customer_address_display
			self.delivery_contact_name = snapshot.customer_contact
			self.delivery_contact = snapshot.customer_contact_display
			self.delivery_contact_email = snapshot.customer_contact_email
		else:
			self.pickup_from_type = "Customer"
			self.pickup_customer = request.customer
			self.pickup_company = None
			self.pickup_contact_person = None
			self.pickup_supplier = None
			self.pickup = request.customer
			self.pickup_address_name = snapshot.customer_address
			self.pickup_address = snapshot.customer_address_display
			self.pickup_contact_name = snapshot.customer_contact
			self.pickup_contact = snapshot.customer_contact_display
			self.pickup_contact_email = snapshot.customer_contact_email
			if source.doctype == "Asset Movement":
				self.delivery_to_type = "Company"
				self.delivery_company = source.company

	def on_submit(self):
		if not self.shipment_parcel:
			frappe.throw(_("Please enter Shipment Parcel information"))
		if self.value_of_goods == 0:
			frappe.throw(_("Value of goods cannot be 0"))
		self.db_set("status", "Submitted")
		has_transit_stock = False
		for entry in self.get("shipment_documents"):
			if entry.reference_doctype == "Asset Movement":
				movement = frappe.get_doc("Asset Movement", entry.reference_name)
				if movement.requires_shipment and movement.docstatus == 0:
					movement.submit()
					has_transit_stock = True
				continue
			if entry.reference_doctype != "Stock Entry":
				continue
			stock_entry = frappe.get_doc("Stock Entry", entry.reference_name)
			if stock_entry.requires_shipment and stock_entry.docstatus == 0:
				if stock_entry.purpose != "Material Receipt":
					stock_entry.submit()
				has_transit_stock = True
		if has_transit_stock:
			self.tracking_status = "In Progress"
			self.db_set("tracking_status", "In Progress")

	def on_update_after_submit(self):
		if self.tracking_status != "Delivered" or self.flags.get("receiving_stock_for_shipment"):
			return

		from erpnext.stock.doctype.stock_entry.shipment_stock_entry import make_transit_receipt

		new_receipts = []
		for entry in self.get("shipment_documents"):
			if entry.reference_doctype == "Asset Movement":
				movement = frappe.get_doc("Asset Movement", entry.reference_name)
				if not movement.requires_shipment:
					continue
				from erpnext.assets.doctype.asset_movement.shipment_asset_movement import make_transit_receipt

				receipt_name = make_transit_receipt(movement.name, self.name)
				if not frappe.db.exists(
					"Shipment Document",
					{
						"parent": self.name,
						"reference_doctype": "Asset Movement",
						"reference_name": receipt_name,
					},
				):
					self.append("shipment_documents", {"reference_doctype": "Asset Movement", "reference_name": receipt_name})
					new_receipts.append(receipt_name)
				continue
			if entry.reference_doctype != "Stock Entry":
				continue
			stock_entry = frappe.get_doc("Stock Entry", entry.reference_name)
			if not stock_entry.requires_shipment:
				continue
			if stock_entry.purpose == "Material Receipt":
				if stock_entry.docstatus == 0:
					stock_entry.submit()
				continue
			receipt_name = make_transit_receipt(stock_entry.name, self.name)
			if not frappe.db.exists(
				"Shipment Document",
				{
					"parent": self.name,
					"reference_doctype": "Stock Entry",
					"reference_name": receipt_name,
				},
			):
				self.append(
					"shipment_documents",
					{"reference_doctype": "Stock Entry", "reference_name": receipt_name},
				)
				new_receipts.append(receipt_name)

		self.db_set("status", "Completed")
		if new_receipts:
			self.flags.receiving_stock_for_shipment = True
			self.save(ignore_permissions=True)

	def on_cancel(self):
		for entry in self.get("shipment_documents"):
			if entry.reference_doctype == "Asset Movement":
				movement = frappe.get_doc("Asset Movement", entry.reference_name)
				if movement.requires_shipment and movement.docstatus == 1:
					frappe.throw(
						_("A Shipment with posted in-transit Assets cannot be cancelled. Record a return or receipt movement first.")
					)
				if movement.requires_shipment and movement.shipment == self.name:
					movement.db_set("shipment", None)
				continue
			if entry.reference_doctype != "Stock Entry":
				continue
			stock_entry = frappe.get_doc("Stock Entry", entry.reference_name)
			if stock_entry.requires_shipment and stock_entry.docstatus == 1:
				frappe.throw(
					_("A Shipment with posted in-transit stock cannot be cancelled. Record a return or receipt movement first.")
				)
			if stock_entry.requires_shipment and stock_entry.shipment == self.name:
				stock_entry.db_set("shipment", None)
		self.db_set("status", "Cancelled")

	def validate_weight(self):
		for parcel in self.shipment_parcel:
			if flt(parcel.weight) <= 0:
				frappe.throw(_("Parcel weight cannot be 0"))

	def set_total_weight(self):
		self.total_weight = self.get_total_weight()

	def get_total_weight(self):
		return sum(flt(parcel.weight) * parcel.count for parcel in self.shipment_parcel if parcel.count > 0)

	def validate_pickup_time(self):
		if self.pickup_from and self.pickup_to and get_time(self.pickup_to) < get_time(self.pickup_from):
			frappe.throw(_("Pickup To time should be greater than Pickup From time"))

	def set_value_of_goods(self):
		value_of_goods = 0
		linked_delivery_notes = set()
		for entry in self.get("shipment_delivery_note"):
			value_of_goods += flt(entry.get("grand_total"))
			if entry.get("delivery_note"):
				linked_delivery_notes.add(entry.delivery_note)

		for entry in self.get("shipment_documents"):
			if not entry.reference_doctype or not entry.reference_name:
				continue
			if entry.reference_doctype == "Delivery Note" and entry.reference_name in linked_delivery_notes:
				continue
			if entry.reference_doctype == "Stock Entry" and frappe.db.get_value(
				"Stock Entry", entry.reference_name, "outgoing_stock_entry"
			):
				continue
			if entry.reference_doctype == "Asset Movement" and frappe.db.get_value(
				"Asset Movement", entry.reference_name, "shipment_source_movement"
			):
				continue
			value_of_goods += get_transaction_value(entry.reference_doctype, entry.reference_name)

		self.value_of_goods = value_of_goods if value_of_goods else self.value_of_goods

	def validate_shipment_documents(self):
		seen = set()
		for row in self.get("shipment_documents"):
			if not row.reference_doctype or not row.reference_name:
				continue
			if row.reference_doctype not in SHIPPABLE_TRANSACTION_DOCTYPES:
				frappe.throw(_("{0} cannot be linked to a Shipment").format(row.reference_doctype))
			key = (row.reference_doctype, row.reference_name)
			if key in seen:
				frappe.throw(_("{0} {1} is linked more than once").format(*key))
			seen.add(key)
			if not frappe.db.exists(row.reference_doctype, row.reference_name):
				frappe.throw(_("{0} {1} does not exist").format(*key))
			docstatus = frappe.db.get_value(row.reference_doctype, row.reference_name, "docstatus")
			if docstatus != 1:
				can_link_stock_entry_draft = (
					row.reference_doctype == "Stock Entry"
					and docstatus == 0
					and frappe.db.get_value("Stock Entry", row.reference_name, "requires_shipment")
					and frappe.db.get_value("Stock Entry", row.reference_name, "shipment") == self.name
				)
				can_link_asset_movement_draft = (
					row.reference_doctype == "Asset Movement"
					and docstatus == 0
					and frappe.db.get_value("Asset Movement", row.reference_name, "requires_shipment")
					and frappe.db.get_value("Asset Movement", row.reference_name, "shipment") == self.name
				)
				if not (can_link_stock_entry_draft or can_link_asset_movement_draft):
					frappe.throw(_("Only submitted documents can be linked to a Shipment: {0} {1}").format(*key))
			frappe.has_permission(row.reference_doctype, "read", doc=row.reference_name, throw=True)


SHIPPABLE_TRANSACTION_DOCTYPES = ("Delivery Note", "Purchase Receipt", "Stock Entry", "Asset Movement")


def get_stock_entry_material_requests(stock_entry):
	request_names = {
		item.material_request
		or frappe.db.get_value("Material Request Item", item.material_request_item, "parent")
		for item in stock_entry.items
		if item.material_request or item.material_request_item
	}
	request_names.discard(None)
	return request_names


def get_custody_loan_stock_direction(stock_entry, loan):
	if stock_entry.purpose == "Material Receipt":
		return "Pickup"
	directions = set()
	for row in stock_entry.items:
		target_warehouse = (
			stock_entry.shipment_destination_warehouse
			if stock_entry.requires_shipment and stock_entry.shipment_destination_warehouse
			else row.t_warehouse
		)
		if target_warehouse == loan.customer_loan_warehouse:
			directions.add("Delivery")
		elif row.s_warehouse == loan.customer_loan_warehouse:
			directions.add("Pickup")
		else:
			frappe.throw(_("Custody Loan Stock Entry must move to or from the Custody Warehouse"))
	if len(directions) != 1:
		frappe.throw(_("A Custody Loan Stock Entry Shipment must move in one direction"))
	return directions.pop()


def set_custody_loan_shipment_party(shipment, loan, direction, source):
	shipment.custody_loan = loan.name
	shipment.custody_loan_direction = direction
	shipment.material_request = loan.material_request
	shipment.material_request_direction = direction
	company_address = get_party_shipping_address("Company", loan.company)
	party_type = loan.borrower_type
	party_name = loan.borrower
	if party_type == "Contact":
		party_link = frappe.db.get_value(
			"Dynamic Link",
			{"parent": party_name, "parenttype": "Contact", "link_doctype": ["in", ["Customer", "Supplier"]]},
			["link_doctype", "link_name"],
			as_dict=True,
		)
		if party_link:
			party_type, party_name = party_link.link_doctype, party_link.link_name
		else:
			party_type, party_name = "Company", loan.company

	if direction == "Delivery":
		warehouse = getattr(source, "from_warehouse", None) or next(
			(row.s_warehouse for row in getattr(source, "items", []) if row.s_warehouse), None
		)
		movement_row = source.assets[0] if source.doctype == "Asset Movement" and source.assets else None
		if movement_row and movement_row.source_location:
			pickup_address = get_party_shipping_address("Location", movement_row.source_location)
		else:
			pickup_address = get_party_shipping_address("Warehouse", warehouse) if warehouse else None
		shipment.pickup_from_type = "Company"
		shipment.pickup_company = loan.company
		shipment.pickup_customer = None
		shipment.pickup_supplier = None
		shipment.pickup = loan.company
		shipment.pickup_contact_person = frappe.session.user
		shipment.pickup_address_name = pickup_address or company_address
		shipment.delivery_to_type = party_type if party_type in ("Customer", "Supplier") else "Company"
		shipment.delivery_company = loan.company if shipment.delivery_to_type == "Company" else None
		shipment.delivery_customer = party_name if party_type == "Customer" else None
		shipment.delivery_supplier = party_name if party_type == "Supplier" else None
		shipment.delivery_to = party_name
		shipment.delivery_address_name = loan.recipient_address
		shipment.delivery_address = loan.recipient_address_display
		shipment.delivery_contact_name = loan.recipient_contact or (loan.borrower if loan.borrower_type == "Contact" else None)
		shipment.delivery_contact = loan.recipient_contact_display
		shipment.delivery_contact_email = loan.recipient_contact_email
	else:
		shipment.pickup_from_type = party_type if party_type in ("Customer", "Supplier") else "Company"
		shipment.pickup_company = loan.company if shipment.pickup_from_type == "Company" else None
		shipment.pickup_customer = party_name if party_type == "Customer" else None
		shipment.pickup_supplier = party_name if party_type == "Supplier" else None
		shipment.pickup = party_name
		shipment.pickup_address_name = loan.recipient_address
		shipment.pickup_address = loan.recipient_address_display
		shipment.pickup_contact_name = loan.recipient_contact or (loan.borrower if loan.borrower_type == "Contact" else None)
		shipment.pickup_contact = loan.recipient_contact_display
		shipment.pickup_contact_email = loan.recipient_contact_email
		shipment.delivery_to_type = "Company"
		shipment.delivery_company = loan.company
		shipment.delivery_customer = None
		shipment.delivery_supplier = None
		shipment.delivery_to = loan.company
		warehouse = getattr(source, "shipment_destination_warehouse", None) or getattr(source, "to_warehouse", None)
		movement_row = source.assets[0] if source.doctype == "Asset Movement" and source.assets else None
		if movement_row:
			shipment.delivery_address_name = get_party_shipping_address(
				"Location", movement_row.shipment_destination_location or movement_row.target_location
			) or company_address
		else:
			shipment.delivery_address_name = get_party_shipping_address("Warehouse", warehouse) or company_address


def append_custody_loan_return_documents(shipment, source):
	if source.get("custody_loan_source_doctype") != "Custody Loan Return":
		return

	return_doc = frappe.get_doc("Custody Loan Return", source.custody_loan_source_name)
	if not return_doc.requires_shipment:
		return

	for doctype, fieldname in (
		("Stock Entry", "stock_entry"),
		("Stock Entry", "replacement_stock_entry"),
		("Asset Movement", "asset_movement"),
	):
		name = return_doc.get(fieldname)
		if not name or (doctype == source.doctype and name == source.name):
			continue
		doc = frappe.get_doc(doctype, name)
		if not doc.get("requires_shipment"):
			continue
		if doc.docstatus != 0:
			frappe.throw(_("All generated return documents must be Draft before creating their Shipment."))
		if doc.shipment and doc.shipment != shipment.name:
			frappe.throw(_("All generated return documents must use the same Shipment."))
		doc.check_permission("write")
		if not frappe.db.exists(
			"Shipment Document",
			{"parent": shipment.name, "reference_doctype": doctype, "reference_name": name},
		):
			shipment.append("shipment_documents", {"reference_doctype": doctype, "reference_name": name})
		doc.db_set("shipment", shipment.name)



def get_document_value_field(doctype):
	return {
		"Delivery Note": "grand_total",
		"Purchase Receipt": "grand_total",
		"Asset Movement": None,
	}.get(doctype)


def get_transaction_value(doctype, name):
	field = get_document_value_field(doctype)
	if field:
		return flt(frappe.db.get_value(doctype, name, field))
	if doctype == "Stock Entry":
		outgoing, incoming = frappe.db.get_value(
			"Stock Entry", name, ["total_outgoing_value", "total_incoming_value"]
		)
		return flt(outgoing or incoming)
	if doctype == "Asset Movement":
		assets = frappe.get_all("Asset Movement Item", filters={"parent": name}, pluck="asset")
		return sum(flt(frappe.db.get_value("Asset", asset, "purchase_amount")) for asset in assets if asset)
	return 0


@frappe.whitelist()
def make_shipment_from_document(source_doctype, source_name):
	frappe.has_permission("Shipment", "create", throw=True)
	if source_doctype not in SHIPPABLE_TRANSACTION_DOCTYPES:
		frappe.throw(_("{0} cannot be linked to a Shipment").format(source_doctype))
	frappe.has_permission(source_doctype, "read", doc=source_name, throw=True)
	docstatus = frappe.db.get_value(source_doctype, source_name, "docstatus")
	if source_doctype == "Stock Entry" and docstatus == 0:
		source = frappe.get_doc(source_doctype, source_name)
		is_replacement_receipt = (
			source.purpose == "Material Receipt"
			and source.get("is_custody_loan_transaction")
			and source.get("custody_loan_source_doctype") == "Custody Loan Return"
		)
		if source.get("is_custody_loan_transaction"):
			if not source.requires_shipment or (source.purpose != "Material Transfer" and not is_replacement_receipt):
				frappe.throw(_("Only a draft Custody Loan transfer or replacement receipt marked Ship via Shipment can create a Shipment."))
			if source.shipment and frappe.db.exists("Shipment", source.shipment):
				return frappe.get_doc("Shipment", source.shipment).as_dict()
			source.check_permission("write")
			loan = frappe.get_doc("Custody Loan", source.custody_loan)
			direction = get_custody_loan_stock_direction(source, loan)
			shipment = frappe.new_doc("Shipment")
			shipment.set_new_name()
			set_custody_loan_shipment_party(shipment, loan, direction, source)
			shipment.description_of_content = (
				_("Custody Loan return from {0}").format(loan.borrower)
				if direction == "Pickup"
				else _("Custody Loan delivery to {0}").format(loan.borrower)
			)
			shipment.append("shipment_documents", {"reference_doctype": "Stock Entry", "reference_name": source.name})
			source.db_set("shipment", shipment.name)
			append_custody_loan_return_documents(shipment, source)
			shipment.insert(ignore_permissions=True, ignore_mandatory=True)
			return shipment.as_dict()

		if not source.requires_shipment or source.purpose != "Material Transfer":
			frappe.throw(_("Only a draft Material Transfer marked Ship via Shipment can create a Shipment."))
		if source.shipment and frappe.db.exists("Shipment", source.shipment):
			return frappe.get_doc("Shipment", source.shipment).as_dict()
		source.check_permission("write")
		source_warehouse = source.from_warehouse or next(
			(row.s_warehouse for row in source.items if row.s_warehouse), None
		)
		shipment = frappe.new_doc("Shipment")
		shipment.set_new_name()
		shipment.pickup_from_type = "Company"
		shipment.pickup_company = source.company
		shipment.pickup_contact_person = frappe.session.user
		shipment.pickup_address_name = get_party_shipping_address("Warehouse", source_warehouse) or get_party_shipping_address("Company", source.company)
		shipment.delivery_to_type = "Company"
		shipment.delivery_company = source.company
		shipment.delivery_address_name = get_party_shipping_address(
			"Warehouse", source.shipment_destination_warehouse
		) or get_party_shipping_address("Company", source.company)
		request_names = get_stock_entry_material_requests(source)
		if len(request_names) > 1:
			frappe.throw(_("A Shipment Stock Entry must reference one Material Request"))
		request_name = next(iter(request_names), None)
		if request_name:
			request = frappe.get_doc("Material Request", request_name)
			if source.shipment_destination_warehouse == request.customer_loan_warehouse:
				shipment.delivery_to_type = "Customer"
				shipment.delivery_customer = request.customer
				shipment.delivery_address_name = source.customer_address
				shipment.delivery_address = source.customer_address_display
				shipment.delivery_contact_name = source.customer_contact
				shipment.delivery_contact = source.customer_contact_display
				shipment.delivery_contact_email = source.customer_contact_email
			else:
				shipment.pickup_from_type = "Customer"
				shipment.pickup_customer = request.customer
				shipment.pickup_address_name = source.customer_address
				shipment.pickup_address = source.customer_address_display
				shipment.pickup_contact_name = source.customer_contact
				shipment.pickup_contact = source.customer_contact_display
				shipment.pickup_contact_email = source.customer_contact_email
		shipment.description_of_content = _("Stock transfer from {0} to {1}").format(
			source_warehouse, source.shipment_destination_warehouse
		)
		shipment.append(
			"shipment_documents",
			{"reference_doctype": "Stock Entry", "reference_name": source.name},
		)
		source.db_set("shipment", shipment.name)
		shipment.insert(ignore_permissions=True, ignore_mandatory=True)
		return shipment.as_dict()

	if source_doctype == "Asset Movement" and docstatus == 0:
		source = frappe.get_doc(source_doctype, source_name)
		if source.get("is_custody_loan_transaction"):
			if not source.requires_shipment or source.purpose not in ("Transfer", "Receipt"):
				frappe.throw(_("Only a draft Custody Loan Transfer or Receipt marked Ship via Shipment can create a Shipment."))
			if source.shipment and frappe.db.exists("Shipment", source.shipment):
				return frappe.get_doc("Shipment", source.shipment).as_dict()
			source.check_permission("write")
			loan = frappe.get_doc("Custody Loan", source.custody_loan)
			direction = "Pickup" if source.purpose == "Receipt" else "Delivery"
			shipment = frappe.new_doc("Shipment")
			shipment.set_new_name()
			shipment.shipment_type = "Goods"
			set_custody_loan_shipment_party(shipment, loan, direction, source)
			shipment.description_of_content = (
				_("Custody Loan return from {0}").format(loan.borrower)
				if direction == "Pickup"
				else _("Custody Loan delivery to {0}").format(loan.borrower)
			)
			shipment.append("shipment_documents", {"reference_doctype": source_doctype, "reference_name": source.name})
			source.db_set("shipment", shipment.name)
			shipment.insert(ignore_permissions=True, ignore_mandatory=True)
			return shipment.as_dict()

		if not source.requires_shipment or source.purpose not in ("Transfer", "Receipt"):
			frappe.throw(_("Only a draft Transfer or Receipt Asset Movement marked Ship via Shipment can create a Shipment."))
		if source.shipment and frappe.db.exists("Shipment", source.shipment):
			return frappe.get_doc("Shipment", source.shipment).as_dict()
		source.check_permission("write")
		row = source.assets[0] if source.assets else None
		if not row:
			frappe.throw(_("Add at least one Asset before creating a Shipment."))
		company_address = get_party_shipping_address("Company", source.company)
		shipment = frappe.new_doc("Shipment")
		shipment.set_new_name()
		shipment.shipment_type = "Goods"
		if source.purpose == "Receipt" and source.customer:
			shipment.pickup_from_type = "Customer"
			shipment.pickup_customer = source.customer
			shipment.pickup_address_name = source.customer_address
			shipment.pickup_address = source.customer_address_display
			shipment.pickup_contact_name = source.customer_contact
			shipment.pickup_contact = source.customer_contact_display
			shipment.pickup_contact_email = source.customer_contact_email
			shipment.delivery_to_type = "Company"
			shipment.delivery_company = source.company
			shipment.delivery_address_name = get_party_shipping_address("Location", row.shipment_destination_location or row.target_location) or company_address
		elif source.customer:
			shipment.pickup_from_type = "Company"
			shipment.pickup_company = source.company
			shipment.pickup_contact_person = frappe.session.user
			shipment.pickup_address_name = get_party_shipping_address("Location", row.source_location) or company_address
			shipment.delivery_to_type = "Customer"
			shipment.delivery_customer = source.customer
			if source.reference_doctype == "Material Request":
				shipment.material_request = source.reference_name
				shipment.delivery_contact_name = source.customer_contact
				shipment.delivery_contact = source.customer_contact_display
				shipment.delivery_contact_email = source.customer_contact_email
				shipment.delivery_address_name = source.customer_address
				shipment.delivery_address = source.customer_address_display
			else:
				shipment.delivery_contact_name = source.customer_contact or get_default_contact("Customer", source.customer)
				shipment.delivery_address_name = (
					get_party_shipping_address("Location", row.shipment_destination_location or row.target_location)
					or get_party_shipping_address("Customer", source.customer)
					or company_address
				)
		else:
			shipment.pickup_from_type = "Company"
			shipment.pickup_company = source.company
			shipment.pickup_contact_person = frappe.session.user
			shipment.pickup_address_name = get_party_shipping_address("Location", row.source_location) or company_address
			shipment.delivery_to_type = "Company"
			shipment.delivery_company = source.company
			shipment.delivery_address_name = get_party_shipping_address(
				"Location", row.shipment_destination_location or row.target_location
			) or company_address
		shipment.description_of_content = _("Asset transfer from {0} to {1}").format(
			row.source_location or "—", row.shipment_destination_location or row.target_location or "—"
		)
		shipment.append("shipment_documents", {"reference_doctype": source_doctype, "reference_name": source_name})
		source.db_set("shipment", shipment.name)
		shipment.insert(ignore_permissions=True, ignore_mandatory=True)
		return shipment.as_dict()

	if docstatus != 1:
		frappe.throw(_("Only submitted documents can be linked to a Shipment"))

	source = frappe.get_doc(source_doctype, source_name)
	if source_doctype == "Stock Entry" and source.purpose in (
		"Manufacture",
		"Repack",
		"Material Consumption for Manufacture",
	):
		frappe.throw(_("This Stock Entry does not represent a transportable stock movement"))
	shipment = frappe.new_doc("Shipment")
	shipment.shipment_type = "Goods"
	shipment.append("shipment_documents", {"reference_doctype": source_doctype, "reference_name": source_name})
	shipment.value_of_goods = get_transaction_value(source_doctype, source_name)

	if source_doctype == "Delivery Note":
		shipment.pickup_from_type = "Company"
		shipment.pickup_company = source.company
		shipment.pickup_address_name = getattr(source, "company_address", None)
		shipment.delivery_to_type = "Customer"
		shipment.delivery_customer = source.customer
		shipment.delivery_address_name = (
			getattr(source, "shipping_address_name", None) or getattr(source, "customer_address", None)
		)
	elif source_doctype == "Purchase Receipt":
		shipment.pickup_from_type = "Supplier"
		shipment.pickup_supplier = source.supplier
		shipment.pickup_address_name = getattr(source, "supplier_address", None) or get_party_shipping_address(
			"Supplier", source.supplier
		)
		shipment.pickup_contact_name = get_default_contact("Supplier", source.supplier)
		shipment.delivery_to_type = "Company"
		shipment.delivery_company = source.company
		shipment.delivery_address_name = getattr(source, "company_address", None) or get_party_shipping_address(
			"Company", source.company
		)
		shipment.delivery_contact_name = None
	else:
		shipment.pickup_from_type = "Company"
		shipment.pickup_company = source.company
		shipment.delivery_to_type = "Company"
		shipment.delivery_company = source.company
		company_address = get_party_shipping_address("Company", source.company)
		shipment.pickup_address_name = company_address
		shipment.delivery_address_name = company_address
		shipment.pickup_contact_person = frappe.session.user
		if source_doctype == "Stock Entry":
			shipment.pickup_address_name = (
				get_party_shipping_address("Warehouse", source.from_warehouse)
				if source.from_warehouse
				else None
			) or company_address
			shipment.delivery_address_name = (
				get_party_shipping_address("Warehouse", source.to_warehouse)
				if source.to_warehouse
				else None
			) or company_address
			if source.purpose == "Send to Subcontractor" and source.supplier:
				shipment.delivery_to_type = "Supplier"
				shipment.delivery_supplier = source.supplier
				shipment.delivery_address_name = get_party_shipping_address("Supplier", source.supplier)
				shipment.delivery_contact_name = get_default_contact("Supplier", source.supplier)
			elif source.purpose == "Receive from Subcontractor" and source.supplier:
				shipment.pickup_from_type = "Supplier"
				shipment.pickup_supplier = source.supplier
				shipment.pickup_address_name = get_party_shipping_address("Supplier", source.supplier)
				shipment.pickup_contact_name = get_default_contact("Supplier", source.supplier)
			shipment.description_of_content = _("Stock movement: {0} to {1}").format(
				getattr(source, "from_warehouse", "") or "—", getattr(source, "to_warehouse", "") or "—"
			)
		elif source_doctype == "Asset Movement":
			movement_row = source.assets[0] if source.assets else None
			shipment.pickup_address_name = (
				get_party_shipping_address("Location", movement_row.source_location)
				if movement_row and movement_row.source_location
				else None
			) or company_address
			shipment.delivery_address_name = (
				get_party_shipping_address("Location", movement_row.target_location)
				if movement_row and movement_row.target_location
				else None
			) or company_address
			if source.reference_doctype == "Material Request":
				request_type = frappe.db.get_value(
					"Material Request", source.reference_name, "material_request_type"
				)
				if request_type in ("Customer Demo", "Customer Borrow / Loan"):
					shipment.material_request = source.reference_name
					shipment.delivery_to_type = "Customer"
					shipment.delivery_customer = source.customer
					shipment.delivery_contact_name = source.customer_contact
					shipment.delivery_contact = source.customer_contact_display
					shipment.delivery_contact_email = source.customer_contact_email
					shipment.delivery_address_name = source.customer_address
					shipment.delivery_address = source.customer_address_display
				elif request_type == "Asset Transfer":
					shipment.material_request = source.reference_name
					shipment.delivery_to_type = "Company"
					shipment.delivery_company = source.company
			elif source.purpose == "Issue" and source.customer:
				shipment.delivery_to_type = "Customer"
				shipment.delivery_customer = source.customer
				shipment.delivery_address_name = get_party_shipping_address("Customer", source.customer)
				shipment.delivery_contact_name = getattr(source, "customer_contact", None)
			elif source.purpose == "Receipt" and source.customer:
				shipment.pickup_from_type = "Customer"
				shipment.pickup_customer = source.customer
				shipment.pickup_address_name = get_party_shipping_address("Customer", source.customer)
				shipment.pickup_contact_name = getattr(source, "customer_contact", None) or get_default_contact(
					"Customer", source.customer
				)
			if movement_row:
				shipment.description_of_content = _("Asset movement: {0} to {1}").format(
					movement_row.source_location or "—", movement_row.target_location or "—"
				)

	return shipment.as_dict()


@frappe.whitelist()
def get_address_name(ref_doctype, docname):
	# Return address name
	return get_party_shipping_address(ref_doctype, docname)


@frappe.whitelist()
def get_contact_name(ref_doctype, docname):
	# Return address name
	return get_default_contact(ref_doctype, docname)


@frappe.whitelist()
def get_company_contact(user: str):
	frappe.has_permission("User", "read", throw=True)

	contact = frappe.db.get_value(
		"User",
		user,
		[
			"first_name",
			"last_name",
			"email",
			"phone",
			"mobile_no",
			"gender",
		],
		as_dict=1,
	)
	if not contact.phone:
		contact.phone = contact.mobile_no
	return contact
