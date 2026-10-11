# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _

from erpnext.stock.report.storage_location_utils import add_location_column, split_bin_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{"label": _(label), "fieldname": field, "fieldtype": kind, "options": options, "width": 140}
		for label, field, kind, options in (
			("Item", "item_code", "Link", "Item"),
			("Warehouse", "warehouse", "Link", "Warehouse"),
			("Actual", "actual_qty", "Float", None),
			("Requested", "indented_qty", "Float", None),
			("Reserved", "reserved_qty", "Float", None),
			("Ordered", "ordered_qty", "Float", None),
			("Projected", "projected_qty", "Float", None),
		)
	]
	rows = frappe.get_all(
		"Bin",
		fields=[
			"item_code",
			"warehouse",
			"actual_qty",
			"indented_qty",
			"reserved_qty",
			"ordered_qty",
			"projected_qty",
		],
		filters={"projected_qty": ["<", 0]},
		order_by="projected_qty",
	)
	rows = [row for row in split_bin_rows(rows, filters) if row.projected_qty < 0]
	return add_location_column(columns, filters), rows
