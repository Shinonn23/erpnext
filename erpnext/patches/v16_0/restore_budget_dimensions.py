# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import sync_budget_dimensions


def remove_project_custom_field():
	# Project is now a standard field. Keep its database column and stored values.
	frappe.db.delete("Custom Field", {"dt": "Budget", "fieldname": "project"})
	frappe.clear_cache(doctype="Budget")


def execute():
	sync_budget_dimensions()
