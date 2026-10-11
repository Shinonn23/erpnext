import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display, get_default_address
from frappe.contacts.doctype.contact.contact import get_contact_details
from frappe.utils import cint, flt, getdate

from erpnext.accounts.party import get_party_shipping_address
from erpnext.stock.doctype.material_request.material_request import MaterialRequest

CUSTOMER_REQUEST_TYPES = ("Customer Demo", "Customer Borrow / Loan")
ASSET_TRANSFER_TYPE = "Asset Transfer"
MATERIAL_REQUEST_PURPOSES = (
	"Purchase",
	"Material Transfer",
	"Asset Transfer",
	"Customer Demo",
	"Customer Borrow / Loan",
)
ASSET_STATUSES_NOT_AVAILABLE = ("Draft", "Scrapped", "Sold", "Capitalized")


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
	def is_auto_created_by_reorder(self):
		return bool(
			self.get("auto_created_via_reorder")
			and frappe.flags.get("in_auto_reorder_material_request")
		)

	def _validate_selects(self):
		"""Permit legacy purposes only while trusted ERPNext reorder code creates the request."""
		purpose = self.material_request_type
		if self.is_auto_created_by_reorder() and purpose not in MATERIAL_REQUEST_PURPOSES:
			self.material_request_type = MATERIAL_REQUEST_PURPOSES[0]
			try:
				super()._validate_selects()
			finally:
				self.material_request_type = purpose
			return
		super()._validate_selects()

	def validate_material_request_type(self):
		if self.material_request_type not in MATERIAL_REQUEST_PURPOSES and not self.is_auto_created_by_reorder():
			frappe.throw(_("Purpose must be one of: {0}").format(", ".join(MATERIAL_REQUEST_PURPOSES)))
		if self.is_auto_created_by_reorder():
			return super().validate_material_request_type()
		if self.material_request_type == "Customer Demo":
			if not self.customer:
				frappe.throw(_("Customer is required for Customer Demo requests"))
			return
		if self.material_request_type == "Customer Borrow / Loan":
			if self.get("borrow_item_type") not in ("Stock Item", "Asset"):
				frappe.throw(_("Select whether this request borrows Stock Items or Assets"))
			return
		if is_asset_transfer_request(self):
			return
		super().validate_material_request_type()

	def before_validate(self):
		purpose = self.material_request_type
		if self.is_auto_created_by_reorder():
			self.clear_loan_recipient_details()
			self.customer_location = None
			self.expected_return_date = None
			self.customer_loan_warehouse = None
			self.asset_transfer_status = None
			self.request_progress_status = None
			self.request_remarks = None
			self.asset_source_location = None
			self.asset_item_filter = None
			return

		self.reset_amended_workflow_state()
		if self.get("amended_from") and self.docstatus == 0:
			if is_customer_request(self) and self.get("request_progress_status") == "Cancelled":
				self.request_progress_status = "Draft"
			elif is_asset_transfer_request(self) and self.get("asset_transfer_status") == "Cancelled":
				self.asset_transfer_status = "Draft"

		asset_rows = purpose in ("Asset Transfer", "Customer Demo") or (
			purpose == "Customer Borrow / Loan" and self.get("borrow_item_type") == "Asset"
		)
		for row in self.items:
			if asset_rows:
				if row.asset:
					row.item_code = row.item_code or frappe.db.get_value("Asset", row.asset, "item_code")
				row.from_warehouse = None
				row.warehouse = None
				row.item_type = "Fixed Asset"
			else:
				row.asset = None
				row.asset_name = None
				row.asset_serial_number = None
				row.source_location = None
				row.target_location = None
				row.item_type = "Stock Item"
				if purpose != "Material Transfer":
					row.from_warehouse = None

		if purpose == "Customer Borrow / Loan":
			if not self.loan_recipient_type and self.customer:
				self.loan_recipient_type = "Customer"
			if self.loan_recipient_type == "Customer":
				self.loan_recipient = self.loan_recipient or self.customer
				self.customer = self.loan_recipient
			else:
				self.clear_customer_details()
			if self.get("borrow_item_type") == "Asset":
				self.customer_loan_warehouse = None
			else:
				self.customer_loan_warehouse = frappe.db.get_value(
					"Company", self.company, "customer_loan_warehouse"
				)
				if not self.customer_loan_warehouse:
					frappe.throw(_("Set Customer Loan Warehouse on Company {0}").format(self.company))
				for row in self.items:
					row.warehouse = self.customer_loan_warehouse
		else:
			self.clear_loan_recipient_details()
			if purpose != "Customer Demo":
				self.clear_customer_details()
				self.recipient_address = None
				self.recipient_address_display = None
				self.customer_location = None
				self.expected_return_date = None
			self.customer_loan_warehouse = None

		if purpose != "Asset Transfer":
			self.asset_transfer_status = None
			self.asset_source_location = None
		if purpose not in ("Asset Transfer", "Customer Demo") and not (
					purpose == "Customer Borrow / Loan" and self.get("borrow_item_type") == "Asset"
		):
			self.asset_item_filter = None
		if purpose not in CUSTOMER_REQUEST_TYPES:
			self.request_progress_status = None
			self.request_remarks = None

	def reset_amended_workflow_state(self):
		"""Start an amended request at the workflow's initial draft state."""
		if not self.get("amended_from") or self.docstatus != 0:
			return

		workflow_name = frappe.db.get_value(
			"Workflow", {"document_type": self.doctype, "is_active": 1}, "name"
		)
		if not workflow_name:
			return

		workflow = frappe.get_cached_doc("Workflow", workflow_name)
		state_field = workflow.workflow_state_field
		if not state_field or not self.meta.has_field(state_field):
			return

		draft_states = [state.state for state in workflow.states if cint(state.doc_status) == 0]
		initial_state = next(iter(draft_states), None)
		if initial_state and self.get(state_field) not in draft_states:
			self.set(state_field, initial_state)

	def clear_customer_details(self):
		self.customer = None
		self.contact_person = None
		self.customer_contact_display = None
		self.customer_contact_email = None

	def clear_loan_recipient_details(self):
		self.borrow_item_type = None
		self.loan_recipient_type = None
		self.loan_recipient = None
		self.loan_recipient_location = None
		self.loan_recipient_contact = None
		self.loan_recipient_contact_display = None
		self.loan_recipient_contact_email = None

	def validate(self):
		super().validate()
		if self.material_request_type == "Customer Demo":
			self.validate_dates()
			self.validate_customer_contact(required=True)
			self.set_delivery_snapshots()
			if not self.recipient_address:
				frappe.throw(_("Customer Address is required for Customer Demo requests"))
			if not self.contact_person:
				frappe.throw(_("Customer Contact is required for Customer Demo requests"))
			if not self.customer_location:
				frappe.throw(_("Customer Location is required for Customer Demo requests"))
			self.validate_asset_items("Customer Demo")
			self.update_progress()
		elif self.material_request_type == "Customer Borrow / Loan":
			self.validate_dates()
			self.validate_loan_recipient()
			if self.get("borrow_item_type") == "Stock Item":
				self.validate_stock_loan_items()
			else:
				self.validate_asset_items("Customer Borrow / Loan")
			self.update_progress()
		elif is_asset_transfer_request(self):
			self.validate_asset_items("Asset Transfer")
			self.update_progress()
		elif self.material_request_type == "Material Transfer":
			self.validate_material_transfer_warehouses()

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
			self.validate_customer_contact()
			self.set_delivery_snapshots()
		else:
			self.customer = None
			if self.recipient_address:
				if not frappe.db.exists("Address", self.recipient_address):
					frappe.throw(_("Recipient Address does not exist"))
				self.recipient_address_display = get_address_display(self.recipient_address)
			if self.loan_recipient_type == "Contact":
				self.loan_recipient_contact = self.loan_recipient
			if self.loan_recipient_contact:
				if not frappe.db.exists("Contact", self.loan_recipient_contact):
					frappe.throw(_("Recipient Contact does not exist"))
				contact = get_customer_contact_snapshot(self.loan_recipient_contact)
				self.loan_recipient_contact_display = format_contact_display(contact)
				self.loan_recipient_contact_email = contact.email
	def validate_asset_items(self, purpose):
		if not self.items:
			frappe.throw(_("Add at least one Asset"))

		if self._action == "submit":
			for asset_name in sorted({row.asset for row in self.items if row.asset}):
				frappe.db.sql("select name from `tabAsset` where name=%s for update", asset_name)

		seen = set()
		for row in self.items:
			if not row.asset:
				frappe.throw(_("Row {0}: Select an Asset").format(row.idx))
			if row.asset in seen:
				frappe.throw(_("Row {0}: Asset {1} is listed more than once").format(row.idx, row.asset))
			seen.add(row.asset)

			asset = frappe.db.get_value(
				"Asset", row.asset,
				["item_code", "company", "docstatus", "status", "location", "demo_loan_status"],
				as_dict=True,
			)
			if not asset or asset.docstatus != 1 or asset.company != self.company:
				frappe.throw(_("Row {0}: Asset must be submitted and belong to Company {1}").format(row.idx, self.company))
			if asset.status in ASSET_STATUSES_NOT_AVAILABLE:
				frappe.throw(_("Row {0}: {1} Asset cannot be selected").format(row.idx, asset.status))
			if not asset.location:
				frappe.throw(_("Row {0}: Asset must have a current Location").format(row.idx))
			if self.get("asset_source_location") and asset.location != self.get("asset_source_location"):
				frappe.throw(
					_("Row {0}: Asset is not at Source Location {1}").format(
						row.idx, self.get("asset_source_location")
					)
				)
			if asset.demo_loan_status == "With Customer":
				frappe.throw(_("Row {0}: Asset is currently with a Customer").format(row.idx))
			if purpose in ("Customer Borrow / Loan", "Customer Demo") and asset.custodian:
				frappe.throw(_("Row {0}: Asset is currently assigned to an Employee").format(row.idx))
			if not frappe.db.get_value("Item", asset.item_code, "is_fixed_asset"):
				frappe.throw(_("Row {0}: Asset must reference a Fixed Asset Item").format(row.idx))
			if self.get("asset_item_filter") and asset.item_code != self.get("asset_item_filter"):
				frappe.throw(_("Row {0}: Asset does not match the Fixed Asset Item filter").format(row.idx))
			if self.has_active_asset_request(row.asset):
				frappe.throw(_("Row {0}: Asset is already reserved by another active Material Request").format(row.idx))
			if purpose == "Customer Demo" and not frappe.db.get_value("Asset", row.asset, "is_demo_asset"):
				frappe.throw(_("Row {0}: Customer Demo requires an Asset marked as a Demo Asset").format(row.idx))

			row.item_code = asset.item_code
			row.asset_name = frappe.db.get_value("Asset", row.asset, "asset_name")
			row.asset_serial_number = frappe.db.get_value("Asset", row.asset, "asset_serial_number")
			row.source_location = asset.location
			row.qty = 1
			row.item_type = "Fixed Asset"
			item = frappe.get_cached_value("Item", row.item_code, ["stock_uom"], as_dict=True)
			row.uom = item.stock_uom
			row.stock_uom = item.stock_uom
			row.conversion_factor = 1
			row.schedule_date = row.schedule_date or self.schedule_date
			if purpose == "Asset Transfer":
				if not row.target_location:
					frappe.throw(_("Row {0}: Select a Target Location").format(row.idx))
				if row.target_location == asset.location:
					frappe.throw(_("Row {0}: Target Location must differ from the Source Location").format(row.idx))
			elif purpose == "Customer Demo":
				row.target_location = row.target_location or self.customer_location
				if not row.target_location:
					frappe.throw(_("Row {0}: Customer Location is required for this Demo Asset").format(row.idx))
			else:
				row.target_location = row.target_location or self.loan_recipient_location
				if self.loan_recipient_type != "Employee" and not row.target_location:
					frappe.throw(_("Row {0}: Recipient Location is required for this Asset Loan").format(row.idx))
				if row.target_location and row.target_location == asset.location:
					frappe.throw(_("Row {0}: Recipient Location must differ from the Source Location").format(row.idx))

	def has_active_asset_request(self, asset_name):
		return bool(
			frappe.db.sql(
				"""select mr.name from `tabMaterial Request Item` mri
				join `tabMaterial Request` mr on mr.name=mri.parent
				where mri.asset=%s and mr.name!=%s and mr.docstatus=1
				and ((mr.material_request_type='Asset Transfer'
					and ifnull(mr.asset_transfer_status, '') not in ('Fulfilled', 'Cancelled'))
				or (mr.material_request_type in ('Customer Demo', 'Customer Borrow / Loan')
					and (mr.material_request_type='Customer Demo' or mr.borrow_item_type='Asset')
					and ifnull(mr.request_progress_status, '') not in ('Returned', 'Closed', 'Cancelled')))
				limit 1""",
				(asset_name, self.name or ""),
			)
		)

	def set_delivery_snapshots(self):
		if not self.customer:
			return

		if not self.recipient_address:
			self.recipient_address = get_default_customer_address(self.customer)
		if self.recipient_address:
			if not is_address_linked_to_customer(self.recipient_address, self.customer):
				frappe.throw(_("Customer Address must be linked to Customer {0}").format(self.customer))
			self.recipient_address_display = get_address_display(self.recipient_address)
		else:
			self.recipient_address_display = None

		if self.contact_person:
			contact = get_customer_contact_snapshot(self.contact_person)
			self.customer_contact_email = contact.email
			self.customer_contact_display = format_contact_display(contact)
		else:
			self.customer_contact_email = None
			self.customer_contact_display = None

	def validate_customer_contact(self, required=False):
		if required and not self.contact_person:
			frappe.throw(_("Customer Contact is required"))
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

	def validate_demo_items(self):
		"""Keep the old method name for callers while validating the shared MR Items table."""
		self.validate_asset_items("Customer Demo")

	def validate_stock_loan_items(self):
		if not self.items:
			frappe.throw(_("Add at least one Stock Item to borrow"))
		super().validate_items()
		for row in self.items:
			item = frappe.get_cached_value("Item", row.item_code, ["is_stock_item", "stock_uom"], as_dict=True)
			if not item or not item.is_stock_item:
				frappe.throw(_("Row {0}: Stock Loans support Stock Items only").format(row.idx))
			row.item_type = "Stock Item"
			row.uom = item.stock_uom
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than zero").format(row.idx))
			row.warehouse = self.customer_loan_warehouse

	def validate_material_transfer_warehouses(self):
		for row in self.items:
			if not row.warehouse:
				frappe.throw(_("Row {0}: Select a Target Warehouse").format(row.idx))
			for fieldname, label in (("warehouse", "Target"), ("from_warehouse", "Source")):
				warehouse = row.get(fieldname)
				if warehouse and frappe.db.get_value("Warehouse", warehouse, "company") != self.company:
					frappe.throw(
						_("Row {0}: {1} Warehouse must belong to Company {2}").format(
							row.idx, label, self.company
						)
					)

	def get_default_warehouse(self, item_code):
		warehouse = frappe.db.get_value(
			"Item Default", {"parent": item_code, "company": self.company}, "default_warehouse"
		)
		if not warehouse:
			warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
		if warehouse and frappe.db.get_value("Warehouse", warehouse, "company") == self.company:
			return warehouse
		return None

	def set_title(self):
		if self.title:
			return
		if self.material_request_type in ("Asset Transfer", "Customer Demo") or (
			self.material_request_type == "Customer Borrow / Loan" and self.get("borrow_item_type") == "Asset"
		):
			items = [row.asset_name or row.item_name or row.asset for row in self.items]
		else:
			return super().set_title()
		self.title = _("{0} Request for {1}").format(
			_(self.material_request_type), ", ".join(items[:3])
		)[:100]

	def before_submit(self):
		super().before_submit()
		if is_asset_transfer_request(self) or self.material_request_type == "Customer Demo" or (
			self.material_request_type == "Customer Borrow / Loan" and self.get("borrow_item_type") == "Asset"
		):
			self.validate_asset_items(self.material_request_type)
		elif self.material_request_type == "Customer Borrow / Loan":
			self.validate_stock_loan_items()
		if is_customer_request(self):
			self.request_progress_status = "Pending"
		elif is_asset_transfer_request(self):
			self.asset_transfer_status = "Pending"

	def before_cancel(self):
		super().before_cancel()
		self.cancel_linked_custody_loans()
		self.cancel_linked_asset_movements()

	def cancel_linked_custody_loans(self):
		if not is_customer_request(self):
			return

		loan_names = frappe.get_all(
			"Custody Loan",
			filters={"material_request": self.name, "docstatus": 1},
			pluck="name",
			order_by="modified desc",
		)
		for loan_name in loan_names:
			self.cancel_linked_document("Custody Loan", loan_name)

	def cancel_linked_asset_movements(self):
		if not (is_customer_request(self) or is_asset_transfer_request(self)):
			return

		movement_names = frappe.get_all(
			"Asset Movement",
			filters={
				"reference_doctype": self.doctype,
				"reference_name": self.name,
				"docstatus": 1,
			},
			pluck="name",
			order_by="transaction_date desc, modified desc",
		)
		for movement_name in movement_names:
			self.cancel_linked_document("Asset Movement", movement_name)

	def cancel_linked_document(self, doctype, name):
		doc = frappe.get_doc(doctype, name)
		ignore_linked_doctypes = doc.get("ignore_linked_doctypes") or ()
		if isinstance(ignore_linked_doctypes, str):
			ignore_linked_doctypes = (ignore_linked_doctypes,)
		doc.ignore_linked_doctypes = tuple(set(ignore_linked_doctypes) | {self.doctype})
		doc.cancel()

	def on_submit(self):
		super().on_submit()
		if is_customer_request(self) or is_asset_transfer_request(self):
			self.update_progress()

	def on_cancel(self):
		super().on_cancel()
		if is_customer_request(self):
			self.db_set("request_progress_status", "Cancelled")
		elif is_asset_transfer_request(self):
			self.db_set("asset_transfer_status", "Cancelled")
		if self.meta.has_field("workflow_state"):
			self.db_set("workflow_state", "")

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
		if not is_customer_request(self) and not is_asset_transfer_request(self):
			return
		if self.docstatus != 1:
			self.update_request_status()
			return
		if is_customer_request(self):
			self.update_custody_loan_progress()
			return

		is_transfer = is_asset_transfer_request(self)
		child_doctype = "Material Request Item"
		child_field = "request_item"
		rows = self.items
		fulfilled_total = returned_total = requested_total = 0
		for row in rows:
			requested_total += flt(row.qty)
			entries = frappe.db.sql(
				f"""select am.name, am.purpose, count(ami.name) as qty from `tabAsset Movement Item` ami
				join `tabAsset Movement` am on am.name=ami.parent
				where ami.{child_field}=%s and am.docstatus=1
				and am.reference_doctype='Material Request' and am.reference_name=%s
				group by am.name, am.purpose order by am.transaction_date desc, am.modified desc""",
				(row.name, self.name),
				as_dict=True,
			)
			row.fulfilled_qty = sum(flt(entry.qty) for entry in entries if entry.purpose == "Transfer")
			row.returned_qty = (
				sum(flt(entry.qty) for entry in entries if entry.purpose == "Receipt") if not is_transfer else 0
			)
			row.outstanding_qty = max(flt(row.fulfilled_qty) - flt(row.returned_qty), 0)
			row.asset_movement = entries[0].name if entries else None
			fulfilled_total += flt(row.fulfilled_qty)
			returned_total += flt(row.returned_qty)
			row.status = self.get_row_status(row)
			if row.name and self.docstatus == 1:
				frappe.db.set_value(
					child_doctype, row.name,
					{
						"fulfilled_qty": row.fulfilled_qty,
						"returned_qty": row.returned_qty,
						"outstanding_qty": row.outstanding_qty,
						"status": row.status,
						"asset_movement": row.get("asset_movement"),
					},
				)
		if is_customer_request(self) and self.request_progress_status not in ("On Hold", "Closed"):
			if not fulfilled_total:
				self.request_progress_status = "Pending"
			elif fulfilled_total < requested_total:
				self.request_progress_status = "Partially Fulfilled"
			else:
				if returned_total >= fulfilled_total:
					self.request_progress_status = "Returned"
				else:
					self.request_progress_status = "Fulfilled"
		elif is_transfer:
			if fulfilled_total >= requested_total:
				self.asset_transfer_status = "Fulfilled"
			elif fulfilled_total:
				self.asset_transfer_status = "Partially Fulfilled"
			else:
				self.asset_transfer_status = "Pending"

	def update_custody_loan_progress(self):
		loan_items = frappe.db.sql(
				"""select cli.name, cli.material_request_item, cli.qty, cli.returned_qty,
				cli.outstanding_qty, l.status, l.delivery_note, l.asset_movement, l.name as custody_loan
				from `tabCustody Loan Item` cli
			join `tabCustody Loan` l on l.name = cli.parent
			where l.material_request = %s and l.docstatus = 1""",
			self.name,
			as_dict=True,
		)
		request_field = "material_request_item"
		child_doctype = "Material Request Item"
		rows = self.items
		by_request_row = {}
		for loan_item in loan_items:
			by_request_row.setdefault(loan_item.get(request_field), []).append(loan_item)

		fulfilled_total = returned_total = requested_total = 0
		for row in rows:
			loan_rows = by_request_row.get(row.name, [])
			from erpnext.loan.doctype.custody_loan.custody_loan_core import transaction_is_received

			issued_rows = [
				item
				for item in loan_rows
				if transaction_is_received(item.delivery_note, item.asset_movement)
			]
			row.fulfilled_qty = sum(flt(item.qty) for item in issued_rows)
			row.returned_qty = sum(flt(item.returned_qty) for item in loan_rows)
			row.outstanding_qty = sum(flt(item.outstanding_qty) for item in issued_rows)
			row.delivery_note = next((item.delivery_note for item in loan_rows if item.delivery_note), None)
			row.asset_movement = next((item.asset_movement for item in loan_rows if item.asset_movement), None)
			row.custody_loan = next((item.custody_loan for item in loan_rows), None)
			row.status = self.get_row_status(row)
			requested_total += flt(row.qty)
			fulfilled_total += flt(row.fulfilled_qty)
			returned_total += flt(row.returned_qty)
			if row.name:
				frappe.db.set_value(
					child_doctype,
					row.name,
					{
						"fulfilled_qty": row.fulfilled_qty,
						"returned_qty": row.returned_qty,
						"outstanding_qty": row.outstanding_qty,
						"status": row.status,
						"delivery_note": row.delivery_note,
						"asset_movement": row.asset_movement,
						"custody_loan": row.custody_loan,
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
			asset_movement=False,
			stock_custody_loan=False,
			asset_custody_loan=False,
		)
		if self.docstatus != 1:
			return actions
		if is_asset_transfer_request(self):
			actions.asset_movement = any(flt(row.qty) - flt(row.fulfilled_qty) > 0 for row in self.items)
			return actions
		if is_customer_request(self):
			from erpnext.loan.doctype.custody_loan.custody_loan_core import get_custody_loan_actions

			loan_actions = get_custody_loan_actions(self)
			actions.stock_custody_loan = loan_actions.stock_loan
			actions.asset_custody_loan = loan_actions.asset_loan
		return actions

	@frappe.whitelist()
	def make_custody_loan(self, loan_type):
		self.reload()
		if self.docstatus != 1 or self.material_request_type not in ("Customer Demo", "Customer Borrow / Loan"):
			frappe.throw(_("Submit a Customer Demo or Customer Borrow / Loan Material Request first"))
		from erpnext.loan.doctype.custody_loan.custody_loan_core import make_custody_loan_from_request

		return make_custody_loan_from_request(self, loan_type)

	@frappe.whitelist()
	def make_asset_movement(self):
		self.reload()
		self.check_permission("read")
		if self.docstatus != 1:
			frappe.throw(_("Submit the request before creating an Asset Movement"))
		if is_asset_transfer_request(self):
			return self.make_asset_transfer_movement()
		if self.material_request_type in ("Customer Demo", "Customer Borrow / Loan"):
			frappe.throw(_("Create Asset Movements from the Custody Loan"))
		frappe.throw(_("Asset Movements can only be created from Asset Transfer requests"))

	def make_asset_transfer_movement(self):
		if self.asset_transfer_status == "Cancelled":
			frappe.throw(_("Cancelled Asset Transfer requests cannot create an Asset Movement"))
		movement = frappe.new_doc("Asset Movement")
		movement.company = self.company
		movement.purpose = "Transfer"
		movement.reference_doctype = "Material Request"
		movement.reference_name = self.name
		transfer_rows = []
		for row in self.items:
			if row.asset and flt(row.fulfilled_qty) < flt(row.qty):
				transfer_rows.append(row)

		asset_names = list({row.asset for row in transfer_rows})
		asset_details = {
			asset.name: asset
			for asset in frappe.get_all(
				"Asset",
				filters={"name": ["in", asset_names]},
				fields=["name", "asset_name", "asset_serial_number", "is_demo_asset", "demo_loan_status"],
			)
		} if asset_names else {}

		for row in transfer_rows:
			asset = asset_details.get(row.asset) or frappe._dict()
			movement.append(
				"assets",
				{
					"asset": row.asset,
					"asset_name": asset.asset_name,
					"asset_serial_number": asset.asset_serial_number,
					"is_demo_asset": asset.is_demo_asset,
					"demo_loan_status": asset.demo_loan_status,
					"request_item": row.name,
					"asset_item_code": row.item_code,
					"source_location": row.source_location,
					"target_location": row.target_location,
				},
			)
		if not movement.assets:
			frappe.throw(_("There are no unfulfilled Assets on this request"))
		return movement

def get_available_assets(item_code, company, demo_only=False, location=None, exclude_custodian=True):
	filters = ["a.company=%s", "a.docstatus=1", "a.status not in ('Draft','Sold','Scrapped','Capitalized')"]
	values = [company]
	if item_code:
		filters.append("a.item_code=%s")
		values.append(item_code)
	if location:
		filters.append("a.location=%s")
		values.append(location)
	if demo_only:
		filters.append("a.is_demo_asset=1")
	if exclude_custodian:
		filters.append("ifnull(a.custodian, '') = ''")
	return frappe.db.sql(
		f"""select a.name, a.asset_name, a.item_code, a.location from `tabAsset` a
		where {' and '.join(filters)}
		and exists (select 1 from `tabItem` item where item.name=a.item_code and item.is_fixed_asset=1)
		and ifnull(a.demo_loan_status, 'Available') = 'Available'
		and not exists (
			select 1 from `tabMaterial Request Item` mri
			join `tabMaterial Request` mr on mr.name=mri.parent
			where mri.asset=a.name and mr.docstatus=1
			and ((mr.material_request_type='Asset Transfer'
				and ifnull(mr.asset_transfer_status, '') not in ('Fulfilled', 'Cancelled'))
			or (mr.material_request_type='Customer Demo'
				and ifnull(mr.request_progress_status, '') not in ('Returned', 'Closed', 'Cancelled'))
			or (mr.material_request_type='Customer Borrow / Loan' and mr.borrow_item_type='Asset'
				and ifnull(mr.request_progress_status, '') not in ('Returned', 'Closed', 'Cancelled')))
		)
		order by name""",
		values, as_dict=True,
	)


@frappe.whitelist()
def get_assets_for_material_request(company, location, item_code=None):
	"""Return available Fixed Assets at the selected Source Location."""
	frappe.has_permission("Asset", "read", throw=True)
	frappe.has_permission("Company", "read", doc=company, throw=True)
	if not location or not frappe.db.exists("Location", location):
		frappe.throw(_("Select a valid Source Location"))
	if item_code and not frappe.db.get_value("Item", item_code, "is_fixed_asset"):
		frappe.throw(_("The Item filter must be a Fixed Asset Item"))
	assets = get_available_assets(item_code, company, location=location, exclude_custodian=False)
	asset_names = [asset.name for asset in assets]
	assets = frappe.get_list(
		"Asset",
		filters={"name": ["in", asset_names or [""]]},
		fields=["name", "asset_name", "asset_serial_number", "item_code", "location"],
		order_by="name asc",
	)
	for asset in assets:
		asset.stock_uom = frappe.get_cached_value("Item", asset.item_code, "stock_uom")
	return assets


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def asset_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	"""Search available Fixed Asset records for Material Request asset rows."""
	frappe.has_permission("Asset", "read", throw=True)
	filters = frappe._dict(filters or {})
	if (
		not filters.company
		or not frappe.db.exists("Company", filters.company)
		or filters.material_request_type not in ("Asset Transfer", "Customer Demo", "Customer Borrow / Loan")
	):
		return []
	frappe.has_permission("Company", "read", doc=filters.company, throw=True)

	from erpnext.controllers.queries import get_match_cond

	conditions = [
		"tabAsset.company=%(company)s",
		"tabAsset.docstatus=1",
		"tabAsset.status not in ('Draft', 'Sold', 'Scrapped', 'Capitalized')",
		"tabItem.is_fixed_asset=1",
		"ifnull(tabAsset.demo_loan_status, 'Available')='Available'",
		"not exists (select 1 from `tabMaterial Request Item` mri join `tabMaterial Request` mr on mr.name=mri.parent "
		"where mri.asset=tabAsset.name and mr.docstatus=1 and ((mr.material_request_type='Asset Transfer' "
		"and ifnull(mr.asset_transfer_status, '') not in ('Fulfilled', 'Cancelled')) or "
		"(mr.material_request_type='Customer Demo' "
		"and ifnull(mr.request_progress_status, '') not in ('Returned', 'Closed', 'Cancelled')) or "
		"(mr.material_request_type='Customer Borrow / Loan' and mr.borrow_item_type='Asset' "
		"and ifnull(mr.request_progress_status, '') not in ('Returned', 'Closed', 'Cancelled'))))",
	]
	if filters.material_request_type == "Customer Borrow / Loan":
		conditions.append("ifnull(tabAsset.custodian, '')=''")
	if filters.material_request_type == "Customer Demo":
		conditions.append("tabAsset.is_demo_asset=1")
	values = {"company": filters.company, "txt": f"%{txt}%", "start": start, "page_len": page_len}
	if filters.item_code:
		conditions.append("tabAsset.item_code=%(item_code)s")
		values["item_code"] = filters.item_code
	if filters.location:
		conditions.append("tabAsset.location=%(location)s")
		values["location"] = filters.location
	conditions.append("(tabAsset.name like %(txt)s or tabAsset.asset_name like %(txt)s or tabAsset.asset_serial_number like %(txt)s)")
	match_condition = get_match_cond("Asset")
	result_fields = (
		"tabAsset.name, tabAsset.asset_serial_number, tabAsset.asset_name, "
		"tabAsset.item_code, tabAsset.location, tabItem.stock_uom"
		if as_dict
		else "tabAsset.name, tabAsset.asset_name, tabAsset.item_code, tabAsset.location"
	)
	return frappe.db.sql(
		f"""select {result_fields}
		from `tabAsset` tabAsset inner join `tabItem` tabItem on tabItem.name=tabAsset.item_code
		where {' and '.join(conditions)} {match_condition}
		order by tabAsset.name limit %(page_len)s offset %(start)s""",
		values,
		as_dict=as_dict,
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
	frappe.throw(
		_("Customer Demo and Borrow stock must use the Loan Delivery Note workflow, not a Stock Entry")
	)
