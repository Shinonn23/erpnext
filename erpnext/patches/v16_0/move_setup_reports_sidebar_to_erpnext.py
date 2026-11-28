"""Adopt native report links in the site sidebar without disturbing local arrangements."""

import frappe
from frappe.desk.doctype.custom_sidebar.custom_sidebar import ADDED_ITEM_FIELDS, SITE_LAYER

from erpnext.patches.v16_0.move_setup_reports_to_erpnext import REPORT_MODULES


def execute():
	if not frappe.db.table_exists("Custom Sidebar"):
		return
	for report, native_module in REPORT_MODULES.items():
		if not frappe.db.exists("Report", {"name": report, "module": native_module, "is_standard": "Yes"}):
			continue
		sidebar = frappe.db.get_value("Sidebar", {"module": native_module, "standard": 1}, "name")
		if not sidebar or not frappe.db.exists(
			"Sidebar Item",
			{"parent": sidebar, "parenttype": "Sidebar", "link_type": "Report", "link_to": report},
		):
			continue

		legacy_module = "Stock" if native_module == "Borrowing" else native_module
		layer = frappe.db.get_value("Custom Sidebar", {"module": legacy_module, "user": SITE_LAYER}, "name")
		if not layer:
			continue

		fields = (*ADDED_ITEM_FIELDS, "navigate_to_tab")
		filters = {
			"parent": layer,
			"parenttype": "Custom Sidebar",
			"type": "Link",
			"link_type": "Report",
			"link_to": report,
			"added": 1,
			"hidden": 0,
		}
		original = {
			"type": "Link",
			"label": report,
			"link_type": "Report",
			"link_to": report,
			"child": 1,
			"collapsible": 1,
			"open_in_new_tab": 1,
		}
		for row in frappe.get_all("Sidebar Item", filters=filters, fields=["name", *fields]):
			if any((row.get(field) or None) != original.get(field) for field in fields):
				continue
			if legacy_module == native_module:
				frappe.db.set_value(
					"Sidebar Item", row.name, {"added": 0, "label": None}, update_modified=False
				)
			else:
				frappe.db.delete("Sidebar Item", {"name": row.name})
