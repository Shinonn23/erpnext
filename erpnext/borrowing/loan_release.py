"""The Stock team decides how an approved Demo / Borrow request is released (Siwat, 5 Nov 2026, relayed by the user).

The requester names the Items and the quantities. Nothing else is asked of him. After the approval:

- a Stock Item stays stock unless the team explicitly selects mapped Assets or prepares a Stock conversion;
- a row of a Fixed Asset Item can only go as Assets: the Stock team picks one Asset for each unit, once for the whole
  request (`decide_release`, the dialog "Decide release" of My Tasks and of the request). A row for N units becomes N
  rows of one Asset, which is how the rest of the system (reservations, Borrowings, returns) already works.

When every row is decided the Stock team can explicitly prepare the loans (`issue_loan`). Only the Stock User and the Stock
Manager decide; the MD and the CEO approve, they do not release.

An Asset that is back and ready is lent now ("Ready Asset"); a Demo Asset still with a customer is reserved ("Wait
Return", Demo requests only) and the loan can be prepared when it returns. A recipient that is not an Employee needs a Location
for the Assets: the one on the request, else the Location named like the recipient (made here when it does not exist yet).

The route of the request (`loan_approval.py`) valued each such row at the highest book value of the Item's Assets, so
no Asset the team picks is worth more than the approvers saw. If a decision still raised the total above the ceiling of
the frozen band, it is refused with a message (a request in the Item Group or MD band is capped by
`quotation_ceo_amount`); the request is then cancelled and made again, which routes it to the CEO.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from .policy import (
	actor_allowed,
	asset_item_plan,
	asset_location_defaults,
	prepare_asset_item,
	sync_request_tasks,
	workflow_state_rules,
)

LOAN_TYPES = ("Customer Demo", "Borrow", "Customer Borrow")
PENDING_FIELD = "loan_release_pending"

PROBLEM_FIELD = "loan_issue_problem"
STOCK_ITEM = "Stock Item"
READY = "Ready Asset"
WAIT = "Wait Return"
# A row stores only what applies to it (UX WP7, 19 Nov 2026): an Asset row has no warehouse or Storage Location, a
# Stock row has no Asset or Asset Location. Row edits after submit go through db.set_value and miss the fork's
# before_validate, so the write paths clear them themselves. Prepare Conversion keeps the Stock row's from_warehouse
# on purpose: it records where the stock is converted from.
ASSET_ROW_CLEARS = {
	"warehouse": None,
	"from_warehouse": None,
	"storage_location": None,
	"from_storage_location": None,
}
STOCK_ROW_CLEARS = {
	"asset": None,
	"asset_name": None,
	"asset_serial_number": None,
	"source_location": None,
	"target_location": None,
}
COPY_SKIP = {
	"name",
	"idx",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"parent",
	"parenttype",
	"parentfield",
	"doctype",
	"docstatus",
	"__islocal",
	"__unsaved",
}


def rows_to_decide(doc):
	"""Fixed Asset rows and requested Stock conversions wait for the Stock team's physical Asset selection."""
	if doc.get("material_request_type") not in LOAN_TYPES:
		return []
	return [
		row
		for row in doc.get("items") or []
		if not row.get("asset")
		and (
			row.get("demo_acquisition_method") in ("Convert Stock", READY)
			or (row.get("item_type") != STOCK_ITEM and not row.get("demo_acquisition_method"))
		)
	]


def _check_stock_team(request):
	if not actor_allowed("prepare_release", request):
		frappe.throw(
			_("Only the Stock team decides how a Demo or Borrow request is released."), frappe.PermissionError
		)
	request.check_permission("read")


def _asset_item_code(row):
	if row.get("item_type") == STOCK_ITEM:
		return frappe.get_cached_value("Item", row.item_code, "default_asset_item_for_conversion")
	return row.item_code


def _release_qty(row):
	return flt(row.get("stock_qty") or row.qty) if row.get("item_type") == STOCK_ITEM else flt(row.qty)


def _row_conversions(request, row):
	state_field, _states = workflow_state_rules("Stock to Asset Conversion", "release_conversion_reservation")
	conversion = frappe.qb.DocType("Stock to Asset Conversion")
	source = frappe.qb.DocType("Stock to Asset Conversion Item")
	fields = [conversion.name, conversion.docstatus, source.name.as_("source_item_row")]
	if frappe.db.has_column("Stock to Asset Conversion", state_field):
		fields.append(conversion[state_field].as_("workflow_state"))
	return (
		frappe.qb.from_(conversion)
		.join(source)
		.on(source.parent == conversion.name)
		.select(*fields)
		.where(
			(conversion.material_request == request.name)
			& (source.material_request_item == row.name)
			& (conversion.docstatus < 2)
		)
		.orderby(conversion.creation)
		.run(as_dict=True)
	)


