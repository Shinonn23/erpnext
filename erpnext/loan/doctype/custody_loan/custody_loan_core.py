from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.get_item_details import get_item_details
from erpnext.stock.serial_batch_bundle import get_serial_nos_from_bundle

BORROWER_TYPES = {"Employee", "Customer", "Supplier", "Contact"}
LOAN_SOURCE_DOCTYPES = {"Custody Loan", "Custody Loan Return", "Custody Loan Adjustment"}
_CUSTODY_OPERATION_TOKEN = object()


def is_trusted_custody_operation(doc):
	"""Distinguish native generation/reversal from client-supplied transaction markers."""
	return doc.flags.get("custody_operation_token") is _CUSTODY_OPERATION_TOKEN


def get_custody_loan_return_delivery_note(quality_inspection):
	if quality_inspection.reference_type != "Delivery Note" or not quality_inspection.reference_name:
		return None
	delivery_note = frappe.db.get_value(
		"Delivery Note",
		quality_inspection.reference_name,
		["is_return", "is_custody_loan_transaction", "custody_loan_source_doctype"],
		as_dict=True,
	)
	if (
		delivery_note
		and delivery_note.is_return
		and delivery_note.is_custody_loan_transaction
		and delivery_note.custody_loan_source_doctype == "Custody Loan Return"
	):
		return quality_inspection.reference_name
	return None


class CustodyLoanQualityInspectionMixin:
	def update_qc_reference(self, remove_reference=False):
		delivery_note_name = get_custody_loan_return_delivery_note(self)
		if not delivery_note_name:
			return super().update_qc_reference(remove_reference)

		# A loan return inspection belongs to one exact row. ERPNext's default
		# implementation links every row with the same item code to this inspection.
		frappe.db.set_value(
			"Delivery Note Item",
			self.child_row_reference,
			"quality_inspection",
			self.name if self.docstatus < 2 and not remove_reference else "",
			update_modified=False,
		)

	def validate(self):
		result = super().validate()
		delivery_note_name = get_custody_loan_return_delivery_note(self)
		if delivery_note_name:
			validate_custody_loan_return_quality_inspection(self, delivery_note_name)
		return result

	def validate_inspection_required(self):
		if get_custody_loan_return_delivery_note(self):
			return
		return super().validate_inspection_required()

	def before_submit(self):
		if (
			get_custody_loan_return_delivery_note(self)
			and self.manual_inspection
		):
			if self.status not in ("Accepted", "Rejected"):
				frappe.throw(_("Set the Loan Return inspection result before submitting this Quality Inspection"))
			if not (self.remarks or "").strip():
				frappe.throw(_("Record the Loan Return inspection findings before submitting this Quality Inspection"))
		return super().before_submit()

	def before_cancel(self):
		delivery_note_name = get_custody_loan_return_delivery_note(self)
		if delivery_note_name and frappe.db.get_value("Delivery Note", delivery_note_name, "docstatus") == 1:
			frappe.throw(_("Cancel the Loan Return Delivery Note before cancelling its Quality Inspection"))
		before_cancel = getattr(super(), "before_cancel", None)
		if before_cancel:
			return before_cancel()

	def on_submit(self):
		result = super().on_submit()
		update_custody_loan_return_status_from_quality_inspection(self)
		return result

	def on_cancel(self):
		result = super().on_cancel()
		update_custody_loan_return_status_from_quality_inspection(self)
		return result


