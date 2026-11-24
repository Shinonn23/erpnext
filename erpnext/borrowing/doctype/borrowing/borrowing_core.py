from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import set_workflow_state_on_action
from frappe.utils import cint, flt, getdate

from erpnext.borrowing.doctype.borrowing.borrowing_tracking import (
	conversion_factors,
	get_balances,
	grouped_open_rows,
	is_tracked,
	sync_tracking_table,
	transaction_row_entries,
	validate_tracked_rows,
)
from erpnext.stock.doctype.pick_list.pick_identity import refuse_mismatch
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.doctype.storage_location.storage_location import (
	QUALITY_INSPECTION,
	get_default_storage_location,
	get_location_type,
	typed_location,
)
from erpnext.stock.get_item_details import get_item_details
from erpnext.stock.serial_batch_bundle import get_serial_nos_from_bundle
from erpnext.stock.warehouse_policy import warehouse_type_allows

BORROWER_TYPES = {"Employee", "Customer"}
# Siwat, 16 Nov 2026: the request type names the borrower (Customer Demo lends to its Customer)
BORROWER_TYPE_OF = {"Borrow": "Employee", "Customer Borrow": "Customer"}
# Siwat, 18 Nov 2026 (UX WP4): a borrowing chains like selling. The goods come back on a return Delivery Note made
# from the issue Delivery Note (an Asset on a return Asset Movement made from the issue movement): both name the
# Borrowing as their source. There is no Borrowing Return document.
LOAN_SOURCE_DOCTYPES = {"Borrowing", "Borrowing Adjustment"}
_CUSTODY_OPERATION_TOKEN = object()


def is_trusted_custody_operation(doc):
	"""Distinguish native generation/reversal from client-supplied transaction markers."""
	return doc.flags.get("custody_operation_token") is _CUSTODY_OPERATION_TOKEN


def get_borrowing_return_delivery_note(quality_inspection):
	"""The return Delivery Note of a borrowing a Quality Inspection inspects, else None."""
	if quality_inspection.reference_type != "Delivery Note" or not quality_inspection.reference_name:
		return None
	delivery_note = frappe.db.get_value(
		"Delivery Note",
		quality_inspection.reference_name,
		["is_return", "is_borrowing_transaction", "borrowing"],
		as_dict=True,
	)
	if delivery_note and is_borrowing_return_delivery_note(delivery_note):
		return quality_inspection.reference_name
	return None


class BorrowingQualityInspectionMixin:
	def update_qc_reference(self, remove_reference=False):
		delivery_note_name = get_borrowing_return_delivery_note(self)
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
		delivery_note_name = get_borrowing_return_delivery_note(self)
		if delivery_note_name:
			# A returned unit is judged by hand; the system records that itself and never asks for the tick
			# (Connect Diagnostics: no forced ticks).
			self.manual_inspection = 1
			for reading in self.get("readings") or []:
				reading.manual_inspection = 1
		result = super().validate()
		if delivery_note_name:
			validate_borrowing_return_quality_inspection(self, delivery_note_name)
		return result

	def validate_inspection_required(self):
		if get_borrowing_return_delivery_note(self):
			return
		return super().validate_inspection_required()

	def before_submit(self):
		if get_borrowing_return_delivery_note(self) and self.manual_inspection:
			if self.status not in ("Accepted", "Rejected"):
				frappe.throw(
					_(
						"Set the inspection result of the returned item before submitting this Quality Inspection"
					)
				)
			# Remarks are optional (Connect Diagnostics, Siwat, 5 Nov 2026, relayed by the user)
		return super().before_submit()

	def before_cancel(self):
		delivery_note_name = get_borrowing_return_delivery_note(self)
		if delivery_note_name and frappe.db.get_value("Delivery Note", delivery_note_name, "docstatus") == 1:
			frappe.throw(
				_("Cancel the borrowing return Delivery Note before cancelling its Quality Inspection")
			)
		before_cancel = getattr(super(), "before_cancel", None)
		if before_cancel:
			return before_cancel()

	def on_submit(self):
		result = super().on_submit()
		update_borrowing_return_status_from_quality_inspection(self)
		return result

	def on_cancel(self):
		result = super().on_cancel()
		update_borrowing_return_status_from_quality_inspection(self)
		return result


