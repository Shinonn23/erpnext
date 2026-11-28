"""One future Demo reservation per Asset and approved, auditable Loan return-date extensions."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, get_datetime, getdate, now_datetime, today

from .loan_release import ASSET_ROW_CLEARS, STOCK_ROW_CLEARS
from .policy import actor_allowed, extension_approver_roles, may_edit_demo_acquisition
from .policy import is_demo_superuser as is_superuser

METHODS = ("Ready Asset", "Wait Return", "Convert Stock")
# The requester names only the Item; the back office decides how each row is supplied (Siwat, 5 Nov 2026, relayed
# by the user). A row with no method is still to be decided; Lend Stock lends a Stock Item as Stock.
LEND_STOCK = "Lend Stock"
WAIT_RETURN = "Wait Return"
ALL_METHODS = (*METHODS, LEND_STOCK)
UNDECIDED = "To Decide"
RESERVATION_FIELDS = ("demo_reserved_request", "demo_reserved_request_item")
EXTENSION_FIELDS = (
	"demo_extension_date",
	"demo_extension_reason",
	"demo_extension_state",
	"demo_extension_requested_by",
	"demo_extension_requested_on",
	"demo_extension_original_date",
	"demo_extension_decided_by",
	"demo_extension_decided_on",
)
_EXTENSION_TOKEN = object()


# (The Material Request Item fields demo_acquisition_method and demo_requested_item are in the DocType JSON of the
# ERPNext fork: setup/fork_fields.py, batch d. The options and the help below are the ones in that JSON.)
ACQUISITION_OPTIONS = "\n" + "\n".join(ALL_METHODS)
ACQUISITION_HELP = (
	"Blank until the back office decides: Lend Stock, or an Asset (Ready, Wait Return, Convert Stock)."
)


def _configured():
	if not frappe.db.exists("DocType", "Borrowing"):
		return False
	return all(
		all(frappe.get_meta(doctype).has_field(field) for field in fields)
		for doctype, fields in (
			("Asset", RESERVATION_FIELDS),
			("Material Request", ("responsible_employee",)),
			("Material Request Item", ("demo_acquisition_method",)),
			("Borrowing", EXTENSION_FIELDS),
		)
	)


def _require_configuration():
	if not _configured():
		frappe.throw(_("Install the Demo reservation and extension fields before using these actions."))


def _native():
	from erpnext.borrowing.doctype.borrowing import borrowing_core

	return borrowing_core


def _enabled_company_user(user, company):
	filters = {"user_id": user, "status": "Active"}
	if company:
		filters["company"] = company
	return bool(
		user
		and user not in {"Administrator", "Guest"}
		and frappe.db.get_value("User", user, "enabled")
		and frappe.db.exists("Employee", filters)
	)


def _request_participants(request):
	responsible = (
		frappe.db.get_value("Employee", request.get("responsible_employee"), "user_id")
		if request.get("responsible_employee")
		else None
	)
	return {user for user in (request.owner, responsible) if _enabled_company_user(user, request.company)}


def _allowed_request_participant(request, user, *, stock=False, executive=False):
	if is_superuser(user):
		return True
	roles = set(frappe.get_roles(user))
	allowed = user in _request_participants(request)
	allowed |= bool(stock and actor_allowed("demo_acquisition_staff", request, user))
	allowed |= bool(executive and roles & extension_approver_roles())
	staffed = _enabled_company_user(user, request.company) or (
		executive and bool(roles & extension_approver_roles()) and _enabled_company_user(user, None)
	)
	return allowed and staffed


def _authorize_request(request, *, stock=False, executive=False):
	request.check_permission("read")
	if not _allowed_request_participant(request, frappe.session.user, stock=stock, executive=executive):
		frappe.throw(
			_("Only the requesting team or authorized Demo operator can use this request."),
			frappe.PermissionError,
		)


def _asset_for_row(request, row, *, for_update=False):
	if not row.get("asset"):
		frappe.throw(_("Select the Asset to reserve while waiting for its return."))
	asset = frappe.get_doc("Asset", row.asset, for_update=for_update)
	asset.check_permission("select")
	if asset.company != request.company or asset.item_code != row.item_code or asset.docstatus != 1:
		frappe.throw(_("The Demo Asset must match the requested model and company."))
	if asset.status in {"Draft", "Scrapped", "Sold", "Capitalized"}:
		frappe.throw(_("Select a usable Asset."))
	# a held asset may be waited for (Wait Return); it is only never lent (_asset_ready, loan_release)
	return asset


def _asset_ready(asset):
	from erpnext.assets.inspection_hold import is_held

	if (
		is_held(asset)
		or asset.get("custodian")
		or asset.get("demo_loan_status") == "With Customer"
		or not asset.get("location")
	):
		return False
	if (
		asset.status
		in {
			"Draft",
			"Cancelled",
			"Scrapped",
			"Sold",
			"Capitalized",
			"In Maintenance",
			"Out of Order",
			"Work In Progress",
		}
		or asset.docstatus != 1
	):
		return False
	if frappe.db.exists(
		"Asset Repair", {"asset": asset.name, "repair_status": "Pending", "docstatus": ["!=", 2]}
	):
		return False
	return not _native().asset_has_unreturned_borrowing(asset.name)


def _validate_deferred_row(request, row):
	if flt(row.qty) <= 0 or (row.get("asset") and flt(row.qty) != 1):
		frappe.throw(_("Row {0}: an Asset supplies one unit.").format(row.idx))
	item = frappe.get_cached_doc("Item", row.item_code)
	if not item.is_fixed_asset or item.disabled:
		frappe.throw(_("Row {0}: select an enabled Fixed Asset Item or Stock Item.").format(row.idx))
	row.item_type = "Fixed Asset"
	row.uom = row.stock_uom = item.stock_uom
	row.conversion_factor = 1
	row.target_location = row.get("target_location") or request.customer_location
	if row.get("demo_acquisition_method") in ("Convert Stock", None, ""):
		if row.get("asset"):
			frappe.throw(_("Clear the Asset when requesting Stock conversion."))
		row.source_location = None
		return
	asset = _asset_for_row(request, row)
	row.asset_name = asset.asset_name
	row.asset_serial_number = asset.get("asset_serial_number")
	row.source_location = asset.location


class DemoMaterialRequestMixin:
	def validate_asset_items(self, purpose):
		if purpose not in ("Customer Demo", "Borrow", "Customer Borrow") or not _configured():
			return super().validate_asset_items(purpose)
		rows = self.items
		deferred = [
			row
			for row in rows
			if row.get("demo_acquisition_method") in METHODS[1:]
			or (not row.get("demo_acquisition_method") and not row.get("asset"))
		]
		if not deferred:
			return super().validate_asset_items(purpose)
		for row in deferred:
			_validate_deferred_row(self, row)
		ready = [row for row in rows if row not in deferred]
		if ready:
			try:
				self.items = ready
				super().validate_asset_items(purpose)
			finally:
				self.items = rows

	@frappe.whitelist()
	def make_borrowing(self, loan_type):
		from .loan_automation import undecided_rows

		if undecided := undecided_rows(self):
			frappe.throw(
				_("Rows {0}: the Stock team must choose the Assets first (Decide release).").format(
					", ".join(str(idx) for idx in undecided)
				)
			)
		if self.material_request_type == "Customer Demo" and loan_type == "Asset":
			prepare_reserved_assets(self.name)
		return super().make_borrowing(loan_type)


def validate_request(doc, method=None):
	if doc.material_request_type != "Customer Demo" or not _configured():
		return
	requester = doc.owner or frappe.session.user
	if doc.docstatus != 2 and not _enabled_company_user(requester, doc.company):
		# Administrator has no Employee (Siwat, 6 Nov 2026): the Responsible Employee answers for the items.
		if is_superuser(requester):
			if not doc.get("responsible_employee"):
				frappe.throw(_("Select the Responsible Employee."))
		else:
			frappe.throw(_("A Demo request must have an active requesting Employee in its company."))
	seen = set()
	for row in doc.items:
		method = row.get("demo_acquisition_method") or None
		if method not in (None, *ALL_METHODS):
			frappe.throw(_("Select a valid Demo Acquisition Method."))
		if doc.docstatus == 0 and not row.get("demo_requested_item"):
			row.demo_requested_item = row.item_code
		if method == LEND_STOCK and row.get("item_type") != "Stock Item":
			frappe.throw(_("Row {0}: Lend Stock needs a Stock Item.").format(row.idx))
		if row.get("item_type") == "Stock Item" and method not in (None, LEND_STOCK):
			frappe.throw(
				_(
					"Row {0}: {1} supplies an Asset: decide it with Decide release or Prepare Conversion."
				).format(row.idx, method)
			)
		if row.get("asset"):
			if row.asset in seen:
				frappe.throw(_("A Demo Asset can only appear once per request."))
			seen.add(row.asset)
			reserved = frappe.db.get_value("Asset", row.asset, "demo_reserved_request", for_update=True)
			if reserved and reserved != doc.name and row.get("demo_acquisition_method") != "Wait Return":
				frappe.throw(_("This Asset already has a future Demo reservation."))
	previous = doc.get_doc_before_save()
	if previous and previous.docstatus == 1:
		before = {row.name: (row.get("demo_acquisition_method"), row.get("asset")) for row in previous.items}
		after = {row.name: (row.get("demo_acquisition_method"), row.get("asset")) for row in doc.items}
		if before != after:
			frappe.throw(
				_("Change approved Demo acquisition through Change Demo Acquisition and record a reason.")
			)


def sync_reservations(doc, method=None):
	if not _configured():
		return
	allocated = {
		row[0]
		for row in frappe.db.sql(
			"""select item.material_request_item from `tabBorrowing Item` item
			join `tabBorrowing` loan on loan.name = item.parent
			where loan.material_request = %s and loan.docstatus = 1 for update""",
			(doc.name,),
		)
	}
	desired = (
		{
			row.asset: row.name
			for row in doc.get("items") or []
			if row.get("asset")
			and row.get("demo_acquisition_method") == "Wait Return"
			and row.name not in allocated
		}
		if doc.docstatus != 2 and doc.material_request_type == "Customer Demo"
		else {}
	)
	stored = frappe.get_all("Asset", filters={"demo_reserved_request": doc.name}, pluck="name")
	for name in sorted(set(stored) | set(desired)):
		_native().lock_asset(name)
		asset = frappe.get_doc("Asset", name, for_update=True)
		if name not in desired:
			if asset.get("demo_reserved_request") == doc.name:
				frappe.db.set_value(
					"Asset", name, dict.fromkeys(RESERVATION_FIELDS, None), update_modified=False
				)
			continue
		if asset.get("demo_reserved_request") and (
			asset.demo_reserved_request != doc.name
			or asset.get("demo_reserved_request_item") != desired[name]
		):
			frappe.throw(_("This Asset already has a future Demo reservation."))
		row = next(row for row in doc.items if row.name == desired[name])
		_asset_for_row(doc, row, for_update=True)
		unallocated = frappe.db.sql(
			"""select mr.name from `tabMaterial Request` mr
			join `tabMaterial Request Item` item on item.parent = mr.name
			where item.asset = %s and mr.name != %s and mr.docstatus = 1
			and ((mr.material_request_type = 'Asset Transfer' and coalesce(mr.asset_transfer_status, '') not in ('Fulfilled', 'Cancelled'))
			or (mr.material_request_type in ('Customer Demo', 'Borrow', 'Customer Borrow')
			and coalesce(mr.request_progress_status, '') not in ('Returned', 'Closed', 'Cancelled')))
			and not exists (select 1 from `tabBorrowing` loan where loan.material_request = mr.name and loan.docstatus = 1)
			limit 1 for update""",
			(name, doc.name),
		)
		if unallocated:
			frappe.throw(
				_("This Asset is already allocated to another approved request awaiting its Borrowing.")
			)
		frappe.db.set_value(
			"Asset",
			name,
			{"demo_reserved_request": doc.name, "demo_reserved_request_item": desired[name]},
			update_modified=False,
		)


def release_reservations(doc, method=None):
	if not _configured():
		return
	for name in sorted(frappe.get_all("Asset", filters={"demo_reserved_request": doc.name}, pluck="name")):
		_native().lock_asset(name)
		if frappe.db.get_value("Asset", name, "demo_reserved_request", for_update=True) == doc.name:
			frappe.db.set_value("Asset", name, dict.fromkeys(RESERVATION_FIELDS, None), update_modified=False)


def validate_asset_reservation_fields(doc, method=None):
	if doc.is_new():
		previous = None
	else:
		previous = doc.get_doc_before_save() or frappe.get_doc("Asset", doc.name, for_update=True)
	if any(
		(doc.get(field) or None) != ((previous.get(field) if previous else None) or None)
		for field in RESERVATION_FIELDS
	):
		frappe.throw(
			_("Future Demo reservations are maintained from their Material Request."), frappe.PermissionError
		)


@frappe.whitelist()
def change_demo_acquisition(material_request, item, acquisition_method, reason, asset=None):
	"""The back office decides how a Demo row is supplied (Siwat, 5 Nov 2026, relayed by the user): Lend Stock lends
	the requested Stock Item; Ready Asset / Wait Return supply a Demo Asset, of any Demo model; Convert Stock turns
	stock into an Asset of the row's Fixed Asset Item. The requested Item stays in Requested Item."""
	_require_configuration()
	_native().lock_material_request(material_request)
	request = frappe.get_doc("Material Request", material_request, for_update=True)
	_authorize_request(request, stock=True)
	if (
		request.material_request_type != "Customer Demo"
		or request.docstatus == 2
		or (request.docstatus == 0 and not may_edit_demo_acquisition(request))
	):
		frappe.throw(_("Change acquisition only on a Draft or approved Demo request."))
	if acquisition_method not in ALL_METHODS or not str(reason or "").strip():
		frappe.throw(_("Select the acquisition method and record why it changes."))
	row = next((row for row in request.items if row.name == item), None)
	if not row:
		frappe.throw(_("Select one existing Demo object from this request."))
	if frappe.db.exists("Borrowing", {"material_request": request.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Finish or cancel the existing Borrowing before changing its acquisition."))
	old_method, old_asset = row.get("demo_acquisition_method") or UNDECIDED, row.get("asset")
	requested = row.get("demo_requested_item") or row.item_code
	for name in sorted({name for name in (old_asset, asset) if name}):
		_native().lock_asset(name)
	row.demo_requested_item = requested
	row.demo_acquisition_method = acquisition_method

	if acquisition_method == LEND_STOCK:
		item = frappe.get_cached_doc("Item", requested) if requested else None
		if not item or not item.get("is_stock_item"):
			frappe.throw(_("Lend Stock needs a Stock Item: {0} is not one.").format(requested))
		row.update(
			{
				**STOCK_ROW_CLEARS,
				# the row names the Customer Demo warehouse for reference only: the Borrowing takes its
				# source from from_warehouse, the default warehouse or stocked_warehouse
				"warehouse": request.get("customer_loan_warehouse")
				or frappe.db.get_value("Company", request.company, "customer_loan_warehouse"),
				"item_code": requested,
				"item_name": item.get("item_name"),
				"item_type": "Stock Item",
				"uom": item.get("stock_uom"),
				"stock_uom": item.get("stock_uom"),
				"conversion_factor": 1,
			}
		)
	else:
		if flt(row.qty) != 1:
			frappe.throw(_("An Asset supplies one unit: this row asks for {0}.").format(flt(row.qty)))
		row.asset = asset or None
		if row.asset:
			# a Demo Asset of any model may stand in for the requested Item
			row.item_code = frappe.db.get_value("Asset", row.asset, "item_code")
		item = frappe.get_cached_doc("Item", row.item_code)
		if acquisition_method == "Convert Stock" and not (item and item.get("is_fixed_asset")):
			frappe.throw(_("Convert Stock needs a row of a Fixed Asset Item; choose a Demo Asset instead."))
		row.update(
			{
				**ASSET_ROW_CLEARS,
				"item_name": item.get("item_name"),
				"item_type": "Fixed Asset",
				"uom": item.get("stock_uom"),
				"stock_uom": item.get("stock_uom"),
				"conversion_factor": 1,
			}
		)
		if acquisition_method == "Ready Asset":
			selected = _asset_for_row(request, row, for_update=True)
			if not _asset_ready(selected):
				frappe.throw(_("The Asset is not genuinely returned and ready."))
			row.source_location = selected.location
			row.asset_name = selected.asset_name
			row.asset_serial_number = selected.get("asset_serial_number")
		else:
			if acquisition_method == "Wait Return":
				_asset_for_row(request, row, for_update=True)
			_validate_deferred_row(request, row)
	frappe.db.set_value(
		"Material Request Item",
		row.name,
		{
			field: row.get(field)
			for field in (
				"demo_acquisition_method",
				"demo_requested_item",
				"asset",
				"asset_name",
				"asset_serial_number",
				"source_location",
				"target_location",
				"item_code",
				"item_name",
				"item_type",
				"uom",
				"stock_uom",
				"conversion_factor",
				*ASSET_ROW_CLEARS,
			)
		},
		update_modified=False,
	)
	sync_reservations(request)
	request.add_comment(
		"Comment",
		_("Demo acquisition changed from {0} ({1}) to {2} ({3}). Reason: {4}").format(
			old_method, old_asset or "—", acquisition_method, row.asset or row.item_code, reason
		),
	)
	loans = []
	if request.docstatus == 1:
		from .loan_release import refresh_pending

		refresh_pending(request.name)
	return {"method": acquisition_method, "asset": row.asset, "item_code": row.item_code, "loans": loans}


@frappe.whitelist()
def prepare_reserved_assets(material_request):
	_require_configuration()
	_native().lock_material_request(material_request)
	request = frappe.get_doc("Material Request", material_request, for_update=True)
	_authorize_request(request, stock=True)
	return _prepare_reserved_assets(request)


def _prepare_reserved_assets(request):
	"""The reserved Assets of an approved Demo request are back and ready (no access check: the system issues)."""
	if request.docstatus != 1 or request.material_request_type != "Customer Demo":
		frappe.throw(_("Use an approved Demo Material Request."))
	for row in request.items:
		if row.get("item_type") == "Stock Item":
			continue
		if row.get("demo_acquisition_method") == "Convert Stock":
			frappe.throw(
				_("Complete the separately approved Stock conversion and select its ready Asset first.")
			)
		_native().lock_asset(row.asset)
		asset = _asset_for_row(request, row, for_update=True)
		if asset.get("demo_reserved_request") and (
			asset.demo_reserved_request != request.name or asset.get("demo_reserved_request_item") != row.name
		):
			frappe.throw(_("This Asset is reserved for another Demo request."))
		if not _asset_ready(asset):
			frappe.throw(_("Wait until the Asset has genuinely been received and is ready."))
		if row.source_location != asset.location:
			frappe.db.set_value(
				"Material Request Item", row.name, "source_location", asset.location, update_modified=False
			)
			request.add_comment(
				"Comment",
				_(
					"Reserved Demo Asset {0} received at {1}; the Borrowing source location was refreshed."
				).format(asset.name, asset.location),
			)
	return True


def prepare_loan_location(doc, method=None):
	"""Refresh returned Demo Assets only when the new Loan is saved."""
	if not doc.is_new() or doc.loan_type != "Asset" or not doc.material_request:
		return
	request = frappe.get_doc("Material Request", doc.material_request)
	request.check_permission("read")
	if request.material_request_type == "Customer Demo":
		_prepare_reserved_assets(request)


def validate_loan(doc, method=None):
	previous = doc.get_doc_before_save()
	trusted = doc.flags.get("demo_extension_token") is _EXTENSION_TOKEN
	for field in EXTENSION_FIELDS:
		current = doc.get(field) or None
		stored = (previous.get(field) if previous else None) or None
		if field.endswith("_date"):
			current, stored = (getdate(value) if value else None for value in (current, stored))
		elif field.endswith("_on"):
			current, stored = (get_datetime(value) if value else None for value in (current, stored))
		if current != stored and not trusted:
			frappe.throw(_("Use the Demo extension request and decision actions."), frappe.PermissionError)
	if (
		previous
		and getdate(doc.expected_return_date) != getdate(previous.expected_return_date)
		and not trusted
	):
		frappe.throw(_("The MD or CEO must approve an extension before the return date changes."))
	if not previous:
		request = frappe.get_doc("Material Request", doc.material_request)
		if getdate(doc.expected_return_date) != getdate(request.expected_return_date):
			frappe.throw(_("The initial Borrowing return date must match its approved Material Request."))
	if _configured() and doc.loan_type == "Asset" and (not previous or previous.docstatus == 0):
		for row in doc.items:
			_native().lock_asset(row.asset)
			reservation = (
				frappe.db.get_value(
					"Asset", row.asset, list(RESERVATION_FIELDS), as_dict=True, for_update=True
				)
				or {}
			)
			if reservation.get("demo_reserved_request") and (
				reservation.demo_reserved_request != doc.material_request
				or reservation.demo_reserved_request_item != row.material_request_item
			):
				frappe.throw(_("This Asset is reserved for another Demo request."))


def on_loan_submit(doc, method=None):
	if not _configured():
		return
	for row in doc.get("items") or []:
		if not row.get("asset"):
			continue
		_native().lock_asset(row.asset)
		stored = (
			frappe.db.get_value("Asset", row.asset, list(RESERVATION_FIELDS), as_dict=True, for_update=True)
			or {}
		)
		if (
			stored.get("demo_reserved_request") == doc.material_request
			and stored.get("demo_reserved_request_item") == row.material_request_item
		):
			frappe.db.set_value(
				"Asset", row.asset, dict.fromkeys(RESERVATION_FIELDS, None), update_modified=False
			)


def _load_loan(loan_name, *, executive=False):
	_require_configuration()
	_native().lock_borrowing(loan_name)
	loan = frappe.get_doc("Borrowing", loan_name, for_update=True)
	request = frappe.get_doc("Material Request", loan.material_request)
	_authorize_request(request, executive=executive)
	if (
		loan.company != request.company
		or loan.docstatus != 1
		or loan.status
		not in {"Active", "Partially Returned", "Pending Receipt", "Pending Quality Inspection"}
	):
		frappe.throw(_("An extension requires an active submitted Borrowing in the request company."))
	return loan, request


@frappe.whitelist()
def request_extension(borrowing, expected_return_date, reason):
	loan, request = _load_loan(borrowing)
	if getdate(expected_return_date) <= getdate(loan.expected_return_date) or not str(reason or "").strip():
		frappe.throw(_("Request a later return date and explain why it is needed."))
	if loan.get("demo_extension_state") == "Pending":
		frappe.throw(_("The existing extension request is still waiting for a decision."))
	loan.update(
		{
			"demo_extension_date": getdate(expected_return_date),
			"demo_extension_reason": reason,
			"demo_extension_state": "Pending",
			"demo_extension_requested_by": frappe.session.user,
			"demo_extension_requested_on": now_datetime(),
			"demo_extension_original_date": loan.expected_return_date,
			"demo_extension_decided_by": None,
			"demo_extension_decided_on": None,
		}
	)
	loan.flags.demo_extension_token = _EXTENSION_TOKEN
	loan.save(ignore_permissions=True)
	loan.add_comment(
		"Comment", _("Return extension requested to {0}. Reason: {1}").format(expected_return_date, reason)
	)
	deciders = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ["in", sorted(extension_approver_roles())]},
		pluck="parent",
	)
	_notify(
		request,
		f"extension-request:{loan.name}:{loan.demo_extension_requested_on}",
		_("Demo extension awaiting decision"),
		_("Review the requested return-date extension on this Demo request."),
		email=True,
		users=deciders,
	)
	return {"state": "Pending", "expected_return_date": loan.expected_return_date}