class CustodyLoan(Document):
	@frappe.whitelist()
	def submit(self):
		lock_material_request(self.material_request)
		return super().submit()

	@frappe.whitelist()
	def cancel(self):
		lock_material_request(self.material_request)
		return super().cancel()

	def validate(self):
		self.validate_material_request()
		self.validate_items()
		self.validate_shipment_scope()
		self.refresh_balances()

	def validate_material_request(self):
		request = frappe.get_doc("Material Request", self.material_request)
		if request.docstatus != 1 or request.material_request_type not in (
			"Customer Demo",
			"Customer Borrow / Loan",
		):
			frappe.throw(_("Custody Loans can only be created from a submitted Demo or Borrow Material Request"))
		if request.company != self.company:
			frappe.throw(_("Custody Loan and Material Request must use the same Company"))
		if self.loan_type not in ("Stock", "Asset"):
			frappe.throw(_("Select Stock or Asset as the Loan Type"))
		expected_loan_type = (
			"Asset"
			if request.material_request_type == "Customer Demo" or request.borrow_item_type == "Asset"
			else "Stock"
		)
		if self.loan_type != expected_loan_type:
			frappe.throw(_("Custody Loan Type must match the Material Request Borrow Item Type"))
		if self.requires_shipment and self.loan_type != "Stock":
			frappe.throw(_("Shipment is available only for Stock Custody Loans"))

		# The borrower and delivery snapshot are owned by the submitted request.
		self.borrower_type = request.loan_recipient_type or ("Customer" if request.customer else None)
		self.borrower = request.loan_recipient or request.customer
		if self.borrower_type not in BORROWER_TYPES or not self.borrower:
			frappe.throw(_("Select an Employee, Customer, Supplier or Contact on the Material Request"))
		if not frappe.db.exists(self.borrower_type, self.borrower):
			frappe.throw(_("Borrower {0} does not exist as a {1}").format(self.borrower, self.borrower_type))
		if not self.expected_return_date:
			self.expected_return_date = request.expected_return_date
		if not self.expected_return_date or getdate(self.expected_return_date) < getdate(request.transaction_date):
			frappe.throw(_("Expected Return Date must be on or after the Material Request date"))

		self.recipient_address = request.recipient_address
		self.recipient_address_display = request.recipient_address_display
		self.recipient_contact = request.loan_recipient_contact or request.contact_person
		self.recipient_contact_display = request.loan_recipient_contact_display or request.customer_contact_display
		self.recipient_contact_email = request.loan_recipient_contact_email or request.customer_contact_email
		self.recipient_location = request.loan_recipient_location or request.customer_location
		if self.loan_type == "Stock":
			self.customer_loan_warehouse = frappe.db.get_value("Company", self.company, "customer_loan_warehouse")
			if not self.customer_loan_warehouse:
				frappe.throw(_("Set Customer Loan Warehouse on Company {0}").format(self.company))
			if frappe.db.get_value("Warehouse", self.customer_loan_warehouse, "company") != self.company:
				frappe.throw(_("Customer Loan Warehouse must belong to Company {0}").format(self.company))
		else:
			self.customer_loan_warehouse = None

		if self.requires_shipment and not self.recipient_address:
			frappe.throw(_("Select a Recipient Address on the Material Request before using Shipment"))

	def validate_shipment_scope(self):
		if not self.requires_shipment:
			return
		if len({row.source_warehouse for row in self.items}) > 1:
			frappe.throw(_("A Stock Loan Shipment must start from one Source Warehouse"))

	def validate_items(self, require_allocation=False, require_source_warehouse=False):
		if not self.items:
			frappe.throw(_("Add at least one item to the Custody Loan"))

		request = frappe.get_cached_doc("Material Request", self.material_request)
		request_rows = {row.name: row for row in request.items}
		request_link_field = "material_request_item"
		qty_by_request_item = {}
		assets_seen = set()
		serials_seen = set()
		for row in self.items:
			request_item = request_rows.get(row.get(request_link_field))
			if not request_item or request_item.parent != request.name:
				frappe.throw(_("Row {0}: Select an item from the linked Material Request").format(row.idx))
			if row.item_code != request_item.item_code:
				frappe.throw(_("Row {0}: Item must match the linked Material Request row").format(row.idx))
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Loan Qty must be greater than zero").format(row.idx))
			if self.loan_type == "Stock":
				if request_item.item_type != "Stock Item":
					frappe.throw(_("Row {0}: Stock Loan rows must match a Stock Item on the request").format(row.idx))
				if require_source_warehouse and not row.source_warehouse:
					frappe.throw(_("Row {0}: Select a Source Warehouse for this Stock Loan Item").format(row.idx))
				if row.source_warehouse and frappe.db.get_value("Warehouse", row.source_warehouse, "company") != self.company:
					frappe.throw(_("Row {0}: Source Warehouse must belong to Company {1}").format(row.idx, self.company))
				item = frappe.get_cached_value("Item", row.item_code, ["has_batch_no", "has_serial_no"], as_dict=True)
				if require_allocation and item.has_batch_no and not row.batch_no:
					frappe.throw(_("Row {0}: Select the Batch to issue").format(row.idx))
				if item.has_serial_no:
					if flt(row.qty) % 1:
						frappe.throw(_("Row {0}: Serial controlled items must have a whole number quantity").format(row.idx))
					serials = normalize_serials(row.serial_no)
					if require_allocation and len(serials) != cint(row.qty):
						frappe.throw(_("Row {0}: Select one Serial No for each whole item").format(row.idx))
					if len(serials) != len(set(serials)):
						frappe.throw(_("Row {0}: A Serial No can only be issued once").format(row.idx))
					if serials_seen.intersection(serials):
						frappe.throw(_("A Serial No can only be issued once per Loan"))
					serials_seen.update(serials)
					row.serial_no = "\n".join(serials)
			else:
				if flt(row.qty) != 1:
					frappe.throw(_("Row {0}: Asset Loans require one Fixed Asset per row"))
				if not request_item.get("asset") or row.asset != request_item.asset:
					frappe.throw(_("Row {0}: Asset must match the Asset selected on the Material Request Item").format(row.idx))
				if row.asset in assets_seen:
					frappe.throw(_("Asset {0} can only be listed once per Loan").format(row.asset))
				assets_seen.add(row.asset)
				asset = frappe.db.get_value(
					"Asset", row.asset, ["item_code", "company", "status", "location", "custodian"], as_dict=True
				)
				if not asset or asset.company != self.company or asset.item_code != row.item_code:
					frappe.throw(_("Row {0}: Asset must match the requested item and Company").format(row.idx))
				if asset.status in ("Draft", "Scrapped", "Sold", "Capitalized") or asset.custodian:
					frappe.throw(_("Row {0}: Asset is not available to loan").format(row.idx))
				if frappe.db.sql(
					"""select loan.name from `tabCustody Loan Item` item
					join `tabCustody Loan` loan on loan.name=item.parent
					where item.asset=%s and loan.docstatus=1
					and loan.status not in ('Returned', 'Closed', 'Cancelled') limit 1""",
					row.asset,
				):
					frappe.throw(_("Row {0}: Asset is already assigned to an active Custody Loan").format(row.idx))
				if not asset.location:
					frappe.throw(_("Row {0}: Asset must have a current Location").format(row.idx))
				if asset.location != request_item.source_location:
					frappe.throw(_("Row {0}: Asset has moved since the Material Request was submitted").format(row.idx))
				row.source_location = asset.location
				row.target_location = row.target_location or self.recipient_location
				if self.borrower_type != "Employee" or self.requires_shipment:
					if not row.target_location:
						frappe.throw(_("Row {0}: Select the recipient Location for this Asset").format(row.idx))
				if row.target_location and row.target_location == row.source_location:
					frappe.throw(_("Row {0}: Recipient Location must differ from the current Location").format(row.idx))
			request_row_name = request_item.name
			qty_by_request_item[request_row_name] = qty_by_request_item.get(request_row_name, 0) + flt(row.qty)

		for request_item_name, qty in qty_by_request_item.items():
			if qty > flt(request_rows[request_item_name].qty):
				frappe.throw(_("Loan Qty exceeds the requested quantity for Material Request row {0}").format(request_item_name))

	def before_submit(self):
		lock_material_request(self.material_request)
		self.validate_material_request()
		for row in self.items:
			if row.asset:
				lock_asset(row.asset)
		self.validate_items(require_allocation=False, require_source_warehouse=True)
		if frappe.db.exists(
			"Custody Loan",
			{"material_request": self.material_request, "loan_type": self.loan_type, "docstatus": 1},
		):
			frappe.throw(_("A submitted {0} Custody Loan already exists for this Material Request").format(self.loan_type))
		self.status = (
			"Pending Pick"
			if self.loan_type == "Stock"
			else "Active"
		)

	def on_submit(self):
		if self.loan_type == "Stock":
			pick_list = self.make_pick_list()
			self.db_set("pick_list", pick_list.name)
			self.pick_list = pick_list.name
		else:
			transaction = self.make_asset_issue()
			self.db_set("asset_movement", transaction.name)
			self.asset_movement = transaction.name
			transaction.submit()
		self.update_status()

	def make_pick_list(self):
		internal_customer = get_internal_loan_customer(self.company)
		if not internal_customer:
			frappe.throw(
				_("Create an Internal Customer representing Company {0} before issuing a Stock Loan").format(
					self.company
				)
			)
		request = frappe.get_cached_doc("Material Request", self.material_request)
		request_items = {row.name: row for row in request.items}
		pick_list = frappe.new_doc("Pick List")
		pick_list.set_new_name()
		pick_list.company = self.company
		pick_list.purpose = "Delivery"
		pick_list.pick_list_type = "Standard"
		pick_list.pick_manually = 1
		pick_list.customer = internal_customer
		pick_list.material_request = self.material_request
		pick_list.custody_loan = self.name
		pick_list.flags.ignore_permissions = True
		for row in self.items:
			request_item = request_items[row.material_request_item]
			pick_list.append(
				"locations",
				{
					"item_code": row.item_code,
					"warehouse": row.source_warehouse,
					"qty": row.qty,
					"stock_qty": row.qty * flt(request_item.conversion_factor or 1),
					"picked_qty": 0,
					"uom": request_item.uom,
					"stock_uom": request_item.stock_uom,
					"conversion_factor": request_item.conversion_factor or 1,
					"material_request": self.material_request,
					"material_request_item": row.material_request_item,
					"custody_loan_item": row.name,
					"batch_no": row.batch_no,
					"serial_no": row.serial_no,
					"use_serial_batch_fields": 1,
				}
			)
		self.db_set("pick_list", pick_list.name)
		self.pick_list = pick_list.name
		pick_list.insert(ignore_permissions=True)
		return pick_list

	@frappe.whitelist()
	def make_delivery_note(self):
		"""Create the standard Loan Delivery Note from the submitted native Pick List."""
		self.reload()
		self.check_permission("read")
		frappe.has_permission("Delivery Note", "create", throw=True)
		if self.docstatus != 1 or self.loan_type != "Stock":
			frappe.throw(_("Create a Loan Delivery Note from a submitted Stock Custody Loan"))

		lock_custody_loan(self.name)
		self.reload()
		if self.delivery_note:
			delivery_note_status = frappe.db.get_value("Delivery Note", self.delivery_note, "docstatus")
			if delivery_note_status in (0, 1):
				delivery_note = frappe.get_doc("Delivery Note", self.delivery_note)
				delivery_note.check_permission("read")
				return delivery_note.as_dict()

		if not self.pick_list or frappe.db.get_value("Pick List", self.pick_list, "docstatus") != 1:
			frappe.throw(_("Submit the Loan Pick List before creating a Loan Delivery Note"))
		pick_list = frappe.get_doc("Pick List", self.pick_list)
		validate_custody_loan_pick_list(pick_list, require_picked_qty=True)

		existing = frappe.db.get_value(
			"Delivery Note",
			{
				"custody_loan": self.name,
				"custody_loan_source_doctype": self.doctype,
				"custody_loan_source_name": self.name,
				"docstatus": ["<", 2],
			},
			"name",
		)
		if existing:
			self.db_set("delivery_note", existing)
			return frappe.get_doc("Delivery Note", existing).as_dict()

		from erpnext.stock.doctype.pick_list.pick_list import map_pl_locations

		delivery_note = frappe.new_doc("Delivery Note")
		delivery_note.company = self.company
		delivery_note.customer = pick_list.customer
		delivery_note.is_internal_customer = 1
		delivery_note.represents_company = self.company
		delivery_note.set_target_warehouse = self.customer_loan_warehouse
		delivery_note.custody_loan = self.name
		delivery_note.requires_shipment = cint(self.requires_shipment)
		map_pl_locations(
			pick_list,
			{"doctype": "Delivery Note Item", "field_map": {"rate": "rate", "name": "name", "parent": ""}},
			delivery_note,
		)
		# Internal stock transfers must be valued in the Company currency even if
		# the internal Customer has an unrelated selling currency configured.
		company_currency = frappe.get_cached_value("Company", self.company, "default_currency")
		delivery_note.currency = company_currency
		delivery_note.conversion_rate = 1
		delivery_note.price_list_currency = company_currency
		delivery_note.plc_conversion_rate = 1
		pick_list_rows = {row.name: row for row in pick_list.locations}
		for row in delivery_note.items:
			pick_list_row = pick_list_rows.get(row.pick_list_item)
			if not pick_list_row:
				frappe.throw(_("Every Loan Delivery Note row must come from the submitted Pick List"))
			row.custody_loan_item = pick_list_row.custody_loan_item
		loan_items = {row.name: row for row in self.items}
		for row in delivery_note.items:
			loan_item = loan_items.get(row.custody_loan_item)
			if not loan_item:
				frappe.throw(_("Every Loan Delivery Note row must come from a Custody Loan Item"))
			row.material_request = self.material_request
			row.material_request_item = loan_item.material_request_item
			row.target_warehouse = self.customer_loan_warehouse
		delivery_note.flags.ignore_mandatory = True
		delivery_note.flags.ignore_permissions = True
		set_loan_transaction_source(delivery_note, self, self)
		delivery_note.insert(ignore_permissions=True)
		self.db_set("delivery_note", delivery_note.name)
		self.delivery_note = delivery_note.name
		return delivery_note.as_dict()

	def capture_delivery_note_details(self, delivery_note=None):
		delivery_note = delivery_note or frappe.get_doc("Delivery Note", self.delivery_note)
		for row in delivery_note.items:
			loan_item = next((item for item in self.items if item.name == row.custody_loan_item), None)
			if not loan_item:
				continue
			if row.batch_no:
				loan_item.batch_no = row.batch_no
			if row.serial_no:
				loan_item.serial_no = row.serial_no
			elif row.serial_and_batch_bundle:
				loan_item.serial_no = "\n".join(get_serial_nos_from_bundle(row.serial_and_batch_bundle))
			loan_item.valuation_rate = row.incoming_rate or row.rate
			loan_item.db_update()

	def update_issued_quantities(self):
		is_received = transaction_is_received(self.delivery_note, self.asset_movement)
		issued_by_item = {}
		if is_received and self.loan_type == "Stock" and self.delivery_note:
			delivery_note = frappe.get_doc("Delivery Note", self.delivery_note)
			for row in delivery_note.items:
				if row.custody_loan_item:
					issued_by_item[row.custody_loan_item] = issued_by_item.get(row.custody_loan_item, 0) + abs(
						flt(row.qty)
					)
		elif is_received and self.loan_type == "Asset" and self.asset_movement:
			movement = frappe.get_doc("Asset Movement", self.asset_movement)
			for row in movement.assets:
				if row.custody_loan_item:
					issued_by_item[row.custody_loan_item] = 1

		for row in self.items:
			issued_qty = issued_by_item.get(row.name, 0)
			row.issued_qty = issued_qty
			if row.name and self.docstatus == 1:
				frappe.db.set_value("Custody Loan Item", row.name, "issued_qty", issued_qty)

	def make_asset_issue(self):
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Transfer" if self.borrower_type != "Employee" else "Issue"
		movement.reference_doctype = "Material Request"
		movement.reference_name = self.material_request
		movement.expected_return_date = self.expected_return_date
		movement.customer = self.borrower if self.borrower_type == "Customer" else None
		movement.customer_contact = self.recipient_contact
		movement.customer_address = self.recipient_address
		movement.customer_address_display = self.recipient_address_display
		movement.customer_contact_display = self.recipient_contact_display
		movement.customer_contact_email = self.recipient_contact_email
		set_loan_transaction_source(movement, self, self)
		for row in self.items:
			movement.append("assets", {
				"asset": row.asset,
				"asset_item_code": row.item_code,
				"request_item": row.material_request_item,
				"source_location": row.source_location,
				"target_location": row.target_location,
				"to_employee": self.borrower if self.borrower_type == "Employee" else None,
				"custody_loan_item": row.name,
				"demo_asset_required": cint(
					frappe.db.get_value("Material Request", self.material_request, "material_request_type")
					== "Customer Demo"
				),
			})
		movement.insert(ignore_permissions=True)
		return movement

	def before_cancel(self):
		if frappe.db.exists("Custody Loan Return", {"custody_loan": self.name, "docstatus": 1}):
			frappe.throw(_("Cancel submitted Loan Returns before cancelling the Custody Loan"))
		if frappe.db.exists("Custody Loan Adjustment", {"custody_loan": self.name, "docstatus": 1}):
			frappe.throw(_("Cancel submitted Loan Adjustments before cancelling the Custody Loan"))
		if self.sales_invoice and frappe.db.get_value("Sales Invoice", self.sales_invoice, "docstatus") == 1:
			frappe.throw(_("Cancel the linked Asset Sale Invoice before cancelling the Custody Loan"))
		self.cancel_generated_transaction("delivery_note")
		self.cancel_generated_transaction("asset_movement")
		self.cancel_generated_transaction("pick_list")

	def cancel_generated_transaction(self, fieldname):
		cancel_source_transaction(self, fieldname)

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")
		self.update_material_request_status()

	def refresh_balances(self):
		self.update_issued_quantities()
		for row in self.items:
			returned, adjusted, sold = get_loan_item_balances(self.name, row.name)
			row.returned_qty = returned
			row.adjusted_qty = adjusted
			row.sold_qty = sold
			row.outstanding_qty = max(flt(row.issued_qty) - returned - adjusted - sold, 0)
			if row.name and self.docstatus == 1:
				frappe.db.set_value("Custody Loan Item", row.name, {
					"returned_qty": returned,
					"adjusted_qty": adjusted,
					"sold_qty": sold,
					"outstanding_qty": row.outstanding_qty,
				})

	def update_status(self):
		if self.docstatus == 2:
			status = "Cancelled"
		elif self.docstatus != 1:
			status = "Draft"
		else:
			self.update_submitted_return_statuses()
			status = self.get_submitted_status()
		if status != self.status:
			self.status = status
			self.db_set("status", status)
		self.update_material_request_status()
		return status

	def get_submitted_status(self):
		if self.loan_type == "Stock" and (
			not self.pick_list or frappe.db.get_value("Pick List", self.pick_list, "docstatus") != 1
		):
			return "Pending Pick"
		if self.loan_type == "Stock" and (
			not self.delivery_note or frappe.db.get_value("Delivery Note", self.delivery_note, "docstatus") != 1
		):
			return "Pending Delivery Note"
		return_statuses = frappe.get_all(
			"Custody Loan Return",
			filters={"custody_loan": self.name, "docstatus": 1},
			pluck="status",
		)
		for pending_status in (
			"Pending Shipment",
			"Pending Quality Inspection",
			"Pending Receipt",
		):
			if pending_status in return_statuses:
				return pending_status
		if self.requires_shipment and not transaction_is_received(self.delivery_note, self.asset_movement):
			return "Pending Shipment"
		self.refresh_balances()
		issued = sum(flt(row.issued_qty) for row in self.items)
		returned = sum(flt(row.returned_qty) for row in self.items)
		adjusted = sum(flt(row.adjusted_qty) for row in self.items)
		sold = sum(flt(row.sold_qty) for row in self.items)
		outstanding = sum(flt(row.outstanding_qty) for row in self.items)
		if outstanding <= 0 and (adjusted or sold):
			return "Closed"
		if outstanding <= 0 and returned >= issued:
			return "Returned"
		if returned or adjusted or sold:
			return "Partially Returned"
		return "Active"

	def update_submitted_return_statuses(self):
		return_names = frappe.get_all(
			"Custody Loan Return",
			filters={"custody_loan": self.name, "docstatus": 1},
			pluck="name",
		)
		for return_name in return_names:
			frappe.get_doc("Custody Loan Return", return_name).update_status()

	def update_material_request_status(self):
		if not self.material_request or not frappe.db.exists("Material Request", self.material_request):
			return
		request = frappe.get_doc("Material Request", self.material_request)
		request.update_progress()
		request.db_set("request_progress_status", request.request_progress_status)

	@frappe.whitelist()
	def make_return(self):
		self.check_permission("read")
		frappe.has_permission("Custody Loan Return", "create", throw=True)
		if self.docstatus != 1 or self.status not in ("Active", "Partially Returned"):
			frappe.throw(_("Only an active submitted Custody Loan can be returned"))
		self.refresh_balances()
		return_doc = frappe.new_doc("Custody Loan Return")
		return_doc.company = self.company
		return_doc.custody_loan = self.name
		return_doc.loan_type = self.loan_type
		for row in self.items:
			available_to_receive = loan_item_available_to_receive(self.name, row)
			if available_to_receive <= 0:
				continue
			item = frappe.get_cached_value("Item", row.item_code, ["has_serial_no"], as_dict=True)
			values = {
				"custody_loan_item": row.name,
				"item_code": row.item_code,
				"returned_qty": available_to_receive,
			}
			if self.loan_type == "Stock":
				values["batch_no"] = row.batch_no
				if item.has_serial_no:
					open_serials = get_open_issued_serials(self.name, row)
					values["serial_no"] = "\n".join(open_serials[:cint(available_to_receive)])
				else:
					values["serial_no"] = row.serial_no
			else:
				values.update(asset=row.asset, target_location=row.source_location)
			return_doc.append("items", values)
		if not return_doc.items:
			frappe.throw(_("There is no outstanding quantity available to return"))
		return return_doc

	@frappe.whitelist()
	def make_adjustment(self):
		self.check_permission("read")
		frappe.has_permission("Custody Loan Adjustment", "create", throw=True)
		if (
			self.docstatus != 1
			or self.loan_type != "Stock"
			or self.status not in ("Active", "Partially Returned")
		):
			frappe.throw(_("Loan Adjustments are available for submitted Stock Loans only"))
		self.refresh_balances()
		account = frappe.db.get_value("Company", self.company, "stock_adjustment_account")
		if not account:
			frappe.throw(_("Set Company Stock Adjustment Account before creating a Loan Adjustment"))
		adjustment = frappe.new_doc("Custody Loan Adjustment")
		adjustment.company = self.company
		adjustment.custody_loan = self.name
		adjustment.adjustment_account = account
		for row in self.items:
			if row.outstanding_qty <= 0:
				continue
			open_serials = get_open_issued_serials(self.name, row)
			adjustment.append("items", {
				"custody_loan_item": row.name,
				"item_code": row.item_code,
				"qty": row.outstanding_qty,
				"batch_no": row.batch_no,
				"serial_no": "\n".join(open_serials) if open_serials else None,
			})
		if not adjustment.items:
			frappe.throw(_("There is no outstanding quantity to adjust"))
		return adjustment

	@frappe.whitelist()
	def make_asset_sale_invoice(self):
		self.reload()
		self.check_permission("read")
		frappe.has_permission("Sales Invoice", "create", throw=True)
		if (
			self.docstatus != 1
			or self.loan_type != "Asset"
			or self.borrower_type != "Customer"
			or self.status not in ("Active", "Partially Returned")
		):
			frappe.throw(_("Asset sale invoices are available for active Customer Asset Loans only"))

		lock_custody_loan(self.name)
		existing = frappe.db.get_value(
			"Sales Invoice",
			{"custody_loan": self.name, "docstatus": ["<", 2], "is_return": 0},
			"name",
		)
		if existing:
			invoice = frappe.get_doc("Sales Invoice", existing)
			invoice.check_permission("read")
			return invoice.as_dict()

		self.refresh_balances()
		available_items = [row for row in self.items if flt(row.outstanding_qty) > 0]
		if not available_items:
			frappe.throw(_("There are no outstanding Assets to sell"))

		invoice = frappe.new_doc("Sales Invoice")
		invoice.company = self.company
		invoice.customer = self.borrower
		invoice.set_missing_values()
		invoice.customer_address = self.recipient_address
		invoice.shipping_address_name = self.recipient_address
		invoice.contact_person = self.recipient_contact
		for loan_item in available_items:
			if not loan_item.asset:
				frappe.throw(_("Custody Loan Item {0} does not reference an Asset").format(loan_item.name))
			item = frappe.get_cached_value("Item", loan_item.item_code, ["stock_uom"], as_dict=True)
			item_details = get_item_details(
				frappe._dict(
					doctype="Sales Invoice",
					item_code=loan_item.item_code,
					customer=self.borrower,
					company=self.company,
					qty=1,
					uom=item.stock_uom,
					conversion_rate=invoice.conversion_rate or 1,
					currency=invoice.currency,
					selling_price_list=invoice.selling_price_list,
					price_list=invoice.selling_price_list,
					price_list_currency=invoice.price_list_currency,
					plc_conversion_rate=invoice.plc_conversion_rate or 1,
					transaction_date=invoice.posting_date,
				),
				doc=invoice,
			)
			invoice.append(
				"items",
				{
					**item_details,
					"item_code": loan_item.item_code,
					"is_fixed_asset": 1,
					"qty": 1,
					"uom": item.stock_uom,
					"conversion_factor": 1,
					"asset": loan_item.asset,
					"custody_loan_item": loan_item.name,
				},
			)
		invoice.custody_loan = self.name
		set_loan_transaction_source(invoice, self, self)
		invoice.set_income_account_for_fixed_assets()
		invoice.insert(ignore_permissions=True)
		self.db_set("sales_invoice", invoice.name)
		return frappe.get_doc("Sales Invoice", invoice.name).as_dict()

	@frappe.whitelist()
	def cancel_asset_sale_invoice(self):
		self.reload()
		self.check_permission("write")
		if not self.sales_invoice:
			frappe.throw(_("This Custody Loan has no linked Asset Sale Invoice"))
		invoice = frappe.get_doc("Sales Invoice", self.sales_invoice)
		if invoice.custody_loan != self.name:
			frappe.throw(_("The linked Asset Sale Invoice does not belong to this Custody Loan"))
		if invoice.docstatus == 0:
			invoice.check_permission("delete")
			self.db_set("sales_invoice", None)
			frappe.delete_doc("Sales Invoice", invoice.name, ignore_permissions=True, force=True)
			return invoice.name
		if invoice.docstatus != 1:
			frappe.throw(_("The linked Asset Sale Invoice is already cancelled"))
		invoice.check_permission("cancel")
		invoice.flags.custody_loan_cancellation = True
		invoice.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN
		invoice.cancel()
		self.update_status()
		return invoice.name


