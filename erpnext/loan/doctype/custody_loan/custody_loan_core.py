from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.serial_batch_bundle import get_serial_nos_from_bundle

BORROWER_TYPES = {"Employee", "Customer", "Supplier", "Contact"}
LOAN_SOURCE_DOCTYPES = {"Custody Loan", "Custody Loan Return", "Custody Loan Adjustment"}


class CustodyLoan(Document):
	def validate(self):
		self.validate_material_request()
		self.validate_items()
		self.validate_shipment_scope()
		self.refresh_balances()

	def validate_material_request(self):
		request = frappe.get_doc("Material Request", self.material_request)
		if request.docstatus != 1 or request.material_request_type != "Customer Borrow / Loan":
			frappe.throw(_("Custody Loans can only be created from a submitted Customer Borrow / Loan Material Request"))
		if request.company != self.company:
			frappe.throw(_("Custody Loan and Material Request must use the same Company"))
		if self.loan_type not in ("Stock", "Asset"):
			frappe.throw(_("Select Stock or Asset as the Loan Type"))

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

		self.recipient_address = request.loan_recipient_address or request.customer_address
		self.recipient_address_display = request.loan_recipient_address_display or request.customer_address_display
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
		if self.loan_type == "Stock":
			if len({row.source_warehouse for row in self.items}) > 1:
				frappe.throw(_("A Stock Loan Shipment must start from one Source Warehouse"))
			return
		if any(not row.target_location for row in self.items):
			frappe.throw(_("Select a recipient Location before shipping an Asset Loan"))
		sources = {row.source_location for row in self.items}
		targets = {row.target_location for row in self.items}
		if len(sources) > 1 or len(targets) > 1:
			frappe.throw(_("An Asset Loan Shipment must use one source and one recipient Location"))

	def validate_items(self, require_allocation=False):
		if not self.items:
			frappe.throw(_("Add at least one item to the Custody Loan"))

		request = frappe.get_cached_doc("Material Request", self.material_request)
		request_items = {row.name: row for row in request.items}
		qty_by_request_item = {}
		assets_seen = set()
		serials_seen = set()
		for row in self.items:
			request_item = request_items.get(row.material_request_item)
			if not request_item:
				frappe.throw(_("Row {0}: Select an item from the linked Material Request").format(row.idx))
			if row.item_code != request_item.item_code:
				frappe.throw(_("Row {0}: Item must match the linked Material Request item").format(row.idx))
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Loan Qty must be greater than zero").format(row.idx))
			if self.loan_type == "Stock":
				if request_item.item_type != "Stock Item" or row.source_warehouse != request_item.from_warehouse:
					frappe.throw(_("Row {0}: Stock Loan rows must match a Stock Item and Source Warehouse on the request").format(row.idx))
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
				if request_item.item_type != "Fixed Asset" or flt(row.qty) != 1:
					frappe.throw(_("Row {0}: Asset Loans require one Fixed Asset per row"))
				if not row.asset and require_allocation:
					frappe.throw(_("Row {0}: Select the Asset to issue").format(row.idx))
				if not row.asset:
					qty_by_request_item[row.material_request_item] = qty_by_request_item.get(row.material_request_item, 0) + flt(row.qty)
					continue
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
				row.source_location = asset.location
				row.target_location = row.target_location or self.recipient_location
				if self.borrower_type != "Employee" or self.requires_shipment:
					if not row.target_location:
						frappe.throw(_("Row {0}: Select the recipient Location for this Asset").format(row.idx))
				if row.target_location and row.target_location == row.source_location:
					frappe.throw(_("Row {0}: Recipient Location must differ from the current Location").format(row.idx))
			qty_by_request_item[row.material_request_item] = qty_by_request_item.get(row.material_request_item, 0) + flt(row.qty)

		for request_item_name, qty in qty_by_request_item.items():
			if qty > flt(request_items[request_item_name].qty):
				frappe.throw(_("Loan Qty exceeds the requested quantity for Material Request Item {0}").format(request_item_name))

	def before_submit(self):
		lock_material_request(self.material_request)
		for row in self.items:
			if row.asset:
				lock_asset(row.asset)
		self.validate_items(require_allocation=True)
		if frappe.db.exists(
			"Custody Loan",
			{"material_request": self.material_request, "loan_type": self.loan_type, "docstatus": 1},
		):
			frappe.throw(_("A submitted {0} Custody Loan already exists for this Material Request").format(self.loan_type))
		self.status = "Pending Shipment" if self.requires_shipment else "Active"

	def on_submit(self):
		if self.loan_type == "Stock":
			transaction = self.make_stock_issue()
			self.db_set("stock_entry", transaction.name)
			self.stock_entry = transaction.name
			if not self.requires_shipment:
				transaction.submit()
				self.capture_stock_issue_details(transaction)
		else:
			transaction = self.make_asset_issue()
			self.db_set("asset_movement", transaction.name)
			self.asset_movement = transaction.name
			if not self.requires_shipment:
				transaction.submit()
		self.update_status()

	def make_stock_issue(self):
		stock_entry = new_stock_entry(self.company, "Material Transfer")
		stock_entry.requires_shipment = cint(self.requires_shipment)
		stock_entry.shipment_destination_warehouse = self.customer_loan_warehouse if self.requires_shipment else None
		set_stock_entry_recipient_snapshot(stock_entry, self)
		set_loan_transaction_source(stock_entry, self, self)
		for row in self.items:
			stock_entry.append("items", {
				"item_code": row.item_code,
				"qty": row.qty,
				"s_warehouse": row.source_warehouse,
				"t_warehouse": self.customer_loan_warehouse,
				"material_request": self.material_request,
				"material_request_item": row.material_request_item,
				"custody_loan_item": row.name,
				"batch_no": row.batch_no,
				"serial_no": row.serial_no,
			})
		stock_entry.insert(ignore_permissions=True)
		return stock_entry

	def make_asset_issue(self):
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Transfer" if self.borrower_type != "Employee" or self.requires_shipment else "Issue"
		movement.requires_shipment = cint(self.requires_shipment)
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
				"shipment_destination_location": row.target_location if self.requires_shipment else None,
				"to_employee": self.borrower if self.borrower_type == "Employee" else None,
				"custody_loan_item": row.name,
			})
		movement.insert(ignore_permissions=True)
		return movement

	def capture_stock_issue_details(self, stock_entry=None):
		stock_entry = stock_entry or frappe.get_doc("Stock Entry", self.stock_entry)
		for row in stock_entry.items:
			loan_item = next((item for item in self.items if item.name == row.custody_loan_item), None)
			if not loan_item:
				continue
			if row.batch_no:
				loan_item.batch_no = row.batch_no
			if row.serial_no:
				loan_item.serial_no = row.serial_no
			elif row.serial_and_batch_bundle:
				loan_item.serial_no = "\n".join(get_serial_nos_from_bundle(row.serial_and_batch_bundle))
			if row.batch_no:
				loan_item.batch_no = row.batch_no
			loan_item.valuation_rate = row.valuation_rate or row.basic_rate
			loan_item.db_update()

	def before_cancel(self):
		if frappe.db.exists("Custody Loan Return", {"custody_loan": self.name, "docstatus": 1}):
			frappe.throw(_("Cancel submitted Loan Returns before cancelling the Custody Loan"))
		if frappe.db.exists("Custody Loan Adjustment", {"custody_loan": self.name, "docstatus": 1}):
			frappe.throw(_("Cancel submitted Loan Adjustments before cancelling the Custody Loan"))
		self.cancel_generated_transaction("stock_entry")
		self.cancel_generated_transaction("asset_movement")

	def cancel_generated_transaction(self, fieldname):
		cancel_source_transaction(self, fieldname)

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")
		self.update_material_request_status()

	def refresh_balances(self):
		for row in self.items:
			returned, replacements, adjusted = get_loan_item_balances(self.name, row.name)
			row.returned_qty = returned
			row.replacement_received_qty = replacements
			row.adjusted_qty = adjusted
			row.outstanding_qty = max(flt(row.qty) - returned - adjusted, 0)
			if row.name and self.docstatus == 1:
				frappe.db.set_value("Custody Loan Item", row.name, {
					"returned_qty": returned,
					"replacement_received_qty": replacements,
					"adjusted_qty": adjusted,
					"outstanding_qty": row.outstanding_qty,
				})

	def update_status(self):
		if self.docstatus == 2:
			status = "Cancelled"
		elif self.docstatus != 1:
			status = "Draft"
		elif transaction_is_in_transit(self.stock_entry, self.asset_movement):
			status = "In Transit"
		elif self.requires_shipment and not transaction_is_received(self.stock_entry, self.asset_movement):
			status = "Pending Shipment"
		else:
			self.refresh_balances()
			issued = sum(flt(row.qty) for row in self.items)
			returned = sum(flt(row.returned_qty) for row in self.items)
			adjusted = sum(flt(row.adjusted_qty) for row in self.items)
			outstanding = sum(flt(row.outstanding_qty) for row in self.items)
			if outstanding <= 0 and adjusted:
				status = "Closed"
			elif outstanding <= 0 and returned >= issued:
				status = "Returned"
			elif returned or adjusted:
				status = "Partially Returned"
			else:
				status = "Active"
		if status != self.status:
			self.status = status
			self.db_set("status", status)
		self.update_material_request_status()
		return status

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
			available_to_receive = max(flt(row.outstanding_qty) - flt(row.replacement_received_qty), 0)
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
		if self.requires_shipment and self.loan_type == "Stock":
			selected = {row.custody_loan_item for row in self.items if row.custody_loan_item}
			warehouses = {row.source_warehouse for row in loan.items if row.name in selected}
			if len(warehouses) > 1:
				frappe.throw(_("A Stock Loan Return Shipment must go to one Source Warehouse"))
		if self.requires_shipment and self.loan_type == "Asset":
			selected = {row.custody_loan_item for row in self.items if row.custody_loan_item}
			locations = {row.source_location for row in loan.items if row.name in selected}
			if len(locations) > 1:
				frappe.throw(_("An Asset Loan Return Shipment must go to one Location"))

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one item to return"))
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		loan_items = {row.name: row for row in loan.items}
		qty_by_loan_item = {}
		serials_seen = set()
		for row in self.items:
			loan_item = loan_items.get(row.custody_loan_item)
			if not loan_item:
				frappe.throw(_("Row {0}: Loan Item must belong to the linked Custody Loan").format(row.idx))
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
				row.matched_qty = 1
				row.replacement_qty = 0
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
				matched_serials = [
					serial for serial in serials
					if serial in issued_serials
					and (not item.has_batch_no or row.batch_no == loan_item.batch_no)
				]
				replacement_serials = [serial for serial in serials if serial not in matched_serials]
				row.serial_no = "\n".join(serials)
				row.matched_serial_no = "\n".join(matched_serials)
				row.replacement_serial_no = "\n".join(replacement_serials)
				row.matched_qty = len(matched_serials)
				row.replacement_qty = len(replacement_serials)
			elif item.has_batch_no:
				if not row.batch_no:
					frappe.throw(_("Row {0}: Select the Batch No actually received").format(row.idx))
				row.matched_qty = flt(row.returned_qty) if row.batch_no == loan_item.batch_no else 0
				row.replacement_qty = flt(row.returned_qty) - flt(row.matched_qty)
				row.matched_serial_no = None
				row.replacement_serial_no = None
			else:
				row.matched_qty = flt(row.returned_qty)
				row.replacement_qty = 0
			if flt(row.replacement_qty) and not row.replacement_reason:
				frappe.throw(_("Row {0}: Enter why a different Batch or Serial No was received").format(row.idx))
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
			if not self.requires_shipment:
				transaction.submit()
		else:
			matched_items = [row for row in self.items if flt(row.matched_qty)]
			replacement_items = [row for row in self.items if flt(row.replacement_qty)]
			if matched_items:
				transaction = self.make_stock_transfer_receipt(matched_items)
				self.db_set("stock_entry", transaction.name)
				self.stock_entry = transaction.name
				if not self.requires_shipment:
					transaction.submit()
			if replacement_items:
				transaction = self.make_replacement_receipt(replacement_items)
				self.db_set("replacement_stock_entry", transaction.name)
				self.replacement_stock_entry = transaction.name
				if not self.requires_shipment:
					transaction.submit()
		self.update_status()
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		loan.update_status()

	def make_asset_receipt(self):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Receipt"
		movement.requires_shipment = cint(self.requires_shipment)
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
				"shipment_destination_location": row.target_location if self.requires_shipment else None,
				"from_employee": loan.borrower if loan.borrower_type == "Employee" else None,
				"custody_loan_item": row.custody_loan_item,
			})
		movement.insert(ignore_permissions=True)
		return movement

	def make_stock_transfer_receipt(self, rows):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		stock_entry = new_stock_entry(self.company, "Material Transfer")
		stock_entry.requires_shipment = cint(self.requires_shipment)
		stock_entry.shipment_destination_warehouse = None
		set_stock_entry_recipient_snapshot(stock_entry, loan)
		set_loan_transaction_source(stock_entry, loan, self)
		for row in rows:
			loan_item = frappe.get_doc("Custody Loan Item", row.custody_loan_item)
			stock_entry.append("items", {
				"item_code": loan_item.item_code,
				"qty": row.matched_qty,
				"s_warehouse": loan.customer_loan_warehouse,
				"t_warehouse": loan_item.source_warehouse,
				"material_request": loan.material_request,
				"material_request_item": loan_item.material_request_item,
				"custody_loan_item": loan_item.name,
				"batch_no": loan_item.batch_no if row.matched_qty else None,
				"serial_no": row.matched_serial_no if row.matched_serial_no else (loan_item.serial_no if not row.replacement_qty else None),
			})
		if self.requires_shipment:
			stock_entry.shipment_destination_warehouse = stock_entry.items[0].t_warehouse
		stock_entry.insert(ignore_permissions=True)
		return stock_entry

	def make_replacement_receipt(self, rows):
		loan = frappe.get_doc("Custody Loan", self.custody_loan)
		account = frappe.db.get_value("Company", self.company, "stock_adjustment_account")
		if not account:
			frappe.throw(_("Set Company Stock Adjustment Account before receiving a replacement Batch or Serial No"))
		stock_entry = new_stock_entry(self.company, "Material Receipt")
		set_loan_transaction_source(stock_entry, loan, self)
		for row in rows:
			loan_item = frappe.get_doc("Custody Loan Item", row.custody_loan_item)
			stock_entry.append("items", {
				"item_code": loan_item.item_code,
				"qty": row.replacement_qty,
				"t_warehouse": loan_item.source_warehouse,
				"custody_loan_item": loan_item.name,
				"batch_no": row.batch_no if row.replacement_qty else None,
				"serial_no": row.replacement_serial_no,
				"basic_rate": loan_item.valuation_rate,
				"expense_account": account,
			})
		if self.requires_shipment:
			stock_entry.requires_shipment = 1
			stock_entry.shipment_destination_warehouse = stock_entry.items[0].t_warehouse
		stock_entry.insert(ignore_permissions=True)
		return stock_entry

	def update_status(self):
		status = update_return_status_on_transaction(self)
		if status != self.status:
			self.status = status
			self.db_set("status", status)
		return status

	def before_cancel(self):
		for fieldname in ("stock_entry", "replacement_stock_entry", "asset_movement"):
			cancel_source_transaction(self, fieldname)

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_set("status", "Cancelled")
		if frappe.db.exists("Custody Loan", self.custody_loan):
			frappe.get_doc("Custody Loan", self.custody_loan).update_status()


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