def _asset_item_info(request, row):
	"""For the Prepare Conversion dialog: how a missing Asset Item would be made."""
	mapped = _asset_item_code(row)
	if not mapped:
		return {"asset_item_plan": asset_item_plan(row.item_code, request.material_request_type)}
	return {"asset_item_plan": None}


@frappe.whitelist()
def get_release(material_request, omit_if_unavailable=False):
	"""What the dialog shows: the rows still to decide, and the Assets the team may pick for each Item."""
	request = frappe.get_doc("Material Request", material_request)
	quiet = cint(omit_if_unavailable)
	if quiet:
		if not request.has_permission("read") or not actor_allowed("prepare_release", request):
			return
	else:
		_check_stock_team(request)
	if request.docstatus != 1 or request.material_request_type not in LOAN_TYPES:
		if quiet:
			return
		frappe.throw(_("Use an approved Demo or Borrow request."))
	rows, stock_rows = [], []
	for row in request.items:
		conversions = _row_conversions(request, row) if row.item_type == STOCK_ITEM else []
		if row.item_type == STOCK_ITEM:
			stock_rows.append(
				{
					"row": row.name,
					"idx": row.idx,
					"item_code": row.item_code,
					"item_name": row.item_name,
					"qty": _release_qty(row),
					"asset_item_code": _asset_item_code(row),
					**_asset_item_info(request, row),
					"warehouse": row.get("from_warehouse"),
					"converting": row.get("demo_acquisition_method") == "Convert Stock",
					"selecting_assets": row.get("demo_acquisition_method") == READY,
					"conversions": conversions,
					"converted": any(conversion.docstatus == 1 for conversion in conversions),
				}
			)
			if row.get("demo_acquisition_method") not in ("Convert Stock", READY):
				continue
		rows.append(
			{
				"row": row.name,
				"idx": row.idx,
				"item_code": row.get("demo_requested_item") or row.item_code,
				"item_name": row.item_name,
				"qty": _release_qty(row),
				"asset_item_code": _asset_item_code(row),
				"conversion_request": request.name
				if row.get("demo_acquisition_method") == "Convert Stock"
				else None,
				"conversion_items": [
					conversion.source_item_row for conversion in conversions if conversion.docstatus == 1
				],
				"asset": row.get("asset"),
				"decided": bool(row.get("asset") or row.get("demo_acquisition_method") == WAIT),
			}
		)
	from erpnext.borrowing.doctype.borrowing.borrowing_core import request_loan_types

	prepared = frappe.get_all(
		"Borrowing", filters={"material_request": request.name, "docstatus": ["<", 2]}, pluck="loan_type"
	)
	available_types = [kind for kind in request_loan_types(request) if kind not in prepared]
	return {
		"rows": rows,
		"stock_rows": stock_rows,
		"demo": request.material_request_type == "Customer Demo",
		"stock_only": request.material_request_type == "Borrow" and request.borrow_item_type != "Asset",
		"needs_preparation": bool(available_types),
		"available_loan_types": available_types,
		"loans": frappe.get_all(
			"Borrowing", filters={"material_request": request.name, "docstatus": ["<", 2]}, pluck="name"
		),
	}


def _preparation_row(material_request, row):
	from erpnext.borrowing.doctype.borrowing.borrowing_core import lock_material_request

	lock_material_request(material_request)
	request = frappe.get_doc("Material Request", material_request, for_update=True)
	_check_stock_team(request)
	if request.docstatus != 1 or request.material_request_type not in LOAN_TYPES:
		frappe.throw(_("Use an approved Demo or Borrow request."))
	if frappe.db.exists("Borrowing", {"material_request": request.name, "docstatus": ["<", 2]}):
		frappe.throw(
			_("Return or cancel the existing borrowings before changing how the items are supplied.")
		)
	source = next((item for item in request.items if item.name == row), None)
	if not source or source.item_type != STOCK_ITEM or source.get("asset"):
		frappe.throw(_("Choose a Stock Item row from this request."))
	return request, source


def _refuse_assets_for_borrow(request):
	if request.material_request_type == "Borrow" and request.borrow_item_type != "Asset":
		frappe.throw(
			_("This request borrows Stock Items. Choose Asset on a new Borrow request to borrow Assets.")
		)