class Borrowing(Document):
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
		self.refresh_balances()

	def validate_material_request(self):
		request = frappe.get_doc("Material Request", self.material_request)
		if request.docstatus != 1 or request.material_request_type not in (
			"Customer Demo",
			"Borrow",
			"Customer Borrow",
		):
			frappe.throw(_("Borrowings can only be created from a submitted Demo or Borrow Material Request"))
		if request.company != self.company:
			frappe.throw(_("Borrowing and Material Request must use the same Company"))
		if any(
			row.get("demo_acquisition_method") in ("Convert Stock", "Ready Asset") and not row.get("asset")
			for row in request.items
		):
			frappe.throw(_("Complete Asset selection or conversion before preparing the borrowings."))
		if self.loan_type not in ("Stock", "Asset"):
			frappe.throw(_("Select Stock or Asset as the Borrowing Type"))
		if self.loan_type not in request_loan_types(request) and not self.get("source_conversion"):
			frappe.throw(_("Borrowing Type must match the Material Request Borrow Item Type"))

		# The borrower and delivery snapshot are owned by the submitted request.
		self.borrower_type = request.loan_recipient_type or ("Customer" if request.customer else None)
		self.borrower = request.loan_recipient or request.customer
		if self.borrower_type not in BORROWER_TYPES or not self.borrower:
			frappe.throw(_("Select an Employee or a Customer on the Material Request"))
		expected = BORROWER_TYPE_OF.get(request.material_request_type)
		if expected and self.borrower_type != expected:
			frappe.throw(
				_("A Borrow request lends to an Employee")
				if expected == "Employee"
				else _("A Customer Borrow request lends to a Customer")
			)
		if not frappe.db.exists(self.borrower_type, self.borrower):
			frappe.throw(_("Borrower {0} does not exist as a {1}").format(self.borrower, self.borrower_type))
		self.set_requester(request)
		if not self.expected_return_date:
			self.expected_return_date = request.expected_return_date
		if not self.expected_return_date or getdate(self.expected_return_date) < getdate(
			request.transaction_date
		):
			frappe.throw(_("Expected Return Date must be on or after the Material Request date"))

		# an Asset loan of converted Demo Assets keeps the address and contact of its demo customer (Siwat, 16 Nov 2026)
		if not (self.get("source_conversion") and self.get("recipient_customer")):
			self.recipient_address = request.recipient_address
			self.recipient_address_display = request.recipient_address_display
			self.recipient_contact = request.loan_recipient_contact or request.contact_person
			self.recipient_contact_display = (
				request.loan_recipient_contact_display or request.customer_contact_display
			)
			self.recipient_contact_email = (
				request.loan_recipient_contact_email or request.customer_contact_email
			)
		self.recipient_location = request.loan_recipient_location or request.customer_location
		if self.loan_type == "Stock":
			self.customer_loan_warehouse = frappe.db.get_value(
				"Company", self.company, "customer_loan_warehouse"
			)
			if not self.customer_loan_warehouse:
				frappe.throw(_("Set Borrowing Warehouse on Company {0}").format(self.company))
			if frappe.db.get_value("Warehouse", self.customer_loan_warehouse, "company") != self.company:
				frappe.throw(_("Borrowing Warehouse must belong to Company {0}").format(self.company))
		else:
			self.customer_loan_warehouse = None

	def set_requester(self, request=None):
		"""The Responsible Employee of the request answers for the items until they come back (Connect Diagnostics,
		Siwat, 5 Nov 2026, relayed by the user): the one named on the Material Request (the requester by default),
		else the Employee of the user who raised it."""
		request = request or frappe.get_cached_doc("Material Request", self.material_request)
		employee = (
			request.get("responsible_employee")
			or request.get("requesting_employee")
			or frappe.db.get_value("Employee", {"user_id": request.owner, "company": self.company}, "name")
		)
		self.requested_by = employee or None
		self.requested_by_name = (
			frappe.db.get_value("Employee", employee, "employee_name") if employee else None
		)
		self.requested_by_user = request.owner

	def backfill_requester(self):
		"""Borrowings submitted before the requester fields existed get them the first time their status is refreshed."""
		if self.docstatus != 1 or self.get("requested_by_user") or not self.material_request:
			return
		self.set_requester(frappe.get_doc("Material Request", self.material_request))
		self.db_set(
			{
				"requested_by": self.requested_by,
				"requested_by_name": self.requested_by_name,
				"requested_by_user": self.requested_by_user,
			},
			update_modified=False,
		)

	def validate_items(self, require_allocation=False, require_source_warehouse=False):
		if not self.items:
			frappe.throw(_("Add at least one item to the Borrowing"))
		if self.get("source_conversion"):
			return self.validate_converted_asset_items()

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
			if self.loan_type == "Stock" and flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Borrowed Qty must be greater than zero").format(row.idx))
			if self.loan_type == "Stock":
				# a Stock row carries no Asset or Location (UX WP4: the screen shows only the fields of its type)
				row.asset = None
				row.target_location = None
				if request_item.item_type != "Stock Item":
					frappe.throw(
						_("Row {0}: Stock Borrowing rows must match a Stock Item on the request").format(
							row.idx
						)
					)
				if require_source_warehouse and not row.source_warehouse:
					frappe.throw(
						_("Row {0}: Select a Source Warehouse for this Stock Borrowing Item").format(row.idx)
					)
				if row.source_warehouse:
					warehouse = frappe.db.get_value(
						"Warehouse", row.source_warehouse, ["company", "warehouse_type"], as_dict=True
					)
					if not warehouse or warehouse.company != self.company:
						frappe.throw(
							_("Row {0}: Source Warehouse must belong to Company {1}").format(
								row.idx, self.company
							)
						)
					if not warehouse_type_allows(warehouse.warehouse_type, "loan"):
						frappe.throw(
							_("Row {0}: Warehouse {1} of type {2} does not lend stock").format(
								row.idx, row.source_warehouse, warehouse.warehouse_type
							)
						)
				item = frappe.get_cached_value(
					"Item", row.item_code, ["has_batch_no", "has_serial_no"], as_dict=True
				)
				if require_allocation and item.has_batch_no and not row.batch_no:
					frappe.throw(_("Row {0}: Select the Batch to issue").format(row.idx))
				if item.has_serial_no:
					if flt(row.qty) % 1:
						frappe.throw(
							_("Row {0}: Serial controlled items must have a whole number quantity").format(
								row.idx
							)
						)
					serials = normalize_serials(row.serial_no)
					if require_allocation and len(serials) != cint(row.qty):
						frappe.throw(_("Row {0}: Select one Serial No for each whole item").format(row.idx))
					if len(serials) != len(set(serials)):
						frappe.throw(_("Row {0}: A Serial No can only be issued once").format(row.idx))
					if serials_seen.intersection(serials):
						frappe.throw(_("A Serial No can only be issued once per Borrowing"))
					serials_seen.update(serials)
					row.serial_no = "\n".join(serials)
			else:
				# an Asset row is one Asset and carries no stock fields
				row.qty = 1
				row.source_warehouse = row.batch_no = row.serial_no = None
				if not request_item.get("asset") or row.asset != request_item.asset:
					frappe.throw(
						_("Row {0}: Asset must match the Asset selected on the Material Request Item").format(
							row.idx
						)
					)
				if row.asset in assets_seen:
					frappe.throw(_("Asset {0} can only be listed once per Borrowing").format(row.asset))
				assets_seen.add(row.asset)
				asset = frappe.db.get_value(
					"Asset",
					row.asset,
					["item_code", "company", "status", "location", "custodian"],
					as_dict=True,
					for_update=True,
				)
				if not asset or asset.company != self.company or asset.item_code != row.item_code:
					frappe.throw(
						_("Row {0}: Asset must match the requested item and Company").format(row.idx)
					)
				if (
					asset.status
					in (
						"Draft",
						"Cancelled",
						"Scrapped",
						"Sold",
						"Capitalized",
						"In Maintenance",
						"Out of Order",
						"Work In Progress",
					)
					or asset.custodian
				):
					frappe.throw(_("Row {0}: Asset is not available for borrowing").format(row.idx))
				if asset_awaiting_inspection(row.asset):
					frappe.throw(
						_("Row {0}: Asset {1} is waiting for the inspection of its return").format(
							row.idx, row.asset
						)
					)
				if asset_has_unreturned_borrowing(row.asset, exclude_loan=self.name):
					frappe.throw(
						_("Row {0}: Asset is already assigned to an active Borrowing").format(row.idx)
					)
				if not asset.location:
					frappe.throw(_("Row {0}: Asset must have a current Location").format(row.idx))
				if asset.location != request_item.source_location:
					frappe.throw(
						_("Row {0}: Asset has moved since the Material Request was submitted").format(row.idx)
					)
				row.source_location = asset.location
				row.target_location = row.target_location or self.recipient_location
				if self.borrower_type != "Employee":
					if not row.target_location:
						frappe.throw(
							_("Row {0}: Select the recipient Location for this Asset").format(row.idx)
						)
				if row.target_location and row.target_location == row.source_location:
					frappe.throw(
						_("Row {0}: Recipient Location must differ from the current Location").format(row.idx)
					)
			request_row_name = request_item.name
			qty_by_request_item[request_row_name] = qty_by_request_item.get(request_row_name, 0) + flt(
				row.qty
			)

		for request_item_name, qty in qty_by_request_item.items():
			if qty > flt(request_rows[request_item_name].qty):
				frappe.throw(
					_("Borrowed Qty exceeds the requested quantity for Material Request row {0}").format(
						request_item_name
					)
				)

	def before_submit(self):
		lock_material_request(self.material_request)
		self.validate_material_request()
		for asset_name in sorted({row.asset for row in self.items if row.asset}):
			lock_asset(asset_name)
		self.validate_items(require_allocation=False, require_source_warehouse=True)
		if self.get("source_conversion"):
			self.status = "Active"
			return
		if frappe.db.exists(
			"Borrowing",
			{"material_request": self.material_request, "loan_type": self.loan_type, "docstatus": 1},
		):
			frappe.throw(
				_("A submitted {0} Borrowing already exists for this Material Request").format(self.loan_type)
			)
		self.status = "Pending Pick" if self.loan_type == "Stock" else "Pending Delivery"

	def on_submit(self):
		self.update_status()

	def validate_converted_asset_items(self):
		"""An Asset loan that records the Demo Assets converted from the borrower's own Borrow stock loan (Connect
		Diagnostics, Siwat 16 Nov 2026): the Assets are already with the borrower (the conversion made them with the
		borrower as custodian), so the loan names exactly those Assets, starts Active and needs no outgoing movement."""
		conversion = frappe.db.get_value(
			"Stock to Asset Conversion",
			self.source_conversion,
			["docstatus", "borrowing", "borrowing_item", "company"],
			as_dict=True,
		)
		if not conversion or conversion.docstatus != 1 or not conversion.borrowing:
			frappe.throw(_("Source Conversion must be a submitted conversion of a Borrowing."))
		stock_loan = frappe.db.get_value(
			"Borrowing",
			conversion.borrowing,
			["material_request", "borrower_type", "borrower", "company"],
			as_dict=True,
		)
		if (
			self.loan_type != "Asset"
			or stock_loan.company != self.company
			or stock_loan.material_request != self.material_request
			or stock_loan.borrower_type != self.borrower_type
			or stock_loan.borrower != self.borrower
		):
			frappe.throw(
				_("The Asset Borrowing must be for the same borrower and request as the Stock Borrowing.")
			)
		other = frappe.db.get_value(
			"Borrowing",
			{"source_conversion": self.source_conversion, "docstatus": 1, "name": ["!=", self.name or ""]},
			"name",
		)
		if other:
			frappe.throw(_("Borrowing {0} already records the Assets of this conversion.").format(other))
		request_item = frappe.db.get_value(
			"Borrowing Item", conversion.borrowing_item, "material_request_item"
		)
		assets = set(
			frappe.get_all(
				"Stock to Asset Conversion Asset",
				filters={"parent": self.source_conversion, "parenttype": "Stock to Asset Conversion"},
				pluck="asset",
			)
		)
		if {row.asset for row in self.items} != assets or len(self.items) != len(assets):
			frappe.throw(_("The Asset Borrowing must list every Asset of the conversion exactly once."))
		for row in self.items:
			asset = frappe.db.get_value(
				"Asset", row.asset, ["item_code", "docstatus", "custodian", "location"], as_dict=True
			)
			if not asset or asset.docstatus != 1:
				frappe.throw(_("Row {0}: Asset must be submitted").format(row.idx))
			if self.borrower_type == "Employee" and asset.custodian != self.borrower:
				frappe.throw(_("Row {0}: Asset is not with the borrower").format(row.idx))
			row.item_code = asset.item_code
			row.qty = 1
			row.material_request_item = request_item
			row.source_location = asset.location
			row.target_location = None

	@frappe.whitelist(methods=["POST"])
	def prepare_pick_list(self):
		"""The warehouse starts picking explicitly; preparing a borrowing does not send anything out."""
		lock_material_request(self.material_request)
		lock_borrowing(self.name)
		self.reload()
		self.check_permission("submit")
		frappe.has_permission("Pick List", "create", throw=True)
		if self.docstatus != 1 or self.loan_type != "Stock":
			frappe.throw(_("Prepare picking from a submitted Stock Borrowing."))
		return self.make_pick_list().as_dict()

	@frappe.whitelist(methods=["POST"])
	def confirm_delivery(self):
		frappe.throw(_("Open Create → Delivery Note or Asset Movement, then save and submit that document."))

	def waits_for_stock_delivery(self):
		"""An Asset loan of a set (the request also lends stock) moves its Assets when the stock loan's goods leave: the
		Delivery Note is submitted (Siwat, 5 Nov 2026, relayed by the user: a set is delivered together). An Asset-only
		request moves at once."""
		if self.loan_type != "Asset" or self.asset_movement or self.get("source_conversion"):
			return False
		stock = frappe.db.get_value(
			"Borrowing",
			{"material_request": self.material_request, "loan_type": "Stock", "docstatus": 1},
			["name", "delivery_note"],
			as_dict=True,
		)
		if not stock:
			return False
		return (
			not stock.delivery_note
			or frappe.db.get_value("Delivery Note", stock.delivery_note, "docstatus") != 1
		)

	def release_assets(self):
		frappe.throw(_("Create and submit the outgoing Asset Movement from the Borrowing."))

	@frappe.whitelist()
	def prepare_asset_movement(self):
		self.reload()
		self.check_permission("read")
		if self.get("source_conversion"):
			frappe.throw(
				_("The Assets of this borrowing are already with the borrower: no Asset Movement is needed.")
			)
		frappe.has_permission("Asset Movement", "create", throw=True)
		if self.docstatus != 1 or self.loan_type != "Asset":
			frappe.throw(_("Create an Asset Movement from a submitted Asset Borrowing."))
		if self.asset_movement and frappe.db.get_value(
			"Asset Movement", self.asset_movement, "docstatus"
		) in (0, 1):
			movement = frappe.get_doc("Asset Movement", self.asset_movement)
			movement.check_permission("read")
			return movement.as_dict()
		self.validate_items()
		return self.make_asset_issue().as_dict()

	def make_pick_list(self):
		if self.pick_list and frappe.db.get_value("Pick List", self.pick_list, "docstatus") in (0, 1):
			pick_list = frappe.get_doc("Pick List", self.pick_list)
			pick_list.check_permission("read")
			return pick_list
		internal_customer = get_internal_loan_customer(self.company)
		if not internal_customer:
			frappe.throw(
				_(
					"Create an Internal Customer representing Company {0} before issuing a Stock Borrowing"
				).format(self.company)
			)
		request = frappe.get_cached_doc("Material Request", self.material_request)
		request_items = {row.name: row for row in request.items}
		pick_list = frappe.new_doc("Pick List")
		pick_list.company = self.company
		pick_list.purpose = "Delivery"
		pick_list.pick_list_type = "Standard"
		pick_list.pick_manually = 1
		pick_list.customer = internal_customer
		pick_list.material_request = self.material_request
		pick_list.borrowing = self.name
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
					"borrowing_item": row.name,
					"batch_no": row.batch_no,
					"serial_no": row.serial_no,
					"use_serial_batch_fields": 1,
				},
			)
		return pick_list

	@frappe.whitelist()
	def make_delivery_note(self):
		"""Create the standard Borrowing Delivery Note from the submitted native Pick List."""
		self.reload()
		self.check_permission("read")
		frappe.has_permission("Delivery Note", "create", throw=True)
		return self._make_delivery_note()

	def _make_delivery_note(self):
		if self.docstatus != 1 or self.loan_type != "Stock":
			frappe.throw(_("Create a Borrowing Delivery Note from a submitted Stock Borrowing"))

		lock_borrowing(self.name)
		self.reload()
		if self.delivery_note:
			delivery_note_status = frappe.db.get_value("Delivery Note", self.delivery_note, "docstatus")
			if delivery_note_status in (0, 1):
				delivery_note = frappe.get_doc("Delivery Note", self.delivery_note)
				delivery_note.check_permission("read")
				return delivery_note.as_dict()

		if not self.pick_list or frappe.db.get_value("Pick List", self.pick_list, "docstatus") != 1:
			frappe.throw(_("Submit the Borrowing Pick List before creating a Borrowing Delivery Note"))
		pick_list = frappe.get_doc("Pick List", self.pick_list)
		validate_borrowing_pick_list(pick_list, require_picked_qty=True)

		existing = frappe.db.get_value(
			"Delivery Note",
			{
				"borrowing": self.name,
				"borrowing_source_doctype": self.doctype,
				"borrowing_source_name": self.name,
				"docstatus": ["<", 2],
			},
			"name",
		)
		if existing:
			delivery_note = frappe.get_doc("Delivery Note", existing)
			delivery_note.check_permission("read")
			return delivery_note.as_dict()

		from erpnext.stock.doctype.pick_list.pick_list import map_pl_locations

		delivery_note = frappe.new_doc("Delivery Note")
		delivery_note.company = self.company
		delivery_note.customer = pick_list.customer
		delivery_note.is_internal_customer = 1
		delivery_note.represents_company = self.company
		delivery_note.set_target_warehouse = self.customer_loan_warehouse
		delivery_note.borrowing = self.name
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
		# nothing is sold (UX WP3): no pricing rule, and the goods go to the Borrowing's recipient
		delivery_note.ignore_pricing_rule = 1
		set_borrowing_recipient(delivery_note, self)
		pick_list_rows = {row.name: row for row in pick_list.locations}
		for row in delivery_note.items:
			pick_list_row = pick_list_rows.get(row.pick_list_item)
			if not pick_list_row:
				frappe.throw(_("Every Borrowing Delivery Note row must come from the submitted Pick List"))
			row.borrowing_item = pick_list_row.borrowing_item
		loan_items = {row.name: row for row in self.items}
		for row in delivery_note.items:
			loan_item = loan_items.get(row.borrowing_item)
			if not loan_item:
				frappe.throw(_("Every Borrowing Delivery Note row must come from a Borrowing Item"))
			row.material_request = self.material_request
			row.material_request_item = loan_item.material_request_item
			row.target_warehouse = self.customer_loan_warehouse
		set_loan_transaction_source(delivery_note, self, self)
		return delivery_note.as_dict()

	def capture_delivery_note_details(self, delivery_note=None):
		delivery_note = delivery_note or frappe.get_doc("Delivery Note", self.delivery_note)
		# a loan row may be delivered from several locations and batches: its rows are read together
		rows_by_item = {}
		for row in delivery_note.items:
			if row.borrowing_item:
				rows_by_item.setdefault(row.borrowing_item, []).append(row)
		for loan_item in self.items:
			rows = rows_by_item.get(loan_item.name)
			if not rows:
				continue
			# every batch and serial is kept in the Serial / Batch Tracking table; the row keeps its batch only
			# when it was picked from one batch
			entries = [entry for row in rows for entry in transaction_row_entries(row)]
			batches = {batch for batch, _serial, _qty in entries if batch}
			loan_item.batch_no = next(iter(batches)) if len(batches) == 1 else None
			serials = [serial for _batch, serial, _qty in entries if serial]
			if not serials:
				serials = [
					serial
					for row in rows
					for serial in (
						normalize_serials(row.serial_no)
						or (
							get_serial_nos_from_bundle(row.serial_and_batch_bundle)
							if row.serial_and_batch_bundle
							else []
						)
					)
				]
			if serials:
				loan_item.serial_no = "\n".join(serials)
			loan_item.valuation_rate = rows[0].incoming_rate or rows[0].rate
			loan_item.db_update()

	def update_issued_quantities(self):
		is_received = transaction_is_received(self.delivery_note, self.asset_movement)
		issued_by_item = {}
		if is_received and self.loan_type == "Stock" and self.delivery_note:
			delivery_note = frappe.get_doc("Delivery Note", self.delivery_note)
			for row in delivery_note.items:
				if row.borrowing_item:
					issued_by_item[row.borrowing_item] = issued_by_item.get(row.borrowing_item, 0) + abs(
						flt(row.qty)
					)
		elif self.loan_type == "Asset" and self.get("source_conversion") and self.docstatus == 1:
			issued_by_item = {row.name: 1 for row in self.items}
		elif is_received and self.loan_type == "Asset" and self.asset_movement:
			movement = frappe.get_doc("Asset Movement", self.asset_movement)
			for row in movement.assets:
				if row.borrowing_item:
					issued_by_item[row.borrowing_item] = 1

		for row in self.items:
			issued_qty = issued_by_item.get(row.name, 0)
			row.issued_qty = issued_qty
			if row.name and self.docstatus == 1:
				frappe.db.set_value("Borrowing Item", row.name, "issued_qty", issued_qty)

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
			movement.append(
				"assets",
				{
					"asset": row.asset,
					"asset_item_code": row.item_code,
					"request_item": row.material_request_item,
					"source_location": row.source_location,
					"target_location": row.target_location,
					"to_employee": self.borrower if self.borrower_type == "Employee" else None,
					"borrowing_item": row.name,
				},
			)
		return movement

	def before_cancel(self):
		if return_documents(self.name, docstatus=(1,)):
			frappe.throw(_("Cancel the submitted return documents before cancelling the Borrowing"))
		if frappe.db.exists("Borrowing Adjustment", {"borrowing": self.name, "docstatus": 1}):
			frappe.throw(_("Cancel submitted Borrowing Adjustments before cancelling the Borrowing"))
		if self.sales_invoice and frappe.db.get_value("Sales Invoice", self.sales_invoice, "docstatus") == 1:
			frappe.throw(_("Cancel the linked Asset Sale Invoice before cancelling the Borrowing"))
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
				frappe.db.set_value(
					"Borrowing Item",
					row.name,
					{
						"returned_qty": returned,
						"adjusted_qty": adjusted,
						"sold_qty": sold,
						"outstanding_qty": row.outstanding_qty,
					},
				)
		sync_tracking_table(self)

	def update_status(self):
		if self.docstatus == 2:
			status = "Cancelled"
		elif self.docstatus != 1:
			status = "Draft"
		else:
			self.backfill_requester()
			status = self.get_submitted_status()
			# the pending statuses return before refresh_balances: keep the tracking table current anyway
			sync_tracking_table(self)
		if status != self.status:
			self.status = status
			self.db_set("status", status)
		self.update_material_request_status()
		return status

	def get_submitted_status(self):
		self.refresh_balances()
		if (
			self.loan_type == "Asset"
			and not self.get("source_conversion")
			and (
				not self.asset_movement
				or frappe.db.get_value("Asset Movement", self.asset_movement, "docstatus") != 1
			)
		):
			return "Pending Delivery"
		if self.loan_type == "Stock" and (
			not self.pick_list or frappe.db.get_value("Pick List", self.pick_list, "docstatus") != 1
		):
			return "Pending Pick"
		if self.loan_type == "Stock" and (
			not self.delivery_note
			or frappe.db.get_value("Delivery Note", self.delivery_note, "docstatus") != 1
		):
			return "Pending Delivery"
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

	def update_material_request_status(self):
		# an Asset loan of converted units records custody only: the request's fulfilment was the stock loan's
		if self.get("source_conversion"):
			return
		if not self.material_request or not frappe.db.exists("Material Request", self.material_request):
			return
		request = frappe.get_doc("Material Request", self.material_request)
		request.update_progress()
		request.db_set("request_progress_status", request.request_progress_status)

	def issue_documents(self):
		"""The submitted issue documents of the borrowing, newest first: its Delivery Notes (Stock) or Asset
		Movements (Asset) that sent the goods out."""
		if self.loan_type == "Stock":
			return frappe.get_all(
				"Delivery Note",
				filters={"borrowing": self.name, "is_return": 0, "docstatus": 1},
				pluck="name",
				order_by="posting_date desc, creation desc",
			)
		return frappe.get_all(
			"Asset Movement",
			filters={
				"borrowing": self.name,
				"borrowing_source_doctype": "Borrowing",
				"purpose": ["!=", "Receipt"],
				"docstatus": 1,
			},
			pluck="name",
			order_by="transaction_date desc, creation desc",
		)

	@frappe.whitelist()
	def make_return(self, source_name=None):
		"""Create > Return on the Borrowing (UX WP4, Siwat 18 Nov 2026: documents chain like selling): the unsaved return
		Delivery Note of its issue Delivery Note (Stock), or the return Asset Movement of its issue movement (Asset). With
		several issue documents the user names one (``source_name``)."""
		self.check_permission("read")
		if self.docstatus != 1 or self.status not in ("Active", "Partially Returned"):
			frappe.throw(_("Only an active submitted Borrowing can be returned"))
		sources = self.issue_documents()
		if not source_name:
			if len(sources) != 1:
				frappe.throw(_("Choose the issue document to return against."))
			source_name = sources[0]
		elif source_name not in sources:
			frappe.throw(_("{0} is not a submitted issue document of this Borrowing").format(source_name))
		if self.loan_type == "Stock":
			return make_borrowing_return_delivery_note(source_name)
		return make_borrowing_return_movement(source_name)

	@frappe.whitelist()
	def make_adjustment(self):
		self.check_permission("read")
		frappe.has_permission("Borrowing Adjustment", "create", throw=True)
		if (
			self.docstatus != 1
			or self.loan_type != "Stock"
			or self.status not in ("Active", "Partially Returned")
		):
			frappe.throw(_("Borrowing Adjustments are available for submitted Stock Borrowings only"))
		self.refresh_balances()
		account = frappe.db.get_value("Company", self.company, "stock_adjustment_account")
		if not account:
			frappe.throw(_("Set Company Stock Adjustment Account before creating a Borrowing Adjustment"))
		adjustment = frappe.new_doc("Borrowing Adjustment")
		adjustment.company = self.company
		adjustment.borrowing = self.name
		adjustment.adjustment_account = account
		set_loan_parties(adjustment, self)
		for row in self.items:
			if row.outstanding_qty <= 0:
				continue
			if is_tracked(row.item_code):
				for group in grouped_open_rows(self, row):
					adjustment.append(
						"items",
						{
							"borrowing_item": row.name,
							"item_code": row.item_code,
							"qty": flt(group["qty"], 6),
							"batch_no": group["batch_no"],
							"serial_no": "\n".join(group["serials"]) or None,
						},
					)
				continue
			open_serials = get_open_issued_serials(self.name, row)
			adjustment.append(
				"items",
				{
					"borrowing_item": row.name,
					"item_code": row.item_code,
					"qty": row.outstanding_qty,
					"batch_no": row.batch_no,
					"serial_no": "\n".join(open_serials) if open_serials else None,
				},
			)
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
			frappe.throw(_("Asset sale invoices are available for active Customer Asset Borrowings only"))

		lock_borrowing(self.name)
		existing = frappe.db.get_value(
			"Sales Invoice",
			{"borrowing": self.name, "docstatus": ["<", 2], "is_return": 0},
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
				frappe.throw(_("Borrowing Item {0} does not reference an Asset").format(loan_item.name))
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
					"borrowing_item": loan_item.name,
				},
			)
		invoice.borrowing = self.name
		set_loan_transaction_source(invoice, self, self)
		invoice.set_income_account_for_fixed_assets()
		invoice.flags.custody_operation_token = None
		return invoice.as_dict()

	@frappe.whitelist()
	def cancel_asset_sale_invoice(self):
		self.reload()
		self.check_permission("write")
		if not self.sales_invoice:
			frappe.throw(_("This Borrowing has no linked Asset Sale Invoice"))
		invoice = frappe.get_doc("Sales Invoice", self.sales_invoice)
		if invoice.borrowing != self.name:
			frappe.throw(_("The linked Asset Sale Invoice does not belong to this Borrowing"))
		if invoice.docstatus == 0:
			invoice.check_permission("delete")
			self.db_set("sales_invoice", None)
			frappe.delete_doc("Sales Invoice", invoice.name, ignore_permissions=True, force=True)
			return invoice.name
		if invoice.docstatus != 1:
			frappe.throw(_("The linked Asset Sale Invoice is already cancelled"))
		invoice.check_permission("cancel")
		invoice.flags.borrowing_cancellation = True
		invoice.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN
		invoice.cancel()
		self.update_status()
		return invoice.name


