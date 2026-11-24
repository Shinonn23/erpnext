"""Retire former party IDs and group-level naming series."""

import frappe


def execute():
	for doctype, fieldname in (
		("Customer", "former_id"),
		("Supplier", "former_id"),
		("Customer Group", "naming_series"),
		("Supplier Group", "naming_series"),
	):
		frappe.db.delete("Property Setter", {"doc_type": doctype, "field_name": fieldname})
		frappe.db.delete("Custom Field", {"dt": doctype, "fieldname": fieldname})
		if frappe.db.has_column(doctype, fieldname):
			frappe.db.sql_ddl(f"alter table `tab{doctype}` drop column `{fieldname}`")
		frappe.clear_cache(doctype=doctype)