def _mapped_asset_item(request, source, *, code=None):
	target = code or _asset_item_code(source)
	if not target:
		frappe.throw(
			_(
				"Item {0} has no Asset Item yet: choose an Asset Category in Prepare Conversion to create it, "
				"or use Item > Create Asset Item."
			).format(frappe.utils.get_link_to_form("Item", source.item_code))
		)
	# an Item created in this request is read fresh, not from the cache
	item = frappe.get_doc("Item", target) if code else frappe.get_cached_doc("Item", target)
	if item.get("disabled") or not item.is_fixed_asset or item.is_stock_item:
		frappe.throw(_("The mapped Item must be an enabled non-stock Fixed Asset Item."))
	return item


@frappe.whitelist(methods=["POST"])
def prepare_asset_selection(material_request, row):
	"""Use an existing mapped Asset; keep the requested Stock code until physical Assets are selected."""
	request, source = _preparation_row(material_request, row)
	_refuse_assets_for_borrow(request)
	_mapped_asset_item(request, source)
	if _row_conversions(request, source):
		frappe.throw(_("Finish or cancel the existing conversion before changing the supply method."))
	frappe.db.set_value(
		"Material Request Item",
		source.name,
		{
			"demo_requested_item": source.get("demo_requested_item") or source.item_code,
			"demo_acquisition_method": READY,
		},
		update_modified=False,
	)
	request.add_comment(
		"Comment", _("Row {0}: existing Asset selection requested; no items have moved.").format(source.idx)
	)
	refresh_pending(request.name)
	return {"pending": True}


@frappe.whitelist(methods=["POST"])
def use_stock(material_request, row, reason):
	"""Abandon an unsaved Asset/conversion choice; saved conversions must be cancelled or deleted first."""
	request, source = _preparation_row(material_request, row)
	if not str(reason or "").strip():
		frappe.throw(_("Record why the supply method changes."))
	if _row_conversions(request, source):
		frappe.throw(_("Cancel or delete the conversion before choosing Stock."))
	frappe.db.set_value(
		"Material Request Item",
		source.name,
		{"demo_acquisition_method": "Lend Stock", **STOCK_ROW_CLEARS},
		update_modified=False,
	)
	request.add_comment("Comment", _("Row {0}: supply as Stock. Reason: {1}").format(source.idx, reason))
	refresh_pending(request.name)
	return {"pending": False}


@frappe.whitelist(methods=["POST"])
def prepare_conversion(
	material_request,
	row,
	warehouse=None,
	asset_item_code=None,
	asset_category=None,
	asset_location=None,
	serial_nos=None,
):
	"""Choose conversion explicitly; opening the native form neither saves a conversion nor moves stock.

	A Stock Item with no Asset Item yet gets one here, from the Asset Category the dialog asked for, through the same
	code as Item > Create Asset Item (``demo_items.ensure_asset_item``; Siwat, 17 Nov 2026): a second press reuses it.
	The dialog also gives the Asset Location (default ``default_asset_location``) and the Serial Numbers of the units
	(FL05 round 2, 17 Nov 2026), so the conversion can go to review and be submitted without editing its locked rows."""
	from erpnext.assets.doctype.stock_to_asset_conversion.stock_to_asset_conversion import (
		get_conversion_defaults,
	)
	from erpnext.stock.doctype.storage_location.storage_location import serial_locations

	frappe.has_permission("Stock to Asset Conversion", "create", throw=True)
	request, source = _preparation_row(material_request, row)
	_refuse_assets_for_borrow(request)
	conversions = _row_conversions(request, source)
	if conversions:
		_mapped_asset_item(request, source)
		conversion = frappe.get_doc("Stock to Asset Conversion", conversions[-1].name)
		conversion.check_permission("read")
		if conversion.docstatus == 1:
			frappe.throw(
				_("Conversion {0} is approved. Choose its ready Assets with Decide release.").format(
					frappe.utils.get_link_to_form(conversion.doctype, conversion.name)
				)
			)
		return conversion.as_dict()
	warehouse = warehouse or source.get("from_warehouse") or request.get("set_from_warehouse")
	if not warehouse or not frappe.db.exists(
		"Warehouse",
		{
			"name": warehouse,
			"company": request.company,
			"is_group": 0,
		},
	):
		frappe.throw(_("Choose a source Warehouse in this Company."))
	qty = _release_qty(source)
	if qty <= 0 or qty != int(qty):
		frappe.throw(_("Conversion needs a positive whole number of stock units."))
	require_convertible_item(source.item_code, source.idx)
	location = _conversion_location(request.company, warehouse, asset_location)
	serials = _checked_serials(source.item_code, warehouse, qty, serial_nos)
	locations = serial_locations(serials, warehouse)
	if locations and (len(locations) != 1 or sum(locations.values()) != len(serials)):
		frappe.throw(_("Choose Serial Numbers from the same Storage Location for this conversion."))
	source_location = next(iter(locations), None)
	mapped, created = _asset_item_code(source), False
	if not mapped:
		result = prepare_asset_item(request, source, asset_item_code, asset_category)
		if result:
			mapped, created = result["item"], result["created"]
	item = _mapped_asset_item(request, source, code=mapped)
	target = item.name
	frappe.db.set_value(
		"Material Request Item",
		source.name,
		{
			"demo_requested_item": source.get("demo_requested_item") or source.item_code,
			"demo_acquisition_method": "Convert Stock",
			"from_warehouse": warehouse,
		},
		update_modified=False,
	)
	comment = _("Row {0}: Stock conversion selected; no stock has moved.").format(source.idx)
	if created:
		comment += " " + _("Asset Item {0} created from {1}.").format(target, source.item_code)
	request.add_comment("Comment", comment)
	refresh_pending(request.name)
	conversion = frappe.new_doc("Stock to Asset Conversion")
	conversion.update(get_conversion_defaults(request.company, request.name))
	conversion.material_request = request.name
	if location:
		conversion.asset_location = location
	child = conversion.append(
		"stock_items",
		{
			"item_code": source.item_code,
			"asset_item_code": target,
			"qty": qty,
			"stock_qty": qty,
			"stock_uom": frappe.get_cached_value("Item", source.item_code, "stock_uom"),
			"warehouse": warehouse,
			"storage_location": source_location,
			"material_request_item": source.name,
		},
	)
	if serials:
		bundle = _outward_bundle(conversion, child, source.item_code, warehouse, serials)
		if child is not None:
			child.serial_and_batch_bundle = bundle
	return conversion.as_dict()