def validate_loan_stock_entry_unit(row):
	stock_uom = frappe.get_cached_value("Item", row.item_code, "stock_uom")
	if (
		row.get("uom") != stock_uom
		or row.get("stock_uom") != stock_uom
		or flt(row.get("conversion_factor")) != 1
		or flt(row.get("transfer_qty")) != flt(row.qty)
		or row.get("set_basic_rate_manually")
		or row.get("allow_zero_valuation_rate")
		or row.get("valuation_type")
		or row.get("secondary_item_type")
		or row.get("bom_secondary_item")
	):
		frappe.throw(
			_("Borrowing stock movements must use the Stock UOM, unit conversion and native valuation.")
		)


def validate_manual_adjustment_issue(doc, loan):
	source = frappe.get_doc("Borrowing Adjustment", doc.borrowing_source_name)
	source.check_permission("read")
	lock_borrowing(loan.name)
	if source.docstatus != 1 or doc.purpose != "Material Issue" or len(doc.items) != len(source.items):
		frappe.throw(_("Write-off Stock Entry must match a submitted Borrowing Adjustment."))
	if doc.get("additional_costs"):
		frappe.throw(_("Write-off Stock Entries cannot add costs."))
	factors = conversion_factors(loan)
	expected = Counter(
		(
			row.borrowing_item,
			row.item_code,
			row.batch_no or None,
			tuple(sorted(normalize_serials(row.serial_no))),
			flt(flt(row.qty) * factors.get(row.borrowing_item, 1), 9),
		)
		for row in source.items
	)
	actual = Counter()
	for row in doc.items:
		validate_loan_stock_entry_unit(row)
		if (
			row.s_warehouse != loan.customer_loan_warehouse
			or row.t_warehouse
			or row.expense_account != source.adjustment_account
		):
			frappe.throw(_("Write-off rows must use the Borrowing warehouse and adjustment account."))
		actual[
			(
				row.borrowing_item,
				row.item_code,
				row.get("batch_no") or None,
				tuple(sorted(normalize_serials(row.get("serial_no")))),
				flt(row.qty, 9),
			)
		] += 1
	if actual != expected:
		frappe.throw(_("Write-off rows must exactly match every source row, batch, serial and quantity."))
	if doc.docstatus == 1:
		source.validate_items(loan)


@frappe.whitelist()
def make_borrowing_return_delivery_note(source_name, target_doc=None, args=None):
	"""Create > Return on the issue Delivery Note of a borrowing (UX WP4, Siwat 18 Nov 2026: "SO -> DN ... documents
	should be made one from another", as a sales return is made from its Delivery Note): the unsaved return Delivery
	Note of what is still with the borrower, one row per issued row and batch. Rows of an Item inspected on receipt
	come back into the quarantine of the warehouse they were lent from. The user reviews, saves and submits it."""
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	issue = frappe.get_doc("Delivery Note", source_name)
	issue.check_permission("read")
	frappe.has_permission("Delivery Note", "create", throw=True)
	if (
		issue.docstatus != 1
		or issue.is_return
		or not issue.get("is_borrowing_transaction")
		or not issue.borrowing
	):
		frappe.throw(_("Make the return from a submitted issue Delivery Note of a Borrowing"))
	loan = frappe.get_doc("Borrowing", issue.borrowing)
	loan.check_permission("read")
	if (
		loan.docstatus != 1
		or loan.loan_type != "Stock"
		or loan.status not in ("Active", "Partially Returned")
	):
		frappe.throw(_("Only an active submitted Stock Borrowing can be returned"))

	delivery_note = make_return_doc("Delivery Note", source_name)
	templates = {row.dn_detail: row.as_dict() for row in delivery_note.items if row.get("dn_detail")}
	delivery_note.set("items", [])

	def append(original, qty, batch_no=None, serials=None):
		values = frappe._dict(templates.get(original.name) or original.as_dict())
		for field in ("name", "idx", "parent", "parentfield", "parenttype", "doctype", "docstatus"):
			values.pop(field, None)
		factor = flt(original.conversion_factor) or 1
		values.update(
			qty=-round(flt(qty), 9),
			stock_qty=-round(flt(qty), 9) * factor,
			dn_detail=original.name,
			warehouse=original.warehouse,
			target_warehouse=loan.customer_loan_warehouse,
			batch_no=batch_no,
			serial_no="\n".join(serials or []) or None,
			serial_and_batch_bundle=None,
			quality_inspection=None,
			against_pick_list=None,
			pick_list_item=None,
			use_serial_batch_fields=1,
			borrowing_item=original.borrowing_item,
		)
		row = delivery_note.append("items", values)
		if borrowing_return_row_requires_inspection(row):
			# received first, inspected after: into the quarantine of the warehouse it was lent from
			receive_row_into_quarantine(row, loan.company, original.warehouse)

	for loan_item in loan.items:
		originals = [row for row in issue.items if row.borrowing_item == loan_item.name]
		if not originals:
			continue
		if is_tracked(loan_item.item_code):
			for group in grouped_open_rows(loan, loan_item):
				append(
					_issue_row_for(originals, group["batch_no"], group["serials"]),
					group["qty"],
					group["batch_no"],
					group["serials"],
				)
			continue
		left = loan_item_available_to_receive(loan.name, loan_item)
		for original in originals:
			take = min(left, abs(flt(original.qty)))
			if take <= 0:
				continue
			append(original, take)
			left -= take
	if not delivery_note.items:
		frappe.throw(_("Nothing of this Borrowing is still with the borrower"))
	delivery_note.is_internal_customer = 1
	delivery_note.represents_company = loan.company
	delivery_note.set_target_warehouse = loan.customer_loan_warehouse
	delivery_note.ignore_pricing_rule = 1
	set_borrowing_recipient(delivery_note, loan)
	set_loan_transaction_source(delivery_note, loan, loan)
	delivery_note.run_method("calculate_taxes_and_totals")
	return delivery_note


def _issue_row_for(originals, batch_no, serials):
	"""The issue row a returned group reverses: the one that held its serial numbers, else its batch, else the first."""
	if serials:
		for original in originals:
			if set(serials) & set(normalize_serials(original.serial_no)):
				return original
			if original.serial_and_batch_bundle and set(serials) & set(
				get_serial_nos_from_bundle(original.serial_and_batch_bundle)
			):
				return original
	if batch_no:
		for original in originals:
			if original.batch_no == batch_no or any(
				batch == batch_no for batch, _serial, _qty in transaction_row_entries(original)
			):
				return original
	return originals[0]


@frappe.whitelist()
def make_borrowing_return_movement(source_name, target_doc=None, args=None):
	"""Create > Return Movement on the issue Asset Movement of a borrowing (UX WP4, Siwat 18 Nov 2026): the unsaved
	movement that takes the Assets still with the borrower back to the Location they were lent from."""
	issue = frappe.get_doc("Asset Movement", source_name)
	issue.check_permission("read")
	frappe.has_permission("Asset Movement", "create", throw=True)
	if (
		issue.docstatus != 1
		or not issue.get("borrowing")
		or issue.get("borrowing_source_doctype") != "Borrowing"
		or is_borrowing_return_movement(issue)
	):
		frappe.throw(_("Make the return from a submitted issue Asset Movement of a Borrowing"))
	loan = frappe.get_doc("Borrowing", issue.borrowing)
	loan.check_permission("read")
	if (
		loan.docstatus != 1
		or loan.loan_type != "Asset"
		or loan.status not in ("Active", "Partially Returned")
	):
		frappe.throw(_("Only an active submitted Asset Borrowing can be returned"))
	items = {row.name: row for row in loan.items}
	movement = frappe.new_doc("Asset Movement")
	movement.company = loan.company
	movement.purpose = "Receipt"
	movement.return_against = issue.name
	movement.reference_doctype = "Material Request"
	movement.reference_name = loan.material_request
	movement.expected_return_date = loan.expected_return_date
	movement.customer = loan.borrower if loan.borrower_type == "Customer" else None
	movement.customer_contact = loan.recipient_contact
	movement.customer_address = loan.recipient_address
	movement.customer_address_display = loan.recipient_address_display
	movement.customer_contact_display = loan.recipient_contact_display
	movement.customer_contact_email = loan.recipient_contact_email
	set_loan_transaction_source(movement, loan, loan)
	for row in issue.assets:
		loan_item = items.get(row.borrowing_item)
		if not loan_item or loan_item_available_to_receive(loan.name, loan_item) < 1:
			continue
		movement.append(
			"assets",
			{
				"asset": loan_item.asset,
				"asset_item_code": loan_item.item_code,
				"request_item": loan_item.material_request_item,
				"source_location": frappe.db.get_value("Asset", loan_item.asset, "location"),
				"target_location": loan_item.source_location,
				"from_employee": loan.borrower if loan.borrower_type == "Employee" else None,
				"borrowing_item": loan_item.name,
			},
		)
	if not movement.assets:
		frappe.throw(_("No Asset of this Borrowing is still with the borrower"))
	return movement


def return_batch_swaps(delivery_note, loan):
	"""[(borrowing item, item code, lent batch, returned batch, stock qty)]: the rows of a return Delivery Note that
	bring back a batch the borrowing did not lend (Siwat, 5 Nov 2026: allowed for the same Item), each taken from the
	lent batches of the same borrowing row still out, earliest batch first."""
	balances = get_balances(loan, exclude=("Delivery Note", delivery_note.name))
	factors = conversion_factors(loan)
	room = {
		key: flt(entry.open_qty)
		for key, entry in balances.items()
		if entry.issued_qty > 0 and not entry.serial_no and key[1]
	}
	items = {row.name: row.item_code for row in loan.items}
	swaps = []
	for row in _return_rows_view(delivery_note).items:
		if not row.batch_no or row.serial_no:
			continue
		entry = balances.get((row.borrowing_item, row.batch_no, None))
		if entry and entry.issued_qty > 0:
			continue
		left = flt(row.returned_qty) * factors.get(row.borrowing_item, 1)
		for key in sorted(room, key=lambda k: k[1]):
			if left <= 1e-9:
				break
			if key[0] != row.borrowing_item:
				continue
			take = min(room[key], left)
			if take <= 0:
				continue
			room[key] -= take
			left -= take
			swaps.append(
				(row.borrowing_item, items[row.borrowing_item], key[1], row.batch_no, round(take, 9))
			)
	return swaps


def post_return_batch_swaps(doc, method=None):
	"""Delivery Note before_submit: a return that brings back another batch of the same Item first swaps the lent
	batch for it inside the Borrowed warehouse (a value-preserving Repack the system posts and links to the return
	through ``delivery_note_no``), so the return takes out of the Borrowed warehouse the batch that comes back, valued
	at the issued row's rate. The user never makes it; cancelling the return cancels it."""
	if not is_borrowing_return_delivery_note(doc):
		return
	loan = frappe.get_doc("Borrowing", doc.borrowing)
	lock_borrowing(loan.name)
	for loan_item, item_code, lent_batch, returned_batch, qty in return_batch_swaps(doc, loan):
		entry = new_stock_entry(doc.company, "Repack")
		set_loan_transaction_source(entry, loan, loan)
		entry.delivery_note_no = doc.name
		entry.set_posting_time = 1
		entry.posting_date = doc.posting_date
		entry.posting_time = doc.posting_time
		entry.remarks = _("Batch {0} came back for batch {1} on the borrowing return {2}").format(
			returned_batch, lent_batch, doc.name
		)
		location = get_default_storage_location(loan.customer_loan_warehouse, item_code)
		stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
		common = {
			"uom": stock_uom,
			"stock_uom": stock_uom,
			"conversion_factor": 1,
			"transfer_qty": qty,
			"item_code": item_code,
			"qty": qty,
			"borrowing_item": loan_item,
			"use_serial_batch_fields": 1,
		}
		entry.append(
			"items",
			{
				**common,
				"s_warehouse": loan.customer_loan_warehouse,
				"storage_location": location,
				"batch_no": lent_batch,
			},
		)
		entry.append(
			"items",
			{
				**common,
				"t_warehouse": loan.customer_loan_warehouse,
				"to_storage_location": location,
				"batch_no": returned_batch,
				"is_finished_item": 1,
			},
		)
		entry.insert()
		entry.submit()


def cancel_return_batch_swaps(doc, method=None):
	"""Delivery Note on_cancel: the batch swaps a borrowing return made are cancelled with it (after its own stock
	ledger is reversed)."""
	if not is_borrowing_return_delivery_note(doc):
		return
	for name in frappe.get_all(
		"Stock Entry",
		filters={
			"delivery_note_no": doc.name,
			"borrowing": doc.borrowing,
			"purpose": "Repack",
			"docstatus": 1,
		},
		pluck="name",
	):
		entry = frappe.get_doc("Stock Entry", name)
		entry.flags.ignore_permissions = True
		entry.flags.borrowing_cancellation = True
		entry.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN
		entry.ignore_linked_doctypes = tuple(
			set(entry.get("ignore_linked_doctypes") or ())
			| LOAN_SOURCE_DOCTYPES
			| {"Material Request", "Delivery Note"}
		)
		entry.cancel()


def validate_batch_swap(doc, loan):
	"""The only Stock Entry a Borrowing is the source of: the batch swap a return Delivery Note posts (above)."""
	delivery_note = (
		frappe.db.get_value(
			"Delivery Note",
			doc.get("delivery_note_no"),
			["borrowing", "is_return", "is_borrowing_transaction", "docstatus"],
			as_dict=True,
		)
		if doc.get("delivery_note_no")
		else None
	)
	if (
		doc.purpose != "Repack"
		or len(doc.items) != 2
		or not delivery_note
		or not is_borrowing_return_delivery_note(delivery_note)
		or delivery_note.borrowing != loan.name
		or delivery_note.docstatus == 2
	):
		frappe.throw(_("A Stock Entry of a Borrowing is only the batch swap of its return Delivery Note."))
	if (
		doc.get("additional_costs")
		or doc.get("bom_no")
		or doc.get("work_order")
		or flt(doc.get("process_loss_qty"))
	):
		frappe.throw(_("A batch swap keeps the value of the returned Item."))
	for row in doc.items:
		validate_loan_stock_entry_unit(row)
	outgoing, incoming = doc.items
	if (
		outgoing.s_warehouse != loan.customer_loan_warehouse
		or outgoing.t_warehouse
		or incoming.t_warehouse != loan.customer_loan_warehouse
		or incoming.s_warehouse
		or outgoing.item_code != incoming.item_code
		or flt(outgoing.qty) != flt(incoming.qty)
		or outgoing.get("serial_no")
		or incoming.get("serial_no")
		or outgoing.is_finished_item
		or not incoming.is_finished_item
	):
		frappe.throw(
			_("A batch swap takes one batch out of the Borrowed warehouse and puts the same quantity back.")
		)


def borrowing_return_row_requires_inspection(row):
	"""A returned stock row is inspected when its Item is inspected on receipt ('before Purchase'): goods coming back
	are a receipt (Connect Diagnostics, 5 Nov 2026: only goods received are inspected)."""
	return bool(cint(frappe.get_cached_value("Item", row.item_code, "inspection_required_before_purchase")))


def require_quarantine_warehouse(return_doc):
	"""Goods whose Item is inspected on receipt are received into the Quarantine warehouse, so a return with an
	inspected Item needs one: say so plainly when the return is saved, never leave the goods unreceived."""
	if return_doc.docstatus != 0:
		return
	inspected = [
		row.item_code
		for row in return_doc.items
		if borrowing_return_row_requires_inspection(row)
		and get_location_type(row.get("storage_location"), row.get("warehouse")) != QUALITY_INSPECTION
		and not quarantine_warehouse_for(return_doc.company, getattr(row, "warehouse", None))
	]
	if inspected:
		frappe.throw(
			_(
				"{0} is inspected when it comes back and is received into the Quarantine warehouse, but {1} has none. "
				"Ask the Stock team to add a warehouse of type Quarantine, then return again."
			).format(", ".join(sorted(set(inspected))), return_doc.company),
			title=_("No Quarantine Warehouse"),
		)


