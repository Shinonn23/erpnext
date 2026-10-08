import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display, get_default_address
from frappe.contacts.doctype.contact.contact import get_contact_details
from frappe.utils import cint, flt, getdate

from erpnext.accounts.party import get_party_shipping_address
from erpnext.stock.doctype.material_request.material_request import MaterialRequest

CUSTOMER_REQUEST_TYPES = ("Customer Demo", "Customer Borrow / Loan")
ASSET_TRANSFER_TYPE = "Asset Transfer"


def is_customer_request(request):
	return request.material_request_type in CUSTOMER_REQUEST_TYPES


def is_asset_transfer_request(request):
	return request.material_request_type == ASSET_TRANSFER_TYPE


def is_address_linked_to_customer(address, customer):
	return bool(
		frappe.db.exists(
			"Dynamic Link",
			{
				"parent": address,
				"parenttype": "Address",
				"link_doctype": "Customer",
				"link_name": customer,
			},
		)
	)


def get_default_customer_address(customer):
	if not customer:
		return None

	addresses = [
		get_party_shipping_address("Customer", customer),
		frappe.db.get_value("Customer", customer, "customer_primary_address"),
		get_default_address("Customer", customer, sort_key="is_primary_address"),
	]
	return next(
		(address for address in addresses if address and is_address_linked_to_customer(address, customer)),
		None,
	)


@frappe.whitelist()
def get_default_request_address(customer):
	frappe.has_permission("Customer", "read", doc=customer, throw=True)
	address = get_default_customer_address(customer)
	if address:
		frappe.has_permission("Address", "read", doc=address, throw=True)
	return address


def get_customer_contact_snapshot(contact_name):
	if not contact_name:
		return frappe._dict()

	contact = get_contact_details(contact_name)
	return frappe._dict(
		full_name=contact.get("contact_display"),
		email=contact.get("contact_email"),
		phone=contact.get("contact_phone"),
		mobile=contact.get("contact_mobile"),
	)


def format_contact_display(contact):
	values = [
		contact.get("full_name"),
		contact.get("email"),
		contact.get("phone") or contact.get("mobile"),
	]
	return "<br>".join(value for value in values if value)


def get_customer_delivery_snapshot(
	customer,
	address=None,
	address_display=None,
	contact_person=None,
	contact_display=None,
	contact_email=None,
):
	address = address or get_default_customer_address(customer)
	if address and not is_address_linked_to_customer(address, customer):
		frappe.throw(_("Customer Address must be linked to Customer {0}").format(customer))
	if address and not address_display:
		address_display = get_address_display(address)

	if contact_person and not contact_display:
		contact = get_customer_contact_snapshot(contact_person)
		contact_display = format_contact_display(contact)
		contact_email = contact.email

	return frappe._dict(
		customer_address=address,
		customer_address_display=address_display,
		customer_contact=contact_person,
		customer_contact_display=contact_display,
		customer_contact_email=contact_email,
	)