def default_asset_location(company, warehouse=None):
	"""Prefer the warehouse's Asset Location, then a configured location for the same company."""
	if warehouse:
		own = frappe.db.get_value("Warehouse", warehouse, "asset_location")
		if own:
			return own
	for named, location in asset_location_defaults().items():
		if frappe.db.get_value("Warehouse", named, "company") != company:
			continue
		stored = frappe.db.get_value("Warehouse", named, "asset_location") or location
		if frappe.db.exists("Location", stored):
			return stored
	return None


def _conversion_location(company, warehouse, typed):
	if typed:
		if not frappe.db.exists("Location", typed):
			frappe.throw(_("Location {0} does not exist.").format(typed))
		return typed
	return default_asset_location(company, warehouse)


def _split_serials(serial_nos):
	if isinstance(serial_nos, str):
		serial_nos = frappe.parse_json(serial_nos) if serial_nos.strip().startswith("[") else serial_nos
	if isinstance(serial_nos, str):
		serial_nos = serial_nos.replace(",", "\n").splitlines()
	return [str(serial).strip() for serial in serial_nos or [] if str(serial or "").strip()]


def require_convertible_item(item_code, idx):
	"""ERPNext converts only a serial Item valued serial by serial (``StockToAssetConversion.before_submit``): refuse
	any other row now, before review and approval lock it."""
	item = frappe.get_cached_value(
		"Item", item_code, ["has_serial_no", "use_serial_no_wise_valuation"], as_dict=True
	)
	if not item or not item.has_serial_no or not item.use_serial_no_wise_valuation:
		frappe.throw(
			_(
				"Row {0}: {1} cannot be converted to an Asset: only an Item with Serial Numbers and serial-wise "
				"valuation can be. Lend it as Stock or choose another Item."
			).format(idx, item_code)
		)


def _serials_of_open_conversions(item_code, exclude_conversion=None):
	"""Serial Numbers already chosen on a Stock to Asset Conversion not yet submitted (any other request): the
	submit of one of them would consume the unit first."""
	filters = {"item_code": item_code, "docstatus": 0, "serial_and_batch_bundle": ("is", "set")}
	rows = frappe.get_all(
		"Stock to Asset Conversion Item",
		filters=filters,
		fields=["parent", "serial_and_batch_bundle"],
	)
	rows = [row for row in rows if row.parent != exclude_conversion]
	if not rows:
		return set()
	state_field, rejected_states = workflow_state_rules("Stock to Asset Conversion", "release_conversion_reservation")
	rejected = set()
	if rejected_states and frappe.db.has_column("Stock to Asset Conversion", state_field):
		rejected = set(
			frappe.get_all(
				"Stock to Asset Conversion",
				filters={
					"name": ("in", sorted({row.parent for row in rows})),
					"docstatus": 0,
					state_field: ("in", sorted(rejected_states)),
				},
				pluck="name",
			)
		)
	bundles = [row.serial_and_batch_bundle for row in rows if row.parent not in rejected]
	if not bundles:
		return set()
	return set(
		frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": ("in", bundles), "is_cancelled": 0, "serial_no": ("is", "set")},
			pluck="serial_no",
		)
	)