class CustodyLoanReturn(Document):
	def validate(self):
		self.set_loan_details()
		self.validate_items()

	def set_loan_details(self):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		if loan.docstatus != 1 or loan.status not in ("Active", "Partially Returned"):
			frappe.throw(_("Select an active submitted Custody Loan"))
		if self.company and self.company != loan.company:
			frappe.throw(_("Loan Return and Custody Loan must use the same Company"))
		self.company = loan.company
		self.loan_type = loan.loan_type
		if self.requires_shipment and not loan.recipient_address:
			frappe.throw(_("The linked Custody Loan has no Recipient Address for Shipment"))
		if self.requires_shipment and self.loan_type != "Stock":
			frappe.throw(_("Shipment is available only for Stock Custody Loan Returns"))
		if self.requires_shipment and self.loan_type == "Stock":
			selected = {row.custody_loan_item for row in self.items if row.custody_loan_item}
			warehouses = {row.source_warehouse for row in loan.items if row.name in selected}
			if len(warehouses) > 1:
				frappe.throw(_("A Stock Loan Return Shipment must go to one Source Warehouse"))

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one item to return"))
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		loan_items = {row.name: row for row in loan.items}
		qty_by_loan_item = {}
		seen_loan_items = set()
		serials_seen = set()
		for row in self.items:
			loan_item = loan_items.get(row.custody_loan_item)
			if not loan_item:
				frappe.throw(_("Row {0}: Loan Item must belong to the linked Custody Loan").format(row.idx))
			if loan_item.name in seen_loan_items:
				frappe.throw(_("Row {0}: A Loan Item can only appear once per Loan Return").format(row.idx))
			seen_loan_items.add(loan_item.name)
			if flt(row.returned_qty) <= 0:
				frappe.throw(_("Row {0}: Received Qty must be greater than zero").format(row.idx))
			qty_by_loan_item[row.custody_loan_item] = (
				qty_by_loan_item.get(row.custody_loan_item, 0) + flt(row.returned_qty)
			)
			row.item_code = loan_item.item_code
			if self.loan_type == "Asset":
				if flt(row.returned_qty) != 1 or row.asset != loan_item.asset:
					frappe.throw(_("Row {0}: Return the same Fixed Asset that was issued").format(row.idx))
				if flt(row.returned_qty) > loan_item_available_to_receive(self.custody_loan, loan_item):
					frappe.throw(_("Row {0}: This Asset has already been returned or adjusted").format(row.idx))
				row.target_location = row.target_location or loan_item.source_location
				if not row.target_location:
					frappe.throw(_("Row {0}: Select the Location receiving the Asset").format(row.idx))
				continue

			item = frappe.get_cached_value("Item", row.item_code, ["has_batch_no", "has_serial_no"], as_dict=True)
			if item.has_serial_no:
				serials = normalize_serials(row.serial_no)
				if len(serials) != cint(row.returned_qty) or len(serials) != len(set(serials)):
					frappe.throw(_("Row {0}: Select one unique Serial No for each returned whole item").format(row.idx))
				if serials_seen.intersection(serials):
					frappe.throw(_("A Serial No can only be received once per Loan Return"))
				serials_seen.update(serials)
				issued_serials = set(normalize_serials(loan_item.serial_no))
				if item.has_batch_no and not row.batch_no:
					frappe.throw(_("Row {0}: Select the Batch No actually received").format(row.idx))
				already_received = set(get_received_serials(self.custody_loan, loan_item.name))
				already_reserved = set(get_reserved_serials(self.custody_loan, loan_item.name))
				already_adjusted = set(get_adjusted_serials(self.custody_loan, loan_item.name))
				if set(serials).intersection(already_received | already_reserved | already_adjusted):
					frappe.throw(_("Row {0}: A returned or adjusted Serial No cannot be received again").format(row.idx))
				if any(
					serial not in issued_serials
					or (item.has_batch_no and row.batch_no != loan_item.batch_no)
					for serial in serials
				):
					frappe.throw(_("Row {0}: Return the original Serial Nos issued for this Loan").format(row.idx))
				row.serial_no = "\n".join(serials)
			elif item.has_batch_no:
				if not row.batch_no:
					frappe.throw(_("Row {0}: Select the Batch No actually received").format(row.idx))
				if row.batch_no != loan_item.batch_no:
					frappe.throw(_("Row {0}: Return the original Batch No issued for this Loan").format(row.idx))
		for loan_item_name, qty in qty_by_loan_item.items():
			available = loan_item_available_to_receive(self.custody_loan, loan_items[loan_item_name])
			if qty > available:
				frappe.throw(_("Received Qty exceeds the remaining quantity for Loan Item {0}").format(loan_item_name))

	def before_submit(self):
		lock_custody_loan(self.custody_loan)
		self.validate_items()

	def on_submit(self):
		if self.loan_type == "Asset":
			transaction = self.make_asset_receipt()
			self.db_set("asset_movement", transaction.name)
			self.asset_movement = transaction.name
			transaction.submit()
		else:
			transaction = self.make_stock_return_delivery_note()
			self.db_set("delivery_note", transaction.name)
			self.delivery_note = transaction.name
		self.update_status()
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		loan.update_status()

	def make_stock_return_delivery_note(self):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		if not loan.delivery_note or frappe.db.get_value("Delivery Note", loan.delivery_note, "docstatus") != 1:
			frappe.throw(_("Submit the original Loan Delivery Note before receiving stock"))
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

		delivery_note = make_sales_return(loan.delivery_note)
		return_items = {row.custody_loan_item: row for row in self.items}
		delivery_note.set(
			"items",
			[row for row in delivery_note.items if row.custody_loan_item in return_items],
		)
		return_items = {row.custody_loan_item: row for row in self.items}
		loan_item_by_name = {row.name: row for row in loan.items}
		original_rows = {
			row.custody_loan_item: row
			for row in frappe.get_doc("Delivery Note", loan.delivery_note).items
		}
		delivery_note.is_internal_customer = 1
		delivery_note.represents_company = loan.company
		delivery_note.set_target_warehouse = loan.customer_loan_warehouse
		for row in delivery_note.items:
			return_item = return_items[row.custody_loan_item]
			loan_item = loan_item_by_name[row.custody_loan_item]
			original_row = original_rows[row.custody_loan_item]
			row.qty = -flt(return_item.returned_qty)
			row.stock_qty = -flt(return_item.returned_qty) * flt(original_row.conversion_factor or 1)
			row.warehouse = loan_item.source_warehouse
			row.target_warehouse = loan.customer_loan_warehouse
			row.batch_no = return_item.batch_no or None
			row.serial_no = return_item.serial_no or None
			row.quality_inspection = None
			row.serial_and_batch_bundle = None
			row.against_pick_list = None
			row.pick_list_item = None
			row.use_serial_batch_fields = 1
			row.material_request = loan.material_request
			row.material_request_item = loan_item.material_request_item
		delivery_note.custody_loan = loan.name
		delivery_note.custody_loan_shipment = None
		delivery_note.is_custody_loan_transaction = 1
		delivery_note.custody_loan_source_doctype = self.doctype
		delivery_note.custody_loan_source_name = self.name
		delivery_note.requires_shipment = cint(self.requires_shipment)
		delivery_note.flags.custody_loan_generated = True
		delivery_note.flags.ignore_permissions = True
		set_loan_transaction_source(delivery_note, loan, self)
		delivery_note.insert(ignore_permissions=True)
		return delivery_note

	def make_asset_receipt(self):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Receipt"
		movement.reference_doctype = "Material Request"
		movement.reference_name = loan.material_request
		movement.expected_return_date = loan.expected_return_date
		movement.customer = loan.borrower if loan.borrower_type == "Customer" else None
		movement.customer_contact = loan.recipient_contact
		movement.customer_address = loan.recipient_address
		movement.customer_address_display = loan.recipient_address_display
		movement.customer_contact_display = loan.recipient_contact_display
		movement.customer_contact_email = loan.recipient_contact_email
		set_loan_transaction_source(movement, loan, self)
		for row in self.items:
			loan_item = frappe.get_doc("Custody Loan Item", row.custody_loan_item)
			movement.append("assets", {
				"asset": loan_item.asset,
				"asset_item_code": loan_item.item_code,
				"request_item": loan_item.material_request_item,
				"source_location": frappe.db.get_value("Asset", loan_item.asset, "location"),
				"target_location": row.target_location,
				"from_employee": loan.borrower if loan.borrower_type == "Employee" else None,
				"custody_loan_item": row.custody_loan_item,
			})
		movement.insert(ignore_permissions=True)
		return movement

	def update_status(self):
		status = update_return_status_on_transaction(self)
		if status != self.status:
			self.status = status
			self.db_set("status", status)
		return status

	def before_cancel(self):
		for fieldname in ("delivery_note", "asset_movement"):
			cancel_source_transaction(self, fieldname)
		if self.delivery_note:
			quality_inspections = frappe.get_all(
				"Quality Inspection",
				filters={"reference_type": "Delivery Note", "reference_name": self.delivery_note},
				pluck="name",
			)
			for quality_inspection_name in quality_inspections:
				quality_inspection = frappe.get_doc("Quality Inspection", quality_inspection_name)
				if quality_inspection.docstatus == 1:
					quality_inspection.cancel()
				elif quality_inspection.docstatus == 0:
					frappe.delete_doc("Quality Inspection", quality_inspection.name, force=True)

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")
		if frappe.db.exists("Custody Loan", self.custody_loan):
			frappe.get_doc("Custody Loan", self.custody_loan).update_status()