class CustomerMaterialRequest(MaterialRequest):
	def validate_material_request_type(self):
		if self.material_request_type == "Customer Demo":
			if not self.customer:
				frappe.throw(_("Customer is required for Customer Demo requests"))
			return
		if self.material_request_type == "Customer Borrow / Loan":
			if not self.loan_recipient_type or not self.loan_recipient:
				frappe.throw(_("Select a Recipient Type and Recipient for the Loan request"))
			return
		if is_asset_transfer_request(self):
			return
		super().validate_material_request_type()

	def before_validate(self):
		if is_asset_transfer_request(self):
			self.customer = None
			self.customer_address = None
			self.customer_address_display = None
			self.contact_person = None
			self.customer_contact_display = None
			self.customer_contact_email = None
			self.expected_return_date = None
			self.customer_location = None
			self.customer_loan_warehouse = None
			for row in self.items:
				if row.asset_transfer_asset:
					row.item_code = frappe.db.get_value("Asset", row.asset_transfer_asset, "item_code")
		elif self.material_request_type == "Customer Borrow / Loan":
			if not self.loan_recipient_type and self.customer:
				self.loan_recipient_type = "Customer"
			if self.loan_recipient_type == "Customer":
				self.loan_recipient = self.loan_recipient or self.customer
				self.customer = self.loan_recipient
			else:
				self.customer = None
				self.customer_address = None
				self.customer_address_display = None
				self.contact_person = None
				self.customer_contact_display = None
				self.customer_contact_email = None

	def validate(self):
		super().validate()
		if is_customer_request(self):
			self.customer_loan_warehouse = frappe.db.get_value(
				"Company", self.company, "customer_loan_warehouse"
			)
			self.validate_dates()
			if self.material_request_type == "Customer Borrow / Loan":
				self.validate_loan_recipient()
			else:
				self.validate_customer_contact()
				self.set_delivery_snapshots()
			self.validate_items()
			self.update_progress()
		elif is_asset_transfer_request(self):
			self.validate_asset_transfer_items()
			self.update_progress()

	def validate_loan_recipient(self):
		if self.loan_recipient_type not in ("Employee", "Customer", "Supplier", "Contact"):
			frappe.throw(_("Recipient Type must be Employee, Customer, Supplier or Contact"))
		if not self.loan_recipient or not frappe.db.exists(self.loan_recipient_type, self.loan_recipient):
			frappe.throw(_("Select a valid {0} Recipient").format(self.loan_recipient_type))
		if self.loan_recipient_type == "Employee" and frappe.db.get_value(
			"Employee", self.loan_recipient, "company"
		) != self.company:
			frappe.throw(_("Loan Employee must belong to Company {0}").format(self.company))

		if self.loan_recipient_type == "Customer":
			self.customer = self.loan_recipient
			self.customer_address = self.loan_recipient_address or self.customer_address
			self.contact_person = self.loan_recipient_contact or self.contact_person
			self.validate_customer_contact()
			self.set_delivery_snapshots()
			self.loan_recipient_address = self.customer_address
			self.loan_recipient_address_display = self.customer_address_display
			self.loan_recipient_contact = self.contact_person
			self.loan_recipient_contact_display = self.customer_contact_display
			self.loan_recipient_contact_email = self.customer_contact_email
		else:
			self.customer = None
			if self.loan_recipient_address:
				if not frappe.db.exists("Address", self.loan_recipient_address):
					frappe.throw(_("Recipient Address does not exist"))
				self.loan_recipient_address_display = get_address_display(self.loan_recipient_address)
			if self.loan_recipient_type == "Contact":
				self.loan_recipient_contact = self.loan_recipient
			if self.loan_recipient_contact:
				if not frappe.db.exists("Contact", self.loan_recipient_contact):
					frappe.throw(_("Recipient Contact does not exist"))
				contact = get_customer_contact_snapshot(self.loan_recipient_contact)
				self.loan_recipient_contact_display = format_contact_display(contact)
				self.loan_recipient_contact_email = contact.email
	def validate_asset_transfer_items(self):
		if self.customer:
			frappe.throw(_("Asset Transfer requests cannot be linked to a Customer"))
		if not self.items:
			frappe.throw(_("Add at least one Asset to transfer"))

		assets_seen = set()
		for row in self.items:
			if not row.asset_transfer_asset:
				frappe.throw(_("Row {0}: Select an Asset to transfer").format(row.idx))
			if row.asset_transfer_asset in assets_seen:
				frappe.throw(_("Row {0}: Asset {1} is listed more than once").format(row.idx, row.asset_transfer_asset))
			assets_seen.add(row.asset_transfer_asset)

			asset = frappe.db.get_value(
				"Asset",
				row.asset_transfer_asset,
				["item_code", "company", "status", "location", "demo_loan_status"],
				as_dict=True,
			)
			if not asset or asset.company != self.company:
				frappe.throw(_("Row {0}: Asset must belong to Company {1}").format(row.idx, self.company))
			if asset.status in ("Draft", "Scrapped", "Sold", "Capitalized"):
				frappe.throw(_("Row {0}: {1} Asset cannot be transferred").format(row.idx, asset.status))
			if asset.demo_loan_status in ("With Customer", "In Transit") or is_asset_in_transit(
				row.asset_transfer_asset
			):
				frappe.throw(_("Row {0}: Asset is currently with a Customer or in transit").format(row.idx))
			if not asset.location:
				frappe.throw(_("Row {0}: Asset must have a current Location before it can be transferred").format(row.idx))
			if not row.asset_transfer_target_location:
				frappe.throw(_("Row {0}: Select a Target Location").format(row.idx))
			if row.asset_transfer_target_location == asset.location:
				frappe.throw(_("Row {0}: Target Location must differ from the current Location").format(row.idx))

			item = frappe.get_cached_value(
				"Item", asset.item_code, ["is_fixed_asset", "stock_uom"], as_dict=True
			)
			if not item.is_fixed_asset:
				frappe.throw(_("Row {0}: Asset must reference a Fixed Asset Item").format(row.idx))
			row.item_code = asset.item_code
			row.item_type = "Fixed Asset"
			row.qty = 1
			row.uom = item.stock_uom
			row.stock_uom = item.stock_uom
			row.stock_qty = 1
			row.asset_transfer_source_location = asset.location
			row.from_warehouse = None

	def set_delivery_snapshots(self):
		if not self.customer:
			return

		if not self.customer_address:
			self.customer_address = get_default_customer_address(self.customer)
		if self.customer_address:
			if not is_address_linked_to_customer(self.customer_address, self.customer):
				frappe.throw(_("Customer Address must be linked to Customer {0}").format(self.customer))
			self.customer_address_display = get_address_display(self.customer_address)
		else:
			self.customer_address_display = None

		if self.contact_person:
			contact = get_customer_contact_snapshot(self.contact_person)
			self.customer_contact_email = contact.email
			self.customer_contact_display = format_contact_display(contact)
		else:
			self.customer_contact_email = None
			self.customer_contact_display = None

	def validate_customer_contact(self):
		if self.contact_person and not frappe.db.exists(
			"Dynamic Link",
			{"parent": self.contact_person, "parenttype": "Contact", "link_doctype": "Customer", "link_name": self.customer},
		):
			frappe.throw(_("Customer Contact must be linked to Customer {0}").format(self.customer))

	@frappe.whitelist()
	def get_default_delivery_address(self):
		return get_default_customer_address(self.customer)

	def validate_dates(self):
		if not self.expected_return_date:
			frappe.throw(_("Expected Return Date is required for Customer Demo / Loan requests"))
		if self.transaction_date and getdate(self.expected_return_date) < getdate(self.transaction_date):
			frappe.throw(_("Expected Return Date cannot be before Request Date"))

	def validate_items(self):
		super().validate_items()
		if not is_customer_request(self):
			return

		if not self.items:
			frappe.throw(_("At least one Item is required"))
		for row in self.items:
			item = frappe.get_cached_value(
				"Item", row.item_code, ["is_stock_item", "is_fixed_asset", "stock_uom"], as_dict=True
			)
			is_stock = bool(item.is_stock_item)
			is_asset = bool(item.is_fixed_asset and not item.is_stock_item)
			if not (is_stock or is_asset):
				frappe.throw(_("Row {0}: Requests support Stock Items and Fixed Asset Items only").format(row.idx))
			if self.material_request_type == "Customer Demo" and is_stock and not frappe.db.get_value("Item", row.item_code, "demo_asset_item_code"):
				frappe.throw(_("Row {0}: Set Demo Asset Item Code on {1}").format(row.idx, row.item_code))
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than zero").format(row.idx))
			if (is_asset or (self.material_request_type == "Customer Demo" and is_stock)) and flt(row.qty) % 1:
				frappe.throw(_("Row {0}: Asset quantity must be a whole number").format(row.idx))
			row.item_type = "Stock Item" if is_stock else "Fixed Asset"
			row.uom = item.stock_uom
			if is_stock and not row.from_warehouse:
				row.from_warehouse = self.get_default_warehouse(row.item_code)
			if is_stock and not row.from_warehouse:
				frappe.throw(_("Row {0}: Select a Source Warehouse").format(row.idx))
			if row.from_warehouse:
				warehouse_company = frappe.db.get_value("Warehouse", row.from_warehouse, "company")
				if warehouse_company != self.company:
					frappe.throw(_("Row {0}: Source Warehouse must belong to Company {1}").format(row.idx, self.company))
		if self.material_request_type == "Customer Demo" and not self.customer_location:
			frappe.throw(_("Customer Location is required for Customer Demo requests"))
		if (
			self.material_request_type == "Customer Borrow / Loan"
			and self.loan_recipient_type != "Employee"
			and any(row.item_type == "Fixed Asset" for row in self.items)
			and not (self.loan_recipient_location or self.customer_location)
		):
			frappe.throw(_("Recipient Location is required for Asset Loans to Customers, Suppliers or Contacts"))

	def get_default_warehouse(self, item_code):
		warehouse = frappe.db.get_value(
			"Item Default", {"parent": item_code, "company": self.company}, "default_warehouse"
		)
		if not warehouse:
			warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
		if warehouse and frappe.db.get_value("Warehouse", warehouse, "company") == self.company:
			return warehouse
		return None

	def before_submit(self):
		super().before_submit()
		if is_customer_request(self):
			self.request_progress_status = "Pending"
		elif is_asset_transfer_request(self):
			self.asset_transfer_status = "Pending"

	def on_submit(self):
		super().on_submit()
		if is_customer_request(self) or is_asset_transfer_request(self):
			self.update_progress()

	def on_cancel(self):
		super().on_cancel()
		if is_customer_request(self):
			self.request_progress_status = "Cancelled"
		elif is_asset_transfer_request(self):
			self.asset_transfer_status = "Cancelled"

	def update_request_status(self):
		status = "Cancelled" if self.docstatus == 2 else "Draft" if self.docstatus == 0 else "Pending"
		if is_customer_request(self):
			if self.request_progress_status not in ("On Hold", "Closed") or status == "Cancelled":
				self.request_progress_status = status
		elif is_asset_transfer_request(self):
			self.asset_transfer_status = status

	def update_status(self, status):
		if is_customer_request(self) or is_asset_transfer_request(self):
			frappe.throw(_("Status actions are not available for this Material Request type"))
		return super().update_status(status)

	def update_progress(self):
		customer_request = is_customer_request(self)
		asset_transfer = is_asset_transfer_request(self)
		if not customer_request and not asset_transfer:
			return
		if self.docstatus != 1:
			self.update_request_status()
			return
		if customer_request and self.material_request_type == "Customer Borrow / Loan":
			self.update_custody_loan_progress()
			return
		fulfilled_total = returned_total = requested_total = 0
		for row in self.items:
			requested_total += flt(row.qty)
			if asset_transfer:
				entries = frappe.db.sql(
					"""select am.name, count(ami.name) as qty from `tabAsset Movement Item` ami
					join `tabAsset Movement` am on am.name=ami.parent
					where ami.request_item=%s and am.docstatus=1 and am.purpose='Transfer'
					and am.reference_doctype='Material Request' and am.reference_name=%s
					and ifnull(am.shipment_transit_leg, 0)=0
					group by am.name order by am.transaction_date desc, am.modified desc""",
					(row.name, self.name),
					as_dict=True,
				)
				row.fulfilled_qty = sum(flt(entry.qty) for entry in entries)
				row.returned_qty = 0
				row.asset_movement = entries[0].name if entries else None
			else:
				entries = frappe.db.sql(
					"""select am.name, am.purpose, count(*) as qty from `tabAsset Movement Item` ami
					join `tabAsset Movement` am on am.name=ami.parent
					where ami.request_item=%s and am.docstatus=1 and am.reference_doctype='Material Request' and am.reference_name=%s
					and ifnull(am.shipment_transit_leg, 0)=0
					group by am.name, am.purpose order by am.transaction_date desc, am.modified desc""",
					(row.name, self.name),
					as_dict=True,
				)
				row.fulfilled_qty = sum(flt(entry.qty) for entry in entries if entry.purpose == "Transfer")
				row.returned_qty = sum(flt(entry.qty) for entry in entries if entry.purpose == "Receipt")
				row.asset_movement = entries[0].name if entries else None
			row.outstanding_qty = max(flt(row.fulfilled_qty) - flt(row.returned_qty), 0)
			fulfilled_total += flt(row.fulfilled_qty)
			returned_total += flt(row.returned_qty)
			row.status = self.get_row_status(row)
			if row.name and self.docstatus == 1:
				frappe.db.set_value(
					"Material Request Item", row.name,
					{
						"fulfilled_qty": row.fulfilled_qty,
						"returned_qty": row.returned_qty,
						"outstanding_qty": row.outstanding_qty,
						"status": row.status,
						"stock_entry": row.get("stock_entry"),
						"asset_movement": row.get("asset_movement"),
					},
				)
		if customer_request and self.request_progress_status not in ("On Hold", "Closed"):
			if not fulfilled_total:
				self.request_progress_status = "Pending"
			elif fulfilled_total < requested_total:
				self.request_progress_status = "Partially Fulfilled"
			else:
				if returned_total >= fulfilled_total:
					self.request_progress_status = "Returned"
				else:
					self.request_progress_status = "Fulfilled"
		elif asset_transfer:
			if not fulfilled_total:
				self.asset_transfer_status = "Pending"
			elif fulfilled_total < requested_total:
				self.asset_transfer_status = "Partially Fulfilled"
			else:
				self.asset_transfer_status = "Fulfilled"

	def update_custody_loan_progress(self):
		loan_items = frappe.db.sql(
			"""select cli.name, cli.material_request_item, cli.qty, cli.returned_qty,
				cli.outstanding_qty, l.status, l.stock_entry, l.asset_movement
			from `tabCustody Loan Item` cli
			join `tabCustody Loan` l on l.name = cli.parent
			where l.material_request = %s and l.docstatus = 1""",
			self.name,
			as_dict=True,
		)
		by_request_item = {}
		for loan_item in loan_items:
			by_request_item.setdefault(loan_item.material_request_item, []).append(loan_item)

		fulfilled_total = returned_total = requested_total = 0
		for row in self.items:
			rows = by_request_item.get(row.name, [])
			issued_rows = [
				item for item in rows
				if item.status not in ("Pending Shipment", "In Transit", "Cancelled")
			]
			row.fulfilled_qty = sum(flt(item.qty) for item in issued_rows)
			row.returned_qty = sum(flt(item.returned_qty) for item in rows)
			row.outstanding_qty = sum(flt(item.outstanding_qty) for item in issued_rows)
			row.stock_entry = next((item.stock_entry for item in rows if item.stock_entry), None)
			row.asset_movement = next((item.asset_movement for item in rows if item.asset_movement), None)
			row.status = self.get_row_status(row)
			requested_total += flt(row.qty)
			fulfilled_total += flt(row.fulfilled_qty)
			returned_total += flt(row.returned_qty)
			if row.name:
				frappe.db.set_value(
					"Material Request Item",
					row.name,
					{
						"fulfilled_qty": row.fulfilled_qty,
						"returned_qty": row.returned_qty,
						"outstanding_qty": row.outstanding_qty,
						"status": row.status,
						"stock_entry": row.stock_entry,
						"asset_movement": row.asset_movement,
					},
				)

		if self.request_progress_status in ("On Hold", "Closed"):
			return
		if not fulfilled_total:
			self.request_progress_status = "Pending"
		elif fulfilled_total < requested_total:
			self.request_progress_status = "Partially Fulfilled"
		else:
			loan_states = {item.status for item in loan_items}
			if loan_states and loan_states.issubset({"Returned", "Closed"}):
				self.request_progress_status = "Closed" if "Closed" in loan_states else "Returned"
			elif returned_total or loan_states.intersection({"Partially Returned", "Closed", "Returned"}):
				self.request_progress_status = "Partially Returned"
			else:
				self.request_progress_status = "Fulfilled"

	def get_row_status(self, row):
		if not row.fulfilled_qty:
			return "Pending"
		if row.returned_qty >= row.fulfilled_qty:
			return "Returned"
		if row.returned_qty:
			return "Partially Returned"
		if row.fulfilled_qty < row.qty:
			return "Partially Fulfilled"
		return "Fulfilled"

	@frappe.whitelist()
	def get_post_submit_actions(self):
		self.reload()
		self.check_permission("read")
		actions = frappe._dict(
			check_asset_availability=False,
			asset_movement=False,
			stock_entry_issue=False,
			stock_entry_return=False,
			pick_list=False,
			return_assets=False,
			hold_for_asset_return=False,
			release_hold=False,
			stock_custody_loan=False,
			asset_custody_loan=False,
		)
		if self.docstatus != 1:
			return actions
		if is_asset_transfer_request(self):
			actions.asset_movement = any(
				flt(row.qty) - flt(row.fulfilled_qty) - get_in_transit_asset_movement_qty(row.name, "Transfer") > 0
				for row in self.items
			)
			return actions
		if self.material_request_type == "Customer Borrow / Loan":
			from erpnext.loan.doctype.custody_loan.custody_loan_core import get_custody_loan_actions

			loan_actions = get_custody_loan_actions(self)
			actions.stock_custody_loan = loan_actions.stock_loan
			actions.asset_custody_loan = loan_actions.asset_loan
			return actions
		if not is_customer_request(self):
			return actions
		if self.request_progress_status == "On Hold":
			actions.release_hold = True
			return actions

		is_demo = self.material_request_type == "Customer Demo"
		actions.check_asset_availability = is_demo or any(row.item_type == "Fixed Asset" for row in self.items)
		actions.hold_for_asset_return = self.request_progress_status not in ("Returned", "Closed", "Cancelled")
		available_by_item = {}
		stock_items = []

		for row in self.items:
			in_transit_issue = get_in_transit_asset_movement_qty(row.name, "Transfer")
			in_transit_return = get_in_transit_asset_movement_qty(row.name, "Receipt")
			asset_issue_qty = max(flt(row.qty) - flt(row.fulfilled_qty) - in_transit_issue, 0)
			asset_return_qty = max(flt(row.outstanding_qty) - in_transit_return, 0)
			if row.item_type == "Fixed Asset":
				actions.asset_movement |= asset_issue_qty > 0
				actions.return_assets |= asset_return_qty > 0
			elif is_demo and row.item_type == "Stock Item":
				stock_items.append(row)
				if asset_return_qty > 0:
					actions.return_assets = True
				asset_item_code = frappe.db.get_value("Item", row.item_code, "demo_asset_item_code")
				if asset_item_code not in available_by_item:
					available_by_item[asset_item_code] = (
						len(get_available_assets(asset_item_code, self.company)) if asset_item_code else 0
					)
				available = available_by_item[asset_item_code]
				use_existing = min(asset_issue_qty, available)
				available_by_item[asset_item_code] = available - use_existing
				actions.asset_movement |= use_existing > 0
				actions.pick_list |= asset_issue_qty > use_existing

		if is_demo and stock_items and frappe.db.exists(
			"Pick List",
			{
				"material_request": self.name,
				"docstatus": ["<", 2],
				"status": ["not in", ["Completed", "Cancelled"]],
			},
		):
			actions.pick_list = True

		return actions

	@frappe.whitelist()
	def make_custody_loan(self, loan_type):
		self.reload()
		if self.docstatus != 1 or self.material_request_type != "Customer Borrow / Loan":
			frappe.throw(_("Submit a Customer Borrow / Loan Material Request first"))
		from erpnext.loan.doctype.custody_loan.custody_loan_core import make_custody_loan_from_request

		return make_custody_loan_from_request(self, loan_type)

	@frappe.whitelist()
	def check_asset_availability(self):
		self.reload()
		self.check_permission("read")
		if not is_customer_request(self):
			frappe.throw(_("Asset availability is only available for Customer Demo / Loan requests"))
		results = []
		for row in self.items:
			if row.item_type == "Stock Item" and self.material_request_type != "Customer Demo":
				continue
			asset_item_code = row.item_code
			if row.item_type == "Stock Item" and self.material_request_type == "Customer Demo":
				asset_item_code = frappe.db.get_value("Item", row.item_code, "demo_asset_item_code")
			if not asset_item_code:
				continue
			available = get_available_assets(
				asset_item_code, self.company, demo_only=self.material_request_type == "Customer Demo"
			)
			due_soon = get_assets_due_soon(asset_item_code, self.company) if self.material_request_type == "Customer Demo" else []
			results.append({"request_item": row.name, "item_code": row.item_code, "asset_item_code": asset_item_code, "available": available, "due_soon": due_soon})
		return results

	@frappe.whitelist()
	def make_asset_movement(self):
		self.reload()
		self.check_permission("read")
		if self.docstatus != 1:
			frappe.throw(_("Submit the request before creating an Asset Movement"))
		if is_asset_transfer_request(self):
			return self.make_asset_transfer_movement()
		if self.material_request_type == "Customer Borrow / Loan":
			frappe.throw(_("Create Asset Movements from the Custody Loan"))
		if self.material_request_type != "Customer Demo":
			frappe.throw(_("Asset Movement is only available for Customer Demo or Asset Transfer requests"))
		if self.request_progress_status == "On Hold":
			frappe.throw(_("Release the request hold before creating an Asset Movement"))
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Transfer"
		movement.customer = self.customer
		movement.customer_contact = self.contact_person
		movement.customer_address = self.customer_address
		movement.customer_address_display = self.customer_address_display
		movement.customer_contact_display = self.customer_contact_display
		movement.customer_contact_email = self.customer_contact_email
		movement.expected_return_date = self.expected_return_date
		movement.reference_doctype = "Material Request"
		movement.reference_name = self.name
		available_by_item = {}
		for row in self.items:
			if row.item_type == "Fixed Asset":
				remaining_qty = max(
					cint(flt(row.qty) - flt(row.fulfilled_qty) - get_in_transit_asset_movement_qty(row.name, "Transfer")),
					0,
				)
				for _asset_index in range(remaining_qty):
					movement.append("assets", {
						"request_item": row.name, "target_location": self.customer_location,
						"asset_item_code": row.item_code,
						"demo_asset_required": cint(self.material_request_type == "Customer Demo"),
					})
			elif self.material_request_type == "Customer Demo":
				asset_item_code = frappe.db.get_value("Item", row.item_code, "demo_asset_item_code")
				if not asset_item_code:
					continue
				if asset_item_code not in available_by_item:
					available_by_item[asset_item_code] = len(get_available_assets(asset_item_code, self.company))
				remaining_qty = max(
					cint(flt(row.qty) - flt(row.fulfilled_qty) - get_in_transit_asset_movement_qty(row.name, "Transfer")),
					0,
				)
				asset_qty = min(remaining_qty, available_by_item[asset_item_code])
				available_by_item[asset_item_code] -= asset_qty
				for _asset_index in range(asset_qty):
					movement.append("assets", {
						"request_item": row.name, "target_location": self.customer_location,
						"asset_item_code": asset_item_code,
						"demo_asset_required": 1,
					})
		if not movement.assets:
			if self.material_request_type == "Customer Demo":
				frappe.throw(_("There are no unfulfilled Demo Assets available for a new Asset Movement"))
			frappe.throw(_("There are no unfulfilled Fixed Assets on this request"))
		return movement

	def make_asset_transfer_movement(self):
		if self.asset_transfer_status == "Cancelled":
			frappe.throw(_("Cancelled Asset Transfer requests cannot create an Asset Movement"))
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Transfer"
		movement.reference_doctype = "Material Request"
		movement.reference_name = self.name
		for row in self.items:
			if row.asset_transfer_asset and flt(row.fulfilled_qty) < flt(row.qty):
				if get_in_transit_asset_movement_qty(row.name, "Transfer"):
					continue
				movement.append(
					"assets",
					{
						"asset": row.asset_transfer_asset,
						"request_item": row.name,
						"asset_item_code": row.item_code,
						"source_location": row.asset_transfer_source_location,
						"target_location": row.asset_transfer_target_location,
					},
				)
		if not movement.assets:
			frappe.throw(_("There are no unfulfilled Assets on this request"))
		return movement

	@frappe.whitelist()
	def make_asset_receipt(self, requires_shipment=False):
		"""Create the customer return movement for Assets issued on this request."""
		self.reload()
		self.check_permission("read")
		if self.material_request_type == "Customer Borrow / Loan":
			frappe.throw(_("Create Asset Returns from the Custody Loan"))
		if self.material_request_type != "Customer Demo":
			frappe.throw(_("Asset returns are only available for Customer Demo requests"))
		if self.docstatus != 1:
			frappe.throw(_("Submit the request before creating an Asset Receipt"))
		if self.request_progress_status == "On Hold":
			frappe.throw(_("Release the request hold before creating an Asset Receipt"))

		self.update_progress()
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Receipt"
		movement.requires_shipment = cint(requires_shipment)
		movement.customer = self.customer
		movement.customer_contact = self.contact_person
		movement.customer_address = self.customer_address
		movement.customer_address_display = self.customer_address_display
		movement.customer_contact_display = self.customer_contact_display
		movement.customer_contact_email = self.customer_contact_email
		movement.expected_return_date = self.expected_return_date
		movement.reference_doctype = "Material Request"
		movement.reference_name = self.name

		for request_item in self.items:
			if request_item.item_type != "Fixed Asset" and not (
				self.material_request_type == "Customer Demo" and request_item.item_type == "Stock Item"
			):
				continue
			return_qty = max(
				flt(request_item.outstanding_qty)
				- get_in_transit_asset_movement_qty(request_item.name, "Receipt"),
				0,
			)
			if return_qty <= 0:
				continue
			assets = frappe.db.sql(
				"""select a.name, a.item_code, a.location,
					(select ami0.source_location from `tabAsset Movement Item` ami0
					 join `tabAsset Movement` am0 on am0.name = ami0.parent
					 where ami0.asset = a.name and ami0.request_item = %(request_item)s
					 and am0.reference_doctype = 'Material Request' and am0.reference_name = %(request)s
					 and am0.purpose = 'Transfer' and am0.docstatus = 1
					 and (am0.shipment_transit_leg = 1 or ifnull(am0.shipment_source_movement, '') = '')
					 order by am0.transaction_date asc, am0.modified asc limit 1) as return_location
					from `tabAsset` a
					join `tabAsset Movement Item` ami on ami.asset = a.name
					join `tabAsset Movement` am on am.name = ami.parent
					where ami.request_item = %(request_item)s
					and am.reference_doctype = 'Material Request' and am.reference_name = %(request)s
					and am.purpose = 'Transfer' and am.docstatus = 1
					and a.docstatus = 1 and a.company = %(company)s
					and a.demo_loan_status = 'With Customer'
					group by a.name, a.item_code, a.location
					order by a.name limit %(qty)s""",
				{
					"request_item": request_item.name,
					"request": self.name,
					"company": self.company,
					"qty": cint(return_qty),
				},
				as_dict=True,
			)
			if len(assets) < cint(return_qty):
				frappe.throw(
					_("Request Item {0}: all outstanding Assets must be at the Customer before they can be received.").format(
						request_item.idx
					)
				)
			for asset in assets:
				return_location = asset.return_location or frappe.db.get_value(
					"Warehouse", frappe.db.get_value("Asset", asset.name, "source_warehouse"), "asset_location"
				)
				if not return_location:
					frappe.throw(_("Set a return Location on the source Warehouse for Asset {0}").format(asset.name))
				movement.append(
					"assets",
					{
						"asset": asset.name,
						"request_item": request_item.name,
						"asset_item_code": asset.item_code,
						"source_location": asset.location,
						"target_location": return_location,
						"shipment_destination_location": return_location if requires_shipment else None,
						"demo_asset_required": cint(self.material_request_type == "Customer Demo"),
					},
				)

		if not movement.assets:
			frappe.throw(_("There are no outstanding Assets to receive for this request"))
		return movement

	@frappe.whitelist()
	def make_stock_entry(self, return_stock=False):
		self.reload()
		self.check_permission("read")
		if self.material_request_type != "Customer Borrow / Loan":
			frappe.throw(_("Stock Entries are only available for Customer Borrow / Loan requests"))
		frappe.throw(_("Create Stock Entries from the Custody Loan"))

	@frappe.whitelist()
	def release_hold(self):
		self.reload()
		self.check_permission("write")
		if self.material_request_type != "Customer Demo":
			frappe.throw(_("Hold actions are only available for Customer Demo requests"))
		if self.docstatus != 1 or self.request_progress_status != "On Hold":
			frappe.throw(_("Only a submitted request on hold can be released"))
		self.db_set("request_progress_status", "Pending")
		return "Pending"

	@frappe.whitelist()
	def hold_for_asset_return(self):
		self.reload()
		self.check_permission("write")
		if self.material_request_type != "Customer Demo":
			frappe.throw(_("Hold actions are only available for Customer Demo requests"))
		if self.docstatus != 1:
			frappe.throw(_("Submit the request before putting it on hold"))
		if self.request_progress_status in ("Returned", "Closed", "Cancelled"):
			frappe.throw(_("Completed Customer Demo requests cannot be put on hold"))
		self.db_set("request_progress_status", "On Hold")
		return self.request_progress_status

	@frappe.whitelist()
	def make_pick_list(self):
		self.reload()
		self.check_permission("read")
		if self.docstatus != 1:
			frappe.throw(_("Submit the request before creating a Pick List"))
		if self.request_progress_status == "On Hold":
			frappe.throw(_("Release the request hold before creating a Pick List"))
		if self.material_request_type != "Customer Demo":
			frappe.throw(_("Pick Lists for Stock to Asset Conversion are only available for Customer Demo requests"))
		active_pick_list = frappe.db.get_value(
			"Pick List",
			{"material_request": self.name, "docstatus": ["<", 2], "status": ["not in", ["Completed", "Cancelled"]]},
			"name",
		)
		if active_pick_list:
			return frappe.get_doc("Pick List", active_pick_list).as_dict()
		available_by_item = {}
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Material Transfer"
		pick_list.pick_list_type = "Customer Demo - Asset Conversion"
		pick_list.pick_manually = 1
		pick_list.material_request = self.name
		for row in self.items:
			if row.item_type != "Stock Item":
				continue
			asset_item_code = frappe.db.get_value("Item", row.item_code, "demo_asset_item_code")
			if asset_item_code not in available_by_item:
				available_by_item[asset_item_code] = (
					len(get_available_assets(asset_item_code, self.company)) if asset_item_code else 0
				)
			available = available_by_item[asset_item_code]
			remaining = max(
				flt(row.qty) - flt(row.fulfilled_qty) - get_in_transit_asset_movement_qty(row.name, "Transfer"),
				0,
			)
			use_existing = min(remaining, available)
			available_by_item[asset_item_code] = available - use_existing
			shortage = remaining - use_existing
			if not shortage:
				continue
			item = frappe.get_cached_doc("Item", row.item_code)
			pick_list.append("locations", {
				"item_code": row.item_code, "warehouse": row.from_warehouse,
				"qty": shortage, "stock_qty": shortage, "picked_qty": shortage,
				"stock_uom": item.stock_uom, "uom": item.stock_uom,
				"conversion_factor": 1, "material_request_item": row.name,
			})
		if not pick_list.locations:
			frappe.throw(_("There are no remaining Demo Stock Items that need conversion"))
		pick_list.insert()
		return pick_list.as_dict()