def asset_awaiting_inspection(asset):
	"""Whether a returned Asset is held for the inspection of its return (Connect Diagnostics, Siwat, 9 Nov 2026): the
	Asset field ``awaiting_inspection`` is made by that app (a Custom Field), so a site without it holds no Asset."""
	if not asset or not frappe.get_meta("Asset").has_field("awaiting_inspection"):
		return False
	return bool(cint(frappe.db.get_value("Asset", asset, "awaiting_inspection")))


def is_quarantine_warehouse(warehouse, company):
	"""A warehouse of the company whose Warehouse Type is Quarantine (by the type alone, WP10): returned goods that fail
	inspection are received there."""
	from erpnext.stock.warehouse_policy import is_quarantine_type

	values = frappe.db.get_value("Warehouse", warehouse, ["warehouse_type", "company"], as_dict=True)
	return bool(values and values.company == company and is_quarantine_type(values.warehouse_type))


def receive_row_into(row, warehouse):
	"""Receive a stock row into ``warehouse``. A return copies the original delivery's Storage Location (a location of
	the warehouse the goods were picked from), which another warehouse refuses and the stock ledger needs: a location of
	another warehouse (or none) becomes the new warehouse's default (``get_default_storage_location``, which also
	honours the Item's Storage Condition: its STAGING location). The one place that moves a receiving row."""
	row.warehouse = warehouse
	location = row.get("storage_location")
	if location and frappe.db.get_value("Storage Location", location, "warehouse") == warehouse:
		return
	row.storage_location = get_default_storage_location(warehouse, row.get("item_code"))


def receive_row_into_quarantine(row, company, near=None):
	"""Receive the row into the Quarantine warehouse that holds the goods of warehouse ``near`` (default: the row's own),
	at its default Storage Location. Returns the quarantine warehouse, or None when the company has none (the row is
	not changed). A row already in a Quarantine warehouse is left as it is."""
	current = row.get("warehouse")
	warehouse = near or current
	if location := typed_location(warehouse, row.get("item_code"), QUALITY_INSPECTION):
		row.warehouse = warehouse
		row.storage_location = location
		return warehouse
	if typed_location(warehouse, None, QUALITY_INSPECTION):
		frappe.throw(_("No Quality Inspection location in {0} supports Item {1}.").format(
			warehouse, row.get("item_code")
		))
	if current and is_quarantine_warehouse(current, company):
		return current
	quarantine = quarantine_warehouse_for(company, near or current)
	if quarantine:
		receive_row_into(row, quarantine)
	return quarantine


def is_borrowing_return_delivery_note(doc):
	"""A return Delivery Note of a borrowing: made from its issue Delivery Note (Create > Return), it brings the goods
	back from the Borrowed warehouse (UX WP4, Siwat 18 Nov 2026)."""
	return bool(doc.get("is_return") and doc.get("is_borrowing_transaction") and doc.get("borrowing"))


def is_borrowing_return_movement(doc):
	"""A return Asset Movement of a borrowing: made from its issue movement (Create > Return Movement), it takes the
	Assets back to the Location they were lent from."""
	return bool(
		doc.get("is_borrowing_transaction")
		and doc.get("borrowing")
		and doc.get("borrowing_source_doctype") == "Borrowing"
		and doc.get("purpose") == "Receipt"
	)


def validate_borrowing_return_quality_inspection(quality_inspection, delivery_note_name):
	delivery_note = frappe.get_doc("Delivery Note", delivery_note_name)
	if not is_borrowing_return_delivery_note(delivery_note):
		frappe.throw(_("Quality Inspection must reference a borrowing return Delivery Note"))
	if delivery_note.docstatus != 1:
		frappe.throw(_("Inspect the goods of a borrowing return once its Delivery Note is submitted"))
	if not quality_inspection.child_row_reference:
		# a QI made by hand names the Item: take its return row when only one row of the note fits
		candidates = [
			item.name
			for item in delivery_note.items
			if item.item_code == quality_inspection.item_code
			and item.borrowing_item
			and (not quality_inspection.batch_no or item.batch_no == quality_inspection.batch_no)
		]
		if len(candidates) == 1:
			quality_inspection.child_row_reference = candidates[0]
	row = frappe.db.get_value(
		"Delivery Note Item",
		quality_inspection.child_row_reference,
		["parent", "item_code", "qty", "batch_no", "serial_no", "borrowing_item"],
		as_dict=True,
	)
	if (
		not row
		or row.parent != delivery_note.name
		or row.item_code != quality_inspection.item_code
		or not row.borrowing_item
	):
		frappe.throw(_("Quality Inspection must reference a returned item on this Delivery Note"))
	if quality_inspection.inspection_type != "Incoming":
		frappe.throw(_("The inspection of a borrowing return must be an incoming inspection"))
	if flt(quality_inspection.sample_size) <= 0 or flt(quality_inspection.sample_size) > abs(flt(row.qty)):
		frappe.throw(_("Quality Inspection sample size must be within the returned quantity"))
	if row.batch_no and quality_inspection.batch_no != row.batch_no:
		frappe.throw(_("Quality Inspection Batch No must match the returned Batch No"))
	serials = set(normalize_serials(row.serial_no))
	if quality_inspection.item_serial_no and quality_inspection.item_serial_no not in serials:
		frappe.throw(_("Quality Inspection Serial No must match a returned Serial No"))


def update_borrowing_return_status_from_quality_inspection(quality_inspection):
	"""A borrowing return is received before it is inspected, so an inspection does not change any status; the
	borrowing is only refreshed."""
	delivery_note_name = get_borrowing_return_delivery_note(quality_inspection)
	if not delivery_note_name:
		return
	loan_name = frappe.db.get_value("Delivery Note", delivery_note_name, "borrowing")
	if loan_name and frappe.db.get_value("Borrowing", loan_name, "docstatus") == 1:
		frappe.get_doc("Borrowing", loan_name).update_status()


def is_quarantine_row(row):
	"""Whether a Warehouse row (with ``warehouse_type``) is a Quarantine warehouse: by the type alone (WP10)."""
	from erpnext.stock.warehouse_policy import is_quarantine_type

	return is_quarantine_type(row.get("warehouse_type"))


def quarantine_warehouse_for(company, near=None):
	"""Find a dedicated quarantine warehouse for a company without a Quality Inspection location."""
	if not company:
		return None
	rows = [
		row
		for row in frappe.get_all(
			"Warehouse",
			filters={"company": company, "is_group": 0, "disabled": 0},
			fields=["name", "parent_warehouse", "warehouse_type"],
			order_by="name",
		)
		if is_quarantine_row(row)
	]
	parent = frappe.db.get_value("Warehouse", near, "parent_warehouse") if near else None
	nearby = [row for row in rows if parent and row.parent_warehouse == parent]
	return nearby[0].name if len(nearby) == 1 else rows[0].name if len(rows) == 1 else None


class BorrowingAdjustment(Document):
	def validate(self):
		loan = self.validate_loan()
		self.validate_items(loan)

	def validate_loan(self):
		loan = frappe.get_doc("Borrowing", self.borrowing)
		if (
			loan.docstatus != 1
			or loan.loan_type != "Stock"
			or loan.status not in ("Active", "Partially Returned")
		):
			frappe.throw(_("Borrowing Adjustments require a submitted Stock Borrowing"))
		if self.company and self.company != loan.company:
			frappe.throw(_("Borrowing Adjustment and Borrowing must use the same Company"))
		self.company = loan.company
		set_loan_parties(self, loan)
		previous = self.get_doc_before_save()
		if previous and any(
			previous.get(field) != self.get(field)
			for field in (
				"demo_consumption",
				"demo_customer",
				"demo_confirmed_by",
				"demo_request_token",
				"demo_request_hash",
			)
		):
			frappe.throw(_("Demo consumption identity cannot be changed after creation."))
		if self.get("demo_consumption"):
			from connect_diagnostics_setup.stock.loan_consumables import expense_account

			if (
				loan.borrower_type != "Employee"
				or frappe.db.get_value("Employee", loan.borrower, "user_id") != self.get("demo_confirmed_by")
				or not self.get("demo_customer")
			):
				frappe.throw(_("Demo consumption must identify the borrowing holder and the Customer."))
			if self.is_new() and frappe.session.user != "Administrator":
				frappe.throw(
					_("Create demo consumption from the employee's borrowing."), frappe.PermissionError
				)
			account = expense_account(loan.company)
		else:
			account = frappe.db.get_value("Company", loan.company, "stock_adjustment_account")
		if not account or self.adjustment_account != account:
			frappe.throw(_("Borrowing Adjustment must use the configured account {0}").format(account or ""))
		return loan

	def validate_items(self, loan):
		if not self.items:
			frappe.throw(_("Add at least one Stock Item to adjust"))
		resolve_loan_item_rows(self, loan)
		loan_items = {row.name: row for row in loan.items}
		qty_by_loan_item = {}
		for row in self.items:
			loan_item = loan_items.get(row.borrowing_item)
			if not loan_item:
				frappe.throw(_("Row {0}: Borrowing Item must belong to the linked Borrowing").format(row.idx))
			row.item_code = loan_item.item_code
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Adjusted Qty must be positive").format(row.idx))
			qty_by_loan_item[row.borrowing_item] = qty_by_loan_item.get(row.borrowing_item, 0) + flt(row.qty)
			# one reason for the whole adjustment; a row may give its own
			row.reason = (row.reason or "").strip() or (self.get("reason") or "").strip()
			if not row.reason:
				frappe.throw(_("Row {0}: give the reason the items will not come back").format(row.idx))
			if not is_tracked(loan_item.item_code):
				row.batch_no = row.serial_no = None
		validate_tracked_rows(self, loan, "qty")
		for loan_item_name, qty in qty_by_loan_item.items():
			loan_item = loan_items[loan_item_name]
			if qty > loan_item_available_to_adjust(self.borrowing, loan_item, exclude_adjustment=self.name):
				frappe.throw(
					_("Adjusted Qty exceeds the original outstanding Qty for Borrowing Item {0}").format(
						loan_item_name
					)
				)

	def before_submit(self):
		lock_borrowing(self.borrowing)
		self.validate_items(frappe.get_doc("Borrowing", self.borrowing))
		self.status = "Pending Issue"

	def on_submit(self):
		self.update_status()
		frappe.get_doc("Borrowing", self.borrowing).update_status()

	def update_status(self):
		status = "Cancelled" if self.docstatus == 2 else "Draft"
		if self.docstatus == 1:
			status = (
				"Submitted"
				if self.stock_entry and frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") == 1
				else "Pending Issue"
			)
		if self.status != status:
			self.db_set("status", status)
			self.status = status
		return status

	@frappe.whitelist()
	def prepare_stock_entry(self):
		self.reload()
		self.check_permission("read")
		frappe.has_permission("Stock Entry", "create", throw=True)
		if self.docstatus != 1:
			frappe.throw(_("Submit the Borrowing Adjustment before creating its Stock Entry."))
		if self.stock_entry and frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus") in (0, 1):
			stock_entry = frappe.get_doc("Stock Entry", self.stock_entry)
			stock_entry.check_permission("read")
			return stock_entry.as_dict()
		loan = frappe.get_doc("Borrowing", self.borrowing)
		stock_entry = new_stock_entry(self.company, "Material Issue")
		set_loan_transaction_source(stock_entry, loan, self)
		factors = conversion_factors(loan)
		for row in self.items:
			loan_item = frappe.get_doc("Borrowing Item", row.borrowing_item)
			stock_uom = frappe.get_cached_value("Item", loan_item.item_code, "stock_uom")
			qty = flt(row.qty) * factors.get(row.borrowing_item, 1)
			stock_entry.append(
				"items",
				{
					"item_code": loan_item.item_code,
					"qty": qty,
					"uom": stock_uom,
					"stock_uom": stock_uom,
					"conversion_factor": 1,
					"transfer_qty": qty,
					"s_warehouse": loan.customer_loan_warehouse,
					# the goods sit in the loan warehouse's own location; the batch ledger of a loan remembers the
					# location it was picked from, which is not a place of this warehouse
					"storage_location": get_default_storage_location(
						loan.customer_loan_warehouse, loan_item.item_code
					),
					"borrowing_item": loan_item.name,
					# the row names its lot: without this the Stock Entry picks the warehouse's batches by itself
					# and refuses the row ("Serial and Batch Bundle has already created") when they differ
					"use_serial_batch_fields": 1,
					"batch_no": row.batch_no,
					"serial_no": row.serial_no,
					"expense_account": self.adjustment_account,
				},
			)
		return stock_entry.as_dict()

	def before_cancel(self):
		frappe.db.sql("select name from `tabBorrowing Adjustment` where name=%s for update", self.name)
		if frappe.db.exists(
			"Sales Invoice",
			{
				"borrowing_source_doctype": "Borrowing Adjustment",
				"borrowing_source_name": self.name,
				"docstatus": ["<", 2],
			},
		):
			frappe.throw(
				_("Cancel or delete the consumption invoice before cancelling its Borrowing Adjustment.")
			)
		cancel_source_transaction(self, "stock_entry")

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		frappe.get_doc("Borrowing", self.borrowing).update_status()


def resolve_loan_item_rows(doc, loan):
	"""Link each Return/Adjustment row to its loan row from the Item the user chose, as ERPNext links a Delivery
	Note row to its Sales Order row: the row link stays hidden (Connect Diagnostics, Siwat, 5 Nov 2026, relayed by
	the user). With the Item on several loan rows, the first one with quantity still out is taken."""
	loan_items = {row.name: row for row in loan.items}
	for row in doc.items:
		current = loan_items.get(row.borrowing_item)
		if current and (not row.item_code or row.item_code == current.item_code):
			row.item_code = current.item_code
			continue
		if not row.item_code:
			frappe.throw(_("Row {0}: Select the Item").format(row.idx))
		candidates = [
			item
			for item in loan.items
			if item.item_code == row.item_code and (not row.get("asset") or item.asset == row.asset)
		]
		if not candidates:
			frappe.throw(
				_("Row {0}: Item {1} is not on Borrowing {2}").format(row.idx, row.item_code, loan.name)
			)
		open_rows = [item for item in candidates if flt(item.outstanding_qty) > 0]
		row.borrowing_item = (open_rows or candidates)[0].name


def set_loan_parties(doc, loan):
	"""A Return or Adjustment shows who has the items and who requested them, from its borrowing."""
	doc.borrower_type = loan.borrower_type
	doc.borrower = loan.borrower
	doc.requested_by_name = loan.get("requested_by_name")


def new_stock_entry(company, purpose):
	# the standard type first: other types of the same purpose (Stock Write-Off, a Material Issue) are not a loan move
	stock_entry_type = frappe.db.get_value(
		"Stock Entry Type", {"purpose": purpose, "is_standard": 1}, "name"
	) or frappe.db.get_value("Stock Entry Type", {"purpose": purpose}, "name")
	if not stock_entry_type:
		frappe.throw(_("A {0} Stock Entry Type is required").format(purpose))
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = company
	stock_entry.stock_entry_type = stock_entry_type
	stock_entry.purpose = purpose
	stock_entry.flags.ignore_permissions = True
	stock_entry.flags.borrowing_generated = True
	return stock_entry


def set_loan_transaction_source(transaction, loan, source):
	transaction.is_system_generated = 1
	transaction.is_borrowing_transaction = 1
	transaction.borrowing = loan.name
	transaction.borrowing_source_doctype = source.doctype
	transaction.borrowing_source_name = source.name
	transaction.flags.borrowing_generated = True
	transaction.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN


def link_manual_loan_transaction(source, field, transaction):
	loan_name = source.name if source.doctype == "Borrowing" else source.borrowing
	lock_borrowing(loan_name)
	linked = frappe.db.get_value(source.doctype, source.name, field)
	if linked and linked != transaction.name:
		if frappe.db.get_value(transaction.doctype, linked, "docstatus") in (0, 1):
			frappe.throw(
				_("An active {0} already exists for this document: {1}").format(transaction.doctype, linked)
			)
	if linked != transaction.name:
		source.db_set(field, transaction.name, update_modified=False)


