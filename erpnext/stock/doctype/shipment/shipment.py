# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.contacts.doctype.contact.contact import get_default_contact
from frappe.model.document import Document
from frappe.utils import cint, flt, get_time, getdate, now_datetime, today

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
		borrowing: DF.Link | None
		borrowing_direction: DF.Literal["Delivery", "Pickup"] | None
		awb_number: DF.Data | None
		carrier: DF.Data | None
		carrier_service: DF.Data | None
		delivery_address: DF.TextEditor | None
		delivery_address_name: DF.Link | None
		delivery_company: DF.Link | None
		delivery_contact: DF.TextEditor | None
		delivery_contact_email: DF.Data | None
		delivery_contact_name: DF.Link | None
		delivery_customer: DF.Link | None
		delivery_supplier: DF.Link | None
		delivery_to: DF.Data | None
		delivery_to_type: DF.Literal["Company", "Customer", "Supplier"]
		description_of_content: DF.SmallText
		recipient_details: DF.SmallText | None
		sent_by_employee: DF.Link | None
		shipment_purpose: DF.Literal[
			"",
			"Sale Delivery",
			"Borrowing",
			"Demo",
			"Stock Transfer",
			"Purchase",
			"Return",
			"Miscellaneous",
		]
		incoterm: DF.Link | None
		pallets: DF.Literal["No", "Yes"]
		parcel_template: DF.Link | None
		pickup: DF.Data | None
		pickup_address: DF.TextEditor | None
		pickup_address_name: DF.Link | None
		pickup_company: DF.Link | None
		pickup_contact: DF.TextEditor | None
		pickup_contact_email: DF.Data | None
		pickup_contact_name: DF.Link | None
		pickup_contact_person: DF.Link | None
		pickup_customer: DF.Link | None
		pickup_date: DF.Date
		pickup_from: DF.Time | None
		pickup_from_type: DF.Literal["Company", "Customer", "Supplier"]
		pickup_supplier: DF.Link | None
		pickup_to: DF.Time | None
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
		self.validate_shipment_delivery_notes()
		self.sync_material_request_snapshots()
		self.validate_weight()
		self.validate_pickup_time()
		self.validate_pickup_date()
		self.validate_shipment_documents()
		self.set_shipment_purpose()
		self.validate_addresses()
		self.set_value_of_goods()
		self.set_total_weight()
		if self.tracking_status == "Delivered":
			if not self.received_on:
				self.received_on = now_datetime()
			if not self.received_by or not self.delivery_signature:
				frappe.throw(
					_("Record the receiver, receipt time, and signature to mark this Shipment as Delivered.")
				)
		if self.docstatus == 0:
			self.status = "Draft"

	def sync_material_request_snapshots(self):
		sources = []
		borrowing_sources = []
		for row in self.get("shipment_documents"):
			if not row.reference_name:
				continue
			if row.reference_doctype == "Stock Entry":
				stock_entry = frappe.get_doc("Stock Entry", row.reference_name)
				if stock_entry.get("outgoing_stock_entry"):
					continue
				if stock_entry.get("is_borrowing_transaction"):
					loan = frappe.get_doc("Borrowing", stock_entry.borrowing)
					borrowing_sources.append(
						(loan.name, get_borrowing_stock_direction(stock_entry, loan), stock_entry)
					)
					continue
				request_names = get_stock_entry_material_requests(stock_entry)
				if len(request_names) > 1:
					frappe.throw(_("A Shipment Stock Entry must reference one Material Request"))
				if request_names:
					request_name = next(iter(request_names))
					request = frappe.get_doc("Material Request", request_name)
					if request.material_request_type not in ("Borrow", "Customer Borrow"):
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
						frappe.throw(
							_("A Customer Borrowing Stock Entry Shipment must move in one direction.")
						)
					sources.append((request_name, directions.pop(), stock_entry))
			elif row.reference_doctype == "Delivery Note":
				delivery_note = frappe.get_doc("Delivery Note", row.reference_name)
				if delivery_note.get("is_borrowing_transaction"):
					loan = frappe.get_doc("Borrowing", delivery_note.borrowing)
					borrowing_sources.append(
						(
							loan.name,
							get_borrowing_delivery_note_direction(delivery_note),
							delivery_note,
						)
					)
		if borrowing_sources:
			if sources:
				frappe.throw(
					_("A Shipment cannot combine Borrowing transactions with other source documents")
				)
			loan_names = {source[0] for source in borrowing_sources}
			directions = {source[1] for source in borrowing_sources}
			if len(loan_names) != 1 or len(directions) != 1:
				frappe.throw(_("A Shipment can contain documents for one Borrowing and one direction"))
			loan_name, direction, source = borrowing_sources[0]
			loan = frappe.get_doc("Borrowing", loan_name)
			set_borrowing_shipment_party(self, loan, direction, source)
			return
		if not sources:
			return

		request_names = {source[0] for source in sources}
		directions = {source[1] for source in sources}
		if self.material_request and request_names and self.material_request not in request_names:
			frappe.throw(_("A Shipment cannot combine different Material Requests"))
		if len(request_names) > 1 or len(directions) > 1:
			frappe.throw(
				_(
					"A Shipment can contain documents for one Customer Demo / Borrowing Material Request and one direction."
				)
			)
		request_name, direction, source = sources[0]
		from erpnext.assets.doctype.material_request.material_request import (
			get_customer_delivery_snapshot,
		)

		request = frappe.get_doc("Material Request", request_name)
		customer = request.customer
		if (
			request.material_request_type in ("Borrow", "Customer Borrow")
			and request.loan_recipient_type == "Customer"
		):
			customer = customer or request.loan_recipient
		contact_person = request.contact_person or request.loan_recipient_contact
		contact_display = request.customer_contact_display or request.loan_recipient_contact_display
		contact_email = request.customer_contact_email or request.loan_recipient_contact_email
		snapshot = get_customer_delivery_snapshot(
			customer,
			request.recipient_address or source.customer_address,
			request.recipient_address_display or source.customer_address_display,
			contact_person or source.customer_contact,
			contact_display or source.customer_contact_display,
			contact_email or source.customer_contact_email,
		)
		if not snapshot.customer_address or not snapshot.customer_address_display:
			frappe.throw(_("Select a Customer Address on the Material Request before creating a Shipment."))
		self.material_request = request_name
		self.material_request_direction = direction
		if direction == "Delivery":
			self.delivery_to_type = "Customer"
			self.delivery_customer = customer
			self.delivery_company = None
			self.delivery_supplier = None
			self.delivery_to = customer
			self.delivery_address_name = snapshot.customer_address
			self.delivery_address = snapshot.customer_address_display
			self.delivery_contact_name = snapshot.customer_contact
			self.delivery_contact = snapshot.customer_contact_display
			self.delivery_contact_email = snapshot.customer_contact_email
		else:
			self.pickup_from_type = "Customer"
			self.pickup_customer = customer
			self.pickup_company = None
			self.pickup_contact_person = None
			self.pickup_supplier = None
			self.pickup = customer
			self.pickup_address_name = snapshot.customer_address
			self.pickup_address = snapshot.customer_address_display
			self.pickup_contact_name = snapshot.customer_contact
			self.pickup_contact = snapshot.customer_contact_display
			self.pickup_contact_email = snapshot.customer_contact_email

	def before_update_after_submit(self):
		# the type may be corrected after submit (Stock team), but never into a kind the documents contradict
		self.validate_miscellaneous()

	def on_submit(self):
		miscellaneous = self.shipment_purpose == MISCELLANEOUS
		# a Miscellaneous shipment counts as one parcel when none is entered
		if not self.shipment_parcel and not miscellaneous:
			frappe.throw(_("Please enter Shipment Parcel information"))
		if has_linked_goods(self) and self.shipment_type != "Documents" and not flt(self.value_of_goods):
			frappe.throw(_("Value of goods cannot be 0"))
		self.db_set("status", "Submitted")
		has_transit_stock = False
		for entry in self.get("shipment_documents"):
			if entry.reference_doctype == "Delivery Note":
				# a Shipment of a Delivery Note, a loan's included, changes nothing in it (Siwat, 5 Nov 2026)
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
		if self.get("borrowing"):
			frappe.get_doc("Borrowing", self.borrowing).update_status()

	def on_update_after_submit(self):
		if self.tracking_status != "Delivered" or self.flags.get("receiving_stock_for_shipment"):
			return

		from erpnext.stock.doctype.stock_entry.shipment_stock_entry import make_transit_receipt

		new_receipts = []
		for entry in self.get("shipment_documents"):
			if entry.reference_doctype == "Delivery Note":
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
		if self.get("borrowing"):
			frappe.get_doc("Borrowing", self.borrowing).update_status()

	def on_cancel(self):
		for entry in self.get("shipment_documents"):
			if entry.reference_doctype == "Delivery Note":
				# a Shipment of a Delivery Note can be cancelled whatever the note's state: it changes nothing in it
				if (
					frappe.db.get_value("Delivery Note", entry.reference_name, "borrowing_shipment")
					== self.name
				):
					frappe.db.set_value("Delivery Note", entry.reference_name, "borrowing_shipment", None)
				continue
			if entry.reference_doctype != "Stock Entry":
				continue
			stock_entry = frappe.get_doc("Stock Entry", entry.reference_name)
			if stock_entry.requires_shipment and stock_entry.docstatus == 1:
				frappe.throw(
					_(
						"A Shipment with posted in-transit stock cannot be cancelled. Record a return or receipt movement first."
					)
				)
			if stock_entry.requires_shipment and stock_entry.shipment == self.name:
				stock_entry.db_set("shipment", None)
		self.db_set("status", "Cancelled")
		if self.get("borrowing"):
			frappe.get_doc("Borrowing", self.borrowing).update_status()

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

	def validate_pickup_date(self):
		"""A carrier pickup is booked for today or later; a shipment recorded after the fact (Self delivery) may
		carry a past date. Checked only when the date is set or changed on a draft."""
		if self.docstatus != 0 or self.pickup_type != "Pickup" or not self.pickup_date:
			return
		previous = None if self.is_new() else self.get_doc_before_save()
		if previous and getdate(previous.pickup_date) == getdate(self.pickup_date):
			return
		if getdate(self.pickup_date) < getdate(today()):
			frappe.throw(_("Pickup Date cannot be before today for a carrier pickup"))

	def validate_shipment_delivery_notes(self):
		"""The legacy Delivery Note table: each row is a submitted Delivery Note of the delivery party, once, that the
		user may read. Its rows move into Stock Transactions (one table for what is shipped); a delivery to a Supplier
		carries none."""
		rows = [row for row in self.get("shipment_delivery_note") or [] if row.get("delivery_note")]
		if not rows:
			self.set("shipment_delivery_note", [])
			return
		if self.delivery_to_type == "Supplier":
			self.set("shipment_delivery_note", [])
			return
		party = self.delivery_customer if self.delivery_to_type == "Customer" else None
		internal = (
			frappe.db.get_value(
				"Customer", {"is_internal_customer": 1, "represents_company": self.delivery_company}, "name"
			)
			if self.delivery_to_type == "Company" and self.delivery_company
			else None
		)
		seen = set()
		for row in rows:
			name = row.delivery_note
			if name in seen:
				frappe.throw(_("Delivery Note {0} is linked more than once").format(name))
			seen.add(name)
			note = frappe.db.get_value(
				"Delivery Note", name, ["docstatus", "customer", "is_borrowing_transaction"], as_dict=True
			)
			if not note:
				frappe.throw(_("Delivery Note {0} does not exist").format(name))
			frappe.has_permission("Delivery Note", "read", doc=name, throw=True)
			if note.docstatus != 1:
				frappe.throw(
					_("Only submitted documents can be linked to a Shipment: {0} {1}").format(
						"Delivery Note", name
					)
				)
			expected = party or internal
			if not note.is_borrowing_transaction and expected and note.customer != expected:
				frappe.throw(
					_("Delivery Note {0} is for {1}, not for the delivery party {2}").format(
						name, note.customer, expected
					)
				)
		linked = {(row.reference_doctype, row.reference_name) for row in self.get("shipment_documents") or []}
		for name in seen:
			if ("Delivery Note", name) not in linked:
				self.append(
					"shipment_documents", {"reference_doctype": "Delivery Note", "reference_name": name}
				)
		self.set("shipment_delivery_note", [])

	def set_shipment_purpose(self):
		"""Show each linked document's kind and fill the Shipment Type from the first one while it is empty; a type
		the user chose is never overwritten."""
		kinds = []
		for row in self.get("shipment_documents") or []:
			if not row.reference_doctype or not row.reference_name:
				continue
			row.document_kind = purpose_of_document(row.reference_doctype, row.reference_name)
			if row.document_kind:
				kinds.append(row.document_kind)
		if not self.shipment_purpose and kinds:
			self.shipment_purpose = kinds[0]
		self.validate_miscellaneous()

	def validate_miscellaneous(self):
		if self.shipment_purpose == MISCELLANEOUS and has_linked_goods(self):
			frappe.throw(
				_(
					"A Miscellaneous shipment links no document. Remove the linked documents or choose another Shipment Type."
				)
			)

	def validate_addresses(self):
		if self.shipment_purpose == MISCELLANEOUS:
			if not (self.recipient_details or "").strip() and not self.delivery_address_name:
				frappe.throw(
					_(
						"Enter the Recipient / Destination of a Miscellaneous shipment, or link a delivery Address."
					)
				)
			return
		missing = [
			_(label)
			for fieldname, label in (
				("pickup_address_name", "Pickup Address"),
				("delivery_address_name", "Delivery Address"),
			)
			if not self.get(fieldname)
		]
		if missing and not self.flags.ignore_mandatory:
			frappe.throw(_("Set the {0} of the Shipment.").format(", ".join(missing)))

	def set_value_of_goods(self):
		value_of_goods = documents_value(
			(entry.reference_doctype, entry.reference_name) for entry in self.get("shipment_documents") or []
		)
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
				can_link_delivery_note_draft = (
					row.reference_doctype == "Delivery Note"
					and docstatus == 0
					and frappe.db.get_value("Delivery Note", row.reference_name, "requires_shipment")
					and frappe.db.get_value("Delivery Note", row.reference_name, "borrowing_shipment")
					== self.name
					and frappe.db.get_value("Delivery Note", row.reference_name, "is_borrowing_transaction")
				)
				if not (can_link_stock_entry_draft or can_link_delivery_note_draft):
					frappe.throw(
						_("Only submitted documents can be linked to a Shipment: {0} {1}").format(*key)
					)
			frappe.has_permission(row.reference_doctype, "read", doc=row.reference_name, throw=True)