@frappe.whitelist()
def make_custody_loan_return_quality_inspections(delivery_note_name):
	delivery_note = frappe.get_doc("Delivery Note", delivery_note_name)
	delivery_note.check_permission("write")
	frappe.has_permission("Quality Inspection", "create", throw=True)
	if (
		delivery_note.docstatus != 0
		or not is_custody_loan_return_delivery_note(delivery_note)
	):
		frappe.throw(_("Prepare inspections only for a draft Custody Loan Return Delivery Note"))

	frappe.db.sql("select name from `tabDelivery Note` where name=%s for update", delivery_note.name)
	delivery_note.reload()
	return_doc = frappe.get_doc("Custody Loan Return", delivery_note.custody_loan_source_name)
	if return_doc.docstatus != 1 or return_doc.loan_type != "Stock" or return_doc.delivery_note != delivery_note.name:
		frappe.throw(_("Delivery Note must link to a submitted Stock Custody Loan Return"))
	validate_custody_loan_return_shipment_arrival(delivery_note, return_doc)
	return_items = {row.custody_loan_item: row for row in return_doc.items}
	quality_inspection_names = []
	for row in delivery_note.items:
		if row.custody_loan_item not in return_items:
			frappe.throw(_("Delivery Note row {0} does not match a Loan Return item").format(row.idx))
		if row.quality_inspection:
			quality_inspection = frappe.get_doc("Quality Inspection", row.quality_inspection)
			if quality_inspection.docstatus == 0 or (
				quality_inspection.docstatus == 1 and quality_inspection.status == "Accepted"
			):
				validate_custody_loan_return_quality_inspection(quality_inspection, delivery_note.name)
				quality_inspection_names.append(quality_inspection.name)
				continue

		quality_inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"company": delivery_note.company,
				"inspection_type": "Incoming",
				"reference_type": "Delivery Note",
				"reference_name": delivery_note.name,
				"child_row_reference": row.name,
				"item_code": row.item_code,
				"item_serial_no": normalize_serials(row.serial_no)[0]
				if normalize_serials(row.serial_no)
				else None,
				"batch_no": row.batch_no,
				"sample_size": abs(flt(row.qty)),
				"manual_inspection": 1,
				"inspected_by": frappe.session.user,
			}
		)
		quality_inspection.insert()
		quality_inspection.update_qc_reference()
		quality_inspection_names.append(quality_inspection.name)

	return_doc.update_status()
	frappe.get_doc("Custody Loan", return_doc.custody_loan).update_status()
	return quality_inspection_names