def validate_generated_transaction(doc, method=None):
	manual_new = (
		doc.doctype in ("Delivery Note", "Asset Movement", "Stock Entry", "Sales Invoice")
		and doc.is_new()
		and doc.get("borrowing_source_doctype") in LOAN_SOURCE_DOCTYPES
		and bool(doc.get("borrowing"))
		and (doc.doctype != "Stock Entry" or doc.get("borrowing_source_doctype") == "Borrowing Adjustment")
	)
	trusted_system_generation = (
		manual_new or doc.flags.get("borrowing_generated") or doc.flags.get("system_generated")
	)
	stored_system_generated = None
	if doc.name and frappe.db.exists(doc.doctype, doc.name):
		stored_system_generated = cint(frappe.db.get_value(doc.doctype, doc.name, "is_system_generated"))
	if not trusted_system_generation and (
		cint(doc.get("is_system_generated")) != cint(stored_system_generated or 0)
		or (stored_system_generated is None and cint(doc.get("is_system_generated")))
	):
		frappe.throw(_("Is System Generated can only be set by a server-side process"))

	source_doctype = doc.get("borrowing_source_doctype")
	source_name = doc.get("borrowing_source_name")
	marked = cint(doc.get("is_borrowing_transaction"))
	if not source_doctype and not source_name and not marked and not doc.get("borrowing"):
		return
	if source_doctype not in LOAN_SOURCE_DOCTYPES or not source_name or not doc.get("borrowing"):
		frappe.throw(_("Borrowing transaction markers require a valid Borrowing source document"))

	if not manual_new and not doc.flags.get("borrowing_generated"):
		stored = frappe.db.get_value(
			doc.doctype,
			doc.name,
			[
				"is_borrowing_transaction",
				"borrowing",
				"borrowing_source_doctype",
				"borrowing_source_name",
			],
			as_dict=True,
		)
		if (
			not stored
			or not stored.is_borrowing_transaction
			or any(
				stored.get(field) != doc.get(field)
				for field in ("borrowing", "borrowing_source_doctype", "borrowing_source_name")
			)
		):
			frappe.throw(_("Borrowing transaction links can only be set by the Borrowing module"))

	loan = frappe.get_doc("Borrowing", doc.borrowing)
	if loan.docstatus != 1:
		frappe.throw(_("Generated transaction must reference a submitted Borrowing"))
	if loan.company != doc.company:
		frappe.throw(_("Generated transaction must use the Borrowing Company"))
	if source_doctype == "Borrowing":
		valid_source = source_name == loan.name
	else:
		valid_source = frappe.db.get_value(source_doctype, source_name, "borrowing") == loan.name
	if not valid_source:
		frappe.throw(_("Borrowing source document does not belong to the linked Borrowing"))
	if doc.doctype in ("Stock Entry", "Delivery Note") and loan.loan_type != "Stock":
		frappe.throw(_("Generated document type does not match the Borrowing Type"))
	consumption_invoice = doc.doctype == "Sales Invoice" and source_doctype == "Borrowing Adjustment"
	if (
		doc.doctype in ("Asset Movement", "Sales Invoice")
		and loan.loan_type != "Asset"
		and not consumption_invoice
	):
		frappe.throw(_("Generated document type does not match the Borrowing Type"))
	for row in (
		doc.get("items", [])
		if doc.doctype in ("Stock Entry", "Delivery Note", "Sales Invoice")
		else doc.get("assets", [])
	):
		loan_item_name = row.get("borrowing_item")
		loan_item = (
			frappe.db.get_value("Borrowing Item", loan_item_name, ["parent", "item_code"], as_dict=True)
			if loan_item_name
			else None
		)
		item_code = row.get("item_code") if doc.doctype != "Asset Movement" else row.get("asset_item_code")
		if not loan_item or loan_item.parent != loan.name or item_code != loan_item.item_code:
			frappe.throw(_("Every generated transaction row must link to an item on the Borrowing"))

	if doc.doctype == "Delivery Note":
		validate_borrowing_delivery_note(doc, loan, source_doctype, source_name)
	elif doc.doctype == "Asset Movement":
		validate_asset_movement_borrowing(doc)
	elif doc.doctype == "Stock Entry" and source_doctype == "Borrowing":
		validate_batch_swap(doc, loan)
	elif doc.doctype == "Stock Entry" and source_doctype == "Borrowing Adjustment":
		validate_manual_adjustment_issue(doc, loan)
	elif doc.doctype == "Sales Invoice":
		if consumption_invoice:
			validate_consumption_invoice(doc, loan, source_name)
		else:
			validate_borrowing_sales_invoice(doc, loan, source_doctype, source_name)

	if doc.doctype in ("Delivery Note", "Asset Movement", "Sales Invoice") or (
		doc.doctype == "Stock Entry" and source_doctype == "Borrowing Adjustment"
	):
		source = frappe.get_doc(source_doctype, source_name)
		source.check_permission("read")
		if source.docstatus != 1:
			frappe.throw(_("Submit the source Borrowing or Adjustment before saving its movement document."))
		field = {
			"Delivery Note": "delivery_note",
			"Asset Movement": "asset_movement",
			"Sales Invoice": "sales_invoice",
			"Stock Entry": "stock_entry",
		}[doc.doctype]
		# a return document names its issue document (return_against), never the borrowing's own link to it
		is_return = is_borrowing_return_delivery_note(doc) or is_borrowing_return_movement(doc)
		if (source_doctype != "Borrowing Adjustment" or doc.doctype != "Sales Invoice") and not is_return:
			link_manual_loan_transaction(source, field, doc)
	doc.is_system_generated = 1
	doc.is_borrowing_transaction = 1
	if doc.doctype != "Sales Invoice":
		doc.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN


def validate_generated_transaction_cancellation(doc, method=None):
	if not doc.get("is_borrowing_transaction"):
		return
	consumption_invoice = (
		doc.doctype == "Sales Invoice" and doc.get("borrowing_source_doctype") == "Borrowing Adjustment"
	)
	if consumption_invoice:
		doc.check_permission("cancel")
	if not doc.flags.get("borrowing_cancellation") and not consumption_invoice:
		doc.check_permission("cancel")
		if doc.doctype not in ("Delivery Note", "Asset Movement", "Stock Entry", "Sales Invoice"):
			frappe.throw(_("Cancel this generated transaction from its Borrowing source."))
		if (
			doc.doctype in ("Delivery Note", "Asset Movement")
			and doc.get("borrowing_source_doctype") == "Borrowing"
			and not is_borrowing_return_delivery_note(doc)
			and not is_borrowing_return_movement(doc)
		):
			if return_documents(doc.borrowing, docstatus=(1,)) or frappe.db.exists(
				"Borrowing Adjustment", {"borrowing": doc.borrowing, "docstatus": 1}
			):
				frappe.throw(
					_(
						"Cancel the submitted return documents and Borrowing Adjustments before cancelling the outgoing document."
					)
				)

	source_doctype = doc.get("borrowing_source_doctype")
	source_name = doc.get("borrowing_source_name")
	loan_name = doc.get("borrowing")
	if not check_borrowing_source(source_doctype, source_name, loan_name):
		frappe.throw(_("Borrowing transaction source does not match the linked Borrowing"))

	if doc.doctype == "Stock Entry" and source_doctype == "Borrowing Adjustment":
		frappe.db.sql("select name from `tabBorrowing Adjustment` where name=%s for update", source_name)
		if frappe.db.exists(
			"Sales Invoice",
			{
				"borrowing_source_doctype": source_doctype,
				"borrowing_source_name": source_name,
				"docstatus": ["<", 2],
			},
		):
			frappe.throw(_("Cancel or delete the consumption invoice before cancelling its Stock Entry."))
	source = frappe.get_doc(source_doctype, source_name)
	if (
		doc.doctype == "Stock Entry"
		and source_doctype == "Borrowing"
		and not doc.flags.get("borrowing_cancellation")
	):
		frappe.throw(_("A batch swap is cancelled with the return Delivery Note that made it."))
	if source.doctype != doc.get("borrowing_source_doctype"):
		frappe.throw(_("Borrowing transaction source does not match this document"))
	if source.doctype != "Borrowing" and source.borrowing != loan_name:
		frappe.throw(_("Borrowing transaction source does not match this document"))

	loan = frappe.get_doc("Borrowing", loan_name)
	for source_type in LOAN_SOURCE_DOCTYPES:
		if not frappe.db.table_exists(source_type):
			continue
		for field in frappe.get_meta(source_type).fields:
			if field.fieldtype != "Link" or field.options != doc.doctype:
				continue
			linked_names = frappe.get_all(source_type, filters={field.fieldname: doc.name}, pluck="name")
			if any(name != source_name for name in linked_names):
				frappe.throw(_("Generated transaction is linked to another active Borrowing document"))

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
				row.parent != loan.material_request or row.name not in expected_names for row in linked_rows
			):
				frappe.throw(_("Generated transaction is linked to an unrelated Material Request"))

	doc.ignore_linked_doctypes = tuple(LOAN_SOURCE_DOCTYPES | {"Material Request"})
	if (
		doc.doctype == "Delivery Note"
		and is_trusted_custody_operation(doc)
		and doc.flags.get("borrowing_shipment_cancellation") == doc.get("borrowing_shipment")
		and doc.get("borrowing_shipment")
	):
		doc.ignore_linked_doctypes += ("Shipment",)
	if (
		doc.doctype == "Delivery Note"
		and source_doctype == "Borrowing"
		and doc.flags.get("borrowing_cancellation")
	):
		# The owning Return (or Loan: cancel_own_inspections) cancels its row inspections after reversing the stock.
		# Inspections cannot be cancelled while that Delivery Note is still submitted.
		doc.ignore_linked_doctypes += ("Quality Inspection",)

	doc.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN


# A Borrowing / Demo Delivery Note sells nothing (UX WP3, Siwat 18 Nov 2026): it carries no taxes, sales team, sales
# partner, shipping rule or discount. ERPNext fills some of them by itself on every save (the company's default tax
# template, the Territory's Sales Person), so they are emptied after the native validate; a value a user typed into
# a saved Borrowing Delivery Note is refused.
BORROWING_EMPTY_FIELDS = (
	"taxes_and_charges",
	"tax_category",
	"shipping_rule",
	"sales_partner",
	"commission_rate",
	"additional_discount_percentage",
	"discount_amount",
	"base_discount_amount",
)
BORROWING_EMPTY_TABLES = ("taxes", "sales_team")
BORROWING_EMPTY_ROW_FIELDS = (
	"price_list_rate",
	"base_price_list_rate",
	"margin_rate_or_amount",
	"discount_percentage",
	"discount_amount",
)


def set_borrowing_recipient(delivery_note, loan):
	"""The Borrowing's recipient address and contact on its Delivery Note (the customer is our internal one)."""
	if loan.get("recipient_address"):
		delivery_note.shipping_address_name = loan.recipient_address
		delivery_note.shipping_address = loan.get("recipient_address_display")
	if loan.get("recipient_contact"):
		delivery_note.contact_person = loan.recipient_contact
		delivery_note.contact_display = loan.get("recipient_contact_display")
		delivery_note.contact_email = loan.get("recipient_contact_email")


def borrowing_delivery_parties(delivery_note):
	"""(party type, party) pairs whose addresses and contacts a Borrowing Delivery Note may name: the internal
	customer it is made out to, the Borrowing's recipient customer and its borrower."""
	parties = []
	if delivery_note.get("customer"):
		parties.append(("Customer", delivery_note.customer))
	if delivery_note.get("borrowing"):
		loan = frappe.db.get_value(
			"Borrowing",
			delivery_note.borrowing,
			["recipient_customer", "borrower_type", "borrower"],
			as_dict=True,
		)
		if loan:
			if loan.recipient_customer:
				parties.append(("Customer", loan.recipient_customer))
			if loan.borrower_type and loan.borrower:
				parties.append((loan.borrower_type, loan.borrower))
	return list(dict.fromkeys(parties))


def _linked_to_parties(parenttype, name, parties):
	for party_type, party in parties:
		if frappe.db.exists(
			"Dynamic Link",
			{"parenttype": parenttype, "parent": name, "link_doctype": party_type, "link_name": party},
		):
			return True
	return False


def validate_borrowing_address_and_contact(delivery_note):
	"""Replaces the native party address / contact check on a Borrowing Delivery Note: the address and contact
	of the recipient are not the internal customer's, they must belong to one of `borrowing_delivery_parties`."""
	parties = borrowing_delivery_parties(delivery_note)
	if not parties:
		return
	names = ", ".join(party for _party_type, party in parties)
	for fieldname, label in (
		("customer_address", _("Billing Address")),
		("shipping_address_name", _("Shipping Address")),
	):
		address = delivery_note.get(fieldname)
		if address and not _linked_to_parties("Address", address, parties):
			frappe.throw(_("{0} does not belong to {1}").format(label, names))
	contact = delivery_note.get("contact_person")
	if contact and not _linked_to_parties("Contact", contact, parties):
		frappe.throw(_("Contact Person does not belong to {0}").format(names))


def _table_key(rows):
	return [
		(row.get("charge_type"), row.get("account_head"), flt(row.get("rate")), flt(row.get("tax_amount")))
		if row.get("doctype") == "Sales Taxes and Charges"
		else (row.get("sales_person"), flt(row.get("allocated_percentage")))
		for row in rows or []
	]


def prepare_borrowing_delivery_note(doc):
	"""Before the native validate of a Borrowing Delivery Note: refuse a selling value typed into a saved one,
	switch off the pricing rules and the automatic taxes."""
	before = None if doc.is_new() else doc.get_doc_before_save()
	if before is not None:
		typed = [
			doc.meta.get_label(field)
			for field in BORROWING_EMPTY_FIELDS
			if doc.get(field) and doc.get(field) != before.get(field)
		]
		typed += [
			doc.meta.get_label(table)
			for table in BORROWING_EMPTY_TABLES
			if doc.get(table) and _table_key(doc.get(table)) != _table_key(before.get(table))
		]
		if typed:
			frappe.throw(
				_("A Borrowing Delivery Note sells nothing: remove {0}.").format(
					", ".join(_(t) for t in typed)
				),
				title=_("Not a Sale"),
			)
	doc.ignore_pricing_rule = 1
	doc.flags.dont_auto_add_taxes = True


def clear_borrowing_selling_fields(doc) -> bool:
	"""After the native validate: empty what ERPNext filled by itself. True when anything was emptied (the totals
	are computed again)."""
	changed = False
	for field in BORROWING_EMPTY_FIELDS:
		if doc.get(field):
			doc.set(field, None if doc.meta.get_field(field).fieldtype in ("Link", "Data") else 0)
			changed = True
	for table in BORROWING_EMPTY_TABLES:
		if doc.get(table):
			doc.set(table, [])
			changed = True
	for row in doc.get("items") or []:
		for field in BORROWING_EMPTY_ROW_FIELDS:
			if flt(row.get(field)):
				row.set(field, 0)
				changed = True
	return changed


def validate_borrowing_delivery_note(doc, loan, source_doctype, source_name):
	if not doc.is_internal_customer or doc.represents_company != loan.company:
		frappe.throw(_("Borrowing Delivery Notes must use the Internal Customer for the Borrowing Company"))
	if doc.customer != frappe.db.get_value(
		"Customer", {"is_internal_customer": 1, "represents_company": loan.company}, "name"
	):
		frappe.throw(_("Borrowing Delivery Note must use the Internal Customer representing its Company"))
	if not doc.is_return:
		if doc.docstatus == 1:
			lock_material_request(loan.material_request)
			for entry in delivery_loans(loan):
				lock_borrowing(entry.name)
				entry.reload()
				entry.check_permission("submit")
				if entry.loan_type == "Asset" and not entry.asset_movement:
					entry.validate_items()
				entry.run_method("before_delivery")
		if source_doctype != "Borrowing" or source_name != loan.name:
			frappe.throw(
				_("Borrowing issue Delivery Note must be a non-return Delivery Note linked to the Borrowing")
			)
		if not loan.pick_list or frappe.db.get_value("Pick List", loan.pick_list, "docstatus") != 1:
			frappe.throw(_("Submit the linked Borrowing Pick List before creating its Delivery Note"))
		pick_list_items = {row.name: row for row in frappe.get_doc("Pick List", loan.pick_list).locations}
		loan_items = {row.name: row for row in loan.items}
		seen_pick_rows = set()
		delivered_by_item = {}
		for row in doc.items:
			loan_item = loan_items.get(row.borrowing_item)
			pick_row = pick_list_items.get(row.pick_list_item)
			if (
				not loan_item
				or not pick_row
				or pick_row.name in seen_pick_rows
				or pick_row.borrowing_item != loan_item.name
				or row.against_pick_list != loan.pick_list
				or row.warehouse != pick_row.warehouse
				or row.target_warehouse != loan.customer_loan_warehouse
				or flt(pick_row.picked_qty, 9) != flt(pick_row.stock_qty, 9)
				or flt(row.stock_qty, 9) != flt(pick_row.picked_qty, 9)
			):
				frappe.throw(
					_("Borrowing issue Delivery Note row does not match the Borrowing item and warehouses")
				)
			if frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
				# the place, batch and serial nos picked are the ones delivered (UX WP2)
				refuse_mismatch(row, pick_row)
			seen_pick_rows.add(pick_row.name)
			delivered_by_item[loan_item.name] = delivered_by_item.get(loan_item.name, 0) + flt(row.qty)
		if set(delivered_by_item) != set(loan_items) or any(
			flt(qty, 9) != flt(loan_items[name].qty, 9) for name, qty in delivered_by_item.items()
		):
			frappe.throw(_("Borrowing Delivery Note must include every Borrowing Item exactly once"))
		return

	validate_borrowing_return_delivery_note(doc, loan, source_doctype, source_name)


