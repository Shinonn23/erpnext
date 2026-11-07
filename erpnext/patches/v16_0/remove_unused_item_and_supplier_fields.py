"""Remove the Supplier "Old Supplier Code" field (Siwat's UX review, 6 Nov 2026).

The field is not read by any code or report. Schema only: it left the DocType JSON; this drops its leftover column,
and only while no row holds a value (a value is never discarded). Idempotent.

The Item "Product Group" was removed by the same review but came back on 7 Nov 2026 (Siwat: approvals and QI route by
Product Group), so it is no longer dropped here: a site where this patch already ran keeps whatever the field's
column held, and model sync re-adds the column from the DocType JSON where it was dropped.
"""

import frappe

COLUMNS = (("Supplier", "old_supplier_code"),)


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
