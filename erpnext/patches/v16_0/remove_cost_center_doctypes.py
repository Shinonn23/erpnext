# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe


def execute():
	remove_cost_center_accounting_dimension()
	remove_cost_center_accounting_dimension_filters()
	remove_cost_center_custom_fields()
	remove_cost_center_property_setters()
	remove_cost_center_doc_types()
	frappe.clear_cache()


def remove_cost_center_accounting_dimension():
	name = frappe.db.get_value("Accounting Dimension", {"document_type": "Cost Center"})
	if name:
		frappe.delete_doc(
			"Accounting Dimension",
			name,
			force=True,
			ignore_permissions=True,
			ignore_on_trash=True,
			delete_permanently=True,
		)


def remove_cost_center_accounting_dimension_filters():
	for name in frappe.get_all(
		"Accounting Dimension Filter", filters={"accounting_dimension": "Cost Center"}, pluck="name"
	):
		frappe.delete_doc(
			"Accounting Dimension Filter",
			name,
			force=True,
			ignore_permissions=True,
			delete_permanently=True,
		)


def remove_cost_center_custom_fields():
	for name in frappe.get_all("Custom Field", filters={"options": "Cost Center"}, pluck="name"):
		frappe.delete_doc(
			"Custom Field",
			name,
			force=True,
			ignore_permissions=True,
			delete_permanently=True,
		)


def remove_cost_center_property_setters():
	for name in frappe.get_all(
		"Property Setter", filters={"field_name": ["like", "%cost_center%"]}, pluck="name"
	):
		frappe.delete_doc(
			"Property Setter",
			name,
			force=True,
			ignore_permissions=True,
			delete_permanently=True,
		)


def remove_cost_center_doc_types():
	for doctype in (
		"Employee Cost Center",
		"PSoA Cost Center",
		"Distributed Cost Center",
		"Cost Center Allocation Percentage",
		"Cost Center Allocation",
		"Cost Center",
	):
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc(
				"DocType",
				doctype,
				force=True,
				ignore_permissions=True,
				delete_permanently=True,
			)