def validate_borrowing_return_delivery_note(doc, loan, source_doctype, source_name):
	"""A return Delivery Note of a borrowing (UX WP4, Siwat 18 Nov 2026): made from the issue Delivery Note, it takes
	back only units still with the borrower, each row reversing a row of that issue note. Goods whose Item is inspected
	on receipt are received into the quarantine of the warehouse they were lent from. A batch the borrowing did not
	lend may come back for the same Item (a swap, valued at the issued row's rate); a serial number never may."""
	if source_doctype != "Borrowing" or source_name != loan.name or not doc.is_return:
		frappe.throw(
			_("A borrowing return Delivery Note must be made from the issue Delivery Note of the Borrowing")
		)
	issue = (
		frappe.db.get_value(
			"Delivery Note",
			doc.return_against,
			["docstatus", "is_return", "borrowing", "is_borrowing_transaction"],
			as_dict=True,
		)
		if doc.return_against
		else None
	)
	if (
		not issue
		or issue.docstatus != 1
		or issue.is_return
		or issue.borrowing != loan.name
		or not issue.is_borrowing_transaction
	):
		frappe.throw(
			_(
				"Borrowing return Delivery Note must return against a submitted issue Delivery Note of the Borrowing"
			)
		)
	loan_items = {row.name: row for row in loan.items}
	issue_rows = {
		row.name: row
		for row in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": doc.return_against, "parenttype": "Delivery Note"},
			fields=["name", "borrowing_item", "warehouse", "item_code"],
		)
	}
	qty_by_item = {}
	for row in doc.items:
		loan_item = loan_items.get(row.borrowing_item)
		issue_row = issue_rows.get(row.dn_detail)
		if not loan_item or not issue_row or issue_row.borrowing_item != loan_item.name:
			frappe.throw(
				_("Row {0}: a returned row must reverse a row of the issue Delivery Note {1}").format(
					row.idx, doc.return_against
				)
			)
		if row.item_code != loan_item.item_code:
			frappe.throw(_("Row {0}: the returned Item must be the Item lent").format(row.idx))
		if flt(row.qty) >= 0:
			frappe.throw(_("Row {0}: the returned quantity must be negative").format(row.idx))
		lent_from = {issue_row.warehouse, loan_item.source_warehouse}
		if borrowing_return_row_requires_inspection(row) and typed_location(
			issue_row.warehouse, None, QUALITY_INSPECTION
		):
			receive_row_into_quarantine(row, doc.company, issue_row.warehouse)
		if row.warehouse not in lent_from and not (
			is_quarantine_warehouse(row.warehouse, doc.company)
			and borrowing_return_row_requires_inspection(row)
		):
			frappe.throw(
				_("Row {0}: the goods come back into the warehouse they were lent from ({1})").format(
					row.idx, issue_row.warehouse
				)
			)
		if row.target_warehouse != loan.customer_loan_warehouse:
			frappe.throw(
				_("Row {0}: the goods come back from the Borrowed warehouse {1}").format(
					row.idx, loan.customer_loan_warehouse
				)
			)
		qty_by_item[loan_item.name] = qty_by_item.get(loan_item.name, 0) + abs(flt(row.qty))
	exclude = ("Delivery Note", doc.name) if doc.name and not doc.is_new() else None
	for loan_item_name, qty in qty_by_item.items():
		available = loan_item_available_to_receive(
			loan.name, loan_items[loan_item_name], exclude_return=exclude
		)
		if round(qty, 6) > round(flt(available), 6):
			frappe.throw(
				_("{0}: only {1} is still with the borrower; the return takes {2}").format(
					loan_items[loan_item_name].item_code, flt(available), flt(qty)
				),
				title=_("More than was lent"),
			)
	validate_tracked_rows(_return_rows_view(doc), loan, "returned_qty", allow_other_lots=True)
	require_quarantine_warehouse(doc)


def _return_rows_view(doc):
	"""The rows of a return Delivery Note as the tracking check reads them: one per row and batch, in the borrowing
	unit (the Serial and Batch Bundle when the row has one, else its Batch No / Serial No fields)."""
	rows = []
	for row in doc.items:
		if not row.get("borrowing_item"):
			continue
		factor = flt(row.conversion_factor) or 1
		by_batch = {}
		for batch, serial, qty in transaction_row_entries(row):
			group = by_batch.setdefault(batch, {"serials": [], "qty": 0})
			if serial:
				group["serials"].append(serial)
				group["qty"] += 1
			else:
				group["qty"] += qty / factor
		for batch, group in by_batch.items():
			rows.append(
				frappe._dict(
					idx=row.idx,
					borrowing_item=row.borrowing_item,
					batch_no=batch,
					serial_no="\n".join(group["serials"]) or None,
					returned_qty=round(group["qty"], 9),
				)
			)
	name = doc.name if doc.name and not doc.is_new() else None
	# a plain namespace: a frappe._dict's ``items`` is the dict method
	return SimpleNamespace(doctype="Delivery Note", name=name, items=rows)


def validate_consumption_invoice(doc, loan, adjustment_name):
	frappe.db.sql("select name from `tabBorrowing Adjustment` where name=%s for update", adjustment_name)
	source = frappe.get_doc("Borrowing Adjustment", adjustment_name)
	request_type = frappe.db.get_value("Material Request", loan.material_request, "material_request_type")
	if (
		source.docstatus != 1
		or source.borrowing != loan.name
		or loan.loan_type != "Stock"
		or loan.borrower_type != "Customer"
		or request_type != "Customer Borrow"
		or source.reason != "Consumed by the borrower"
		or doc.customer != loan.borrower
		or doc.company != loan.company
		or doc.is_return
		or doc.update_stock
	):
		frappe.throw(
			_("Consumption invoices must bill the borrower for a submitted Stock Borrowing Adjustment.")
		)
	duplicate = frappe.db.exists(
		"Sales Invoice",
		{
			"borrowing_source_doctype": "Borrowing Adjustment",
			"borrowing_source_name": source.name,
			"docstatus": ["<", 2],
			"name": ["!=", doc.name or ""],
		},
	)
	if duplicate:
		frappe.throw(_("A consumption invoice already exists for this Borrowing Adjustment."))
	if not source.stock_entry or frappe.db.get_value("Stock Entry", source.stock_entry, "docstatus") != 1:
		frappe.throw(_("Submit the write-off Stock Entry before billing its consumption."))
	factors = conversion_factors(loan)
	expected = {}
	for row in source.items:
		expected[row.borrowing_item] = expected.get(row.borrowing_item, 0) + flt(row.qty) * factors.get(
			row.borrowing_item, 1
		)
	actual = {}
	for row in doc.items:
		if (
			row.get("asset")
			or row.get("is_fixed_asset")
			or flt(row.qty) <= 0
			or flt(row.conversion_factor) != 1
			or row.uom != frappe.get_cached_value("Item", row.item_code, "stock_uom")
		):
			frappe.throw(_("Consumption invoice rows must use the consumed stock quantity and unit."))
		actual[row.borrowing_item] = actual.get(row.borrowing_item, 0) + flt(row.qty)
	if actual.keys() != expected.keys() or any(
		flt(actual[name], 9) != flt(qty, 9) for name, qty in expected.items()
	):
		frappe.throw(_("Consumption invoice quantities must match the Borrowing Adjustment."))


def validate_borrowing_sales_invoice(doc, loan, source_doctype, source_name):
	if doc.company != loan.company or loan.loan_type != "Asset":
		frappe.throw(_("Asset Sale Invoice must use the linked Asset Borrowing Company"))
	if doc.is_return:
		original = frappe.get_doc("Sales Invoice", doc.return_against) if doc.return_against else None
		if not original or original.docstatus != 1 or original.is_return or original.borrowing != loan.name:
			frappe.throw(
				_("Borrowing Asset Sale returns must reference a submitted sale invoice for this Borrowing")
			)
		if doc._action == "submit":
			lock_borrowing(loan.name)
			loan_items = {row.name: row for row in loan.items}
			original_items = {row.name: row for row in original.items}
			seen_assets = set()
			for row in doc.items:
				loan_item = loan_items.get(row.borrowing_item)
				original_row = original_items.get(row.sales_invoice_item)
				if (
					not loan_item
					or not original_row
					or original_row.borrowing_item != loan_item.name
					or not loan_item.asset
					or row.asset != loan_item.asset
					or row.item_code != loan_item.item_code
					or not row.is_fixed_asset
					or flt(row.qty) != -1
					or row.asset in seen_assets
				):
					frappe.throw(
						_("Every credit note row must return one Asset from its original Borrowing sale")
					)
				seen_assets.add(row.asset)
				if loan_item_available_to_receive(loan.name, loan_item) != 0:
					frappe.throw(_("Asset {0} is not currently sold on this Borrowing").format(row.asset))
		return
	else:
		if (
			source_doctype != "Borrowing"
			or source_name != loan.name
			or loan.borrower_type != "Customer"
			or doc.customer != loan.borrower
		):
			frappe.throw(_("Only the Customer on an Asset Borrowing can be invoiced for its Assets"))
	if doc.update_stock:
		frappe.throw(_("Sales Invoices for Assets on a Borrowing must not update Stock"))
	if doc._action != "submit":
		return

	lock_borrowing(loan.name)
	seen_assets = set()
	loan_items = {row.name: row for row in loan.items}
	for row in doc.items:
		loan_item = loan_items.get(row.borrowing_item)
		if (
			not loan_item
			or not loan_item.asset
			or row.asset != loan_item.asset
			or row.item_code != loan_item.item_code
			or not row.is_fixed_asset
			or flt(row.qty) != 1
			or row.asset in seen_assets
		):
			frappe.throw(_("Every sale row must invoice one available Asset from this Borrowing"))
		seen_assets.add(row.asset)
		if loan_item_available_to_receive(loan.name, loan_item) < 1:
			frappe.throw(_("Asset {0} has already been returned, adjusted or sold").format(row.asset))


def validate_asset_movement_borrowing(doc):
	loan = frappe.get_doc("Borrowing", doc.borrowing)
	if loan.loan_type != "Asset" or loan.docstatus != 1 or loan.company != doc.company:
		frappe.throw(_("Asset Movement must reference a submitted Asset Borrowing for the same Company"))
	if doc.reference_doctype != "Material Request" or doc.reference_name != loan.material_request:
		frappe.throw(_("Asset Movement must link to the Material Request that created the Borrowing"))
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
	doc.expected_return_date = loan.expected_return_date
	doc.customer = loan.borrower if loan.borrower_type == "Customer" else None
	doc.customer_contact = recipient_contact
	doc.customer_address = recipient_address
	doc.customer_address_display = recipient_address_display
	doc.customer_contact_display = recipient_contact_display
	doc.customer_contact_email = recipient_contact_email
	doc.recipient_type = loan.borrower_type
	doc.recipient = loan.borrower
	if doc.get("borrowing_source_doctype") != "Borrowing" or doc.get("borrowing_source_name") != loan.name:
		frappe.throw(_("Asset Movement must be generated by its Borrowing"))
	items = {row.name: row for row in loan.items}
	is_return = is_borrowing_return_movement(doc)
	expected_purpose = (
		"Receipt" if is_return else ("Issue" if loan.borrower_type == "Employee" else "Transfer")
	)
	if doc.purpose != expected_purpose:
		frappe.throw(_("Asset Movement Purpose must be {0} for this Borrowing").format(expected_purpose))
	if is_return:
		validate_borrowing_return_movement(doc, loan, items)
		return
	if doc.get("return_against"):
		frappe.throw(_("An issue Asset Movement does not return against another movement"))
	expected_items = set(items)
	if doc.docstatus == 1:
		lock_material_request(loan.material_request)
		for entry in delivery_loans(loan):
			lock_borrowing(entry.name)
			entry.reload()
			if entry.loan_type == "Stock" and (
				not entry.delivery_note
				or frappe.db.get_value("Delivery Note", entry.delivery_note, "docstatus") != 1
			):
				frappe.throw(_("Submit the Stock Borrowing Delivery Note before issuing the Assets."))
		loan.run_method("before_delivery")
	if len(doc.assets) != len(expected_items) or {row.borrowing_item for row in doc.assets} != expected_items:
		frappe.throw(_("Asset Movement must contain every source Asset exactly once."))
	for row in doc.assets:
		loan_item = items.get(row.borrowing_item)
		if (
			not loan_item
			or row.asset != loan_item.asset
			or row.request_item != loan_item.material_request_item
		):
			frappe.throw(_("Asset Movement row does not match an Asset on the linked Borrowing"))
		if loan.borrower_type == "Employee":
			if row.to_employee != loan.borrower:
				frappe.throw(_("Asset Movement must issue the Asset to the Borrowing Employee"))
		elif row.target_location != loan_item.target_location:
			frappe.throw(_("Asset Movement must send the Asset to the recipient Location on the Borrowing"))


def validate_borrowing_return_movement(doc, loan, items):
	"""A return Asset Movement (UX WP4, Siwat 18 Nov 2026): made from the issue movement, it takes back Assets still
	with the borrower, each to the Location it was lent from."""
	issue = (
		frappe.db.get_value(
			"Asset Movement",
			doc.return_against,
			["docstatus", "borrowing", "purpose", "borrowing_source_doctype"],
			as_dict=True,
		)
		if doc.get("return_against")
		else None
	)
	if (
		not issue
		or issue.docstatus != 1
		or issue.borrowing != loan.name
		or issue.purpose == "Receipt"
		or issue.borrowing_source_doctype != "Borrowing"
	):
		frappe.throw(
			_("A return Asset Movement must return against a submitted issue movement of the Borrowing")
		)
	issued = set(
		frappe.get_all(
			"Asset Movement Item",
			filters={"parent": doc.return_against, "parenttype": "Asset Movement"},
			pluck="borrowing_item",
		)
	)
	if not doc.assets:
		frappe.throw(_("Add the Assets that come back"))
	seen = set()
	exclude = ("Asset Movement", doc.name) if doc.name and not doc.is_new() else None
	for row in doc.assets:
		loan_item = items.get(row.borrowing_item)
		if (
			not loan_item
			or row.borrowing_item not in issued
			or row.asset != loan_item.asset
			or row.request_item != loan_item.material_request_item
			or row.borrowing_item in seen
		):
			frappe.throw(_("Row {0}: the Asset must be one the issue movement lent, once").format(row.idx))
		seen.add(row.borrowing_item)
		if row.target_location != loan_item.source_location:
			frappe.throw(_("Returned Asset must go back to its original Location"))
		if loan_item_available_to_receive(loan.name, loan_item, exclude_return=exclude) < 1:
			frappe.throw(
				_("Row {0}: Asset {1} has already come back or is on another return").format(
					row.idx, row.asset
				)
			)


def update_borrowing_status(doc, method=None):
	if doc.doctype == "Shipment" and doc.get("borrowing"):
		frappe.get_doc("Borrowing", doc.borrowing).update_status()
		return
	source_doctype = doc.get("borrowing_source_doctype")
	source_name = doc.get("borrowing_source_name")
	if not source_doctype or not source_name:
		return
	if source_doctype == "Borrowing":
		loan = frappe.get_doc("Borrowing", source_name)
		if doc.doctype == "Delivery Note" and method == "on_submit" and not doc.get("is_return"):
			loan.capture_delivery_note_details(doc)
		loan.update_status()
	elif source_doctype == "Borrowing Adjustment":
		adjustment = frappe.get_doc(source_doctype, source_name)
		adjustment.update_status()
		frappe.get_doc("Borrowing", adjustment.borrowing).update_status()