@frappe.whitelist()
def decide_extension(borrowing, approve):
	if not set(frappe.get_roles()) & extension_approver_roles():
		frappe.throw(_("Only the MD or CEO can decide a Demo extension."), frappe.PermissionError)
	loan, request = _load_loan(borrowing, executive=True)
	if loan.get("demo_extension_state") != "Pending" or getdate(loan.expected_return_date) != getdate(
		loan.demo_extension_original_date
	):
		frappe.throw(_("The pending extension no longer matches the current return date."))
	if str(approve) not in {"0", "1"}:
		frappe.throw(_("Select Approve or Reject."))
	approved = cint(approve) == 1
	if approved:
		loan.expected_return_date = loan.demo_extension_date
	loan.demo_extension_state = "Approved" if approved else "Rejected"
	loan.demo_extension_decided_by = frappe.session.user
	loan.demo_extension_decided_on = now_datetime()
	loan.flags.demo_extension_token = _EXTENSION_TOKEN
	loan.save(ignore_permissions=True)
	loan.add_comment(
		"Comment",
		_("Extension {0}; expected return date {1}.").format(
			loan.demo_extension_state, loan.expected_return_date
		),
	)
	if approved:
		for row in loan.items:
			if row.get("asset"):
				waiting = frappe.db.get_value("Asset", row.asset, "demo_reserved_request")
				if waiting and waiting != request.name:
					waiting_request = frappe.get_doc("Material Request", waiting)
					_notify(
						waiting_request,
						f"extension:{loan.name}:{loan.demo_extension_requested_on}",
						_("Demo Asset return postponed"),
						_("A reserved Asset now has an approved return date of {0}.").format(
							loan.expected_return_date
						),
						email=True,
					)
	return {"state": loan.demo_extension_state, "expected_return_date": loan.expected_return_date}


