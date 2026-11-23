import frappe

FIELDS = ("lost_warehouse", "found_warehouse", "quarantine_warehouse", "damage_warehouse")


def execute():
	for field in FIELDS:
		if frappe.db.exists("DocField", {"parent": "Warehouse", "fieldname": field}):
			frappe.throw(f"Reload Warehouse before dropping {field}.")
	frappe.db.delete("Property Setter", {"doc_type": "Warehouse", "field_name": ["in", FIELDS]})
	for field in frappe.get_all(
		"Custom Field", filters={"dt": "Warehouse", "fieldname": ["in", FIELDS]}, pluck="name"
	):
		frappe.delete_doc("Custom Field", field, ignore_permissions=True)
	columns = [field for field in FIELDS if frappe.db.has_column("Warehouse", field)]
	if columns:
		frappe.db.sql_ddl(
			"ALTER TABLE tabWarehouse " + ", ".join(f"DROP COLUMN {field}" for field in columns)
		)
	frappe.clear_cache(doctype="Warehouse")