def get_available_assets(item_code, company, demo_only=True):
	demo_filter = "and a.is_demo_asset=1" if demo_only else ""
	return frappe.db.sql(
		f"""select a.name, a.asset_name, a.location from `tabAsset` a
		where a.item_code=%s and a.company=%s and a.docstatus=1
		{demo_filter}
		and a.status not in ('Sold','Scrapped','Capitalized')
		and ifnull(a.demo_loan_status, 'Available') = 'Available'
		and ifnull(a.custodian, '') = ''
		and not exists (
			select 1 from `tabAsset Movement Item` ami
			join `tabAsset Movement` am on am.name=ami.parent
			join `tabShipment` s on s.name=am.shipment
			where ami.asset=a.name and am.docstatus=1 and am.shipment_transit_leg=1
			and ifnull(s.tracking_status, '') != 'Delivered'
		)
		order by name""",
		(item_code, company), as_dict=True,
	)


def is_asset_in_transit(asset_name):
	return bool(
		frappe.db.sql(
			"""select 1 from `tabAsset Movement Item` ami
			join `tabAsset Movement` am on am.name=ami.parent
			join `tabShipment` s on s.name=am.shipment
			where ami.asset=%s and am.docstatus=1 and am.shipment_transit_leg=1
			and ifnull(s.tracking_status, '') != 'Delivered' limit 1""",
			asset_name,
		)
	)