def is_custody_loan_return_delivery_note(doc):
	return bool(
		doc.is_return
		and doc.get("is_custody_loan_transaction")
		and doc.get("custody_loan_source_doctype") == "Custody Loan Return"
	)


def validate_custody_loan_return_quality_inspection(quality_inspection, delivery_note_name):
	delivery_note = frappe.get_doc("Delivery Note", delivery_note_name)
	if not is_custody_loan_return_delivery_note(delivery_note):
		frappe.throw(_("Quality Inspection must reference a Custody Loan Return Delivery Note"))
	return_doc = frappe.get_doc("Custody Loan Return", delivery_note.custody_loan_source_name)
	if return_doc.docstatus != 1 or return_doc.loan_type != "Stock":
		frappe.throw(_("Quality Inspection must reference a submitted Stock Custody Loan Return"))
	if quality_inspection.reference_type != "Delivery Note" or quality_inspection.reference_name != delivery_note.name:
		frappe.throw(_("Quality Inspection must reference the Loan Return Delivery Note"))
	row = frappe.db.get_value(
		"Delivery Note Item",
		quality_inspection.child_row_reference,
		["parent", "item_code", "qty", "batch_no", "serial_no", "custody_loan_item"],
		as_dict=True,
	)
	if (
		not row
		or row.parent != delivery_note.name
		or row.item_code != quality_inspection.item_code
		or row.custody_loan_item not in {item.custody_loan_item for item in return_doc.items}
	):
		frappe.throw(_("Quality Inspection must reference a Loan Return item on this Delivery Note"))
	if quality_inspection.inspection_type != "Incoming" or not quality_inspection.manual_inspection:
		frappe.throw(_("Loan Return Quality Inspections must record a manual incoming inspection"))
	if flt(quality_inspection.sample_size) <= 0 or flt(quality_inspection.sample_size) > abs(flt(row.qty)):
		frappe.throw(_("Quality Inspection sample size must be within the returned quantity"))
	if row.batch_no and quality_inspection.batch_no != row.batch_no:
		frappe.throw(_("Quality Inspection Batch No must match the returned Batch No"))
	serials = set(normalize_serials(row.serial_no))
	if quality_inspection.item_serial_no and quality_inspection.item_serial_no not in serials:
		frappe.throw(_("Quality Inspection Serial No must match a returned Serial No"))


def validate_custody_loan_return_receipt(delivery_note, method=None):
	if not is_custody_loan_return_delivery_note(delivery_note):
		return
	return_doc = frappe.get_doc("Custody Loan Return", delivery_note.custody_loan_source_name)
	if return_doc.loan_type != "Stock" or return_doc.docstatus != 1:
		frappe.throw(_("Only a submitted Stock Custody Loan Return can be received through a Delivery Note"))
	validate_custody_loan_return_shipment_arrival(delivery_note, return_doc)
	return_items = {row.custody_loan_item for row in return_doc.items}
	seen_items = set()
	for row in delivery_note.items:
		if row.custody_loan_item not in return_items or row.custody_loan_item in seen_items:
			frappe.throw(_("Delivery Note row {0} does not match one unique Loan Return item").format(row.idx))
		seen_items.add(row.custody_loan_item)
		if not row.quality_inspection:
			frappe.throw(_("Create and submit an accepted Quality Inspection for Delivery Note row {0} before receiving the Loan Return").format(row.idx))
		quality_inspection = frappe.get_doc("Quality Inspection", row.quality_inspection)
		validate_custody_loan_return_quality_inspection(quality_inspection, delivery_note.name)
		if quality_inspection.child_row_reference != row.name:
			frappe.throw(_("Quality Inspection must match Delivery Note row {0}").format(row.idx))
		if quality_inspection.docstatus != 1 or quality_inspection.status != "Accepted":
			frappe.throw(_("Delivery Note row {0} requires a submitted accepted Quality Inspection").format(row.idx))
	if seen_items != return_items:
		frappe.throw(_("Loan Return Delivery Note must include every returned item exactly once"))


def validate_custody_loan_return_shipment_arrival(delivery_note, return_doc):
	if not return_doc.requires_shipment:
		return
	shipment_name = delivery_note.get("custody_loan_shipment")
	shipment = (
		frappe.db.get_value("Shipment", shipment_name, ["docstatus", "tracking_status"], as_dict=True)
		if shipment_name
		else None
	)
	if not shipment or shipment.docstatus != 1 or shipment.tracking_status != "Delivered":
		frappe.throw(_("Mark the Loan Return Shipment as Delivered before inspection or receipt"))


def update_custody_loan_return_status_from_quality_inspection(quality_inspection):
	delivery_note_name = get_custody_loan_return_delivery_note(quality_inspection)
	if not delivery_note_name:
		return
	source_name = frappe.db.get_value("Delivery Note", delivery_note_name, "custody_loan_source_name")
	if not source_name or not frappe.db.exists("Custody Loan Return", source_name):
		return
	return_doc = frappe.get_doc("Custody Loan Return", source_name)
	if return_doc.docstatus != 1:
		return
	return_doc.update_status()
	frappe.get_doc("Custody Loan", return_doc.custody_loan).update_status()


class CustodyLoanAdjustment(Document):
	def validate(self):
		loan = self.validate_loan()
		self.validate_items(loan)

	def validate_loan(self):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		if (
			loan.docstatus != 1
			or loan.loan_type != "Stock"
			or loan.status not in ("Active", "Partially Returned")
		):
			frappe.throw(_("Loan Adjustments require a submitted Stock Custody Loan"))
		if self.company and self.company != loan.company:
			frappe.throw(_("Loan Adjustment and Custody Loan must use the same Company"))
		self.company = loan.company
		account = frappe.db.get_value("Company", loan.company, "stock_adjustment_account")
		if not account or self.adjustment_account != account:
			frappe.throw(_("Loan Adjustment must use Company Stock Adjustment Account {0}").format(account or ""))
		return loan

	def validate_items(self, loan):
		if not self.items:
			frappe.throw(_("Add at least one Stock Item to adjust"))
		loan_items = {row.name: row for row in loan.items}
		qty_by_loan_item = {}
		serials_seen = set()
		for row in self.items:
			loan_item = loan_items.get(row.custody_loan_item)
			if not loan_item:
				frappe.throw(_("Row {0}: Loan Item must belong to the linked Custody Loan").format(row.idx))
			row.item_code = loan_item.item_code
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Adjusted Qty must be positive").format(row.idx))
			qty_by_loan_item[row.custody_loan_item] = qty_by_loan_item.get(row.custody_loan_item, 0) + flt(row.qty)
			item = frappe.get_cached_value("Item", loan_item.item_code, ["has_batch_no", "has_serial_no"], as_dict=True)
			if item.has_batch_no and row.batch_no != loan_item.batch_no:
				frappe.throw(_("Row {0}: Adjustment must use the original Batch No").format(row.idx))
			if item.has_serial_no:
				serials = normalize_serials(row.serial_no)
				open_serials = set(get_open_issued_serials(self.custody_loan, loan_item))
				if len(serials) != cint(row.qty) or len(serials) != len(set(serials)):
					frappe.throw(_("Row {0}: Select one unique original Serial No for each adjusted item").format(row.idx))
				if serials_seen.intersection(serials):
					frappe.throw(_("An issued Serial No can only be adjusted once per Loan Adjustment"))
				serials_seen.update(serials)
				if not set(serials).issubset(open_serials):
					frappe.throw(_("Row {0}: Adjustment can only write off outstanding original Serial Nos").format(row.idx))
				row.serial_no = "\n".join(serials)
		for loan_item_name, qty in qty_by_loan_item.items():
			loan_item = loan_items[loan_item_name]
			if qty > loan_item_available_to_adjust(self.custody_loan, loan_item):
				frappe.throw(_("Adjusted Qty exceeds the original outstanding Qty for Loan Item {0}").format(loan_item_name))

	def before_submit(self):
		lock_custody_loan(self.custody_loan)
		self.validate_items(frappe.get_doc("Custody Loan", self.custody_loan))
		self.status = "Submitted"

	def on_submit(self):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		stock_entry = new_stock_entry(self.company, "Material Issue")
		set_loan_transaction_source(stock_entry, loan, self)
		for row in self.items:
			loan_item = frappe.get_doc("Custody Loan Item", row.custody_loan_item)
			stock_entry.append("items", {
				"item_code": loan_item.item_code,
				"qty": row.qty,
				"s_warehouse": loan.customer_loan_warehouse,
				"custody_loan_item": loan_item.name,
				"batch_no": row.batch_no,
				"serial_no": row.serial_no,
				"expense_account": self.adjustment_account,
			})
		stock_entry.insert(ignore_permissions=True)
		self.db_set("stock_entry", stock_entry.name)
		self.stock_entry = stock_entry.name
		stock_entry.submit()
		loan.update_status()

	def before_cancel(self):
		cancel_source_transaction(self, "stock_entry")

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		frappe.get_doc("Custody Loan", self.custody_loan).update_status()


def new_stock_entry(company, purpose):
	stock_entry_type = frappe.db.get_value("Stock Entry Type", {"purpose": purpose}, "name")
	if not stock_entry_type:
		frappe.throw(_("A {0} Stock Entry Type is required").format(purpose))
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = company
	stock_entry.stock_entry_type = stock_entry_type
	stock_entry.purpose = purpose
	stock_entry.flags.ignore_permissions = True
	stock_entry.flags.custody_loan_generated = True
	return stock_entry


def set_loan_transaction_source(transaction, loan, source):
	transaction.is_system_generated = 1
	transaction.is_custody_loan_transaction = 1
	transaction.custody_loan = loan.name
	transaction.custody_loan_source_doctype = source.doctype
	transaction.custody_loan_source_name = source.name
	transaction.flags.custody_loan_generated = True
	transaction.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN


