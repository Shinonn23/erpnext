"""Remove the DocTypes Borrowing Return and Borrowing Return Item (UX WP4, Siwat 18 Nov 2026).

A borrowing now chains like selling: the goods come back on a return Delivery Note made from the issue Delivery Note,
an Asset on a return Asset Movement made from the issue movement. Schema only: a site that still holds Borrowing
Return records keeps the two DocTypes until the Connect Diagnostics seed step that settles those records
(``e2e/data_access/borrowing_return_chain``) has linked their receiving documents to the Borrowing and deleted them; it
then calls ``drop_borrowing_return_doctypes`` again.
"""

import frappe

DOCTYPES = ("Borrowing Return Item", "Borrowing Return")


def has_records():
	return any(
		frappe.db.table_exists(doctype) and frappe.db.sql(f"select name from `tab{doctype}` limit 1")
		for doctype in DOCTYPES
	)


def drop_borrowing_return_doctypes():
	"""Delete the two DocTypes when no record is left; returns whether they are gone."""
	if has_records():
		return False
	for doctype in DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True, ignore_missing=True)
		frappe.db.sql_ddl(f"drop table if exists `tab{doctype}`")
	for doctype, filters in (
		("Property Setter", {"doc_type": ["in", DOCTYPES]}),
		("Custom Field", {"dt": ["in", DOCTYPES]}),
		("Custom DocPerm", {"parent": ["in", DOCTYPES]}),
	):
		for name in frappe.get_all(doctype, filters=filters, pluck="name"):
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	return True


def execute():
	drop_borrowing_return_doctypes()
