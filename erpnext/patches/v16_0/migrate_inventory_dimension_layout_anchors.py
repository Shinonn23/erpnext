import frappe


def execute():
	doctypes = frappe.get_hooks("inventory_dimension_doctypes")
	doctypes = list(dict.fromkeys([*doctypes, "Stock Ledger Entry"]))

	for doctype in doctypes:
		for fieldname, fieldtype in (
			("inventory_dimension", "Section Break"),
			("inventory_dimension_col_break", "Column Break"),
		):
			docfield_filters = {
				"parent": doctype,
				"fieldname": fieldname,
				"fieldtype": fieldtype,
			}
			if not frappe.db.exists("DocField", docfield_filters):
				continue

			custom_field = frappe.db.get_value(
				"Custom Field",
				{"dt": doctype, "fieldname": fieldname, "fieldtype": fieldtype},
				"name",
			)
			if custom_field:
				frappe.delete_doc("Custom Field", custom_field, force=True, ignore_missing=True)

		frappe.clear_cache(doctype=doctype)