def set_stock_entry_recipient_snapshot(stock_entry, loan):
	stock_entry.customer_address = loan.recipient_address
	stock_entry.customer_address_display = loan.recipient_address_display
	stock_entry.customer_contact = loan.recipient_contact
	stock_entry.customer_contact_display = loan.recipient_contact_display
	stock_entry.customer_contact_email = loan.recipient_contact_email


def set_loan_transaction_source(transaction, loan, source):
	transaction.is_system_generated = 1
	transaction.is_custody_loan_transaction = 1
	transaction.custody_loan = loan.name
	transaction.custody_loan_source_doctype = source.doctype
	transaction.custody_loan_source_name = source.name
	transaction.flags.custody_loan_generated = True


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
	if (doc.doctype == "Stock Entry" and loan.loan_type != "Stock") or (
		doc.doctype == "Asset Movement" and loan.loan_type != "Asset"
	):
		frappe.throw(_("Generated document type does not match the Custody Loan Type"))
	for row in doc.get("items", []) if doc.doctype == "Stock Entry" else doc.get("assets", []):
		loan_item_name = row.get("custody_loan_item")
		loan_item = (
			frappe.db.get_value("Custody Loan Item", loan_item_name, ["parent", "item_code"], as_dict=True)
			if loan_item_name
			else None
		)
		item_code = row.get("item_code") if doc.doctype == "Stock Entry" else row.get("asset_item_code")
		if not loan_item or loan_item.parent != loan.name or item_code != loan_item.item_code:
			frappe.throw(_("Every generated transaction row must link to an item on the Custody Loan"))

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

	expected_request_items = {row.material_request_item for row in loan.items}
	for fieldname in ("stock_entry", "asset_movement"):
		if not frappe.db.has_column("Material Request Item", fieldname):
			continue
		linked_rows = frappe.get_all(
			"Material Request Item",
			filters={fieldname: doc.name},
			fields=["name", "parent"],
		)
		if any(
			row.parent != loan.material_request or row.name not in expected_request_items
			for row in linked_rows
		):
			frappe.throw(_("Generated transaction is linked to an unrelated Material Request"))

	doc.ignore_linked_doctypes = tuple(LOAN_SOURCE_DOCTYPES | {"Material Request"})


