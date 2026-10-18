# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Item UOM pickers and saved rows only take a UOM the Item has a conversion factor for.
	frappe.db.set_single_value("Stock Settings", "allow_uom_with_conversion_rate_defined_in_item", 1)