def _available_serials(item_code, warehouse, exclude_conversion=None):
	"""Serial Numbers free in the warehouse: ERPNext's available list (it drops Stock Reservation and POS
	reservations) less the units another open conversion already chose."""
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
		get_available_serial_nos,
	)

	has_batch_no = cint(frappe.get_cached_value("Item", item_code, "has_batch_no"))
	available = get_available_serial_nos(
		frappe._dict(item_code=item_code, warehouse=warehouse, has_serial_no=1, has_batch_no=has_batch_no)
	)
	taken = _serials_of_open_conversions(item_code, exclude_conversion)
	rows = [
		frappe._dict(serial_no=row.get("serial_no"), batch_no=row.get("batch_no"))
		for row in available
		if row.get("serial_no") and row.get("serial_no") not in taken
	]
	return sorted(rows, key=lambda row: row.serial_no)


def _checked_serials(item_code, warehouse, qty, serial_nos):
	"""The Serial Numbers picked in the dialog: none (picked later on the row), or one Active one in the source
	warehouse for every unit."""
	serials = _split_serials(serial_nos)
	if not serials:
		return []
	if len(set(serials)) != len(serials):
		frappe.throw(_("A Serial Number is chosen twice."))
	if len(serials) != int(qty):
		frappe.throw(_("Choose {0} Serial Number(s), one for every unit.").format(int(qty)))
	available = {row.serial_no for row in _available_serials(item_code, warehouse)}
	missing = [serial for serial in serials if serial not in available]
	if missing:
		frappe.throw(
			_("Serial Number(s) {0} are not available in {1}.").format(", ".join(missing), warehouse)
		)
	return serials


def _reusable_bundle(item_code, warehouse, serials):
	"""A draft Outward bundle of a conversion that was never saved (an earlier press, a discarded form) with the same
	units, so pressing again does not leave one more orphan draft."""
	candidates = frappe.get_all(
		"Serial and Batch Bundle",
		filters={
			"voucher_type": "Stock to Asset Conversion",
			"docstatus": 0,
			"item_code": item_code,
			"warehouse": warehouse,
			"type_of_transaction": "Outward",
			"voucher_no": ("is", "not set"),
		},
		pluck="name",
	)
	linked = set(
		frappe.get_all(
			"Stock to Asset Conversion Item",
			filters={"serial_and_batch_bundle": ("in", candidates or [""])},
			pluck="serial_and_batch_bundle",
		)
	)
	wanted = sorted(serials)
	for name in candidates:
		if name in linked:
			continue
		entries = frappe.get_all("Serial and Batch Entry", filters={"parent": name}, pluck="serial_no")
		if sorted(entries) == wanted:
			return name
	return None


def _outward_bundle(conversion, child, item_code, warehouse, serials):
	"""The Outward Serial and Batch Bundle of the row, made the way the row's Select Serial / Batch button makes it;
	each serial carries its own batch."""
	reused = _reusable_bundle(item_code, warehouse, serials)
	if reused:
		return reused
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
		create_serial_batch_no_ledgers,
	)

	batches = {row.serial_no: row.batch_no for row in _available_serials(item_code, warehouse)}
	entries = [{"serial_no": serial, "batch_no": batches.get(serial), "qty": 1} for serial in serials]
	row = frappe._dict(
		doctype="Stock to Asset Conversion Item",
		parenttype="Stock to Asset Conversion",
		item_code=item_code,
		warehouse=warehouse,
		type_of_transaction="Outward",
	)
	parent = {
		"doctype": "Stock to Asset Conversion",
		"company": conversion.get("company"),
		"posting_date": conversion.get("posting_date"),
		"posting_time": conversion.get("posting_time"),
	}
	return create_serial_batch_no_ledgers(entries, row, parent, warehouse).name


@frappe.whitelist()
def conversion_choices(material_request, row, warehouse=None):
	"""For the Prepare Conversion dialog: the default Asset Location and the Serial Numbers free in the warehouse."""
	request = frappe.get_doc("Material Request", material_request)
	_check_stock_team(request)
	source = next((item for item in request.items if item.name == row), None)
	if not source:
		frappe.throw(_("Choose a Stock Item row from this request."))
	warehouse = warehouse or source.get("from_warehouse") or request.get("set_from_warehouse")
	item = frappe.get_cached_value("Item", source.item_code, ["has_serial_no", "has_batch_no"], as_dict=True)
	return {
		"asset_location": default_asset_location(request.company, warehouse),
		"has_serial_no": cint(item and item.has_serial_no),
		"has_batch_no": cint(item and item.has_batch_no),
		"serial_nos": _available_serials(source.item_code, warehouse)
		if warehouse and item and item.has_serial_no
		else [],
		"qty": _release_qty(source),
	}