def validate_asset_movement_custody_loan(doc):
	loan = frappe.get_doc("Custody Loan", doc.custody_loan)
	if loan.loan_type != "Asset" or loan.docstatus != 1 or loan.company != doc.company:
		frappe.throw(_("Asset Movement must reference a submitted Asset Custody Loan for the same Company"))
	if doc.reference_doctype != "Material Request" or doc.reference_name != loan.material_request:
		frappe.throw(_("Asset Movement must link to the Material Request that created the Custody Loan"))
	doc.expected_return_date = loan.expected_return_date
	doc.customer = loan.borrower if loan.borrower_type == "Customer" else None
	doc.customer_contact = loan.recipient_contact
	doc.customer_address = loan.recipient_address
	doc.customer_address_display = loan.recipient_address_display
	doc.customer_contact_display = loan.recipient_contact_display
	doc.customer_contact_email = loan.recipient_contact_email
	source_type = doc.get("custody_loan_source_doctype")
	if source_type not in ("Custody Loan", "Custody Loan Return"):
		frappe.throw(_("Asset Movement must be generated by a Custody Loan or Loan Return"))
	items = {row.name: row for row in loan.items}
	is_return = source_type == "Custody Loan Return"
	expected_purpose = "Receipt" if is_return else (
		"Issue" if loan.borrower_type == "Employee" and not loan.requires_shipment else "Transfer"
	)
	if doc.purpose != expected_purpose:
		frappe.throw(_("Asset Movement Purpose must be {0} for this Custody Loan").format(expected_purpose))
	for row in doc.assets:
		loan_item = items.get(row.custody_loan_item)
		if not loan_item or row.asset != loan_item.asset or row.request_item != loan_item.material_request_item:
			frappe.throw(_("Asset Movement row does not match an Asset on the linked Custody Loan"))
		if is_return:
			final_location = (
				row.shipment_destination_location
				if doc.requires_shipment and row.shipment_destination_location
				else row.target_location
			)
			if final_location != loan_item.source_location:
				frappe.throw(_("Returned Asset must go back to its original Location"))
		elif loan.borrower_type == "Employee":
			if row.to_employee != loan.borrower:
				frappe.throw(_("Asset Movement must issue the Asset to the Loan Employee"))
			if doc.requires_shipment and row.shipment_destination_location != loan_item.target_location:
				frappe.throw(_("Shipment must send the Asset to the Loan recipient Location"))
		elif (row.shipment_destination_location if doc.requires_shipment else row.target_location) != loan_item.target_location:
			frappe.throw(_("Asset Movement must send the Asset to the recipient Location on the Loan"))


