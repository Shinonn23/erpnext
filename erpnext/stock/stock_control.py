# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import cint

CONTROL_ROLES = {"Stock Manager", "Quality Manager", "System Manager"}
OPERATIONS = (
	"sales",
	"demo",
	"loan",
	"issue",
	"manufacture",
	"disposal",
	"reconciliation",
	"internal_transfer",
)
QA_CATEGORIES = {"QA", "Quarantine"}


def stock_control_permission_field(doctype, operation):
	"""Warehouse transfer retains its published field; Location uses its native field."""
	return (
		"allow_transfer"
		if doctype in ("Warehouse", "Warehouse Type") and operation == "internal_transfer"
		else "allow_" + operation
	)


def require_stock_control_role():
	if not CONTROL_ROLES.intersection(frappe.get_roles()):
		frappe.throw(
			_("Only a Stock Manager, Quality Manager or System Manager may change stock control."),
			frappe.PermissionError,
		)


def is_stock_control_enabled(company):
	return bool(company and cint(_current_doc("Company", company).get("enable_stock_control")))


def lock_stock_control_items(item_codes, asset_names=()):
	for doctype, names in (("Item", item_codes), ("Asset", asset_names)):
		for name in sorted(set(filter(None, names))):
			frappe.db.get_value(doctype, name, "name", for_update=True)


def _current_doc(doctype, name, exclusive=False):
	if exclusive:
		values = frappe.db.get_value(doctype, name, "*", as_dict=True, for_update=True)
	else:
		if doctype not in (
			"Company",
			"Item",
			"Asset",
			"Warehouse",
			"Location",
			"Warehouse Type",
			"Location Type",
		):
			frappe.throw(_("Unsupported stock control lock."))
		rows = frappe.db.multisql(
			{
				"mariadb": f"SELECT * FROM `tab{doctype}` WHERE name=%s LOCK IN SHARE MODE",
				"postgres": f'SELECT * FROM "tab{doctype}" WHERE name=%s FOR SHARE',
			},
			(name,),
			as_dict=True,
		)
		values = rows[0] if rows else None
	if not values:
		frappe.throw(_("{0} {1} does not exist.").format(doctype, name))
	return frappe._dict(values)


def lock_stock_control_policies(warehouse_names=(), location_names=(), exclusive=False):
	# Assignment rows precede type rows so incoming and outgoing movements share the same order.
	types = set()
	for doctype, names, field, type_doctype in (
		("Warehouse", warehouse_names, "warehouse_type", "Warehouse Type"),
		("Location", location_names, "location_type", "Location Type"),
	):
		for name in sorted(set(filter(None, names))):
			row = _current_doc(doctype, name, exclusive=exclusive)
			if row.get(field):
				types.add((type_doctype, row.get(field)))
	for doctype, name in sorted(types):
		_current_doc(doctype, name, exclusive=exclusive)


def get_asset_stock_control_items(asset_doc):
	items = {asset_doc.get("item_code")}
	if asset_doc.get("stock_to_asset_conversion_item"):
		items.add(
			frappe.db.get_value(
				"Stock to Asset Conversion Item", asset_doc.stock_to_asset_conversion_item, "item_code"
			)
		)
	for field, doctype, item_field in (
		("source_serial_no", "Serial No", "item_code"),
		("source_batch_no", "Batch", "item"),
	):
		if asset_doc.get(field):
			items.add(frappe.db.get_value(doctype, asset_doc.get(field), item_field))
	return sorted(filter(None, items))