def customer_site(request):
	"""The Location the Assets go to at the recipient: the one on the request, else the Location named like the
	recipient (made when missing); None for an Employee (the Asset is assigned to the person)."""
	if request.get("loan_recipient_type") == "Employee":
		return None
	name = request.get("loan_recipient_location") or request.get("customer_location")
	if name:
		return name
	recipient = request.get("loan_recipient") or request.get("customer")
	if not recipient:
		return None
	kind = request.get("loan_recipient_type") or "Customer"
	label = {"Customer": "customer_name", "Supplier": "supplier_name"}.get(kind)
	title = (frappe.db.get_value(kind, recipient, label) if label else None) or recipient
	if frappe.db.exists("Location", title):
		return title
	location = frappe.get_doc({"doctype": "Location", "location_name": title, "is_group": 0})
	location.insert(ignore_permissions=True)
	return location.name


def _usable_asset(request, row, name, taken):
	"""(asset, method) of an Asset picked for a row, or a plain message why it cannot be lent."""
	from erpnext.assets.inspection_hold import is_held, require_not_held

	from .demo_reservations import _asset_ready

	asset = frappe.get_doc("Asset", name) if name and frappe.db.exists("Asset", name) else None
	if not asset:
		frappe.throw(_("Row {0}: choose an Asset for every unit.").format(row.idx))
	if name in taken:
		frappe.throw(_("Row {0}: {1} is chosen twice.").format(row.idx, name))
	target = _asset_item_code(row)
	if asset.docstatus != 1 or asset.company != request.company or asset.item_code != target:
		frappe.throw(
			_("Row {0}: {1} is not an Asset of {2} in this company.").format(row.idx, name, row.item_code)
		)
	if row.get("demo_acquisition_method") == "Convert Stock" and (
		asset.get("material_request") != request.name
		or not asset.get("stock_to_asset_conversion_item")
		or frappe.db.get_value(
			"Stock to Asset Conversion Item", asset.stock_to_asset_conversion_item, "material_request_item"
		)
		!= row.name
	):
		frappe.throw(_("Choose an Asset produced by this row's approved conversion."))
	if asset.status in {"Draft", "Cancelled", "Scrapped", "Sold", "Capitalized"}:
		frappe.throw(_("Row {0}: {1} cannot be lent ({2}).").format(row.idx, name, _(asset.status)))
	# an asset received back and waiting for its inspection is never lent now (Siwat, 9 Nov 2026); a Demo may wait for it
	held = is_held(asset)
	if not held and _asset_ready(asset) and not request.has_active_asset_request(name):
		return asset, READY
	if held and request.material_request_type != "Customer Demo":
		require_not_held(asset, _("Row {0}: {1}").format(row.idx, name))
	waiting = request.material_request_type == "Customer Demo" and (
		held
		or asset.get("demo_loan_status") == "With Customer"
		or asset.get("custodian")
		or asset.status in {"In Maintenance", "Out of Order", "Work In Progress"}
	)
	reserved = asset.get("demo_reserved_request")
	# the loan that holds the Asset now is no obstacle to waiting for it; another request already waiting for it is
	if waiting and not (reserved and reserved != request.name) and not _waited_for_by_another(request, name):
		return asset, WAIT
	frappe.throw(_("Row {0}: {1} is not free to lend now.").format(row.idx, name))


def _waited_for_by_another(request, asset):
	"""Another submitted Demo / Borrow request already lists the Asset and has no loan for it yet."""
	return bool(
		frappe.db.sql(
			"""select mr.name from `tabMaterial Request` mr
			join `tabMaterial Request Item` item on item.parent = mr.name
			where item.asset = %s and mr.name != %s and mr.docstatus = 1
			and mr.material_request_type in ('Customer Demo', 'Borrow', 'Customer Borrow')
			and coalesce(mr.request_progress_status, '') not in ('Returned', 'Closed', 'Cancelled')
			and not exists (select 1 from `tabBorrowing` loan
				where loan.material_request = mr.name and loan.docstatus = 1)
			limit 1""",
			(asset, request.name),
		)
	)


def _total_after(request, new_values):
	"""The request value if the new Asset values replace the rows being decided."""
	total = 0
	for row in request.get("items"):
		total += flt(new_values[row.name]) if row.name in new_values else flt(row.amount)
	return total


