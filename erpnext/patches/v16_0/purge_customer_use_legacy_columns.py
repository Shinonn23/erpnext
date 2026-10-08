import frappe
from frappe import _

from erpnext.patches.v16_0.remove_customer_use_request_doctypes import (
	LEGACY_LINK_FIELDS,
	assert_no_legacy_data,
	quote_identifier,
)

LEGACY_PROGRESS_FIELDS = (
	("customer_use_status", "request_progress_status"),
	("customer_use_remarks", "request_remarks"),
)


def execute():
	assert_no_legacy_data()
	assert_request_progress_was_preserved()

	columns_to_drop = [*LEGACY_LINK_FIELDS]
	columns_to_drop.extend(("Material Request", old_fieldname) for old_fieldname, _ in LEGACY_PROGRESS_FIELDS)

	for doctype, fieldname in columns_to_drop:
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, fieldname):
			continue

		table_name = quote_identifier("tab" + doctype)
		column_name = quote_identifier(fieldname)
		frappe.db.sql_ddl(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")

	frappe.clear_cache()


def assert_request_progress_was_preserved():
	if not frappe.db.table_exists("Material Request"):
		return

	table_name = quote_identifier("tabMaterial Request")
	for old_fieldname, new_fieldname in LEGACY_PROGRESS_FIELDS:
		if not frappe.db.has_column("Material Request", old_fieldname):
			continue

		old_column = quote_identifier(old_fieldname)
		if not frappe.db.has_column("Material Request", new_fieldname):
			new_values_missing = frappe.db.sql(
				f"SELECT name FROM {table_name} WHERE COALESCE({old_column}, '') != '' LIMIT 5",
				pluck=True,
			)
		else:
			new_column = quote_identifier(new_fieldname)
			new_values_missing = frappe.db.sql(
				f"SELECT name FROM {table_name} "
				f"WHERE COALESCE({old_column}, '') != '' AND COALESCE({new_column}, '') = '' LIMIT 5",
				pluck=True,
			)

		if new_values_missing:
			frappe.throw(
				_("Cannot remove legacy Material Request field {0}; values are missing from {1}: {2}").format(
					old_fieldname, new_fieldname, ", ".join(new_values_missing)
				)
			)
