# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import STANDARD_DIMENSIONS


def execute():
	# A Department, Territory or Sales Person Accounting Dimension used to add these as Custom
	# Fields. Where they are standard fields now the Custom Field would only duplicate them.
	# This runs after the model sync, which is what puts the standard fields in place. The
	# columns and their values stay.
	custom_fields = frappe.get_all(
		"Custom Field",
		filters={"fieldname": ["in", list(STANDARD_DIMENSIONS)]},
		fields=["name", "dt", "fieldname"],
	)
	doctypes = set()
	for custom_field in custom_fields:
		if frappe.db.exists("DocField", {"parent": custom_field.dt, "fieldname": custom_field.fieldname}):
			frappe.db.delete("Custom Field", {"name": custom_field.name})
			doctypes.add(custom_field.dt)

	for doctype in doctypes:
		frappe.clear_cache(doctype=doctype)