@frappe.whitelist()
def can_view_demo_status(material_request):
	if not material_request or not _configured():
		return False
	request = frappe.get_doc("Material Request", material_request)
	return bool(
		request.material_request_type == "Customer Demo"
		and request.docstatus != 2
		and request.has_permission("read")
		and _allowed_request_participant(request, frappe.session.user, stock=True, executive=True)
	)


@frappe.whitelist()
def get_demo_status(material_request):
	_require_configuration()
	request = frappe.get_doc("Material Request", material_request, for_update=True)
	_authorize_request(request, stock=True, executive=True)
	if request.material_request_type != "Customer Demo":
		frappe.throw(_("Select a Demo Material Request."))
	loans = frappe.get_all(
		"Borrowing",
		filters={"material_request": request.name, "docstatus": 1},
		fields=["name", "expected_return_date", "status", *EXTENSION_FIELDS],
	)
	for loan in loans:
		loan["waiting"] = []
		for asset_name in frappe.get_all(
			"Borrowing Item", filters={"parent": loan.name, "asset": ["is", "set"]}, pluck="asset"
		):
			waiting_name = frappe.db.get_value("Asset", asset_name, "demo_reserved_request")
			if waiting_name and waiting_name != request.name:
				waiting = frappe.get_doc("Material Request", waiting_name)
				if waiting.company == request.company and waiting.docstatus != 2:
					loan["waiting"].append(
						{
							"asset": asset_name,
							"request": waiting.name,
							"requesters": sorted(_request_participants(waiting)),
						}
					)
	return {
		"loans": loans,
		"can_decide": bool(set(frappe.get_roles()) & extension_approver_roles()),
		"can_request": is_superuser() or frappe.session.user in _request_participants(request),
		"items": [
			{
				"name": row.name,
				"item_code": row.item_code,
				"asset": row.asset,
				"method": row.get("demo_acquisition_method") or UNDECIDED,
			}
			for row in request.items
		],
	}