def delivery_loans(loan):
	"""Every requested type must be prepared before any part of a set can leave."""
	request = frappe.get_doc("Material Request", loan.material_request)
	request.check_permission("read")
	expected = set(request_loan_types(request))
	names = frappe.get_all(
		"Borrowing",
		filters={"material_request": loan.material_request, "docstatus": 1},
		pluck="name",
		order_by="name",
	)
	loans = [frappe.get_doc("Borrowing", name) for name in names]
	if {entry.loan_type for entry in loans} != expected or len(loans) != len(expected):
		frappe.throw(_("Prepare and submit every Stock and Asset Borrowing of this request before delivery."))
	for entry in loans:
		entry.check_permission("read")
	return loans


def release_held_assets(stock_loan):
	"""Asset movements are saved and submitted explicitly from the Asset borrowing."""
	return


def get_internal_loan_customer(company):
	return frappe.db.get_value(
		"Customer",
		{"is_internal_customer": 1, "represents_company": company, "disabled": 0},
		"name",
	)


def validate_borrowing_pick_list(pick_list, require_picked_qty=False, cancelling=False):
	loan = frappe.get_doc("Borrowing", pick_list.borrowing)
	if loan.loan_type != "Stock" or loan.docstatus != 1 or loan.company != pick_list.company:
		frappe.throw(_("Pick List must reference a submitted Stock Borrowing for the same Company"))
	loan.check_permission("read")
	link_manual_loan_transaction(loan, "pick_list", pick_list)
	if (
		pick_list.purpose != "Delivery"
		or pick_list.material_request != loan.material_request
		or pick_list.customer != get_internal_loan_customer(loan.company)
	):
		frappe.throw(_("Borrowing Pick List must use the linked Material Request and Internal Customer"))
	if cancelling or getattr(pick_list, "_action", None) == "cancel":
		if loan.delivery_note and frappe.db.get_value("Delivery Note", loan.delivery_note, "docstatus") in (
			0,
			1,
		):
			frappe.throw(
				_("Cancel the Borrowing Delivery Note from the Borrowing before cancelling its Pick List")
			)
		return
	if not pick_list.locations:
		frappe.throw(_("Borrowing Pick List must contain the Stock Items on the Borrowing"))

	loan_items = {row.name: row for row in loan.items}
	picked_by_item = {}
	for row in pick_list.locations:
		loan_item = loan_items.get(row.borrowing_item)
		if not loan_item:
			frappe.throw(_("Pick List row {0} must link to a Borrowing Item").format(row.idx))
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
			or row.uom != request_item.uom
			or row.stock_uom != request_item.stock_uom
			or flt(row.conversion_factor, 9) != flt(conversion_factor, 9)
			or flt(row.stock_qty, 9) != flt(row.qty * conversion_factor, 9)
		):
			frappe.throw(_("Pick List row {0} must match the linked Borrowing Item").format(row.idx))
		# a loan row may be picked from several locations and batches, and from any usable warehouse of the company
		# when its default has no stock (the loan row follows when the Pick List is submitted)
		warehouse = frappe.db.get_value(
			"Warehouse", row.warehouse, ["company", "is_group", "disabled"], as_dict=True
		)
		if not warehouse or warehouse.company != loan.company or warehouse.is_group or warehouse.disabled:
			frappe.throw(
				_("Pick List row {0}: choose an enabled warehouse of {1}").format(row.idx, loan.company)
			)
		if require_picked_qty and flt(row.picked_qty, 9) != flt(row.stock_qty, 9):
			frappe.throw(_("Pick List row {0}: Pick the full Borrowed Qty before submitting").format(row.idx))
		picked_by_item[loan_item.name] = picked_by_item.get(loan_item.name, 0) + flt(row.qty)

	if set(picked_by_item) != set(loan_items):
		frappe.throw(_("Pick List must include every Borrowing Item"))
	for name, qty in picked_by_item.items():
		if flt(qty, 9) != flt(loan_items[name].qty, 9):
			frappe.throw(
				_("Pick List rows of {0} add up to {1}, but the Borrowed Qty is {2}").format(
					loan_items[name].item_code, flt(qty), flt(loan_items[name].qty)
				)
			)


def follow_picked_warehouses(pick_list):
	"""A submitted loan Pick List: each loan row's Source Warehouse is where most of it was picked (a return goes back
	to the warehouse each part came from, ``make_borrowing_return_delivery_note``)."""
	loan_items = {row.name: row for row in frappe.get_doc("Borrowing", pick_list.borrowing).items}
	by_item = {}
	for row in pick_list.locations:
		if row.borrowing_item in loan_items:
			by_item.setdefault(row.borrowing_item, {})
			by_item[row.borrowing_item][row.warehouse] = by_item[row.borrowing_item].get(
				row.warehouse, 0
			) + flt(row.picked_qty)
	for name, warehouses in by_item.items():
		warehouse = max(warehouses, key=lambda key: warehouses[key])
		if loan_items[name].source_warehouse != warehouse:
			frappe.db.set_value("Borrowing Item", name, "source_warehouse", warehouse)


def update_borrowing_pick_list_status(pick_list):
	if not pick_list.borrowing or not frappe.db.exists("Borrowing", pick_list.borrowing):
		return
	loan = frappe.get_doc("Borrowing", pick_list.borrowing)
	if pick_list.docstatus == 1 and not loan.delivery_note:
		follow_picked_warehouses(pick_list)
		loan.reload()
	loan.update_status()


def make_borrowing_from_request(request, loan_type, ignore_permissions=False, save=True):
	"""ignore_permissions: the system issues an approved request itself (Connect Diagnostics, Siwat, 5 Nov 2026,
	relayed by the user: after approval and the back office's decision the loan is made automatically)."""
	if not ignore_permissions:
		request.check_permission("read")
		frappe.has_permission("Borrowing", "create", throw=True)
	request.reload()
	if request.docstatus != 1 or request.material_request_type not in (
		"Customer Demo",
		"Borrow",
		"Customer Borrow",
	):
		frappe.throw(_("Submit a Customer Demo or Borrow Material Request first"))
	if loan_type not in ("Stock", "Asset"):
		frappe.throw(_("Select Stock or Asset as the Borrowing Type"))
	allowed_types = request_loan_types(request)
	if loan_type not in allowed_types:
		frappe.throw(
			_("Create a {0} Borrowing to match the Material Request").format(" / ".join(allowed_types) or "-")
		)
	lock_material_request(request.name)
	existing = frappe.db.get_value(
		"Borrowing",
		{"material_request": request.name, "loan_type": loan_type, "docstatus": ["<", 2]},
		"name",
	)
	if existing:
		loan = frappe.get_doc("Borrowing", existing)
		if not ignore_permissions:
			loan.check_permission("read")
		return loan.as_dict()
	request_rows = request_rows_for_loan(request, loan_type)
	if not request_rows:
		frappe.throw(_("The Material Request has no {0} items").format(loan_type))
	loan = frappe.new_doc("Borrowing")
	loan.company = request.company
	loan.material_request = request.name
	loan.loan_type = loan_type
	loan.expected_return_date = request.expected_return_date
	for request_item in request_rows:
		if loan_type == "Stock":
			loan.append(
				"items",
				{
					"material_request_item": request_item.name,
					"item_code": request_item.item_code,
					"item_name": request_item.item_name,
					"qty": request_item.qty,
					"source_warehouse": request_item.from_warehouse
					or request.get_default_warehouse(request_item.item_code)
					or stocked_warehouse(request, request_item.item_code),
				},
			)
		else:
			loan.append(
				"items",
				{
					"material_request_item": request_item.name,
					"asset": request_item.asset,
					"item_code": request_item.item_code,
					"item_name": request_item.asset_name or request_item.item_name,
					"qty": 1,
					"source_location": request_item.source_location,
					"target_location": request_item.target_location
					or request.loan_recipient_location
					or request.customer_location,
				},
			)
	if save:
		loan.insert(ignore_permissions=ignore_permissions)
	else:
		loan.validate_material_request()
	return loan.as_dict()


def stocked_warehouse(request, item_code):
	"""The warehouse of the company that holds the most of the Item, when the request and the Item name none (the
	Stock team is not asked to type what the system can read). Only a warehouse whose Warehouse Type lets stock be
	sold or issued (``warehouse_policy.sellable_warehouse_sql``, WP10): never the loan, a quarantine, Lost, Found,
	Write-Off, Frozen, Transit or another Borrowed warehouse."""
	from erpnext.stock.warehouse_policy import sellable_warehouse_sql

	rows = frappe.db.sql(
		f"""select bin.warehouse from `tabBin` bin join `tabWarehouse` wh on wh.name = bin.warehouse
		where bin.item_code = %(item)s and bin.actual_qty > 0 and wh.company = %(company)s and wh.is_group = 0
		and wh.disabled = 0 and bin.warehouse != %(loan)s
		and {sellable_warehouse_sql("wh")}
		order by bin.actual_qty desc limit 1""",
		{
			"item": item_code,
			"company": request.company,
			"loan": request.get("customer_loan_warehouse") or "",
		},
	)
	return rows[0][0] if rows else None


def request_loan_types(request):
	"""The Loan types a request can make. A Demo or Borrow request lends each row as Stock or as an Asset, so it can
	make one of each."""
	return [loan_type for loan_type in ("Stock", "Asset") if request_rows_for_loan(request, loan_type)]


def request_rows_for_loan(request, loan_type):
	if loan_type == "Stock":
		return [row for row in request.items if row.item_type == "Stock Item"]
	return [row for row in request.items if row.item_type != "Stock Item"]


def get_borrowing_actions(request):
	can_create = frappe.has_permission("Borrowing", "create")
	loan_types = request_loan_types(request)

	def open_for(loan_type):
		# the button is for a loan that does not exist yet (the system makes it by itself once approved)
		return (
			can_create
			and loan_type in loan_types
			and not frappe.db.exists(
				"Borrowing",
				{"material_request": request.name, "loan_type": loan_type, "docstatus": ["<", 2]},
			)
		)

	return frappe._dict(stock_loan=open_for("Stock"), asset_loan=open_for("Asset"))


def lock_material_request(request_name):
	frappe.db.sql("select name from `tabMaterial Request` where name=%s for update", request_name)


def lock_borrowing(loan_name):
	frappe.db.sql("select name from `tabBorrowing` where name=%s for update", loan_name)


def normalize_serials(serial_numbers):
	if not serial_numbers:
		return []
	return [serial for serial in get_serial_nos(serial_numbers) if serial]


def loan_item_available_to_receive(loan_name, loan_item, *, exclude_return=None):
	returned, adjusted, sold = get_loan_item_balances(
		loan_name, loan_item.name, exclude_return=exclude_return
	)
	reserved = get_loan_item_reserved_qty(loan_name, loan_item.name, exclude_return=exclude_return)
	return max(flt(loan_item.qty) - returned - adjusted - sold - reserved, 0)


def loan_issue_factor(loan_item_name):
	"""The UOM conversion factor of the submitted issue Delivery Note row of a loan row (1 when none)."""
	return (
		flt(
			frappe.db.get_value(
				"Delivery Note Item",
				{"borrowing_item": loan_item_name, "docstatus": 1},
				"conversion_factor",
			)
		)
		or 1
	)


def loan_item_available_to_adjust(loan_name, loan_item, *, exclude_adjustment=None, exclude_conversion=None):
	returned, adjusted, sold = get_loan_item_balances(loan_name, loan_item.name)
	reserved = get_loan_item_reserved_qty(
		loan_name,
		loan_item.name,
		exclude_adjustment=exclude_adjustment,
		exclude_conversion=exclude_conversion,
	)
	return max(flt(loan_item.qty) - returned - adjusted - sold - reserved, 0)


CONVERSION_REJECTED_STATE = "Stock to Asset Conversion Rejected"


def loan_conversion_rows(loan_name, loan_item_name=None, *, submitted=None, exclude=None):
	"""The Stock to Asset Conversions of a loan's units (Connect Diagnostics, Siwat 16 Nov 2026: a Borrow unit becomes a
	Demo Asset). A Draft that is not Rejected reserves the unit, a submitted one settles it like an adjustment, a
	cancelled or deleted one counts for nothing. ``submitted``: None both, True submitted only, False drafts only.
	Returns frappe._dict(name, docstatus, borrowing_item, qty, serial_and_batch_bundle, batch_no, serial_no), the
	quantity in loan units."""
	if not frappe.db.has_column("Stock to Asset Conversion", "borrowing"):
		return []
	docstatus = [1] if submitted else [0] if submitted is False else [0, 1]
	filters = {"borrowing": loan_name, "docstatus": ["in", docstatus]}
	if loan_item_name:
		filters["borrowing_item"] = loan_item_name
	if exclude:
		filters["name"] = ["!=", exclude]
	rows = []
	for parent in frappe.get_all(
		"Stock to Asset Conversion",
		filters=filters,
		fields=["name", "docstatus", "borrowing_item", "workflow_state"],
	):
		if parent.docstatus == 0 and parent.workflow_state == CONVERSION_REJECTED_STATE:
			continue
		factor = (
			flt(
				frappe.db.get_value(
					"Delivery Note Item",
					{"borrowing_item": parent.borrowing_item, "docstatus": 1},
					"conversion_factor",
				)
			)
			or 1
		)
		for item in frappe.get_all(
			"Stock to Asset Conversion Item",
			filters={"parent": parent.name, "parenttype": "Stock to Asset Conversion"},
			fields=["qty", "serial_and_batch_bundle"],
		):
			serials, batch = [], None
			if item.serial_and_batch_bundle:
				for entry in frappe.get_all(
					"Serial and Batch Entry",
					filters={"parent": item.serial_and_batch_bundle},
					fields=["serial_no", "batch_no"],
				):
					if entry.serial_no:
						serials.append(entry.serial_no)
					batch = batch or entry.batch_no
			rows.append(
				frappe._dict(
					name=parent.name,
					docstatus=parent.docstatus,
					borrowing_item=parent.borrowing_item,
					qty=flt(item.qty) / factor,
					serial_and_batch_bundle=item.serial_and_batch_bundle,
					batch_no=batch,
					serial_no="\n".join(serials),
				)
			)
	return rows


def loan_conversion_serials(loan_name, loan_item_name, *, submitted=None, exclude=None):
	return [
		serial
		for row in loan_conversion_rows(loan_name, loan_item_name, submitted=submitted, exclude=exclude)
		for serial in normalize_serials(row.serial_no)
	]


def return_documents(loan_name, *, docstatus=(0, 1), exclude=None):
	"""The return documents of a borrowing (UX WP4, Siwat 18 Nov 2026): its return Delivery Notes (Stock) and return
	Asset Movements (Asset). A submitted one has brought the goods back, a draft is on its way back and reserves them.
	``exclude`` is a (doctype, name) left out, so a return being validated is not counted against itself."""
	documents = []
	for doctype, filters in (
		("Delivery Note", {"borrowing": loan_name, "is_return": 1, "is_borrowing_transaction": 1}),
		(
			"Asset Movement",
			{
				"borrowing": loan_name,
				"is_borrowing_transaction": 1,
				"borrowing_source_doctype": "Borrowing",
				"purpose": "Receipt",
			},
		),
	):
		filters["docstatus"] = ["in", list(docstatus)]
		for row in frappe.get_all(
			doctype, filters=filters, fields=["name", "docstatus"], order_by="creation"
		):
			if exclude and tuple(exclude) == (doctype, row.name):
				continue
			documents.append(frappe._dict(doctype=doctype, name=row.name, docstatus=row.docstatus))
	return documents