SHIPPABLE_TRANSACTION_DOCTYPES = ("Delivery Note", "Purchase Receipt", "Stock Entry")
MISCELLANEOUS = "Miscellaneous"
# The Shipment Type (Siwat, 18-19 Nov 2026): why a shipment is sent, the key of the transport-cost KPI.
SHIPMENT_PURPOSES = (
	"Sale Delivery",
	"Borrowing",
	"Demo",
	"Stock Transfer",
	"Purchase",
	"Return",
	MISCELLANEOUS,
)
# Material Request type of a borrowing -> Shipment Type
BORROWING_PURPOSES = {"Customer Demo": "Demo", "Borrow": "Borrowing", "Customer Borrow": "Borrowing"}
SUBCONTRACT_PURPOSES = ("Send to Subcontractor", "Receive from Subcontractor")


def has_linked_goods(shipment):
	return any(row.reference_name for row in shipment.get("shipment_documents") or []) or any(
		row.delivery_note for row in shipment.get("shipment_delivery_note") or []
	)


def borrowing_purpose(borrowing=None, material_request=None):
	request_type = frappe.db.get_value("Borrowing", borrowing, "material_request_type") if borrowing else None
	if not request_type and material_request:
		request_type = frappe.db.get_value("Material Request", material_request, "material_request_type")
	return BORROWING_PURPOSES.get(request_type) or "Borrowing"


