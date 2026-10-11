# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import Date

from erpnext.stock.report.storage_location_utils import add_location_column


def execute(filters=None):
	validate_filters(filters)

	columns = get_columns()
	columns.append(_("Warehouse") + ":Link/Warehouse:150")
	add_location_column(columns, filters)
	data = get_data(filters)

	return columns, data


def validate_filters(filters):
	if not filters:
		frappe.throw(_("Please select the required filters"))

	if not filters.get("from_date"):
		frappe.throw(_("'From Date' is required"))

	if not filters.get("to_date"):
		frappe.throw(_("'To Date' is required"))


def get_columns():
	return [
		_("Item") + ":Link/Item:150",
		_("Item Name") + "::150",
		_("Batch") + ":Link/Batch:150",
		_("Stock UOM") + ":Link/UOM:100",
		_("Quantity") + ":Float:100",
		_("Expires On") + ":Date:100",
		_("Expiry (In Days)") + ":Int:130",
	]


def get_data(filters):
	data = []

	from erpnext.stock.report.available_batch_report.available_batch_report import (
		get_batchwise_data_from_serial_batch_bundle,
		get_batchwise_data_from_stock_ledger,
	)
	from erpnext.stock.report.storage_location_utils import show_location_wise_stock

	report_filters = frappe._dict(filters.copy())
	report_filters.include_expired_batches = 1
	balances = get_batchwise_data_from_stock_ledger(report_filters)
	get_batchwise_data_from_serial_batch_bundle(balances, report_filters)
	batches = {batch.name: batch for batch in get_batch_details(filters)}
	for balance in balances.values():
		batch = batches.get(balance.batch_no)
		if not batch or not balance.balance_qty:
			continue
		row = [
			batch.item,
			batch.item_name,
			batch.name,
			batch.stock_uom,
			balance.balance_qty,
			batch.expiry_date,
			max((batch.expiry_date - frappe.utils.datetime.date.today()).days, 0)
			if batch.expiry_date
			else None,
			balance.warehouse,
		]
		if show_location_wise_stock(filters):
			row.append(balance.get("storage_location"))
		data.append(row)

	return data


def get_batch_details(filters):
	batch = frappe.qb.DocType("Batch")
	query = (
		frappe.qb.from_(batch)
		.select(
			batch.name,
			batch.creation,
			batch.expiry_date,
			batch.item,
			batch.item_name,
			batch.stock_uom,
			batch.batch_qty,
		)
		.where(
			(batch.disabled == 0)
			& (batch.batch_qty > 0)
			& ((Date(batch.creation) >= filters["from_date"]) & (Date(batch.creation) <= filters["to_date"]))
		)
		.orderby(batch.creation)
	)

	if filters.get("item"):
		query = query.where(batch.item == filters["item"])

	return query.run(as_dict=True)
