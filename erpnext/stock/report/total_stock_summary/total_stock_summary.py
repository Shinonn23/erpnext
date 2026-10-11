# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import flt

from erpnext.stock.report.storage_location_utils import show_location_wise_stock, split_bin_rows


def execute(filters=None):
	if not filters:
		filters = {}
	columns = get_columns(filters)
	stock = get_total_stock(filters)

	return columns, stock


def get_columns(filters):
	columns = [
		_("Item") + ":Link/Item:150",
		_("Description") + "::300",
		_("Current Qty") + ":Float:100",
	]

	if filters.get("group_by") == "Warehouse" or show_location_wise_stock(filters):
		columns.insert(0, _("Warehouse") + ":Link/Warehouse:150")
	else:
		columns.insert(0, _("Company") + ":Link/Company:250")

	if show_location_wise_stock(filters):
		columns.append(_("Storage Location") + ":Link/Storage Location:150")
	return columns


def get_total_stock(filters):
	bin = frappe.qb.DocType("Bin")
	item = frappe.qb.DocType("Item")
	wh = frappe.qb.DocType("Warehouse")
	query = (
		frappe.qb.from_(bin)
		.inner_join(item)
		.on(bin.item_code == item.name)
		.inner_join(wh)
		.on(wh.name == bin.warehouse)
		.select(bin.item_code, item.description, bin.warehouse, wh.company, bin.actual_qty)
	)
	if filters.get("company"):
		query = query.where(wh.company == filters.get("company"))
	location_wise = show_location_wise_stock(filters)
	warehouse_wise = location_wise or filters.get("group_by") == "Warehouse"
	totals = {}
	for row in split_bin_rows(query.run(as_dict=True), filters):
		key = (row.warehouse if warehouse_wise else row.company, row.item_code, row.description)
		if location_wise:
			key += (row.get("storage_location"),)
		totals[key] = totals.get(key, 0) + flt(row.actual_qty)
	return [
		list(key[:3]) + [qty] + (list(key[3:]) if location_wise else []) for key, qty in totals.items() if qty
	]