def purpose_of_document(doctype, name):
	"""The Shipment Type a linked document stands for: a sales Delivery Note is a Sale Delivery, a Borrowing one
	a Borrowing or a Demo (from its request type), a return a Return, a Purchase Receipt a Purchase, a Stock Entry a
	Stock Transfer (or the Borrowing / Demo it moves)."""
	if doctype == "Delivery Note":
		note = frappe.db.get_value(
			"Delivery Note", name, ["is_return", "is_borrowing_transaction", "borrowing"], as_dict=True
		)
		if not note:
			return None
		if note.is_borrowing_transaction:
			return borrowing_purpose(note.borrowing)
		return "Return" if note.is_return else "Sale Delivery"
	if doctype == "Purchase Receipt":
		is_return = frappe.db.get_value("Purchase Receipt", name, "is_return")
		return "Return" if cint(is_return) else "Purchase"
	if doctype == "Stock Entry":
		entry = frappe.db.get_value(
			"Stock Entry", name, ["purpose", "is_borrowing_transaction", "borrowing"], as_dict=True
		)
		if not entry:
			return None
		if entry.is_borrowing_transaction:
			return borrowing_purpose(entry.borrowing)
		requests = {
			row.material_request
			for row in frappe.get_all(
				"Stock Entry Detail",
				filters={"parent": name},
				fields=["material_request"],
				limit_page_length=0,
			)
			if row.material_request
		}
		for request in requests:
			kind = BORROWING_PURPOSES.get(
				frappe.db.get_value("Material Request", request, "material_request_type")
			)
			if kind:
				return kind
		return "Purchase" if entry.purpose in SUBCONTRACT_PURPOSES else "Stock Transfer"
	return None


