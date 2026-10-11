# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

from copy import deepcopy

import frappe
from frappe import _
from frappe.utils import cint, flt


def show_location_wise_stock(filters):
	return cint((filters or {}).get("show_location_wise_stock", 1)) == 1


def location_column():
	return {
		"label": _("Storage Location"),
		"fieldname": "storage_location",
		"fieldtype": "Link",
		"options": "Storage Location",
		"width": 150,
	}


def add_location_column(columns, filters):
	if show_location_wise_stock(filters):
		columns.append(location_column())
	return columns


def location_key(row, filters):
	return (row.get("storage_location") or "") if show_location_wise_stock(filters) else ""


def apply_location_query(query, table, filters, group_by=False):
	if show_location_wise_stock(filters):
		query = query.select(table.storage_location)
		if group_by:
			query = query.groupby(table.storage_location)
	if location := (filters or {}).get("storage_location"):
		query = query.where(
			table.storage_location.isin(location if isinstance(location, list) else [location])
		)
	return query


def get_location_balances(item_codes, warehouses=None):
	"""Current on-hand stock belongs to ledger dimensions, rather than warehouse Bin rows."""
	if not item_codes:
		return {}
	from frappe.utils import today

	from erpnext.stock.report.stock_balance.stock_balance import execute as stock_balance

	report_filters = frappe._dict(
		item_code=item_codes,
		warehouse=[warehouse for warehouse in warehouses or [] if warehouse],
		from_date=today(),
		# Bin includes all posted entries, including future-dated transactions.
		to_date="9999-12-31",
		show_location_wise_stock=1,
		include_zero_stock_items=1,
		ignore_closing_balance=1,
	)
	_columns, rows = stock_balance(report_filters)
	balances = {}
	for row in rows:
		row.actual_qty, row.stock_value = row.bal_qty, row.bal_val
		balances.setdefault((row.item_code, row.warehouse), []).append(row)

	return balances


def split_bin_rows(rows, filters):
	"""Split actual stock; leave warehouse-only planning quantities on the unassigned row.

	No allocation of a warehouse's planned demand or supply to locations can be inferred
	from Bin. Keeping that remainder unassigned preserves every warehouse total.
	"""
	if not show_location_wise_stock(filters):
		return rows
	balances = get_location_balances(list({r.item_code for r in rows}), list({r.warehouse for r in rows}))
	result = []
	planning_fields = (
		"planned_qty",
		"indented_qty",
		"ordered_qty",
		"reserved_qty",
		"reserved_qty_for_production",
		"reserved_qty_for_production_plan",
		"reserved_qty_for_sub_contract",
		"reserved_stock",
		"reserved_qty_for_pos",
	)
	for row in rows:
		locations = {r.storage_location or "": r for r in balances.get((row.item_code, row.warehouse), [])}
		if any(flt(row.get(field)) for field in planning_fields) or not locations:
			locations.setdefault("", frappe._dict(actual_qty=0, stock_value=0))
		for location, balance in locations.items():
			new_row = deepcopy(row)
			new_row.storage_location = location or None
			new_row.actual_qty = flt(balance.actual_qty)
			if location:
				for field in planning_fields:
					if field in new_row:
						new_row[field] = 0
			if "projected_qty" in row:
				new_row.projected_qty = new_row.actual_qty + (
					flt(row.projected_qty) - flt(row.actual_qty) if not location else 0
				)
			result.append(new_row)
	return result


def set_location_ledger_balances(data, filters):
	"""Calculate running balances per item/warehouse/location, including opening stock."""
	from frappe.utils import add_days

	from erpnext.stock.report.stock_balance.stock_balance import execute as stock_balance

	if not show_location_wise_stock(filters) and not filters.get("storage_location"):
		return
	opening_filters = frappe._dict(filters.copy())
	if isinstance(opening_filters.get("item_code"), str):
		opening_filters.item_code = [opening_filters.item_code]
	opening_filters.update(
		from_date=add_days(filters.from_date, -1),
		to_date=add_days(filters.from_date, -1),
		show_location_wise_stock=int(show_location_wise_stock(filters)),
		show_stock_ageing_data=0,
		include_zero_stock_items=1,
		include_uom=None,
		show_alt_uom_balance=0,
	)
	balances = {}
	if filters.get("batch_no"):
		from erpnext.stock.report.batch_wise_balance_history.batch_wise_balance_history import (
			get_item_warehouse_batch_map,
		)

		for item, warehouses in get_item_warehouse_batch_map(opening_filters, 6).items():
			for warehouse, batches in warehouses.items():
				for (batch, location), balance in batches.items():
					balances[(item, warehouse, location, batch)] = [balance.bal_qty, balance.bal_value]
	else:
		_opening_columns, opening = stock_balance(opening_filters)
		for row in opening:
			balances[(row.item_code, row.warehouse, location_key(row, filters), "")] = [
				row.bal_qty,
				row.bal_val,
			]
	if any(not row.get("voucher_no") for row in data):
		transactions = [row for row in data if row.get("voucher_no")]
		data[:] = [
			frappe._dict(
				item_code=_("Opening"),
				warehouse=warehouse,
				storage_location=location or None,
				batch_no=batch or None,
				qty_after_transaction=balance[0],
				stock_value=balance[1],
				valuation_rate=balance[1] / balance[0] if balance[0] else 0,
			)
			for (item, warehouse, location, batch), balance in balances.items()
			if balance[0] or balance[1]
		]
		data.extend(transactions)
	reconciliation_deltas = get_reconciliation_quantity_deltas(filters)
	for row in data:
		if not row.get("voucher_no"):
			continue
		batch = (row.get("batch_no") or filters.get("batch_no") or "") if filters.get("batch_no") else ""
		key = (row.item_code, row.warehouse, location_key(row, filters), batch)
		balance = balances.setdefault(key, [0, 0])
		if (
			row.get("voucher_type") == "Stock Reconciliation"
			and not row.get("batch_no")
			and not row.get("serial_and_batch_bundle")
			and not row.get("actual_qty")
		):
			row.actual_qty = reconciliation_deltas.get(row.get("voucher_detail_no"), 0)
			row.in_qty, row.out_qty = max(row.actual_qty, 0), min(row.actual_qty, 0)
		balance[0] += flt(row.get("actual_qty"))
		balance[1] += flt(row.get("stock_value_difference"))
		row.qty_after_transaction, row.stock_value = balance
		row.valuation_rate = balance[1] / balance[0] if balance[0] else 0


