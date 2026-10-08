import frappe


def execute():
	if not frappe.db.exists("DocType", "Inventory Dimension"):
		return

	for dimension_name in frappe.get_all("Inventory Dimension", pluck="name"):
		frappe.get_doc("Inventory Dimension", dimension_name).add_custom_fields()

	for doctype in ("Stock Count Item", "Stock Entry Detail", "Stock Reconciliation Item"):
		frappe.clear_cache(doctype=doctype)
