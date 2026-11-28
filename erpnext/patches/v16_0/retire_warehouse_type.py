"""Retire the Warehouse Type master after its two native links have been converted."""

import frappe

from erpnext.stock.warehouse_policy import CONTROLLED_OUTGOING_WAREHOUSE_TYPES, WAREHOUSE_TYPE_OPERATIONS

DOCTYPE = "Warehouse Type"
TYPE_FIELDS = {"Warehouse": "warehouse_type", "Stock Entry Detail": "t_warehouse_type"}
LEGACY_TYPES = {"Group": "", "Loan": "Borrowed"}


def execute():
	for doctype, fieldname in TYPE_FIELDS.items():
		frappe.db.delete(
			"Property Setter",
			{
				"doc_type": doctype,
				"field_name": fieldname,
				"property": ["in", ("fieldtype", "options")],
			},
		)
		frappe.clear_cache(doctype=doctype)

	for doctype in ("DocField", "Custom Field"):
		if frappe.db.exists(doctype, {"options": DOCTYPE, "fieldtype": ["in", ("Link", "Table", "Table MultiSelect")]}):
			frappe.throw("Remove the remaining fields linked to Warehouse Type before retiring it.")
	if frappe.db.exists("Property Setter", {"property": "options", "value": DOCTYPE}):
		frappe.throw("Remove the remaining Warehouse Type link overrides before retiring it.")

	check_existing_policy()
	for doctype, fieldname in TYPE_FIELDS.items():
		for old, new in LEGACY_TYPES.items():
			frappe.db.set_value(doctype, {fieldname: old}, fieldname, new, update_modified=False)

	frappe.db.delete("Property Setter", {"doc_type": DOCTYPE})
	frappe.db.delete("Custom DocPerm", {"parent": DOCTYPE})
	for name in frappe.get_all("Custom Field", filters={"dt": DOCTYPE}, pluck="name"):
		frappe.delete_doc("Custom Field", name, ignore_permissions=True)
	if frappe.db.exists("DocType", DOCTYPE):
		frappe.delete_doc("DocType", DOCTYPE, force=True, ignore_permissions=True)
	if frappe.db.table_exists(DOCTYPE):
		frappe.db.sql_ddl("DROP TABLE `tabWarehouse Type`")
	frappe.clear_cache(doctype=DOCTYPE)


def check_existing_policy():
	if frappe.db.exists("User Permission", {"allow": DOCTYPE}):
		frappe.throw("Replace Warehouse Type User Permissions with Warehouse User Permissions before retiring it.")
	active_types = {
		kind
		for kind in frappe.get_all(
			"Warehouse", filters={"disabled": 0, "is_group": 0}, pluck="warehouse_type", distinct=True
		)
		if kind
	}
	unsupported = active_types - WAREHOUSE_TYPE_OPERATIONS.keys() - {"Loan"}
	if unsupported:
		frappe.throw(f"Set a supported type on active Warehouses using {', '.join(sorted(unsupported))}.")

	if frappe.db.table_exists(DOCTYPE) and frappe.db.has_column(DOCTYPE, "outgoing_role"):
		old_roles = dict(frappe.db.sql("SELECT name, outgoing_role FROM `tabWarehouse Type`"))
		different = [
			kind
			for kind in sorted(active_types & old_roles.keys())
			if (old_roles[kind] or None)
			!= (
				"Stock Manager"
				if LEGACY_TYPES.get(kind, kind) in CONTROLLED_OUTGOING_WAREHOUSE_TYPES
				else None
			)
		]
		if different:
			frappe.throw(f"Review the existing Outgoing Role for {', '.join(different)} before retiring Warehouse Type.")