def get_in_transit_asset_movement_qty(request_item_name, purpose):
	return cint(
		frappe.db.sql(
			"""select count(*) from `tabAsset Movement Item` ami
			join `tabAsset Movement` am on am.name=ami.parent
			join `tabShipment` s on s.name=am.shipment
			where ami.request_item=%s and am.reference_doctype='Material Request'
			and am.purpose=%s and am.docstatus=1
			and am.shipment_transit_leg=1 and ifnull(s.tracking_status, '') != 'Delivered'""",
			(request_item_name, purpose),
		)[0][0]
	)


def get_in_transit_stock_entry_qty(request, request_item, return_stock=False):
	loan_warehouse = request.customer_loan_warehouse
	if not loan_warehouse or not request_item.from_warehouse:
		return 0

	source_warehouse = loan_warehouse if return_stock else request_item.from_warehouse
	destination_warehouse = request_item.from_warehouse if return_stock else loan_warehouse
	return flt(
		frappe.db.sql(
			"""select sum(greatest(sed.qty - ifnull(receipt_row.qty, 0), 0))
			from `tabStock Entry Detail` sed
			join `tabStock Entry` se on se.name=sed.parent
			left join `tabStock Entry` receipt on receipt.outgoing_stock_entry=se.name and receipt.docstatus=1
			left join `tabStock Entry Detail` receipt_row
				on receipt_row.parent=receipt.name and receipt_row.ste_detail=sed.name
			where sed.material_request_item=%s and sed.s_warehouse=%s
			and sed.material_request=%s and se.shipment_destination_warehouse=%s and se.add_to_transit=1
			and se.requires_shipment=1 and se.docstatus=1""",
			(request_item.name, source_warehouse, request.name, destination_warehouse),
		)[0][0]
		or 0
	)