def documents_value(references):
	"""The value of the goods of the linked documents, each once; the transit receipt of an outgoing Stock Entry is
	the same goods."""
	value, seen = 0, set()
	for doctype, name in references:
		if not doctype or not name or (doctype, name) in seen:
			continue
		seen.add((doctype, name))
		if doctype == "Stock Entry" and frappe.db.get_value("Stock Entry", name, "outgoing_stock_entry"):
			continue
		value += get_transaction_value(doctype, name)
	return value


@frappe.whitelist()
def preview_documents(documents):
	"""What the server will store for the linked documents: the value of the goods, each row's kind and the Shipment
	Type they suggest (the form refreshes them; the save recomputes them)."""
	frappe.has_permission("Shipment", "read", throw=True)
	rows = frappe.parse_json(documents) if isinstance(documents, str) else documents
	references = []
	for row in rows or []:
		doctype, name = row.get("reference_doctype"), row.get("reference_name")
		if doctype not in SHIPPABLE_TRANSACTION_DOCTYPES or not name or not frappe.db.exists(doctype, name):
			continue
		frappe.has_permission(doctype, "read", doc=name, throw=True)
		references.append((doctype, name))
	kinds = [[doctype, name, purpose_of_document(doctype, name)] for doctype, name in references]
	return {
		"value_of_goods": documents_value(references),
		"kinds": kinds,
		"purpose": next((kind for _doctype, _name, kind in kinds if kind), None),
	}