def validate_generated_transaction(doc, method=None):
	trusted_system_generation = doc.flags.get("custody_loan_generated") or doc.flags.get("system_generated")
	stored_system_generated = None
	if doc.name and frappe.db.exists(doc.doctype, doc.name):
		stored_system_generated = cint(frappe.db.get_value(doc.doctype, doc.name, "is_system_generated"))
	if not trusted_system_generation and (
		cint(doc.get("is_system_generated")) != cint(stored_system_generated or 0)
		or (stored_system_generated is None and cint(doc.get("is_system_generated")))
	):
		frappe.throw(_("Is System Generated can only be set by a server-side process"))

	source_doctype = doc.get("custody_loan_source_doctype")
	source_name = doc.get("custody_loan_source_name")
	marked = cint(doc.get("is_custody_loan_transaction"))
	if not source_doctype and not source_name and not marked and not doc.get("custody_loan"):
		return
	if source_doctype not in LOAN_SOURCE_DOCTYPES or not source_name or not doc.get("custody_loan"):
		frappe.throw(_("Custody Loan transaction markers require a valid Loan source document"))

	if not doc.flags.get("custody_loan_generated"):
		stored = frappe.db.get_value(
			doc.doctype,
			doc.name,
			["is_custody_loan_transaction", "custody_loan", "custody_loan_source_doctype", "custody_loan_source_name"],
			as_dict=True,
		)
		if not stored or not stored.is_custody_loan_transaction or any(
			stored.get(field) != doc.get(field)
			for field in ("custody_loan", "custody_loan_source_doctype", "custody_loan_source_name")
		):
			frappe.throw(_("Custody Loan transaction links can only be set by the Custody Loan module"))

	loan = frappe.get_doc("Custody Loan", doc.custody_loan)
	if loan.docstatus != 1:
		frappe.throw(_("Generated transaction must reference a submitted Custody Loan"))
	if loan.company != doc.company:
		frappe.throw(_("Generated transaction must use the Custody Loan Company"))
	if source_doctype == "Custody Loan":
		valid_source = source_name == loan.name
	else:
		valid_source = frappe.db.get_value(source_doctype, source_name, "custody_loan") == loan.name
	if not valid_source:
		frappe.throw(_("Custody Loan source document does not belong to the linked Loan"))
	if doc.doctype in ("Stock Entry", "Delivery Note") and loan.loan_type != "Stock":
		frappe.throw(_("Generated document type does not match the Custody Loan Type"))
	if doc.doctype in ("Asset Movement", "Sales Invoice") and loan.loan_type != "Asset":
		frappe.throw(_("Generated document type does not match the Custody Loan Type"))
	for row in doc.get("items", []) if doc.doctype in ("Stock Entry", "Delivery Note", "Sales Invoice") else doc.get("assets", []):
		loan_item_name = row.get("custody_loan_item")
		loan_item = (
			frappe.db.get_value("Custody Loan Item", loan_item_name, ["parent", "item_code"], as_dict=True)
			if loan_item_name
			else None
		)
		item_code = row.get("item_code") if doc.doctype != "Asset Movement" else row.get("asset_item_code")
		if not loan_item or loan_item.parent != loan.name or item_code != loan_item.item_code:
			frappe.throw(_("Every generated transaction row must link to an item on the Custody Loan"))

	if doc.doctype == "Delivery Note":
		validate_custody_loan_delivery_note(doc, loan, source_doctype, source_name)
	elif doc.doctype == "Sales Invoice":
		validate_custody_loan_sales_invoice(doc, loan, source_doctype, source_name)

	doc.is_system_generated = 1
	doc.is_custody_loan_transaction = 1


def validate_generated_transaction_cancellation(doc, method=None):
	if not doc.get("is_custody_loan_transaction"):
		return
	if not doc.flags.get("custody_loan_cancellation"):
		frappe.throw(_("Cancel this generated transaction from its Custody Loan, Loan Return or Loan Adjustment"))

	source_doctype = doc.get("custody_loan_source_doctype")
	source_name = doc.get("custody_loan_source_name")
	loan_name = doc.get("custody_loan")
	if not check_custody_loan_source(source_doctype, source_name, loan_name):
		frappe.throw(_("Custody Loan transaction source does not match the linked Loan"))

	source = frappe.get_doc(source_doctype, source_name)
	if source.doctype != doc.get("custody_loan_source_doctype"):
		frappe.throw(_("Custody Loan transaction source does not match this document"))
	if source.doctype != "Custody Loan" and source.custody_loan != loan_name:
		frappe.throw(_("Custody Loan transaction source does not match this document"))

	loan = frappe.get_doc("Custody Loan", loan_name)
	for source_type in LOAN_SOURCE_DOCTYPES:
		if not frappe.db.table_exists(source_type):
			continue
		for field in frappe.get_meta(source_type).fields:
			if field.fieldtype != "Link" or field.options != doc.doctype:
				continue
			linked_names = frappe.get_all(
				source_type, filters={field.fieldname: doc.name}, pluck="name"
			)
			if any(name != source_name for name in linked_names):
				frappe.throw(_("Generated transaction is linked to another active Custody Loan document"))

	expected_request_items = {row.material_request_item for row in loan.items if row.material_request_item}
	link_sources = {
		"stock_entry": (("Material Request Item", expected_request_items),),
		"delivery_note": (("Material Request Item", expected_request_items),),
		"asset_movement": (("Material Request Item", expected_request_items),),
	}
	for fieldname, sources in link_sources.items():
		for child_doctype, expected_names in sources:
			if not frappe.db.has_column(child_doctype, fieldname):
				continue
			linked_rows = frappe.get_all(
				child_doctype,
				filters={fieldname: doc.name},
				fields=["name", "parent"],
			)
			if any(
				row.parent != loan.material_request or row.name not in expected_names
				for row in linked_rows
			):
				frappe.throw(_("Generated transaction is linked to an unrelated Material Request"))

	doc.ignore_linked_doctypes = tuple(LOAN_SOURCE_DOCTYPES | {"Material Request"})
	if doc.doctype == "Delivery Note" and source_doctype == "Custody Loan Return":
		# The owning Return cancels its row inspections after reversing receipt.
		# Inspections cannot be cancelled while that receipt is still submitted.
		doc.ignore_linked_doctypes += ("Quality Inspection",)


def validate_custody_loan_delivery_note(doc, loan, source_doctype, source_name):
	if not doc.is_internal_customer or doc.represents_company != loan.company:
		frappe.throw(_("Custody Loan Delivery Notes must use the Internal Customer for the Loan Company"))
	if doc.customer != frappe.db.get_value(
		"Customer", {"is_internal_customer": 1, "represents_company": loan.company}, "name"
	):
		frappe.throw(_("Custody Loan Delivery Note must use the Internal Customer representing its Company"))
	if source_doctype == "Custody Loan":
		if source_name != loan.name or doc.is_return:
			frappe.throw(_("Loan issue Delivery Note must be a non-return Delivery Note linked to the Custody Loan"))
		if doc.requires_shipment and doc._action == "submit":
			validate_delivery_note_shipment_link(doc)
		if not loan.pick_list or frappe.db.get_value("Pick List", loan.pick_list, "docstatus") != 1:
			frappe.throw(_("Submit the linked Loan Pick List before creating its Delivery Note"))
		pick_list_items = {row.name: row for row in frappe.get_doc("Pick List", loan.pick_list).locations}
		loan_items = {row.name: row for row in loan.items}
		seen_loan_items = set()
		for row in doc.items:
			loan_item = loan_items.get(row.custody_loan_item)
			pick_row = pick_list_items.get(row.pick_list_item)
			if (
				not loan_item
				or not pick_row
				or loan_item.name in seen_loan_items
				or pick_row.custody_loan_item != loan_item.name
				or row.against_pick_list != loan.pick_list
				or flt(row.qty, 9) != flt(loan_item.qty, 9)
				or row.warehouse != loan_item.source_warehouse
				or row.target_warehouse != loan.customer_loan_warehouse
				or flt(pick_row.picked_qty, 9) != flt(pick_row.stock_qty, 9)
				or flt(row.stock_qty, 9) != flt(pick_row.picked_qty, 9)
			):
				frappe.throw(_("Loan issue Delivery Note row does not match the Custody Loan item and warehouses"))
			seen_loan_items.add(loan_item.name)
		if seen_loan_items != set(loan_items):
			frappe.throw(_("Loan Delivery Note must include every Custody Loan Item exactly once"))
		return

	if source_doctype != "Custody Loan Return" or not doc.is_return or doc.return_against != loan.delivery_note:
		frappe.throw(_("Loan return Delivery Note must return against the original Loan Delivery Note"))
	if doc.requires_shipment and doc._action == "submit":
		validate_delivery_note_shipment_link(doc)
	return_items = {
		row.custody_loan_item: row
		for row in frappe.get_doc("Custody Loan Return", source_name).items
	}
	loan_items = {row.name: row for row in loan.items}
	seen_return_items = set()
	for row in doc.items:
		loan_item = loan_items.get(row.custody_loan_item)
		return_item = return_items.get(row.custody_loan_item)
		if not loan_item or not return_item or row.custody_loan_item in seen_return_items:
			frappe.throw(_("Loan return Delivery Note row must match a Custody Loan Return item"))
		if (
			flt(row.qty) != -flt(return_item.returned_qty)
			or row.warehouse != loan_item.source_warehouse
			or row.target_warehouse != loan.customer_loan_warehouse
			or row.dn_detail != frappe.db.get_value(
				"Delivery Note Item", {"parent": loan.delivery_note, "custody_loan_item": loan_item.name}, "name"
			)
		):
			frappe.throw(_("Loan return Delivery Note row does not reverse the original stock transfer"))
		seen_return_items.add(row.custody_loan_item)
	if seen_return_items != set(return_items):
		frappe.throw(_("Loan Return Delivery Note must include every returned item exactly once"))


def validate_delivery_note_shipment_link(doc):
	shipment_name = doc.get("custody_loan_shipment")
	if not shipment_name or frappe.db.get_value("Shipment", shipment_name, "docstatus") != 1:
		frappe.throw(_("Submit the linked Loan Shipment before submitting this Delivery Note"))
	if not frappe.db.exists(
		"Shipment Document",
		{"parent": shipment_name, "reference_doctype": "Delivery Note", "reference_name": doc.name},
	):
		frappe.throw(_("Link the Loan Delivery Note to its Shipment before submission"))


