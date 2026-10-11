# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _

from erpnext.stock.report.storage_location_utils import add_location_column, split_bin_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = [
		{"label": _(label), "fieldname": field, "fieldtype": fieldtype, "options": options, "width": 150}
		for label, field, fieldtype, options in (
			("Item", "item_code", "Link", "Item"),
			("Item Name", "item_name", "Data", None),
			("Item Group", "item_group", "Link", "Item Group"),
			("Brand", "brand", "Link", "Brand"),
			("Description", "description", "Data", None),
			("Warehouse", "warehouse", "Link", "Warehouse"),
			("Balance Qty", "actual_qty", "Float", None),
		)
	]
	item, bin = frappe.qb.DocType("Item"), frappe.qb.DocType("Bin")
	query = (
		frappe.qb.from_(item)
		.left_join(bin)
		.on(item.name == bin.item_code)
		.select(
			item.name.as_("item_code"),
			item.item_name,
			item.item_group,
			item.brand,
			item.description,
			bin.warehouse,
			bin.actual_qty,
		)
	)
	rows = query.run(as_dict=True)
	return add_location_column(columns, filters), split_bin_rows(rows, filters)