def _check_ceiling(request, total):
	for validator in frappe.get_hooks("borrowing_demo_release_validators"):
		frappe.call(validator, request=request, total=total)


def _row_fields(asset, method, site, rate):
	return {
		"demo_acquisition_method": method,
		"item_code": asset.item_code,
		"asset": asset.name,
		"asset_name": asset.asset_name,
		"asset_serial_number": asset.get("asset_serial_number"),
		"source_location": asset.location,
		"target_location": site,
		"item_type": "Fixed Asset",
		"qty": 1,
		"stock_qty": 1,
		"conversion_factor": 1,
		"rate": rate,
		"amount": rate,
		"price_list_rate": rate,
		**ASSET_ROW_CLEARS,
	}


def _book_value(asset):
	value = asset.get("value_after_depreciation")
	return flt(value if value is not None else asset.get("net_purchase_amount"))


def _add_row(request, source, values):
	"""A copy of ``source`` on the submitted request, with ``values`` (the request rows are not changed any other way)."""
	data = {key: value for key, value in source.as_dict().items() if key not in COPY_SKIP}
	data.update(values)
	child = frappe.get_doc(
		{
			"doctype": "Material Request Item",
			"parent": request.name,
			"parenttype": "Material Request",
			"parentfield": "items",
			**data,
		}
	)
	child.idx = max([cint(row.idx) for row in request.items] + [0]) + 1
	child.docstatus = 1
	child.db_insert()
	request.append("items", child)
	return child


@frappe.whitelist(methods=["POST"])
def decide_release(material_request, decisions, reason=None):
	"""Decide the rows still undecided in one go (``decisions``: [{"row": name, "assets": [Asset, ...]}], one Asset for
	each unit). A row already decided may be changed too, with a ``reason``. Loan preparation is a separate action."""
	from . import demo_reservations as demo

	decisions = frappe.parse_json(decisions) if isinstance(decisions, str) else decisions
	demo._require_configuration()
	demo._native().lock_material_request(material_request)
	request = frappe.get_doc("Material Request", material_request, for_update=True)
	_check_stock_team(request)
	return _decide(request, decisions, reason)


