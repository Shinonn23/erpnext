# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe

# Stock is held by moving it to a holding Warehouse, so the Stock Control mode and its DocTypes are gone.
DOCTYPES = ("Stock Freeze", "Location Type")


def execute():
	for doctype in DOCTYPES:
		for dependent, field in (
			("Custom DocPerm", "parent"),
			("Property Setter", "doc_type"),
			("Custom Field", "dt"),
			("Custom Field", "options"),
		):
			for name in frappe.get_all(dependent, filters={field: doctype}, pluck="name"):
				frappe.delete_doc(
					dependent, name, force=True, ignore_permissions=True, delete_permanently=True
				)
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc(
				"DocType", doctype, force=True, ignore_permissions=True, delete_permanently=True
			)
	frappe.clear_cache()