def _notify(request, event, subject, message, *, email, users=None):
	for provider in frappe.get_hooks("borrowing_demo_notifications"):
		frappe.call(
			provider, request=request, event=event, subject=subject, message=message, email=email, users=users
		)


def send_demo_reminders():
	if not _configured():
		return
	for name in frappe.get_all(
		"Borrowing",
		filters={"docstatus": 1, "status": ["not in", ["Returned", "Closed", "Cancelled"]]},
		pluck="name",
	):
		_native().lock_borrowing(name)
		loan = frappe.get_doc("Borrowing", name, for_update=True)
		if not any(flt(row.issued_qty) > 0 and flt(row.outstanding_qty) > 0 for row in loan.items):
			continue
		request = frappe.get_doc("Material Request", loan.material_request)
		if (
			request.material_request_type != "Customer Demo"
			or request.company != loan.company
			or request.docstatus != 1
		):
			continue
		days = date_diff(loan.expected_return_date, today())
		if days <= 7:
			kind = "overdue" if days <= 0 else "due"
			_notify(
				request,
				f"{kind}:{loan.name}:{loan.expected_return_date}",
				_("Demo return due") if days > 0 else _("Demo return overdue"),
				_("Please follow up the Demo return due on {0}.").format(loan.expected_return_date),
				email=True,
			)
	for name in frappe.get_all("Asset", filters={"demo_reserved_request": ["is", "set"]}, pluck="name"):
		_notify_ready_asset(name)


def _notify_ready_asset(name):
	_native().lock_asset(name)
	asset = frappe.get_doc("Asset", name, for_update=True)
	if not asset.get("demo_reserved_request") or not _asset_ready(asset):
		return
	request = frappe.get_doc("Material Request", asset.demo_reserved_request)
	row = next((row for row in request.items if row.name == asset.get("demo_reserved_request_item")), None)
	if (
		request.docstatus == 2
		or not row
		or row.asset != name
		or row.get("demo_acquisition_method") != "Wait Return"
	):
		return
	_notify(
		request,
		f"ready:{name}:{row.name}",
		_("Reserved Demo Asset ready"),
		_("The reserved Demo Asset has been received and is ready for the next Borrowing."),
		email=False,
	)


def on_return_event(doc, method=None):
	if not _configured():
		return
	if doc.doctype == "Asset Movement":
		assets = {row.asset for row in doc.get("assets") or [] if row.get("asset")}
	elif doc.doctype == "Asset Repair":
		assets = {doc.asset} if doc.get("asset") else set()
	else:
		assets = set()
	for name in sorted(assets):
		_notify_ready_asset(name)