def validate_custody_loan_sales_invoice(doc, loan, source_doctype, source_name):
	if doc.company != loan.company or loan.loan_type != "Asset":
		frappe.throw(_("Asset Sale Invoice must use the linked Asset Custody Loan Company"))
	if doc.is_return:
		original = frappe.get_doc("Sales Invoice", doc.return_against) if doc.return_against else None
		if (
			not original
			or original.docstatus != 1
			or original.is_return
			or original.custody_loan != loan.name
		):
			frappe.throw(_("Custody Loan Asset Sale returns must reference a submitted sale invoice for this Loan"))
		if doc._action == "submit":
			lock_custody_loan(loan.name)
			loan_items = {row.name: row for row in loan.items}
			original_items = {row.name: row for row in original.items}
			seen_assets = set()
			for row in doc.items:
				loan_item = loan_items.get(row.custody_loan_item)
				original_row = original_items.get(row.sales_invoice_item)
				if (
					not loan_item
					or not original_row
					or original_row.custody_loan_item != loan_item.name
					or not loan_item.asset
					or row.asset != loan_item.asset
					or row.item_code != loan_item.item_code
					or not row.is_fixed_asset
					or flt(row.qty) != -1
					or row.asset in seen_assets
				):
					frappe.throw(_("Every credit note row must return one Asset from its original Custody Loan sale"))
				seen_assets.add(row.asset)
				if loan_item_available_to_receive(loan.name, loan_item) != 0:
					frappe.throw(_("Asset {0} is not currently sold on this Custody Loan").format(row.asset))
		return
	else:
		if (
			source_doctype != "Custody Loan"
			or source_name != loan.name
			or loan.borrower_type != "Customer"
			or doc.customer != loan.borrower
		):
			frappe.throw(_("Only the Customer on an Asset Custody Loan can be invoiced for its Assets"))
	if doc.update_stock:
		frappe.throw(_("Sales Invoices for Assets on a Custody Loan must not update Stock"))
	if doc._action != "submit":
		return

	lock_custody_loan(loan.name)
	seen_assets = set()
	loan_items = {row.name: row for row in loan.items}
	for row in doc.items:
		loan_item = loan_items.get(row.custody_loan_item)
		if (
			not loan_item
			or not loan_item.asset
			or row.asset != loan_item.asset
			or row.item_code != loan_item.item_code
			or not row.is_fixed_asset
			or flt(row.qty) != 1
			or row.asset in seen_assets
		):
			frappe.throw(_("Every sale row must invoice one available Asset from this Custody Loan"))
		seen_assets.add(row.asset)
		if loan_item_available_to_receive(loan.name, loan_item) < 1:
			frappe.throw(_("Asset {0} has already been returned, adjusted or sold").format(row.asset))


def validate_asset_movement_custody_loan(doc):
	loan = frappe.get_doc("Custody Loan", doc.custody_loan)
	if loan.loan_type != "Asset" or loan.docstatus != 1 or loan.company != doc.company:
		frappe.throw(_("Asset Movement must reference a submitted Asset Custody Loan for the same Company"))
	if doc.reference_doctype != "Material Request" or doc.reference_name != loan.material_request:
		frappe.throw(_("Asset Movement must link to the Material Request that created the Custody Loan"))
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
		loan.recipient_contact_email
		or request.loan_recipient_contact_email
		or request.customer_contact_email
	)
	doc.expected_return_date = loan.expected_return_date
	doc.customer = loan.borrower if loan.borrower_type == "Customer" else None
	doc.customer_contact = recipient_contact
	doc.customer_address = recipient_address
	doc.customer_address_display = recipient_address_display
	doc.customer_contact_display = recipient_contact_display
	doc.customer_contact_email = recipient_contact_email
	doc.recipient_type = loan.borrower_type
	doc.recipient = loan.borrower
	source_type = doc.get("custody_loan_source_doctype")
	if source_type not in ("Custody Loan", "Custody Loan Return"):
		frappe.throw(_("Asset Movement must be generated by a Custody Loan or Loan Return"))
	items = {row.name: row for row in loan.items}
	is_return = source_type == "Custody Loan Return"
	expected_purpose = "Receipt" if is_return else ("Issue" if loan.borrower_type == "Employee" else "Transfer")
	if doc.purpose != expected_purpose:
		frappe.throw(_("Asset Movement Purpose must be {0} for this Custody Loan").format(expected_purpose))
	for row in doc.assets:
		loan_item = items.get(row.custody_loan_item)
		if not loan_item or row.asset != loan_item.asset or row.request_item != loan_item.material_request_item:
			frappe.throw(_("Asset Movement row does not match an Asset on the linked Custody Loan"))
		if is_return:
			if row.target_location != loan_item.source_location:
				frappe.throw(_("Returned Asset must go back to its original Location"))
		elif loan.borrower_type == "Employee":
			if row.to_employee != loan.borrower:
				frappe.throw(_("Asset Movement must issue the Asset to the Loan Employee"))
		elif row.target_location != loan_item.target_location:
			frappe.throw(_("Asset Movement must send the Asset to the recipient Location on the Loan"))


def update_custody_loan_status(doc, method=None):
	if doc.doctype == "Shipment" and doc.get("custody_loan"):
		frappe.get_doc("Custody Loan", doc.custody_loan).update_status()
		return
	source_doctype = doc.get("custody_loan_source_doctype")
	source_name = doc.get("custody_loan_source_name")
	if not source_doctype or not source_name:
		return
	if source_doctype == "Custody Loan":
		loan = frappe.get_doc("Custody Loan", source_name)
		if doc.doctype == "Delivery Note" and method == "on_submit":
			loan.capture_delivery_note_details(doc)
		loan.update_status()
	elif source_doctype == "Custody Loan Return":
		return_doc = frappe.get_doc(source_doctype, source_name)
		return_doc.update_status()
		frappe.get_doc("Custody Loan", return_doc.custody_loan).update_status()
	elif source_doctype == "Custody Loan Adjustment":
		adjustment = frappe.get_doc(source_doctype, source_name)
		frappe.get_doc("Custody Loan", adjustment.custody_loan).update_status()


def get_internal_loan_customer(company):
	return frappe.db.get_value(
		"Customer",
		{"is_internal_customer": 1, "represents_company": company, "disabled": 0},
		"name",
	)


def validate_custody_loan_pick_list(pick_list, require_picked_qty=False, cancelling=False):
	loan = frappe.get_doc("Custody Loan", pick_list.custody_loan)
	if loan.loan_type != "Stock" or loan.docstatus != 1 or loan.company != pick_list.company:
		frappe.throw(_("Pick List must reference a submitted Stock Custody Loan for the same Company"))
	if loan.pick_list != pick_list.name:
		frappe.throw(_("Pick List is not the Pick List linked to this Custody Loan"))
	if (
		pick_list.purpose != "Delivery"
		or pick_list.material_request != loan.material_request
		or pick_list.customer != get_internal_loan_customer(loan.company)
	):
		frappe.throw(_("Loan Pick List must use the linked Material Request and Internal Customer"))
	if cancelling or getattr(pick_list, "_action", None) == "cancel":
		if loan.delivery_note and frappe.db.get_value("Delivery Note", loan.delivery_note, "docstatus") in (0, 1):
			frappe.throw(_("Cancel the Loan Delivery Note from the Custody Loan before cancelling its Pick List"))
		if not pick_list.flags.get("custody_loan_cancellation"):
			frappe.throw(_("Cancel this Pick List from its Custody Loan"))
		return
	if not pick_list.locations:
		frappe.throw(_("Loan Pick List must contain the Stock Items on the Custody Loan"))

	loan_items = {row.name: row for row in loan.items}
	seen_loan_items = set()
	for row in pick_list.locations:
		loan_item = loan_items.get(row.custody_loan_item)
		if not loan_item or loan_item.name in seen_loan_items:
			frappe.throw(_("Pick List row {0} must link to one unique Custody Loan Item").format(row.idx))
		seen_loan_items.add(loan_item.name)
		request_item = frappe.db.get_value(
			"Material Request Item",
			loan_item.material_request_item,
			["parent", "item_code", "uom", "stock_uom", "conversion_factor"],
			as_dict=True,
		)
		conversion_factor = flt(request_item.conversion_factor or 1) if request_item else 0
		if (
			not request_item
			or request_item.parent != loan.material_request
			or request_item.item_code != row.item_code
			or row.material_request != loan.material_request
			or row.material_request_item != loan_item.material_request_item
			or row.warehouse != loan_item.source_warehouse
			or flt(row.qty, 9) != flt(loan_item.qty, 9)
			or row.uom != request_item.uom
			or row.stock_uom != request_item.stock_uom
			or flt(row.conversion_factor, 9) != flt(conversion_factor, 9)
			or flt(row.stock_qty, 9) != flt(loan_item.qty * conversion_factor, 9)
		):
			frappe.throw(_("Pick List row {0} must match the linked Custody Loan Item and Source Warehouse").format(row.idx))
		if require_picked_qty and flt(row.picked_qty, 9) != flt(row.stock_qty, 9):
			frappe.throw(_("Pick List row {0}: Pick the full Loan Qty before submitting").format(row.idx))

	if seen_loan_items != set(loan_items):
		frappe.throw(_("Pick List must include every Custody Loan Item exactly once"))


def update_custody_loan_pick_list_status(pick_list):
	if not pick_list.custody_loan or not frappe.db.exists("Custody Loan", pick_list.custody_loan):
		return
	frappe.get_doc("Custody Loan", pick_list.custody_loan).update_status()


def make_custody_loan_from_request(request, loan_type):
	request.check_permission("read")
	frappe.has_permission("Custody Loan", "create", throw=True)
	request.reload()
	if request.docstatus != 1 or request.material_request_type not in ("Customer Demo", "Customer Borrow / Loan"):
		frappe.throw(_("Submit a Customer Demo or Borrow Material Request first"))
	if loan_type not in ("Stock", "Asset"):
		frappe.throw(_("Select Stock or Asset as the Loan Type"))
	expected_loan_type = (
		"Asset"
		if request.material_request_type == "Customer Demo" or request.borrow_item_type == "Asset"
		else "Stock"
	)
	if loan_type != expected_loan_type:
		frappe.throw(_("Create a {0} Custody Loan to match the Material Request").format(expected_loan_type))
	lock_material_request(request.name)
	existing = frappe.db.get_value(
		"Custody Loan",
		{"material_request": request.name, "loan_type": loan_type, "docstatus": ["<", 2]},
		"name",
	)
	if existing:
		loan = frappe.get_doc("Custody Loan", existing)
		loan.check_permission("read")
		return loan.as_dict()
	request_rows = request.items
	if not request_rows:
		frappe.throw(_("The Material Request has no {0} items").format(loan_type))
	loan = frappe.new_doc("Custody Loan")
	loan.company = request.company
	loan.material_request = request.name
	loan.loan_type = loan_type
	loan.expected_return_date = request.expected_return_date
	for request_item in request_rows:
		if loan_type == "Stock":
			loan.append("items", {
				"material_request_item": request_item.name,
				"item_code": request_item.item_code,
				"item_name": request_item.item_name,
				"qty": request_item.qty,
				"source_warehouse": request_item.from_warehouse
				or request.get_default_warehouse(request_item.item_code),
			})
		else:
			loan.append("items", {
				"material_request_item": request_item.name,
				"asset": request_item.asset,
				"item_code": request_item.item_code,
				"item_name": request_item.asset_name or request_item.item_name,
				"qty": 1,
				"source_location": request_item.source_location,
				"target_location": request_item.target_location or request.loan_recipient_location or request.customer_location,
			})
	loan.insert(ignore_permissions=True)
	return loan.as_dict()