def update_custody_loan_status(doc, method=None):
	source_doctype = doc.get("custody_loan_source_doctype")
	source_name = doc.get("custody_loan_source_name")
	if not source_doctype or not source_name:
		return
	if source_doctype == "Custody Loan":
		frappe.get_doc("Custody Loan", source_name).update_status()
	elif source_doctype == "Custody Loan Return":
		return_doc = frappe.get_doc(source_doctype, source_name)
		return_doc.update_status()
		frappe.get_doc("Custody Loan", return_doc.custody_loan).update_status()
	elif source_doctype == "Custody Loan Adjustment":
		adjustment = frappe.get_doc(source_doctype, source_name)
		frappe.get_doc("Custody Loan", adjustment.custody_loan).update_status()


def make_custody_loan_from_request(request, loan_type):
	request.check_permission("read")
	frappe.has_permission("Custody Loan", "create", throw=True)
	request.reload()
	if request.docstatus != 1 or request.material_request_type != "Customer Borrow / Loan":
		frappe.throw(_("Submit a Customer Borrow / Loan Material Request first"))
	if loan_type not in ("Stock", "Asset"):
		frappe.throw(_("Select Stock or Asset as the Loan Type"))
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
	request_rows = [
		row for row in request.items
		if row.item_type == ("Stock Item" if loan_type == "Stock" else "Fixed Asset")
	]
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
				"source_warehouse": request_item.from_warehouse,
			})
		else:
			for _asset_index in range(cint(request_item.qty)):
				loan.append("items", {
					"material_request_item": request_item.name,
					"item_code": request_item.item_code,
					"item_name": request_item.item_name,
					"qty": 1,
					"target_location": request.loan_recipient_location or request.customer_location,
				})
	loan.insert(ignore_permissions=True)
	return loan.as_dict()


