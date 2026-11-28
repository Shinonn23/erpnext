"""Transfer the existing QI Team DocTypes without recreating their tables."""

import json
from pathlib import Path

import frappe

DOCTYPE_ROOT = Path(__file__).resolve().parents[2] / "quality_management" / "doctype"
OLD_MODULE = "Connect Diagnostics Setup"
DOCS = ("quality_inspection_team", "quality_inspection_team_member")


def execute():
	conversions = []
	for folder in DOCS:
		definition = json.loads((DOCTYPE_ROOT / folder / f"{folder}.json").read_text(encoding="utf-8"))
		name = definition["name"]
		current = frappe.db.get_value("DocType", name, ["name", "custom", "istable", "module"], as_dict=True)
		if not current:
			continue
		if not current.custom:
			if current.module == definition["module"]:
				continue
			if current.module != OLD_MODULE:
				frappe.throw(f"Cannot adopt {name}: it belongs to another module ({current.module}).")
		fields = frappe.get_all(
			"DocField", filters={"parent": name}, fields=["fieldname", "fieldtype", "options"]
		)
		actual = {row.fieldname: (row.fieldtype, row.options or "") for row in fields}
		expected = {
			row["fieldname"]: (row["fieldtype"], row.get("options") or "") for row in definition["fields"]
		}
		if bool(current.istable) != bool(definition.get("istable")) or actual != expected:
			frappe.throw(
				f"Cannot adopt custom {name}: its fields or child-table setting differ from the app. "
				"Inspect and reconcile the existing DocType before migration."
			)
		conversions.append((name, definition["module"]))
	for name, module in conversions:
		frappe.db.set_value("DocType", name, {"custom": 0, "module": module}, update_modified=False)
		frappe.clear_cache(doctype=name)