def get_stock_entry_material_requests(stock_entry):
	request_names = {
		item.material_request
		or frappe.db.get_value("Material Request Item", item.material_request_item, "parent")
		for item in stock_entry.items
		if item.material_request or item.material_request_item
	}
	request_names.discard(None)
	return request_names


def get_borrowing_stock_direction(stock_entry, loan):
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
			frappe.throw(_("Borrowing Stock Entry must move to or from the Custody Warehouse"))
	if len(directions) != 1:
		frappe.throw(_("A Borrowing Stock Entry Shipment must move in one direction"))
	return directions.pop()


def get_borrowing_delivery_note_direction(delivery_note):
	return "Pickup" if delivery_note.is_return else "Delivery"


def set_borrowing_shipment_party(shipment, loan, direction, source):
	shipment.borrowing = loan.name
	shipment.borrowing_direction = direction
	shipment.material_request = loan.material_request
	shipment.material_request_direction = direction
	request = frappe.get_cached_doc("Material Request", loan.material_request)
	recipient_address = loan.recipient_address or request.recipient_address
	recipient_address_display = loan.recipient_address_display or request.recipient_address_display
	recipient_contact = loan.recipient_contact or request.loan_recipient_contact or request.contact_person
	recipient_contact_display = (
		loan.recipient_contact_display
		or request.loan_recipient_contact_display
		or request.customer_contact_display
	)
	recipient_contact_email = (
		loan.recipient_contact_email or request.loan_recipient_contact_email or request.customer_contact_email
	)
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
			(
				row.warehouse if source.doctype == "Delivery Note" else row.s_warehouse
				for row in getattr(source, "items", [])
				if row.get("warehouse" if source.doctype == "Delivery Note" else "s_warehouse")
			),
			None,
		)
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
		shipment.delivery_address_name = recipient_address
		shipment.delivery_address = recipient_address_display
		shipment.delivery_contact_name = recipient_contact or (
			loan.borrower if loan.borrower_type == "Contact" else None
		)
		shipment.delivery_contact = recipient_contact_display
		shipment.delivery_contact_email = recipient_contact_email
	else:
		shipment.pickup_from_type = party_type if party_type in ("Customer", "Supplier") else "Company"
		shipment.pickup_company = loan.company if shipment.pickup_from_type == "Company" else None
		shipment.pickup_customer = party_name if party_type == "Customer" else None
		shipment.pickup_supplier = party_name if party_type == "Supplier" else None
		shipment.pickup = party_name
		shipment.pickup_address_name = recipient_address
		shipment.pickup_address = recipient_address_display
		shipment.pickup_contact_name = recipient_contact or (
			loan.borrower if loan.borrower_type == "Contact" else None
		)
		shipment.pickup_contact = recipient_contact_display
		shipment.pickup_contact_email = recipient_contact_email
		shipment.delivery_to_type = "Company"
		shipment.delivery_company = loan.company
		shipment.delivery_customer = None
		shipment.delivery_supplier = None
		shipment.delivery_to = loan.company
		warehouse = (
			next((row.warehouse for row in source.items if row.warehouse), None)
			if source.doctype == "Delivery Note"
			else getattr(source, "shipment_destination_warehouse", None)
			or getattr(source, "to_warehouse", None)
		)
		shipment.delivery_address_name = get_party_shipping_address("Warehouse", warehouse) or company_address