def set_bundle_storage_locations(rows, bundle_field="name"):
	"""Posted bundles take their location from the ledger, including older bundle rows."""
	bundles = list({row.get(bundle_field) for row in rows if row.get(bundle_field)})
	if not bundles:
		return rows
	locations = {
		row.serial_and_batch_bundle: row.storage_location
		for row in frappe.get_all(
			"Stock Ledger Entry",
			fields=["serial_and_batch_bundle", "storage_location"],
			filters={"serial_and_batch_bundle": ["in", bundles], "is_cancelled": 0},
		)
	}
	for row in rows:
		if row.get(bundle_field) in locations:
			row.storage_location = locations[row.get(bundle_field)]
	return rows


def execute_serial_report(filters, fieldnames):
	from erpnext.stock.report.stock_qty_vs_serial_no_count.stock_qty_vs_serial_no_count import (
		get_last_ledger_moves,
	)

	filters = frappe._dict(filters or {})
	query_filters = {
		field: filters[field]
		for field in ("item_code", "warehouse", "company", "status")
		if filters.get(field)
	}
	if filters.get("only_without_warehouse") and not filters.get("warehouse"):
		query_filters["warehouse"] = ["is", "not set"]
	fields = list(dict.fromkeys(["name", "item_code", "warehouse", *fieldnames]))
	rows = frappe.get_list(
		"Serial No", fields=fields, filters=query_filters, order_by="modified desc", limit_page_length=0
	)
	if show_location_wise_stock(filters):
		by_item = {}
		for row in rows:
			by_item.setdefault(row.item_code, []).append(row)
		for item_code, serials in by_item.items():
			moves = get_last_ledger_moves(item_code, [row.name for row in serials])
			for row in serials:
				move = moves.get(row.name)
				row.storage_location = (
					move.get("storage_location") if move and row.warehouse == move.warehouse else None
				)
	meta = frappe.get_meta("Serial No")
	columns = []
	for field in fields:
		df = meta.get_field(field)
		columns.append(
			{
				"label": _(df.label) if df else _("Serial No"),
				"fieldname": field,
				"fieldtype": df.fieldtype if df else "Link",
				"options": df.options if df else "Serial No",
				"width": 150,
			}
		)
	return add_location_column(columns, filters), rows


def get_stock_trend_data(filters, doctype):
	from erpnext.controllers.trends import get_columns, get_data

	conditions = get_columns(filters, doctype)
	conditions["stock_column_count"] = len(conditions["columns"])
	parent, child = frappe.qb.DocType(doctype), frappe.qb.DocType(f"{doctype} Item")
	start, end = frappe.get_cached_value(
		"Fiscal Year", filters.get("fiscal_year"), ["year_start_date", "year_end_date"]
	)
	query = (
		frappe.qb.from_(parent)
		.inner_join(child)
		.on(child.parent == parent.name)
		.select(child.warehouse)
		.distinct()
		.where(
			(parent.docstatus == 1)
			& (parent.company == filters.get("company"))
			& parent.posting_date.between(start, end)
		)
	)
	if show_location_wise_stock(filters):
		query = query.select(child.storage_location)
	data = []
	for scope in query.run():
		for row in get_data(filters, conditions, stock_scope=scope):
			data.append([*row, *scope])
	conditions["columns"].append(
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 150,
		}
	)
	add_location_column(conditions["columns"], filters)
	return conditions, data


def get_reconciliation_quantity_deltas(filters):
	"""Warehouse resets have no ledger actual_qty; their saved count records the change."""
	parent = frappe.qb.DocType("Stock Reconciliation")
	row = frappe.qb.DocType("Stock Reconciliation Item")
	query = (
		frappe.qb.from_(parent)
		.inner_join(row)
		.on(row.parent == parent.name)
		.select(row.name, (row.qty - row.current_qty).as_("qty_delta"))
		.where(parent.docstatus == 1)
	)
	if (filters or {}).get("company"):
		query = query.where(parent.company == filters.get("company"))
	if (filters or {}).get("to_date"):
		query = query.where(parent.posting_date <= filters.get("to_date"))
	if item_codes := (filters or {}).get("item_code"):
		query = query.where(row.item_code.isin(item_codes if isinstance(item_codes, list) else [item_codes]))
	return {row.name: flt(row.qty_delta) for row in query.run(as_dict=True)}