def get_assets_due_soon(item_code, company):
	days = frappe.db.get_value("Company", company, "customer_demo_return_lookahead_days") or 0
	return frappe.db.sql(
		"""select a.name, a.asset_name, a.location, am.customer, am.expected_return_date
		from `tabAsset` a join `tabAsset Movement Item` ami on ami.asset = a.name
		join `tabAsset Movement` am on am.name = ami.parent
		join `tabMaterial Request` cdlr on cdlr.name=am.reference_name and am.reference_doctype='Material Request'
		where a.item_code = %s and a.company = %s and am.docstatus = 1 and am.purpose='Transfer' and cdlr.material_request_type='Customer Demo'
		and am.customer is not null and a.demo_loan_status = 'With Customer'
		and am.expected_return_date between CURDATE() and DATE_ADD(CURDATE(), INTERVAL %s DAY)
		and not exists (select 1 from `tabAsset Movement Item` ami2 join `tabAsset Movement` am2 on am2.name = ami2.parent
		where ami2.asset = a.name and am2.docstatus = 1 and (am2.transaction_date > am.transaction_date
		or (am2.transaction_date = am.transaction_date and am2.modified > am.modified)))
		order by am.expected_return_date""",
		(item_code, company, days),
		as_dict=True,
	)


def validate_stock_entry_request(doc, method=None):
	linked_rows = [row for row in doc.get("items", []) if row.get("material_request_item")]
	if not linked_rows:
		return
	request_names = {
		row.material_request
		or frappe.db.get_value("Material Request Item", row.material_request_item, "parent")
		for row in linked_rows
	}
	if len(linked_rows) != len(doc.get("items", [])) or len(request_names) != 1:
		requests = [frappe.get_doc("Material Request", name) for name in request_names if name]
		if not any(is_customer_request(request) for request in requests):
			return
		frappe.throw(_("Customer Demo / Loan Stock Entries must link every row to one Material Request"))
	request_name = next(iter(request_names))
	if not request_name:
		return
	request = frappe.get_doc("Material Request", request_name)
	if not is_customer_request(request):
		return
	if request.docstatus != 1 or request.company != doc.company:
		frappe.throw(_("Stock Entry must reference a submitted request for the same Company"))
	if doc.purpose != "Material Transfer":
		frappe.throw(_("Customer Demo / Loan Stock Entries must use Material Transfer"))
	if request.material_request_type != "Customer Borrow / Loan":
		frappe.throw(_("Customer Demo stock must be converted and issued as an Asset"))
	if not doc.get("is_custody_loan_transaction") or not doc.get("custody_loan"):
		frappe.throw(_("Create Customer Borrow / Loan Stock Entries from the Custody Loan"))
	loan = frappe.get_doc("Custody Loan", doc.custody_loan)
	if loan.material_request != request.name or loan.loan_type != "Stock" or loan.company != request.company:
		frappe.throw(_("Stock Entry must link to a Stock Custody Loan from this Material Request"))
	from erpnext.loan.doctype.custody_loan.custody_loan_core import set_stock_entry_recipient_snapshot
	set_stock_entry_recipient_snapshot(doc, loan)
	if len(linked_rows) != len(doc.get("items", [])):
		frappe.throw(_("Link every Stock Entry row to a Material Request Item"))
	# A Custody Loan is already submitted when it creates its Stock Entry. Its
	# submitted Loan item must not make the request look fulfilled before this
	# generated Stock Entry has passed validation and posted.
	if not doc.flags.get("custody_loan_generated"):
		request.update_progress()
	transfer_qty = {}
	for row in linked_rows:
		request_row = frappe.get_doc("Material Request Item", row.material_request_item)
		if request_row.parent != request.name or request_row.item_code != row.item_code or request_row.item_type != "Stock Item":
			frappe.throw(_("Row {0}: Item does not match the linked request row").format(row.idx))
		loan_warehouse = request.customer_loan_warehouse
		target_warehouse = (
			doc.shipment_destination_warehouse
			if doc.get("requires_shipment") and doc.shipment_destination_warehouse
			else row.t_warehouse
		)
		if doc.get("outgoing_stock_entry"):
			outgoing = frappe.get_doc("Stock Entry", doc.outgoing_stock_entry)
			outgoing_row_name = row.get("ste_detail")
			outgoing_row = frappe.get_doc("Stock Entry Detail", outgoing_row_name) if outgoing_row_name else None
			if (
				not outgoing_row
				or outgoing_row.material_request != request.name
				or outgoing_row.material_request_item != request_row.name
			):
				frappe.throw(_("Transit receipt row {0} does not match its Material Request transfer").format(row.idx))
			outgoing_target = outgoing.shipment_destination_warehouse or outgoing_row.t_warehouse
			if outgoing_target == loan_warehouse and outgoing_row.s_warehouse == request_row.from_warehouse:
				direction = "issue"
			elif outgoing_target == request_row.from_warehouse and outgoing_row.s_warehouse == loan_warehouse:
				direction = "return"
			else:
				frappe.throw(_("Row {0}: Transit transfer does not match the requested Source and Loan Warehouses").format(row.idx))
		elif target_warehouse == loan_warehouse and row.s_warehouse == request_row.from_warehouse:
			direction = "issue"
		elif row.s_warehouse == loan_warehouse and target_warehouse == request_row.from_warehouse:
			direction = "return"
		else:
			frappe.throw(_("Row {0}: Transfer must be between the requested Source Warehouse and Company Customer Loan Warehouse").format(row.idx))
		key = (request_row.name, direction)
		transfer_qty[key] = (request_row, transfer_qty.get(key, (None, 0))[1] + flt(row.qty))
	if doc.get("outgoing_stock_entry"):
		return
	for (request_item_name, direction), (request_row, qty) in transfer_qty.items():
		in_transit_qty = get_in_transit_stock_entry_qty(
			request, request_row, return_stock=direction == "return"
		)
		available_qty = (
			flt(request_row.qty) - flt(request_row.fulfilled_qty) - in_transit_qty
			if direction == "issue"
			else flt(request_row.outstanding_qty) - in_transit_qty
		)
		if qty > available_qty:
			frappe.throw(_("Request Item {0}: Transfer Qty exceeds the {1} Qty of {2}").format(request_item_name, direction, available_qty))


def update_stock_entry_request_progress(doc, method=None):
	request_names = {
		row.material_request
		or frappe.db.get_value("Material Request Item", row.material_request_item, "parent")
		for row in doc.get("items", [])
		if row.get("material_request_item")
	}
	for request_name in request_names:
		if not request_name:
			continue
		request = frappe.get_doc("Material Request", request_name)
		if not is_customer_request(request):
			continue
		request.update_progress()
		request.db_set("request_progress_status", request.request_progress_status)