def _decide(request, decisions, reason=None, decided_by=None):
	"""The body of ``decide_release`` once the request is locked and the caller may decide it."""
	from . import demo_reservations as demo

	if request.docstatus != 1 or request.material_request_type not in LOAN_TYPES:
		frappe.throw(_("Use an approved Demo or Borrow request."))
	if frappe.db.exists("Borrowing", {"material_request": request.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("The borrowing is already made. Return or cancel it before releasing differently."))
	by_name = {row.name: row for row in request.items}
	taken = {row.asset for row in request.items if row.asset}
	plan = []
	for decision in decisions or []:
		row = by_name.get(decision.get("row"))
		assets = [name for name in decision.get("assets") or [] if name]
		if not row or (
			row.item_type == STOCK_ITEM and row.get("demo_acquisition_method") not in ("Convert Stock", READY)
		):
			frappe.throw(_("Choose Assets for Fixed Asset Items or a requested Stock conversion."))
		if row.get("demo_acquisition_method") == "Convert Stock" and any(
			conversion.docstatus != 1 for conversion in _row_conversions(request, row)
		):
			frappe.throw(_("Finish or cancel the pending conversion before choosing its Assets."))
		decided = bool(row.get("asset") or row.get("demo_acquisition_method") == WAIT)
		if decided:
			if assets == [row.get("asset")]:
				continue
			if not str(reason or "").strip():
				frappe.throw(_("Row {0} was decided before: write why it changes.").format(row.idx))
			taken.discard(row.get("asset"))
		units = _release_qty(row)
		if units != int(units) or len(assets) != int(units):
			frappe.throw(
				_("Row {0} needs {1} Asset(s): choose one for every unit.").format(row.idx, flt(units))
			)
		picked = []
		for name in assets:
			demo._native().lock_asset(name)
			picked.append(_usable_asset(request, row, name, taken))
			taken.add(name)
		plan.append((row, picked, decided))
	if not plan:
		frappe.throw(_("Choose the Assets to lend."))
	site = customer_site(request)
	values = {}
	for row, picked, _decided in plan:
		for index, (asset, _method) in enumerate(picked):
			values[row.name if index == 0 else f"{row.name}#{index}"] = _book_value(asset)
	_check_ceiling(request, _total_after_split(request, plan, values))
	log = []
	for row, picked, _decided in plan:
		item = frappe.get_cached_doc("Item", picked[0][0].item_code)
		base = {
			"demo_requested_item": row.get("demo_requested_item") or row.item_code,
			"item_name": item.item_name,
			"uom": item.stock_uom,
			"stock_uom": item.stock_uom,
		}
		for index, (asset, method) in enumerate(picked):
			fields = {
				**base,
				**_row_fields(asset, method, site, values[row.name if index == 0 else f"{row.name}#{index}"]),
			}
			if index == 0:
				frappe.db.set_value("Material Request Item", row.name, fields, update_modified=False)
			else:
				_add_row(request, row, fields)
			log.append(f"{row.item_code}: {asset.name} ({_(method)})")
	request.reload()
	demo.sync_reservations(request)
	request.add_comment(
		"Comment",
		_("Release decided by {0}: {1}.{2}").format(
			decided_by or frappe.session.user, "; ".join(log), f" {_('Reason')}: {reason}" if reason else ""
		),
	)
	pending = refresh_pending(request.name)
	return {"pending": pending, "loans": []}


def refresh_pending(name):
	"""Store whether rows are still to decide (the Handoff Rule "Decide release" reads it) and bring the task in step.
	Returns the numbers of the rows still to decide."""
	request = frappe.get_doc("Material Request", name)
	pending = [row.idx for row in rows_to_decide(request)]
	frappe.db.set_value("Material Request", name, PENDING_FIELD, int(bool(pending)), update_modified=False)
	sync_request_tasks(frappe.get_doc("Material Request", name))
	return pending


def adopt_converted_assets(doc, method=None):
	"""On submit of a Stock to Asset Conversion made from a Demo / Borrow request (FL05 round 2, 17 Nov 2026): the
	request row it converted takes the Assets it made, as Decide release would, so the request offers its asset
	Borrowing next. The Borrowing is not made here: the Stock team creates it from the request. When the Assets do not
	match the row (another conversion of the row is still open, or the counts differ) the row is left for Decide
	release."""
	from . import demo_reservations as demo

	if not doc.get("material_request") or doc.get("borrowing"):
		return
	if (
		frappe.db.get_value("Material Request", doc.material_request, "material_request_type")
		not in LOAN_TYPES
	):
		return
	rows = {
		row.material_request_item for row in doc.get("stock_items") or [] if row.get("material_request_item")
	}
	if not rows:
		return
	demo._native().lock_material_request(doc.material_request)
	request = frappe.get_doc("Material Request", doc.material_request, for_update=True)
	decisions = []
	for row in request.get("items") or []:
		if row.name not in rows or row.get("asset") or row.get("demo_acquisition_method") != "Convert Stock":
			continue
		conversions = _row_conversions(request, row)
		if not conversions or any(conversion.docstatus != 1 for conversion in conversions):
			continue
		assets = frappe.get_all(
			"Asset",
			filters={
				"material_request": request.name,
				"stock_to_asset_conversion_item": ["in", [c.source_item_row for c in conversions]],
				"docstatus": 1,
			},
			pluck="name",
			order_by="name",
		)
		if len(assets) == _release_qty(row):
			decisions.append({"row": row.name, "assets": assets})
	if not decisions:
		return
	savepoint = "adopt_converted_assets"
	frappe.db.savepoint(savepoint)
	try:
		_decide(request, decisions, decided_by=_("Conversion {0}").format(doc.name))
	except frappe.ValidationError:
		frappe.db.rollback(save_point=savepoint)
		frappe.clear_last_message()
		frappe.get_doc("Material Request", doc.material_request).add_comment(
			"Comment",
			_("Conversion {0} is submitted; choose its Assets with Decide release.").format(doc.name),
		)


def _total_after_split(request, plan, values):
	"""The request value once the planned rows are decided (a split row's units each take their Asset's value)."""
	new = {
		row.name: sum(v for key, v in values.items() if key.split("#")[0] == row.name) for row, _p, _d in plan
	}
	return _total_after(request, new)


@frappe.whitelist(methods=["POST"])
def issue_loan(material_request, loan_type=None):
	"""Open a loan for review without saving or submitting it."""
	from erpnext.borrowing.doctype.borrowing.borrowing_core import make_borrowing_from_request

	request = frappe.get_doc("Material Request", material_request)
	_check_stock_team(request)
	if request.docstatus != 1 or request.material_request_type not in LOAN_TYPES:
		frappe.throw(_("Use an approved Demo or Borrow request."))
	if rows_to_decide(request):
		frappe.throw(_("Choose the Assets before preparing the borrowings."))
	if loan_type not in ("Stock", "Asset"):
		frappe.throw(_("Choose the Stock or Asset borrowing to create."))
	return make_borrowing_from_request(request, loan_type, save=False)