def return_rows(loan_name, loan_item_name=None, *, docstatus=(0, 1), exclude=None):
	"""The rows of the borrowing's return documents, one per batch / serial number:
	frappe._dict(doctype, parent, docstatus, borrowing_item, batch_no, serial_no, qty), qty in the borrowing unit."""
	rows = []
	documents = return_documents(loan_name, docstatus=docstatus, exclude=exclude)
	for doctype, child, fields in (
		(
			"Delivery Note",
			"Delivery Note Item",
			[
				"parent",
				"borrowing_item",
				"batch_no",
				"serial_no",
				"serial_and_batch_bundle",
				"qty",
				"stock_qty",
				"conversion_factor",
			],
		),
		("Asset Movement", "Asset Movement Item", ["parent", "borrowing_item", "asset"]),
	):
		state = {doc.name: doc.docstatus for doc in documents if doc.doctype == doctype}
		if not state:
			continue
		filters = {"parent": ["in", list(state)], "parenttype": doctype, "borrowing_item": ["is", "set"]}
		if loan_item_name:
			filters["borrowing_item"] = loan_item_name
		for item in frappe.get_all(child, filters=filters, fields=fields, order_by="idx"):
			common = dict(doctype=doctype, parent=item.parent, docstatus=state[item.parent])
			if doctype == "Asset Movement":
				rows.append(
					frappe._dict(
						common, borrowing_item=item.borrowing_item, batch_no=None, serial_no=None, qty=1
					)
				)
				continue
			factor = flt(item.conversion_factor) or 1
			for batch, serial, qty in transaction_row_entries(item):
				rows.append(
					frappe._dict(
						common,
						borrowing_item=item.borrowing_item,
						batch_no=batch,
						serial_no=serial,
						qty=1 if serial else qty / factor,
					)
				)
	return rows


def get_loan_item_reserved_qty(
	loan_name, loan_item_name, *, exclude_return=None, exclude_adjustment=None, exclude_conversion=None
):
	"""What a borrowing row has on its way: draft return documents, Borrowing Adjustments not yet issued and draft
	conversions to a Demo Asset. ``exclude_return`` is a (doctype, name) of a return document."""
	reserved = sum(
		row.qty for row in return_rows(loan_name, loan_item_name, docstatus=(0,), exclude=exclude_return)
	)
	filters = {"borrowing": loan_name, "docstatus": 1, "status": "Pending Issue"}
	if exclude_adjustment:
		filters["name"] = ["!=", exclude_adjustment]
	adjustments = frappe.get_all("Borrowing Adjustment", filters=filters, pluck="name")
	if adjustments:
		reserved += sum(
			flt(qty)
			for qty in frappe.get_all(
				"Borrowing Adjustment Item",
				filters={"parent": ["in", adjustments], "borrowing_item": loan_item_name},
				pluck="qty",
			)
		)
	reserved += sum(
		row.qty
		for row in loan_conversion_rows(
			loan_name, loan_item_name, submitted=False, exclude=exclude_conversion
		)
	)
	return reserved


def asset_allocation_returned_condition(item="allocation", loan="custody"):
	"""SQL form of the received-per-object rule for the native MR availability pickers: the Asset row was issued and
	a submitted return Asset Movement of the borrowing took it back."""
	return f"""({item}.issued_qty > 0 and {item}.issued_qty >= {item}.qty and (
		select count(*) from `tabAsset Movement Item` returned
		join `tabAsset Movement` movement on movement.name = returned.parent
		where movement.borrowing = {loan}.name and movement.docstatus = 1 and movement.purpose = 'Receipt'
		and movement.borrowing_source_doctype = 'Borrowing' and returned.borrowing_item = {item}.name
	) >= {item}.issued_qty)"""


def get_loan_item_returned_qty(loan_name, loan_item_name, *, for_update=False):
	"""What came back on the submitted return documents of a borrowing row (in the borrowing unit)."""
	if for_update:
		# read under lock, in SQL: the per-Asset reissue guard asks this while it holds the Asset
		stock = frappe.db.sql(
			"""select coalesce(sum(abs(item.qty)), 0) from `tabDelivery Note Item` item
			join `tabDelivery Note` note on note.name = item.parent
			where note.borrowing = %s and note.is_return = 1 and note.is_borrowing_transaction = 1
			and note.docstatus = 1 and item.borrowing_item = %s for update""",
			(loan_name, loan_item_name),
		)
		assets = frappe.db.sql(
			"""select count(*) from `tabAsset Movement Item` returned
			join `tabAsset Movement` movement on movement.name = returned.parent
			where movement.borrowing = %s and movement.purpose = 'Receipt'
			and movement.borrowing_source_doctype = 'Borrowing' and movement.docstatus = 1
			and returned.borrowing_item = %s for update""",
			(loan_name, loan_item_name),
		)
		return max(flt(stock[0][0] if stock else 0) + flt(assets[0][0] if assets else 0), 0)
	return max(sum(row.qty for row in return_rows(loan_name, loan_item_name, docstatus=(1,))), 0)


def get_loan_item_balances(loan_name, loan_item_name, *, exclude_return=None):
	if exclude_return:
		# Ledger validation runs again after the current return is marked submitted.
		returned = sum(
			row.qty for row in return_rows(loan_name, loan_item_name, docstatus=(1,), exclude=exclude_return)
		)
	else:
		returned = get_loan_item_returned_qty(loan_name, loan_item_name)
	adjusted = sold = 0
	adjustment_names = frappe.get_all(
		"Borrowing Adjustment",
		filters={"borrowing": loan_name, "docstatus": 1, "status": "Submitted"},
		pluck="name",
	)
	if adjustment_names:
		adjustment_rows = frappe.get_all(
			"Borrowing Adjustment Item",
			filters={"parent": ["in", adjustment_names], "borrowing_item": loan_item_name},
			pluck="qty",
		)
		adjusted = sum(flt(qty) for qty in adjustment_rows)
	# a unit converted to a Demo Asset has left the loan: settled like a write-off
	adjusted += sum(row.qty for row in loan_conversion_rows(loan_name, loan_item_name, submitted=True))
	invoice_rows = frappe.db.sql(
		"""select sum(sii.qty) from `tabSales Invoice Item` sii
		join `tabSales Invoice` si on si.name=sii.parent
		where sii.borrowing_item=%s and si.borrowing=%s
		and si.docstatus=1
		and coalesce(si.borrowing_source_doctype, '') != 'Borrowing Adjustment'""",
		(loan_item_name, loan_name),
	)
	sold = flt(invoice_rows[0][0] if invoice_rows else 0)
	return max(returned, 0), adjusted, max(sold, 0)


def get_asset_custody_allocations(asset_name, *, exclude_loan=None):
	"""Current per-object allocations under the same Asset lock used by issue and receipt."""
	lock_asset(asset_name)
	return frappe.db.sql(
		"""select loan.name, loan.material_request, item.name as loan_item, item.material_request_item,
		item.qty, item.issued_qty from `tabBorrowing Item` item
		join `tabBorrowing` loan on loan.name = item.parent
		where item.asset = %s and loan.docstatus = 1 and loan.loan_type = 'Asset' and loan.name != %s
		order by loan.creation desc for update""",
		(asset_name, exclude_loan or ""),
		as_dict=True,
	)


def asset_allocation_is_returned(allocation):
	issued = flt(allocation.issued_qty)
	return (
		issued > 0
		and issued >= flt(allocation.qty)
		and get_loan_item_returned_qty(allocation.name, allocation.loan_item, for_update=True) >= issued
	)


def asset_has_unreturned_borrowing(asset_name, *, exclude_loan=None):
	return any(
		not asset_allocation_is_returned(row)
		for row in get_asset_custody_allocations(asset_name, exclude_loan=exclude_loan)
	)


def get_adjusted_serials(loan_name, loan_item_name):
	adjustment_names = frappe.get_all(
		"Borrowing Adjustment",
		filters={"borrowing": loan_name, "docstatus": 1, "status": "Submitted"},
		pluck="name",
	)
	converted = loan_conversion_serials(loan_name, loan_item_name, submitted=True)
	if not adjustment_names:
		return converted
	rows = frappe.get_all(
		"Borrowing Adjustment Item",
		filters={"parent": ["in", adjustment_names], "borrowing_item": loan_item_name},
		pluck="serial_no",
	)
	return [*(serial for row in rows for serial in normalize_serials(row)), *converted]


def get_received_serials(loan_name, loan_item_name):
	return [row.serial_no for row in return_rows(loan_name, loan_item_name, docstatus=(1,)) if row.serial_no]


def get_reserved_serials(loan_name, loan_item_name, *, exclude_conversion=None):
	reserved = loan_conversion_serials(loan_name, loan_item_name, submitted=False, exclude=exclude_conversion)
	pending = [
		row.serial_no for row in return_rows(loan_name, loan_item_name, docstatus=(0,)) if row.serial_no
	]
	return [*pending, *reserved]


def get_open_issued_serials(loan_name, loan_item):
	issued = normalize_serials(loan_item.serial_no)
	returned = {row.serial_no for row in return_rows(loan_name, loan_item.name) if row.serial_no}
	returned.update(get_adjusted_serials(loan_name, loan_item.name))
	return [serial for serial in issued if serial not in returned]


def lock_asset(asset_name):
	frappe.db.sql("select name from `tabAsset` where name=%s for update", asset_name)


def transaction_is_received(stock_entry_name, asset_movement_name):
	"""A borrowing's issue is complete when its Delivery Note (or Asset Movement) is submitted."""
	if stock_entry_name:
		return bool(frappe.db.get_value("Delivery Note", stock_entry_name, "docstatus") == 1)
	if asset_movement_name:
		return frappe.db.get_value("Asset Movement", asset_movement_name, "docstatus") == 1
	return False


def cancel_own_inspections(doc):
	"""Cancel the submitted Quality Inspections of a cancelled loan transaction (its own rows only), as the cancellation
	of the authorized loan; a draft inspection is deleted."""
	for name in frappe.get_all(
		"Quality Inspection",
		filters={"reference_type": doc.doctype, "reference_name": doc.name, "docstatus": ["<", 2]},
		pluck="name",
	):
		inspection = frappe.get_doc("Quality Inspection", name)
		if inspection.docstatus == 0:
			frappe.delete_doc("Quality Inspection", name, force=True, ignore_permissions=True)
			continue
		inspection.flags.ignore_permissions = True
		inspection.docstatus = 2
		if workflow := inspection.meta.get_workflow():
			set_workflow_state_on_action(inspection, workflow, "cancel")
		inspection.cancel()


def cancel_source_transaction(source, fieldname):
	stored_source = frappe.get_doc(source.doctype, source.name)
	if any(source.get(field) != stored_source.get(field) for field in (fieldname, "company", "borrowing")):
		frappe.throw(_("Borrowing cancellation must use the saved source transaction links"))
	name = stored_source.get(fieldname)
	if not name:
		return
	doctype = {
		"asset_movement": "Asset Movement",
		"delivery_note": "Delivery Note",
		"pick_list": "Pick List",
		"stock_entry": "Stock Entry",
	}.get(fieldname)
	if not doctype:
		frappe.throw(_("Unsupported Borrowing transaction field: {0}").format(fieldname))
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 2:
		return
	shipment_field = "borrowing_shipment" if doctype == "Delivery Note" else "shipment"
	shipment_name = doc.get(shipment_field)
	shipment_status = frappe.db.get_value("Shipment", shipment_name, "docstatus") if shipment_name else None
	shipment = None
	if doctype == "Delivery Note":
		loan_name = stored_source.name if source.doctype == "Borrowing" else stored_source.borrowing
		loan = frappe.get_doc("Borrowing", loan_name)
		if (
			source.doctype not in LOAN_SOURCE_DOCTYPES
			or stored_source.docstatus != 1
			or loan.company != stored_source.company
			or doc.company != stored_source.company
			or not doc.get("is_borrowing_transaction")
			or doc.get("borrowing") != loan_name
			or doc.get("borrowing_source_doctype") != source.doctype
			or doc.get("borrowing_source_name") != source.name
		):
			frappe.throw(_("Delivery Note must belong to the saved Borrowing source and Company"))
		if shipment_status in (0, 1):
			shipment = frappe.get_doc("Shipment", shipment_name)
			references = shipment.get("shipment_documents") or []
			if (
				shipment.get("borrowing") != loan_name
				or len(references) != 1
				or references[0].reference_doctype != doctype
				or references[0].reference_name != name
				or any(row.delivery_note != name for row in shipment.get("shipment_delivery_note") or [])
				or any(
					parent != shipment_name
					for child_doctype, filters in (
						("Shipment Document", {"reference_doctype": doctype, "reference_name": name}),
						("Shipment Delivery Note", {"delivery_note": name}),
					)
					for parent in frappe.get_all(
						child_doctype,
						filters={**filters, "docstatus": ["!=", 2]},
						pluck="parent",
					)
				)
			):
				frappe.throw(
					_("Only the Shipment exclusively linked to this Borrowing transaction can be cancelled")
				)
			stored_source.check_permission("cancel")
			doc.flags.ignore_permissions = True
			doc.flags.borrowing_shipment_cancellation = shipment_name
	elif shipment_status in (0, 1):
		frappe.throw(_("Cancel Shipment {0} before cancelling {1} {2}.").format(shipment_name, doctype, name))
	if doc.docstatus == 1:
		doc.flags.borrowing_cancellation = True
		doc.flags.custody_operation_token = _CUSTODY_OPERATION_TOKEN
		doc.ignore_linked_doctypes = tuple(
			set(doc.get("ignore_linked_doctypes") or ()) | LOAN_SOURCE_DOCTYPES | {"Material Request"}
		)
		doc.cancel()
		if doctype == "Delivery Note" and source.doctype == "Borrowing":
			# its own inspections (QI before delivery) go with it, right after
			cancel_own_inspections(doc)
	if shipment:
		# Shipment callbacks still need the source document, including a draft receipt.
		if shipment.docstatus == 1:
			shipment.flags.ignore_permissions = True
			shipment.cancel()
		else:
			frappe.delete_doc("Shipment", shipment.name, ignore_permissions=True, force=True)
			doc.db_set(shipment_field, None)
	if doc.docstatus == 0:
		frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def unlink_deleted_loan_transaction(doc, method=None):
	if doc.doctype == "Pick List":
		source_doctype, source_name, field = "Borrowing", doc.get("borrowing"), "pick_list"
	else:
		if not doc.get("is_borrowing_transaction"):
			return
		source_doctype = doc.get("borrowing_source_doctype")
		source_name = doc.get("borrowing_source_name")
		field = {
			"Delivery Note": "delivery_note",
			"Asset Movement": "asset_movement",
			"Stock Entry": "stock_entry",
			"Sales Invoice": "sales_invoice",
		}.get(doc.doctype)
		if not field or (doc.doctype == "Stock Entry" and source_doctype != "Borrowing Adjustment"):
			return
	if not source_name or source_doctype not in LOAN_SOURCE_DOCTYPES:
		return
	if doc.docstatus == 1:
		frappe.throw(_("Cancel the movement document before deleting it."))
	if not frappe.db.exists(source_doctype, source_name):
		return
	source = frappe.get_doc(source_doctype, source_name)
	loan_name = source.name if source_doctype == "Borrowing" else source.borrowing
	lock_borrowing(loan_name)
	if source.get(field) == doc.name:
		source.db_set(field, None, update_modified=False)
		source.update_status()
	loan = frappe.get_doc("Borrowing", loan_name)
	if field in ("delivery_note", "asset_movement"):
		for row in loan.items:
			if (
				row.material_request_item
				and frappe.db.get_value("Material Request Item", row.material_request_item, field) == doc.name
			):
				frappe.db.set_value("Material Request Item", row.material_request_item, field, None)
	loan.update_status()


def check_borrowing_source(source_doctype, source_name, loan_name):
	if source_doctype not in LOAN_SOURCE_DOCTYPES or not source_name or not loan_name:
		return False
	if source_doctype == "Borrowing":
		return source_name == loan_name
	return frappe.db.get_value(source_doctype, source_name, "borrowing") == loan_name


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def borrowing_item_query(doctype, txt, searchfield, start, page_len, filters):
	"""Loan Item picker for Return and Adjustment rows.

	Borrowing Item is a child table, so Frappe's generic link search and validation check its permission
	without a parent type and refuse every user. Read rights on the parent loan decide instead.
	"""
	parent = (filters or {}).get("parent") if isinstance(filters, dict) else None
	if (
		not parent
		or not frappe.db.exists("Borrowing", parent)
		or not frappe.has_permission("Borrowing", "read", parent)
	):
		return []
	return frappe.get_all(
		"Borrowing Item",
		filters={"parent": parent, "parenttype": "Borrowing", "name": ["like", f"%{txt}%"]},
		fields=["name", "item_code"],
		order_by="idx",
		limit_start=start,
		limit_page_length=page_len,
		as_list=True,
	)