def get_active_stock_freezes(
	company, item_code, batch_nos=(), serial_nos=(), asset=None, source_item_codes=()
):
	# FOR UPDATE is a current read, including when the transaction already has a repeatable-read snapshot.
	batch_nos = set(filter(None, batch_nos))
	for serial in sorted(set(filter(None, serial_nos))):
		batch = frappe.db.get_value("Serial No", serial, "batch_no", for_update=True)
		if batch:
			batch_nos.add(batch)
	targets = (
		("Item", "item_code", tuple(sorted(set(filter(None, (item_code, *source_item_codes)))))),
		("Batch", "batch_no", tuple(sorted(batch_nos))),
		("Serial No", "serial_no", tuple(sorted(set(filter(None, serial_nos))))),
		("Asset", "asset", (asset,) if asset else ()),
	)
	rows = []
	for level, field, names in targets:
		if names:
			rows.extend(
				frappe.db.get_values(
					"Stock Freeze",
					{"company": company, "status": "Active", "freeze_level": level, field: ["in", names]},
					["name", "freeze_level", "item_code", "batch_no", "serial_no", "asset", "reason"],
					as_dict=True,
					for_update=True,
				)
			)
	return rows


def get_stock_control_policy(doctype, name, require_type=True):
	if doctype not in ("Warehouse", "Location"):
		frappe.throw(_("Stock control supports Warehouse and Location only."))
	row = _current_doc(doctype, name)
	field = "warehouse_type" if doctype == "Warehouse" else "location_type"
	if not row.get(field):
		if not require_type:
			return None
		frappe.throw(_("{0} {1} must have a Type before stock can be issued.").format(doctype, name))
	return _current_doc("Warehouse Type" if doctype == "Warehouse" else "Location Type", row.get(field))


def validate_stock_control_source(
	company,
	doctype,
	name,
	operation,
	item_code=None,
	batch_nos=(),
	serial_nos=(),
	asset=None,
	source_item_codes=(),
	target=None,
	release_reason=None,
):
	if not is_stock_control_enabled(company):
		return
	if operation not in OPERATIONS:
		frappe.throw(_("Unknown stock control operation: {0}").format(operation))
	if operation == "internal_transfer" and not target:
		frappe.throw(_("Internal Transfer requires a target Warehouse or Location."))
	if not name:
		frappe.throw(_("A source {0} is required for {1}.").format(doctype, operation))
	lock_stock_control_policies(
		**(
			{"warehouse_names": (name, target)}
			if doctype == "Warehouse"
			else {"location_names": (name, target)}
		)
	)
	source_row = _current_doc(doctype, name)
	if doctype == "Warehouse" and (source_row.is_group or source_row.disabled):
		frappe.throw(_("Stock cannot be issued from a disabled or group Warehouse."))
	if doctype == "Warehouse" and source_row.company != company:
		frappe.throw(_("Source Warehouse must belong to the transaction Company."))
	policy = get_stock_control_policy(doctype, name)
	if not cint(policy.get(stock_control_permission_field(doctype, operation))):
		frappe.throw(_("{0} {1} does not allow {2}.").format(doctype, name, operation))
	holds = get_active_stock_freezes(company, item_code, batch_nos, serial_nos, asset, source_item_codes)
	target_policy = None
	if operation == "internal_transfer" and target:
		target_row = _current_doc(doctype, target)
		if doctype == "Warehouse" and target_row.company != company:
			frappe.throw(_("Internal Transfer requires Warehouses in the same Company."))
		target_policy = get_stock_control_policy(doctype, target)
		if not cint(target_policy.get(stock_control_permission_field(doctype, "internal_transfer"))):
			frappe.throw(_("Target {0} {1} does not allow Internal Transfer.").format(doctype, target))
	if holds and not (
		operation == "internal_transfer"
		and target_policy
		and target_policy.handling_category in QA_CATEGORIES
	):
		frappe.throw(
			_("Stock is frozen: {0}").format("; ".join(f"{row.name}: {row.reason}" for row in holds))
		)
	if (
		policy.handling_category in QA_CATEGORIES
		and target_policy
		and target_policy.handling_category == "Normal"
	):
		require_stock_control_role()
		if not release_reason or not release_reason.strip():
			frappe.throw(_("A QA Release Reason is required to move stock out of QA or Quarantine."))


