# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _

from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.report.storage_location_utils import add_location_column


def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns()
	columns.append(
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 150,
		}
	)
	add_location_column(columns, filters)
	columns.append(
		{"label": _("Stock Qty Breakdown"), "fieldname": "location_qty", "fieldtype": "Float", "width": 150}
	)
	data = add_location_breakdown(get_data(filters), filters)

	return columns, data


def get_columns() -> list[dict]:
	columns = [
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 200,
		},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{"label": _("Batch"), "fieldname": "batch", "fieldtype": "Link", "options": "Batch", "width": 200},
		{"label": _("Batch Qty"), "fieldname": "batch_qty", "fieldtype": "Float", "width": 150},
		{"label": _("Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "width": 150},
		{"label": _("Difference"), "fieldname": "difference", "fieldtype": "Float", "width": 150},
	]

	return columns


def get_data(filters=None):
	filters = filters or {}

	item = filters.get("item")
	batch_no = filters.get("batch")

	batch_sle_data = (
		get_batch_qty(
			item_code=item,
			batch_no=batch_no,
			for_stock_levels=True,
			consider_negative_batches=True,
			ignore_reserved_stock=True,
		)
		or []
	)

	stock_qty_map = {}
	for row in batch_sle_data:
		batch = row.get("batch_no")
		if not batch:
			continue
		stock_qty_map[batch] = stock_qty_map.get(batch, 0) + (row.get("qty") or 0)

	batch = frappe.qb.DocType("Batch")

	query = (
		frappe.qb.from_(batch)
		.select(batch.name, batch.item, batch.item_name, batch.batch_qty)
		.where(batch.disabled == 0)
	)

	if item:
		query = query.where(batch.item == item)
	if batch_no:
		query = query.where(batch.name == batch_no)

	batch_records = query.run(as_dict=True) or []

	result = []
	for row in batch_records:
		name = row.get("name")
		batch_qty = row.get("batch_qty") or 0
		stock_qty = stock_qty_map.get(name, 0)
		difference = stock_qty - batch_qty

		if difference != 0:
			result.append(
				{
					"item_code": row.get("item"),
					"item_name": row.get("item_name"),
					"batch": name,
					"batch_qty": batch_qty,
					"stock_qty": stock_qty,
					"difference": difference,
				}
			)

	return result


@frappe.whitelist()
def update_batch_qty(selected_batches=None):
	frappe.has_permission("Batch", "write", throw=True, ignore_share_permissions=True)
	if not selected_batches:
		return

	selected_batches = json.loads(selected_batches)
	for row in selected_batches:
		batch_name = row.get("batch")

		batches = get_batch_qty(
			batch_no=batch_name,
			item_code=row.get("item_code"),
			for_stock_levels=True,
			consider_negative_batches=True,
			ignore_reserved_stock=True,
		)
		batch_qty = 0.0
		if batches:
			for batch in batches:
				batch_qty += batch.get("qty")

		frappe.db.set_value("Batch", batch_name, "batch_qty", batch_qty)

	frappe.msgprint(_("Batch Qty updated successfully"), alert=True)


def add_location_breakdown(data, filters):
	from frappe.utils import today

	from erpnext.stock.report.available_batch_report.available_batch_report import (
		get_batchwise_data_from_serial_batch_bundle,
		get_batchwise_data_from_stock_ledger,
	)

	report_filters = frappe._dict(filters.copy())
	report_filters.update(
		to_date=today(),
		include_expired_batches=1,
		item_code=filters.get("item"),
		batch_no=filters.get("batch"),
	)
	balances = get_batchwise_data_from_stock_ledger(report_filters)
	get_batchwise_data_from_serial_batch_bundle(balances, report_filters)
	rows = []
	for row in data:
		row = frappe._dict(row)
		row.indent = 0
		rows.append(row)
		for balance in balances.values():
			if balance.batch_no == row.batch:
				rows.append(
					frappe._dict(
						item_code=row.item_code,
						item_name=row.item_name,
						batch=row.batch,
						warehouse=balance.warehouse,
						storage_location=balance.get("storage_location"),
						location_qty=balance.balance_qty,
						indent=1,
					)
				)
	return rows
