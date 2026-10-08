# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe


REMOVED_DOCTYPES = (
	"Employee Cost Center",
	"PSoA Cost Center",
	"Distributed Cost Center",
	"Cost Center Allocation Percentage",
	"Cost Center Allocation",
	"Cost Center",
)


def execute():
	for doctype in REMOVED_DOCTYPES:
		drop_doctype_table(doctype)

	drop_cost_center_columns()
	frappe.clear_cache()


def drop_doctype_table(doctype):
	expected_table_name = f"tab{doctype}"
	table_name = next(
		(
			table
			for table in frappe.db.get_tables(cached=False)
			if table.casefold() == expected_table_name.casefold()
		),
		None,
	)
	if not table_name:
		return

	frappe.db.sql_ddl(f"DROP TABLE {quote_identifier(table_name)}")


def drop_cost_center_columns():
	removed_tables = {f"tab{doctype}".casefold() for doctype in REMOVED_DOCTYPES}
	for table_name in frappe.db.get_tables(cached=False):
		if table_name.casefold() in removed_tables:
			continue

		for column_name in frappe.db.get_db_table_columns(table_name):
			if "cost_center" not in column_name.lower():
				continue

			frappe.db.sql_ddl(
				f"ALTER TABLE {quote_identifier(table_name)} DROP COLUMN {quote_identifier(column_name)}"
			)


def quote_identifier(identifier):
	if frappe.db.db_type == "postgres":
		return f'"{identifier.replace(chr(34), chr(34) * 2)}"'

	return f"`{identifier.replace('`', '``')}`"