def validate_asset_operation(asset, operation, target_location=None, release_reason=None, lock=True):
	name = asset if isinstance(asset, str) else asset.name
	live = frappe.db.get_value("Asset", name, "*", as_dict=True)
	if not live:
		frappe.throw(_("Asset {0} does not exist.").format(name))
	if not is_stock_control_enabled(live.company):
		return
	items = get_asset_stock_control_items(live)
	source_location = live.location
	lock_stock_control_policies(location_names=(source_location, target_location))
	if lock:
		lock_stock_control_items(items, (name,))
	live = _current_doc("Asset", name)
	if live.location != source_location or get_asset_stock_control_items(live) != items:
		frappe.throw(
			_(
				"Asset location or stock provenance changed during validation. Reload and retry the transaction."
			)
		)
	validate_stock_control_source(
		live.company,
		"Location",
		live.location,
		operation,
		live.item_code,
		batch_nos=(live.source_batch_no,) if live.get("source_batch_no") else (),
		serial_nos=(live.source_serial_no,) if live.get("source_serial_no") else (),
		asset=name,
		source_item_codes=get_asset_stock_control_items(live),
		target=target_location,
		release_reason=release_reason,
	)


def _has_stock(warehouse):
	return bool(
		frappe.db.get_values(
			"Bin", {"warehouse": warehouse, "actual_qty": ["!=", 0]}, "name", for_update=True
		)
	)


def _has_assets(location):
	return bool(
		frappe.db.get_values(
			"Asset",
			{"location": location, "docstatus": 1, "status": ["not in", ["Sold", "Scrapped", "Capitalized"]]},
			"name",
			for_update=True,
		)
	)


def lock_stock_control_assignment_types(doc):
	field, type_doctype = (
		("warehouse_type", "Warehouse Type")
		if doc.doctype == "Warehouse"
		else ("location_type", "Location Type")
	)
	old_type = frappe.db.get_value(doc.doctype, doc.name, field) if not doc.is_new() else None
	# Native _save will lock the mapping row; acquire Type shares first to match Type edits.
	for name in sorted(set(filter(None, (old_type, doc.get(field))))):
		_current_doc(type_doctype, name)


def validate_stock_control_assignment(doc):
	if (
		doc.doctype == "Warehouse"
		and doc.company
		and not doc.is_group
		and not doc.disabled
		and not doc.get("warehouse_type")
	):
		if is_stock_control_enabled(doc.company):
			frappe.throw(_("An enabled Warehouse must have a Warehouse Type."))
	field = "warehouse_type" if doc.doctype == "Warehouse" else "location_type"
	old = doc.get_doc_before_save()
	if (old and old.get(field) == doc.get(field)) or (not old and not doc.get(field)):
		return
	require_stock_control_role()
	if old and not (doc.get("stock_control_change_reason") or "").strip():
		frappe.throw(_("Stock Control Change Reason is required when changing Type."))
	if old:
		_current_doc(doc.doctype, doc.name, exclusive=True)
		old_type = old.get(field)
		if old_type:
			type_doctype = "Warehouse Type" if doc.doctype == "Warehouse" else "Location Type"
			old_category = _current_doc(type_doctype, old_type).handling_category
			new_category = (
				_current_doc(type_doctype, doc.get(field)).handling_category if doc.get(field) else None
			)
			if old_category in QA_CATEGORIES and new_category not in QA_CATEGORIES:
				if _has_stock(doc.name) if doc.doctype == "Warehouse" else _has_assets(doc.name):
					frappe.throw(
						_(
							"Cannot remove QA or Quarantine classification while stock or active Assets remain."
						)
					)
	if doc.doctype == "Location" and old and not doc.get(field):
		companies = frappe.db.get_values(
			"Asset",
			{"location": doc.name, "docstatus": 1, "status": ["not in", ["Sold", "Scrapped", "Capitalized"]]},
			"company",
			pluck=True,
			for_update=True,
		)
		if any(is_stock_control_enabled(company) for company in set(companies)):
			frappe.throw(
				_(
					"A Location containing active Assets in a stock-controlled Company must have a Location Type."
				)
			)
	if doc.get(field):
		_current_doc("Warehouse Type" if doc.doctype == "Warehouse" else "Location Type", doc.get(field))


