# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import sync_budget_dimensions


def execute():
	# Budget Against now also offers Department, Territory and Sales Person
	sync_budget_dimensions()