def append_borrowing_return_documents(shipment, source):
	if source.get("borrowing_source_doctype") != "Borrowing Return":
		return

	return_doc = frappe.get_doc("Borrowing Return", source.borrowing_source_name)
	if not return_doc.requires_shipment:
		return

	for doctype, fieldname in (
		("Stock Entry", "stock_entry"),
		("Stock Entry", "replacement_stock_entry"),
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
	}.get(doctype)


def get_transaction_value(doctype, name):
	field = get_document_value_field(doctype)
	if field:
		value = abs(flt(frappe.db.get_value(doctype, name, field)))
		if value or doctype != "Delivery Note":
			return value
		items = frappe.get_all(
			"Delivery Note Item",
			filters={"parent": name},
			fields=["stock_qty", "qty", "conversion_factor", "incoming_rate", "rate"],
		)
		return sum(
			abs(flt(row.stock_qty) or flt(row.qty) * flt(row.conversion_factor or 1))
			* abs(flt(row.incoming_rate) or flt(row.rate))
			for row in items
		)
	if doctype == "Stock Entry":
		outgoing, incoming = frappe.db.get_value(
			"Stock Entry", name, ["total_outgoing_value", "total_incoming_value"]
		)
		return flt(outgoing or incoming)
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
			and source.get("is_borrowing_transaction")
			and source.get("borrowing_source_doctype") == "Borrowing Return"
		)
		if source.get("is_borrowing_transaction"):
			if not source.requires_shipment or (
				source.purpose != "Material Transfer" and not is_replacement_receipt
			):
				frappe.throw(
					_(
						"Only a draft Borrowing transfer or replacement receipt marked Ship via Shipment can create a Shipment."
					)
				)
			if source.shipment and frappe.db.exists("Shipment", source.shipment):
				return frappe.get_doc("Shipment", source.shipment).as_dict()
			source.check_permission("write")
			loan = frappe.get_doc("Borrowing", source.borrowing)
			direction = get_borrowing_stock_direction(source, loan)
			shipment = frappe.new_doc("Shipment")
			shipment.set_new_name()
			shipment.shipment_purpose = borrowing_purpose(loan.name, loan.material_request)
			set_borrowing_shipment_party(shipment, loan, direction, source)
			shipment.description_of_content = (
				_("Borrowing return from {0}").format(loan.borrower)
				if direction == "Pickup"
				else _("Borrowing delivery to {0}").format(loan.borrower)
			)
			shipment.append(
				"shipment_documents", {"reference_doctype": "Stock Entry", "reference_name": source.name}
			)
			source.db_set("shipment", shipment.name)
			append_borrowing_return_documents(shipment, source)
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
		shipment.shipment_purpose = purpose_of_document("Stock Entry", source.name)
		shipment.pickup_from_type = "Company"
		shipment.pickup_company = source.company
		shipment.pickup_contact_person = frappe.session.user
		shipment.pickup_address_name = get_party_shipping_address(
			"Warehouse", source_warehouse
		) or get_party_shipping_address("Company", source.company)
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
	shipment.shipment_purpose = purpose_of_document(source_doctype, source_name)
	shipment.append(
		"shipment_documents", {"reference_doctype": source_doctype, "reference_name": source_name}
	)
	shipment.value_of_goods = get_transaction_value(source_doctype, source_name)

	if source_doctype == "Delivery Note":
		shipment.pickup_from_type = "Company"
		shipment.pickup_company = source.company
		shipment.pickup_address_name = getattr(source, "company_address", None)
		shipment.delivery_to_type = "Customer"
		shipment.delivery_customer = source.customer
		shipment.delivery_address_name = getattr(source, "shipping_address_name", None) or getattr(
			source, "customer_address", None
		)
	elif source_doctype == "Purchase Receipt":
		shipment.pickup_from_type = "Supplier"
		shipment.pickup_supplier = source.supplier
		shipment.pickup_address_name = getattr(
			source, "supplier_address", None
		) or get_party_shipping_address("Supplier", source.supplier)
		shipment.pickup_contact_name = get_default_contact("Supplier", source.supplier)
		shipment.delivery_to_type = "Company"
		shipment.delivery_company = source.company
		shipment.delivery_address_name = getattr(
			source, "company_address", None
		) or get_party_shipping_address("Company", source.company)
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
				get_party_shipping_address("Warehouse", source.to_warehouse) if source.to_warehouse else None
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
	return shipment.as_dict()


@frappe.whitelist()
def get_address_name(ref_doctype, docname):
	frappe.has_permission(ref_doctype, "select", doc=docname, throw=True)
	return get_party_shipping_address(ref_doctype, docname)


@frappe.whitelist()
def get_contact_name(ref_doctype, docname):
	frappe.has_permission(ref_doctype, "select", doc=docname, throw=True)
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