def get_custody_loan_actions(request):
	items = {row.item_type for row in request.items if flt(row.qty) > 0}
	can_create = frappe.has_permission("Custody Loan", "create")
	return frappe._dict(
		stock_loan=can_create and "Stock Item" in items,
		asset_loan=can_create and "Fixed Asset" in items,
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
	returned, replacements, adjusted = get_loan_item_balances(loan_name, loan_item.name)
	reserved = get_loan_item_reserved_qty(loan_name, loan_item.name)
	return max(flt(loan_item.qty) - returned - replacements - adjusted - reserved, 0)


def loan_item_available_to_adjust(loan_name, loan_item):
	returned, _replacements, adjusted = get_loan_item_balances(loan_name, loan_item.name)
	reserved_matched = get_loan_item_reserved_qty(loan_name, loan_item.name, matched_only=True)
	return max(flt(loan_item.qty) - returned - adjusted - reserved_matched, 0)


def get_loan_item_reserved_qty(loan_name, loan_item_name, matched_only=False):
	return_names = frappe.get_all(
		"Custody Loan Return",
		filters={"custody_loan": loan_name, "docstatus": 1, "status": ["!=", "Received"]},
		pluck="name",
	)
	if not return_names:
		return 0
	field = "matched_qty" if matched_only else "returned_qty"
	rows = frappe.get_all(
		"Custody Loan Return Item",
		filters={"parent": ["in", return_names], "custody_loan_item": loan_item_name},
		pluck=field,
	)
	return sum(flt(qty) for qty in rows)


def get_loan_item_balances(loan_name, loan_item_name):
	return_names = frappe.get_all(
		"Custody Loan Return",
		filters={"custody_loan": loan_name, "docstatus": 1, "status": "Received"},
		pluck="name",
	)
	matched = replacement = adjusted = 0
	if return_names:
		rows = frappe.get_all(
			"Custody Loan Return Item",
			filters={"parent": ["in", return_names], "custody_loan_item": loan_item_name},
			fields=["matched_qty", "replacement_qty"],
		)
		matched = sum(flt(row.matched_qty) for row in rows)
		replacement = sum(flt(row.replacement_qty) for row in rows)
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
	return matched, replacement, adjusted


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
			pluck="matched_serial_no",
		)
		returned.update(serial for row in rows for serial in normalize_serials(row))
	returned.update(get_adjusted_serials(loan_name, loan_item.name))
	return [serial for serial in issued if serial not in returned]