def validate_stock_control_type(doc):
	fields = (
		"handling_category",
		*(
			stock_control_permission_field(doc.doctype, operation)
			for operation in OPERATIONS
			if doc.doctype == "Warehouse Type" or operation != "reconciliation"
		),
		*(("allow_receipt", "freeze_stock", "outgoing_role") if doc.doctype == "Warehouse Type" else ()),
	)
	old = doc.get_doc_before_save()
	changed = not old or any(old.get(field) != doc.get(field) for field in fields)
	if not changed:
		return
	require_stock_control_role()
	if old and not (doc.get("policy_change_reason") or "").strip():
		frappe.throw(_("Policy Change Reason is required when changing stock control permissions."))
	if doc.handling_category not in ("Normal", "QA", "Quarantine"):
		frappe.throw(_("Invalid Handling Category."))
	if doc.handling_category in QA_CATEGORIES and any(
		cint(doc.get(stock_control_permission_field(doc.doctype, op)))
		for op in OPERATIONS
		if op != "internal_transfer"
	):
		frappe.throw(_("QA and Quarantine Types may only allow Internal Transfer."))
	if old:
		doctype, field = (
			("Warehouse", "warehouse_type")
			if doc.doctype == "Warehouse Type"
			else ("Location", "location_type")
		)
		current_type = _current_doc(doc.doctype, doc.name, exclusive=True)
		# The Type lock blocks policy readers; shared current reads also catch new assignments.
		assignments = frappe.db.multisql(
			{
				"mariadb": f"SELECT name FROM `tab{doctype}` WHERE `{field}`=%s ORDER BY name LOCK IN SHARE MODE",
				"postgres": f'SELECT name FROM "tab{doctype}" WHERE "{field}"=%s ORDER BY name FOR SHARE',
			},
			(doc.name,),
			as_dict=True,
		)
		names = [row.name for row in assignments]
		if current_type.handling_category in QA_CATEGORIES and doc.handling_category not in QA_CATEGORIES:
			if any((_has_stock(name) if doctype == "Warehouse" else _has_assets(name)) for name in names):
				frappe.throw(_("Cannot reclassify QA or Quarantine while stock or active Assets remain."))


def validate_company_stock_control(doc):
	old = doc.get_doc_before_save()
	if old and cint(old.get("enable_stock_control")) == cint(doc.get("enable_stock_control")):
		return
	if not old and not cint(doc.get("enable_stock_control")):
		return
	require_stock_control_role()
	if not cint(doc.get("enable_stock_control")):
		return
	missing = frappe.get_all(
		"Warehouse",
		filters={"company": doc.name, "is_group": 0, "disabled": 0, "warehouse_type": ["is", "not set"]},
		pluck="name",
	)
	assets = frappe.get_all(
		"Asset",
		filters={
			"company": doc.name,
			"docstatus": 1,
			"status": ["not in", ["Sold", "Scrapped", "Capitalized"]],
		},
		fields=["name", "location"],
	)
	locations = {row.location for row in assets if row.location}
	missing_locations = [
		name for name in sorted(locations) if not frappe.db.get_value("Location", name, "location_type")
	]
	missing_assets = [row.name for row in assets if not row.location]
	if missing or missing_locations or missing_assets:
		frappe.throw(
			_(
				"Assign Types to all enabled leaf Warehouses and active Asset Locations before enabling Stock Control. Warehouses: {0}; Locations: {1}; Assets without Location: {2}"
			).format(", ".join(missing), ", ".join(missing_locations), ", ".join(missing_assets))
		)
