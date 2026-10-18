# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe

from erpnext.stock.doctype.storage_location.storage_location import ensure_default_storage_location


def execute():
	"""Give every active leaf Warehouse without a default the STAGING location."""
	for name in frappe.get_all(
		"Warehouse",
		filters={"is_group": 0, "disabled": 0, "default_storage_location": ["is", "not set"]},
		pluck="name",
	):
		ensure_default_storage_location(frappe.get_doc("Warehouse", name))
