"""Remove the unused Customer field "Old Customer Code" (Siwat's UX review, 6 Nov 2026).

Schema only: the DocField leaves customer.json; this drops its now unused column, and only when no Customer holds
a value there (a column with data is kept). Idempotent and safe when the column is already gone.
"""

import frappe


def execute():
	if not frappe.db.has_column("Customer", "old_customer_code"):
		return
	if frappe.db.sql("select 1 from `tabCustomer` where ifnull(old_customer_code, '') != '' limit 1"):
		return
	frappe.db.sql_ddl("alter table `tabCustomer` drop column old_customer_code")
	frappe.clear_cache(doctype="Customer")
