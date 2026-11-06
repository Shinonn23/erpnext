"""Remove the Item "Product Group" and the Supplier "Old Supplier Code" fields (Siwat's UX review, 6 Nov 2026).

Neither field is read by any code or report. Schema only: the fields left the DocType JSON; this drops their
leftover columns, and only while no row holds a value (a value is never discarded). Idempotent.
"""

import frappe

COLUMNS = (("Item", "product_group"), ("Supplier", "old_supplier_code"))


def execute():
	for doctype, fieldname in COLUMNS:
		frappe.db.delete("Property Setter", {"doc_type": doctype, "field_name": fieldname})
		frappe.db.delete("Custom Field", {"dt": doctype, "fieldname": fieldname})
		if not frappe.db.has_column(doctype, fieldname):
			continue
		if frappe.db.count(doctype, {fieldname: ["is", "set"]}):
			frappe.log_error(title="Unused field kept", message=f"{doctype}.{fieldname} still holds values")
			continue
		frappe.db.sql_ddl(f"alter table `tab{doctype}` drop column `{fieldname}`")
	frappe.clear_cache()