def lock_asset(asset_name):
	frappe.db.sql("select name from `tabAsset` where name=%s for update", asset_name)


def transaction_is_in_transit(stock_entry_name, asset_movement_name):
	if stock_entry_name:
		entry = frappe.db.get_value("Stock Entry", stock_entry_name, ["docstatus", "requires_shipment", "outgoing_stock_entry"], as_dict=True)
		if entry and entry.docstatus == 1 and entry.requires_shipment and not entry.outgoing_stock_entry:
			return not frappe.db.exists("Stock Entry", {"outgoing_stock_entry": stock_entry_name, "docstatus": 1})
	if asset_movement_name:
		movement = frappe.db.get_value("Asset Movement", asset_movement_name, ["docstatus", "requires_shipment", "shipment_transit_leg", "purpose"], as_dict=True)
		if movement and movement.docstatus == 1 and movement.requires_shipment and movement.shipment_transit_leg:
			return not frappe.db.exists(
				"Asset Movement", {"shipment_source_movement": asset_movement_name, "docstatus": 1}
			)
	return False


def transaction_is_received(stock_entry_name, asset_movement_name):
	if stock_entry_name:
		entry = frappe.db.get_value(
			"Stock Entry", stock_entry_name, ["docstatus", "requires_shipment", "purpose", "shipment"], as_dict=True
		)
		if entry and entry.docstatus == 1 and entry.requires_shipment and entry.purpose == "Material Receipt":
			return bool(entry.shipment and frappe.db.get_value("Shipment", entry.shipment, "tracking_status") == "Delivered")
		if entry and entry.docstatus == 1 and entry.requires_shipment:
			return bool(frappe.db.exists("Stock Entry", {"outgoing_stock_entry": stock_entry_name, "docstatus": 1}))
		return bool(entry and entry.docstatus == 1)
	if asset_movement_name:
		movement = frappe.db.get_value("Asset Movement", asset_movement_name, ["docstatus", "requires_shipment", "shipment_transit_leg"], as_dict=True)
		if movement and movement.requires_shipment and movement.shipment_transit_leg:
			return bool(frappe.db.exists("Asset Movement", {"shipment_source_movement": asset_movement_name, "docstatus": 1}))
		return bool(movement and movement.docstatus == 1)
	return False