def get_custody_loan_actions(request):
	can_create = frappe.has_permission("Custody Loan", "create")
	loan_type = (
		"Asset"
		if request.material_request_type == "Customer Demo" or request.borrow_item_type == "Asset"
		else "Stock"
	)
	return frappe._dict(
		stock_loan=can_create and loan_type == "Stock" and bool(request.items),
		asset_loan=can_create and loan_type == "Asset" and bool(request.items),
	)


def lock_material_request(request_name):
	frappe.db.sql("select name from `tabMaterial Request` where name=%s for update", request_name)


def lock_custody_loan(loan_name):
	frappe.db.sql("select name from `tabCustody Loan` where name=%s for update", loan_name)


def normalize_serials(serial_numbers):
	if not serial_numbers:
		return []
	return [serial for serial in get_serial_nos(serial_numbers) if serial]


def loan_item_available_to_receive(loan_name, loan_item):
	returned, adjusted, sold = get_loan_item_balances(loan_name, loan_item.name)
	reserved = get_loan_item_reserved_qty(loan_name, loan_item.name)
	return max(flt(loan_item.qty) - returned - adjusted - sold - reserved, 0)


def loan_item_available_to_adjust(loan_name, loan_item):
	returned, adjusted, sold = get_loan_item_balances(loan_name, loan_item.name)
	reserved = get_loan_item_reserved_qty(loan_name, loan_item.name)
	return max(flt(loan_item.qty) - returned - adjusted - sold - reserved, 0)


def get_loan_item_reserved_qty(loan_name, loan_item_name):
	return_names = frappe.get_all(
		"Custody Loan Return",
		filters={"custody_loan": loan_name, "docstatus": 1, "status": ["!=", "Received"]},
		pluck="name",
	)
	if not return_names:
		return 0
	rows = frappe.get_all(
		"Custody Loan Return Item",
		filters={"parent": ["in", return_names], "custody_loan_item": loan_item_name},
		pluck="returned_qty",
	)
	return sum(flt(qty) for qty in rows)


def get_loan_item_balances(loan_name, loan_item_name):
	return_names = frappe.get_all(
		"Custody Loan Return",
		filters={"custody_loan": loan_name, "docstatus": 1, "status": "Received"},
		pluck="name",
	)
	returned = adjusted = sold = 0
	if return_names:
		returned = sum(flt(qty) for qty in frappe.get_all(
			"Custody Loan Return Item",
			filters={"parent": ["in", return_names], "custody_loan_item": loan_item_name},
			pluck="returned_qty",
		))
	adjustment_names = frappe.get_all(
		"Custody Loan Adjustment",
		filters={"custody_loan": loan_name, "docstatus": 1},
		pluck="name",
	)
	if adjustment_names:
		adjustment_rows = frappe.get_all(
			"Custody Loan Adjustment Item",
			filters={"parent": ["in", adjustment_names], "custody_loan_item": loan_item_name},
			pluck="qty",
		)
		adjusted = sum(flt(qty) for qty in adjustment_rows)
	invoice_rows = frappe.db.sql(
		"""select sum(sii.qty) from `tabSales Invoice Item` sii
		join `tabSales Invoice` si on si.name=sii.parent
		where sii.custody_loan_item=%s and si.custody_loan=%s
		and si.docstatus=1""",
		(loan_item_name, loan_name),
	)
	sold = flt(invoice_rows[0][0] if invoice_rows else 0)
	return max(returned, 0), adjusted, max(sold, 0)


def get_received_serials(loan_name, loan_item_name):
	return_names = frappe.get_all(
		"Custody Loan Return",
		filters={"custody_loan": loan_name, "docstatus": 1, "status": "Received"},
		pluck="name",
	)
	if not return_names:
		return []
	rows = frappe.get_all(
		"Custody Loan Return Item",
		filters={"parent": ["in", return_names], "custody_loan_item": loan_item_name},
		pluck="serial_no",
	)
	return [serial for row in rows for serial in normalize_serials(row)]


def get_reserved_serials(loan_name, loan_item_name):
	return_names = frappe.get_all(
		"Custody Loan Return",
		filters={"custody_loan": loan_name, "docstatus": 1, "status": ["!=", "Received"]},
		pluck="name",
	)
	if not return_names:
		return []
	rows = frappe.get_all(
		"Custody Loan Return Item",
		filters={"parent": ["in", return_names], "custody_loan_item": loan_item_name},
		pluck="serial_no",
	)
	return [serial for row in rows for serial in normalize_serials(row)]


def get_adjusted_serials(loan_name, loan_item_name):
	adjustment_names = frappe.get_all(
		"Custody Loan Adjustment",
		filters={"custody_loan": loan_name, "docstatus": 1},
		pluck="name",
	)
	if not adjustment_names:
		return []
	rows = frappe.get_all(
		"Custody Loan Adjustment Item",
		filters={"parent": ["in", adjustment_names], "custody_loan_item": loan_item_name},
		pluck="serial_no",
	)
	return [serial for row in rows for serial in normalize_serials(row)]


def get_open_issued_serials(loan_name, loan_item):
	issued = normalize_serials(loan_item.serial_no)
	returned = set()
	return_names = frappe.get_all(
		"Custody Loan Return",
		filters={"custody_loan": loan_name, "docstatus": 1},
		pluck="name",
	)
	if return_names:
		rows = frappe.get_all(
			"Custody Loan Return Item",
			filters={"parent": ["in", return_names], "custody_loan_item": loan_item.name},
			pluck="serial_no",
		)
		returned.update(serial for row in rows for serial in normalize_serials(row))
	returned.update(get_adjusted_serials(loan_name, loan_item.name))
	return [serial for serial in issued if serial not in returned]


def lock_asset(asset_name):
	frappe.db.sql("select name from `tabAsset` where name=%s for update", asset_name)


def transaction_is_received(stock_entry_name, asset_movement_name):
	if stock_entry_name:
		if frappe.db.exists("Delivery Note", stock_entry_name):
			entry = frappe.db.get_value(
				"Delivery Note", stock_entry_name,
				["docstatus", "requires_shipment", "custody_loan_shipment"], as_dict=True,
			)
			if entry and entry.docstatus == 1 and entry.requires_shipment:
				return bool(
					entry.custody_loan_shipment
					and frappe.db.get_value("Shipment", entry.custody_loan_shipment, "tracking_status") == "Delivered"
				)
			return bool(entry and entry.docstatus == 1)
		entry = frappe.db.get_value(
			"Stock Entry", stock_entry_name,
			["docstatus", "requires_shipment", "purpose", "shipment", "outgoing_stock_entry"], as_dict=True,
		)
		if not entry or entry.docstatus != 1:
			return False
		if not entry.requires_shipment:
			return True
			if entry.purpose == "Material Receipt":
				return bool(entry.shipment and frappe.db.get_value("Shipment", entry.shipment, "tracking_status") == "Delivered")
			return bool(frappe.db.exists("Stock Entry", {"outgoing_stock_entry": stock_entry_name, "docstatus": 1}))
	if asset_movement_name:
		return frappe.db.get_value("Asset Movement", asset_movement_name, "docstatus") == 1
	return False


def cancel_source_transaction(source, fieldname):
	name = source.get(fieldname)
	if not name:
		return
	doctype = {
		"asset_movement": "Asset Movement",
		"delivery_note": "Delivery Note",
		"pick_list": "Pick List",
		"stock_entry": "Stock Entry",
	}.get(fieldname)
	if not doctype:
		frappe.throw(_("Unsupported Custody Loan transaction field: {0}").format(fieldname))
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 2:
		return
	shipment_field = "custody_loan_shipment" if doctype == "Delivery Note" else "shipment"
	shipment_name = doc.get(shipment_field)
	shipment_status = frappe.db.get_value("Shipment", shipment_name, "docstatus") if shipment_name else None
	if shipment_status == 1:
		frappe.throw(
			_("Cannot cancel {0} {1} because submitted Shipment {2} has already posted its transaction.").format(
				doctype, name, shipment_name
			)
		)
	if shipment_status == 0:
		frappe.throw(_("Cancel draft Shipment {0} before cancelling {1} {2}.").format(shipment_name, doctype, name))
	if doc.docstatus == 1 and doc.get("requires_shipment") and not shipment_name:
		frappe.throw(_("Cannot cancel {0} {1} because it requires a Shipment that is not linked.").format(doctype, name))
	if doc.docstatus == 1:
		doc.flags.custody_loan_cancellation = True
		doc.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN
		doc.ignore_linked_doctypes = tuple(
			set(doc.get("ignore_linked_doctypes") or ()) | LOAN_SOURCE_DOCTYPES | {"Material Request"}
		)
		doc.cancel()
	elif doc.docstatus == 0:
		frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def update_return_status_on_transaction(return_doc):
	if return_doc.docstatus == 2:
		return "Cancelled"
	if return_doc.docstatus != 1:
		return "Draft"
	if return_doc.loan_type == "Stock":
		delivery_note = (
			frappe.get_doc("Delivery Note", return_doc.delivery_note)
			if return_doc.delivery_note and frappe.db.exists("Delivery Note", return_doc.delivery_note)
			else None
		)
		if not delivery_note:
			return "Pending Quality Inspection"
		if return_doc.requires_shipment:
			shipment_name = delivery_note.get("custody_loan_shipment")
			shipment = (
				frappe.db.get_value("Shipment", shipment_name, ["docstatus", "tracking_status"], as_dict=True)
				if shipment_name
				else None
			)
			if not shipment or shipment.docstatus != 1:
				return "Pending Shipment"
			if shipment.tracking_status != "Delivered":
				return "Pending Receipt"
		if delivery_note.docstatus == 1:
			return "Received"
		return (
			"Pending Receipt"
			if custody_loan_return_inspections_are_accepted(delivery_note)
			else "Pending Quality Inspection"
		)

	if not return_doc.asset_movement:
		return "Pending Receipt"
	if frappe.db.get_value("Asset Movement", return_doc.asset_movement, "docstatus") == 1:
		return "Received"
	return "Pending Receipt"


def custody_loan_return_inspections_are_accepted(delivery_note):
	if not delivery_note.items:
		return False
	for row in delivery_note.items:
		if not row.quality_inspection:
			return False
		inspection = frappe.db.get_value(
			"Quality Inspection", row.quality_inspection, ["docstatus", "status"], as_dict=True
		)
		if not inspection or inspection.docstatus != 1 or inspection.status != "Accepted":
			return False
	return True


def check_custody_loan_source(source_doctype, source_name, loan_name):
	if source_doctype not in LOAN_SOURCE_DOCTYPES or not source_name or not loan_name:
		return False
	if source_doctype == "Custody Loan":
		return source_name == loan_name
	return frappe.db.get_value(source_doctype, source_name, "custody_loan") == loan_name
