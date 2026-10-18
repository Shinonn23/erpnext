"""Read-only site inventory. Run with bench execute before browser fixtures."""

import json
from pathlib import Path

import frappe

UI_PROPERTIES = (
	"fieldtype",
	"options",
	"depends_on",
	"mandatory_depends_on",
	"read_only_depends_on",
	"reqd",
	"read_only",
	"hidden",
	"allow_on_submit",
	"fetch_from",
	"fetch_if_empty",
)


ROOTS = (
	"Material Request",
	"Custody Loan",
	"Custody Loan Return",
	"Custody Loan Adjustment",
	"Pick List",
	"Delivery Note",
	"Shipment",
	"Quality Inspection",
	"Asset",
	"Asset Movement",
	"Sales Invoice",
	"Stock Entry",
	"Warehouse",
	"Storage Location",
	"Location",
	"Warehouse Type",
	"Company",
	"Item",
	"Serial No",
	"Batch",
	"Serial and Batch Bundle",
	"Customer",
	"Employee",
	"Address",
	"Contact",
)


def inventory():
	"""Return facts and blockers; never change configuration, schema, or documents."""
	from erpnext.regional.thailand.constants import (
		BILLING_CUSTOM_FIELDS,
		DEPOSIT_CUSTOM_FIELDS,
		ERP_CUSTOM_FIELDS,
		ERP_PROPERTY_SETTERS,
	)

	report = {
		"site": frappe.local.site,
		"apps": frappe.get_installed_apps(),
		"allow_tests": bool(frappe.conf.allow_tests),
		"admin_password_configured": bool(frappe.conf.admin_password),
		"url": frappe.utils.get_site_url(frappe.local.site),
		"blockers": [],
		"doctypes": {},
		"custom_fields": [],
		"property_setters": [],
	}
	if set(report["apps"]) != {"frappe", "erpnext"}:
		report["blockers"].append("Installed apps must be exactly frappe and erpnext")
	if not report["allow_tests"]:
		report["blockers"].append("allow_tests is disabled")
	for doctype, name in (
		("UOM", "Nos"),
		("Item Group", "All Item Groups"),
		("Customer Group", "All Customer Groups"),
		("Territory", "All Territories"),
		("Currency", "USD"),
		("Gender", "Female"),
	):
		if not frappe.db.exists(doctype, name):
			report["blockers"].append(f"Missing native prerequisite: {doctype} {name}")
	for purpose in ("Material Receipt", "Material Issue"):
		if not frappe.db.exists("Stock Entry Type", {"purpose": purpose}):
			report["blockers"].append(f"Missing Stock Entry Type: {purpose}")
	from erpnext.accounts.utils import get_fiscal_year

	try:
		report["fiscal_year"] = get_fiscal_year(frappe.utils.today())
	except Exception as exc:
		report["blockers"].append(f"Current fiscal period unavailable: {exc}")
	known_fields = {}
	for mapping in (ERP_CUSTOM_FIELDS, BILLING_CUSTOM_FIELDS, DEPOSIT_CUSTOM_FIELDS):
		for doctypes, fields in mapping.items():
			for doctype in (doctypes,) if isinstance(doctypes, str) else doctypes:
				for field in fields:
					known_fields[(doctype, field["fieldname"])] = field
	known_setters = {}
	for doctypes, setters in ERP_PROPERTY_SETTERS.items():
		for doctype in (doctypes,) if isinstance(doctypes, str) else doctypes:
			for field, prop, value, *_rest in setters:
				known_setters[(doctype, field or None, prop)] = str(value)
	setters = frappe.get_all(
		"Property Setter", fields=["name", "doc_type", "field_name", "property", "value"]
	)
	setter_values = {(row.doc_type, row.field_name, row.property): row.value for row in setters}
	queue = list(ROOTS)
	while queue:
		dt = queue.pop(0)
		if dt in report["doctypes"]:
			continue
		if not frappe.db.exists("DocType", dt):
			report["blockers"].append(f"Missing DocType: {dt}")
			report["doctypes"][dt] = {"missing": True}
			continue
		meta = frappe.get_meta(dt)
		module = frappe.db.get_value("DocType", dt, "module")
		path = Path(frappe.get_module_path(module, "doctype", frappe.scrub(dt), frappe.scrub(dt) + ".json"))
		local = json.loads(path.read_text()) if path.exists() else None
		report["doctypes"][dt] = {"source": str(path), "conditions": [], "missing_columns": []}
		if not local or frappe.db.get_value("DocType", dt, "custom"):
			report["blockers"].append(f"DocType source cannot be attributed: {dt}")
		for field in meta.fields:
			if field.fieldtype in ("Table", "Table MultiSelect"):
				queue.append(field.options)
			conditions = {
				key: field.get(key)
				for key in (
					"depends_on",
					"mandatory_depends_on",
					"read_only_depends_on",
					"reqd",
					"read_only",
					"hidden",
				)
				if field.get(key)
			}
			if conditions:
				report["doctypes"][dt]["conditions"].append({"field": field.fieldname, **conditions})
		if local:
			for field in local["fields"]:
				actual = meta.get_field(field["fieldname"])
				if not actual or actual.fieldtype != field["fieldtype"]:
					report["blockers"].append(f"Metadata mismatch: {dt}.{field['fieldname']}")
				if actual:
					for key in UI_PROPERTIES:
						expected = setter_values.get((dt, field["fieldname"], key), field.get(key))
						if _normal(expected) != _normal(actual.get(key)):
							report["blockers"].append(f"Metadata drift: {dt}.{field['fieldname']}.{key}")
				if (
					field["fieldtype"] not in frappe.model.no_value_fields
					and not field.get("is_virtual")
					and not meta.issingle
				):
					if not frappe.db.has_column(dt, field["fieldname"]):
						report["doctypes"][dt]["missing_columns"].append(field["fieldname"])
						report["blockers"].append(f"Missing column: {dt}.{field['fieldname']}")
	for custom in frappe.get_all(
		"Custom Field",
		filters={"dt": ["in", list(report["doctypes"])]},
		fields=["name", "dt", "fieldname", *UI_PROPERTIES],
	):
		known = known_fields.get((custom.dt, custom.fieldname))
		custom["source"] = "erpnext/regional/thailand/constants.py" if known else None
		if not known or any(
			_normal(custom.get(key))
			!= _normal(setter_values.get((custom.dt, custom.fieldname, key), known.get(key)))
			for key in UI_PROPERTIES
		):
			report["blockers"].append(f"Unattributed or modified Custom Field: {custom.name}")
		report["custom_fields"].append(custom)
	for setter in frappe.get_all(
		"Property Setter",
		filters={"doc_type": ["in", list(report["doctypes"])]},
		fields=["name", "doc_type", "field_name", "property", "value"],
	):
		known = known_setters.get((setter.doc_type, setter.field_name or None, setter.property))
		setter["source"] = "erpnext/regional/thailand/constants.py" if known == setter.value else None
		# Settings-produced setters need a separate source/value audit, not a blanket exemption.
		if not setter.source:
			report["blockers"].append(f"Property Setter needs provenance check: {setter.name}")
		report["property_setters"].append(setter)
	for dt, fields in (
		("Client Script", ["name", "dt", "enabled"]),
		("Server Script", ["name", "reference_doctype", "script_type", "disabled"]),
	):
		report[frappe.scrub(dt)] = frappe.get_all(dt, fields=fields)
		for script in report[frappe.scrub(dt)]:
			active = script.enabled if dt == "Client Script" else not script.disabled
			target = script.get("dt") or script.get("reference_doctype")
			if active and (target in report["doctypes"] or not target):
				report["blockers"].append(f"Active script needs provenance check: {script.name}")
	report["workflows"] = frappe.get_all(
		"Workflow",
		filters={"is_active": 1, "document_type": ["in", list(report["doctypes"])]},
		fields=["name", "document_type"],
	)
	for workflow in report["workflows"]:
		report["blockers"].append(f"Active workflow needs provenance check: {workflow.name}")
	report["custom_permissions"] = frappe.get_all(
		"Custom DocPerm",
		filters={"parent": ["in", list(report["doctypes"])]},
		fields=["name", "parent", "role", "read", "write", "create", "submit", "cancel", "delete"],
	)
	if report["custom_permissions"]:
		report["blockers"].append("Custom DocPerm overrides need provenance check")
	report["hooks"] = {
		key: _json_keys(frappe.get_hooks(key))
		for key in (
			"doctype_js",
			"doc_events",
			"override_doctype_class",
			"extend_doctype_class",
			"override_whitelisted_methods",
			"app_include_js",
		)
	}
	return report


def _normal(value):
	return "" if value in (None, "", 0, "0") else str(value)


def _json_keys(value):
	if isinstance(value, dict):
		return {str(key): _json_keys(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [_json_keys(item) for item in value]
	return value