def cancel_source_transaction(source, fieldname):
	name = source.get(fieldname)
	if not name:
		return
	doctype = "Asset Movement" if fieldname == "asset_movement" else "Stock Entry"
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 2:
		return
	shipment_status = frappe.db.get_value("Shipment", doc.shipment, "docstatus") if doc.get("shipment") else None
	if shipment_status in (0, 1) or (doc.docstatus == 1 and doc.get("requires_shipment") and not doc.get("shipment")):
		frappe.throw(_("Cancel the linked Shipment before cancelling this document"))
	if doc.docstatus == 1:
		doc.flags.custody_loan_cancellation = True
		doc.cancel()
	elif doc.docstatus == 0:
		frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def update_return_status_on_transaction(return_doc):
	if return_doc.docstatus == 2:
		return "Cancelled"
	if return_doc.docstatus != 1:
		return "Draft"
	transaction_names = [
		("Stock Entry", return_doc.stock_entry),
		("Stock Entry", return_doc.replacement_stock_entry),
		("Asset Movement", return_doc.asset_movement),
	]
	transaction_names = [(doctype, name) for doctype, name in transaction_names if name]
	if return_doc.requires_shipment and transaction_names:
		if any(
			(not frappe.db.get_value(doctype, name, "docstatus"))
			or not transaction_is_received(name if doctype == "Stock Entry" else None, name if doctype == "Asset Movement" else None)
			for doctype, name in transaction_names
		):
			return "In Transit"
	return "Received"


def check_custody_loan_source(source_doctype, source_name, loan_name):
	if source_doctype not in LOAN_SOURCE_DOCTYPES or not source_name or not loan_name:
		return False
	if source_doctype == "Custody Loan":
		return source_name == loan_name
	return frappe.db.get_value(source_doctype, source_name, "custody_loan") == loan_name
