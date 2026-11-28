"""Stock Age Limits (Siwat, 6 Nov 2026): which goods have stayed in stock too long and which are too close to expiry.

One row per stock lot on hand (quantity above zero) of a warehouse and Storage Location:

- a batch item: one lot for each batch. Received = the first inward Stock Ledger Entry of that batch in the company,
  in whatever warehouse (the goods came in then: a move between shelves or between warehouses, a trip to Quarantine
  and back, does not make them younger);
- a serial item without batch: each serial number on hand is dated by its first inward entry in the company;
- any other item without batch that names "Max Storage Days": the lots left by a FIFO read of the Stock Ledger of
  the warehouse (what leaves is taken from the oldest receipt first, as ERPNext's Stock Ageing does). A movement
  within one warehouse (a Storage Location transfer: out of one shelf, into another, net zero in the voucher) is
  neutral. The remaining receipts are given to the Storage Locations that hold the stock now, the location that
  first received stock getting the oldest. A transfer between warehouses starts the age again in the target
  warehouse. An item without batch and without a limit has nothing to age and is not listed.

Days in stock = as-on date - received date. Remaining shelf life = batch expiry - as-on date. The limits are Item
fields (days; 0 or empty = no limit): "Max Storage Days" (`max_storage_days`) and "Min Remaining Shelf Life"
(`min_remaining_shelf_life`). Status, in this order: Expired, Over storage limit, Below minimum shelf life, OK. A lot
can be several at once; the Status filter matches any condition the lot meets, the column shows the first.

No money column: the report shows quantities and dates only. The warehouses of a user with a warehouse scope
(`workflows/warehouse_scope.py`) are the only ones listed. Item Group User Permissions
(`workflows/item_group_scope.py`) hide the Items of hidden groups. The queries are five whole-report reads (lots, first receipts of batches, first receipts of serial
numbers, serial numbers on hand, ledger rows of non-batch items), then one read of the batches' expiry: never one query
per row.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, today

from erpnext.stock.report_policy import allowed_groups, allowed_warehouses

EXPIRED = "Expired"
OVER = "Over storage limit"
BELOW = "Below minimum shelf life"
OK = "OK"
PROBLEMS = "Problems only"
ALL = "All"
STATUS_ORDER = (EXPIRED, OVER, BELOW)
QTY_PRECISION = 2


# -- pure rules ---------------------------------------------------------------------------------------------------


def fifo_slots(movements):
	"""[(date, qty)] left after a FIFO read of ``movements`` [(date, qty)] in ledger order: an outward quantity
	takes the oldest receipts first; slots of one date are added up. An outward quantity with nothing to take is
	ignored (negative stock)."""
	queue = deque()
	for day, qty in movements:
		qty = round(flt(qty), QTY_PRECISION)
		if qty > 0:
			queue.append([day, qty])
			continue
		left = -qty
		while left > 0 and queue:
			if queue[0][1] <= left:
				left -= queue.popleft()[1]
			else:
				queue[0][1] = round(flt(queue[0][1] - left), QTY_PRECISION)
				left = 0
	merged = {}
	for day, qty in queue:
		merged[day] = round(flt(merged.get(day, 0) + qty), QTY_PRECISION)
	return sorted(merged.items())


def assess(received, as_on, max_days=0, min_shelf=0, expiry=None):
	"""Days in stock, days over the limit, remaining shelf life, the status shown and every status met."""
	as_on = getdate(as_on)
	max_days, min_shelf = cint(max_days), cint(min_shelf)
	days = date_diff(as_on, received) if received else None
	over = days - max_days if days is not None and max_days > 0 and days > max_days else 0
	remaining = date_diff(expiry, as_on) if expiry else None
	met = []
	if remaining is not None and remaining < 0:
		met.append(EXPIRED)
	if over:
		met.append(OVER)
	if min_shelf > 0 and remaining is not None and 0 <= remaining < min_shelf:
		met.append(BELOW)
	return {
		"days_in_stock": days,
		"days_over_limit": over,
		"remaining_shelf_life": remaining,
		"status": met[0] if met else OK,
		"met": met,
	}


def wanted(met, status_filter):
	"""Does a lot that meets ``met`` belong to the Status filter? Empty = problems only (the default)."""
	if not status_filter or status_filter == PROBLEMS:
		return bool(met)
	if status_filter == ALL:
		return True
	if status_filter == OK:
		return not met
	return status_filter in met


# -- data ---------------------------------------------------------------------------------------------------------


def _conditions(filters, origin=False):
	"""WHERE for the report. ``origin``: the reads that date goods by their first receipt in the company, whatever
	the warehouse, so the Warehouse filter, the Lost / Found skip and the warehouse scope do not apply."""
	conditions = ["sle.is_cancelled = 0", "sle.posting_date <= %(as_on)s"]
	values = {"as_on": getdate(filters.get("as_on_date") or today())}
	if filters.get("company"):
		conditions.append("sle.company = %(company)s")
		values["company"] = filters["company"]
	if filters.get("item_code"):
		conditions.append("sle.item_code = %(item_code)s")
		values["item_code"] = filters["item_code"]
	if filters.get("item_group"):
		bounds = frappe.db.get_value("Item Group", filters["item_group"], ["lft", "rgt"])
		if not bounds:
			frappe.throw(_("Item Group {0} does not exist.").format(filters["item_group"]))
		conditions.append(
			"i.item_group in (select name from `tabItem Group` where lft >= %(ig_lft)s and rgt <= %(ig_rgt)s)"
		)
		values["ig_lft"], values["ig_rgt"] = bounds
	groups = allowed_groups()
	if groups is not None:
		if not groups:
			conditions.append("1 = 0")
		else:
			conditions.append("i.item_group in %(visible_groups)s")
			values["visible_groups"] = tuple(sorted(groups))
	if origin:
		return " and ".join(conditions), values
	if filters.get("warehouse"):
		bounds = frappe.db.get_value("Warehouse", filters["warehouse"], ["lft", "rgt"])
		if not bounds:
			frappe.throw(_("Warehouse {0} does not exist.").format(filters["warehouse"]))
		conditions.append(
			"sle.warehouse in (select name from `tabWarehouse` where lft >= %(wh_lft)s and rgt <= %(wh_rgt)s)"
		)
		values["wh_lft"], values["wh_rgt"] = bounds
	if cint(filters.get("sellable_only", 1)):
		# Use the shared sellable Warehouse Type policy (WP10).
		from erpnext.stock.warehouse_policy import sellable_warehouse_sql

		conditions.append(sellable_warehouse_sql("w"))
	scope = allowed_warehouses()
	if scope is not None:
		if not scope:
			conditions.append("1 = 0")
		else:
			conditions.append("sle.warehouse in %(scope)s")
			values["scope"] = tuple(scope)
	return " and ".join(conditions), values


FROM = """
	from `tabStock Ledger Entry` sle
	join `tabItem` i on i.name = sle.item_code
	join `tabWarehouse` w on w.name = sle.warehouse
	left join `tabSerial and Batch Entry` sbe on sbe.parent = sle.serial_and_batch_bundle
"""
# A batch sits on the ledger row (old) or on the entries of its Serial and Batch Bundle (new).
BATCH = "coalesce(sbe.batch_no, sle.batch_no)"
QTY = "coalesce(sbe.qty, sle.actual_qty)"
# Batch lots always; a lot without batch only when its Item names a storage limit.
SCOPE = f"({BATCH} is not null or ifnull(i.max_storage_days, 0) > 0)"


def get_lots(filters):
	"""The lots on hand with the Item fields: one query."""
	where, values = _conditions(filters)
	return frappe.db.sql(
		f"""
		select sle.item_code, i.item_name, i.item_group, i.max_storage_days, i.min_remaining_shelf_life,
			sle.warehouse, ifnull(sle.storage_location, '') as storage_location, {BATCH} as batch_no,
			sum({QTY}) as qty,
			min(case when {QTY} > 0 then sle.posting_date end) as first_in
		{FROM}
		where {where} and {SCOPE}
		group by sle.item_code, sle.warehouse, ifnull(sle.storage_location, ''), {BATCH}
		having sum({QTY}) > 0
		""",
		values,
		as_dict=True,
	)


def get_batch_first_receipts(filters):
	"""{(item, batch): first inward date}: the whole company, whatever the warehouse and Storage Location."""
	where, values = _conditions(filters, origin=True)
	rows = frappe.db.sql(
		f"""
		select sle.item_code, {BATCH} as batch_no, min(sle.posting_date) as first_in
		{FROM}
		where {where} and {BATCH} is not null and {QTY} > 0
		group by sle.item_code, {BATCH}
		""",
		values,
		as_dict=True,
	)
	return {(row.item_code, row.batch_no): row.first_in for row in rows}


SERIAL = "sbe.serial_no is not null and sbe.batch_no is null"


def get_serial_stock(filters, items):
	"""{(item, warehouse, location): [serial no]} on hand, for the serial items without batch."""
	if not items:
		return {}
	where, values = _conditions(filters)
	values["serial_items"] = tuple(items)
	rows = frappe.db.sql(
		f"""
		select sle.item_code, sle.warehouse, ifnull(sle.storage_location, '') as storage_location, sbe.serial_no
		{FROM}
		where {where} and sle.item_code in %(serial_items)s and {SERIAL}
		group by sle.item_code, sle.warehouse, ifnull(sle.storage_location, ''), sbe.serial_no
		having sum(sbe.qty) > 0
		""",
		values,
		as_dict=True,
	)
	stock = defaultdict(list)
	for row in rows:
		stock[(row.item_code, row.warehouse, row.storage_location)].append(row.serial_no)
	return stock


def get_serial_first_receipts(filters, items):
	"""{(item, serial no): first inward date} in the company."""
	if not items:
		return {}
	where, values = _conditions(filters, origin=True)
	values["serial_items"] = tuple(items)
	rows = frappe.db.sql(
		f"""
		select sle.item_code, sbe.serial_no, min(sle.posting_date) as first_in
		{FROM}
		where {where} and sle.item_code in %(serial_items)s and {SERIAL} and sbe.qty > 0
		group by sle.item_code, sbe.serial_no
		""",
		values,
		as_dict=True,
	)
	return {(row.item_code, row.serial_no): row.first_in for row in rows}


def get_movements(filters, items):
	"""{(item, warehouse): [(date, qty)]} in ledger order for the items without batch that have stock.

	The quantities of one voucher, item and warehouse are added up first: a Storage Location transfer (out of one
	shelf, into another of the same warehouse) nets to zero and is left out."""
	if not items:
		return {}
	where, values = _conditions(filters)
	values["fifo_items"] = tuple(items)
	rows = frappe.db.sql(
		f"""
		select sle.item_code, sle.warehouse, sle.posting_date, sum({QTY}) as qty
		{FROM}
		where {where} and sle.item_code in %(fifo_items)s and {BATCH} is null
		group by sle.voucher_type, sle.voucher_no, sle.item_code, sle.warehouse, sle.posting_date,
			sle.posting_time
		having round(sum({QTY}), {QTY_PRECISION}) != 0
		order by sle.posting_date, sle.posting_time, min(sle.creation)
		""",
		values,
		as_dict=True,
	)
	movements = defaultdict(list)
	for row in rows:
		movements[(row.item_code, row.warehouse)].append((row.posting_date, row.qty))
	return movements


def get_expiry(batches):
	if not batches:
		return {}
	return {
		row.name: row.expiry_date
		for row in frappe.get_all(
			"Batch", filters={"name": ("in", list(batches))}, fields=["name", "expiry_date"]
		)
	}


def allocate_slots(slots, places):
	"""[(place, date, qty)]: the dated ``slots`` [(date, qty)] oldest first given to ``places`` [(place, qty)] in
	the order listed. A place left without a date (more stock than slots) gets date None."""
	slots = [list(slot) for slot in slots]
	result = []
	for place, qty in places:
		left = round(flt(qty), QTY_PRECISION)
		while left > 0:
			if not slots:
				result.append((place, None, left))
				break
			take = min(left, slots[0][1])
			result.append((place, slots[0][0], round(flt(take), QTY_PRECISION)))
			left = round(flt(left - take), QTY_PRECISION)
			slots[0][1] = round(flt(slots[0][1] - take), QTY_PRECISION)
			if slots[0][1] <= 0:
				slots.pop(0)
	return result


def dated_lots(lots, first_receipts, serial_stock, serial_receipts, movements):
	"""[(lot, received date, qty)] for every lot on hand."""
	dated = []
	fifo = defaultdict(list)
	for lot in lots:
		if lot.batch_no:
			received = first_receipts.get((lot.item_code, lot.batch_no)) or lot.first_in
			dated.append((lot, received, lot.qty))
		elif serial_stock.get((lot.item_code, lot.warehouse, lot.storage_location)):
			by_date = defaultdict(int)
			for serial in serial_stock[(lot.item_code, lot.warehouse, lot.storage_location)]:
				by_date[serial_receipts.get((lot.item_code, serial))] += 1
			dated.extend((lot, received, qty) for received, qty in sorted(by_date.items(), key=_oldest))
		else:
			fifo[(lot.item_code, lot.warehouse)].append(lot)
	for (item, warehouse), places in fifo.items():
		# the location that first received stock holds the oldest
		places.sort(key=lambda lot: (lot.first_in is None, lot.first_in, lot.storage_location))
		slots = fifo_slots(movements.get((item, warehouse), []))
		by_name = {lot.storage_location: lot for lot in places}
		for name, received, qty in allocate_slots(slots, [(lot.storage_location, lot.qty) for lot in places]):
			dated.append((by_name[name], received or by_name[name].first_in, qty))
	return dated


def _oldest(item):
	return (item[0] is None, item[0] or date.min)


def build_rows(filters):
	filters = frappe._dict(filters or {})
	as_on = getdate(filters.get("as_on_date") or today())
	lots = get_lots(filters)
	first_receipts = get_batch_first_receipts(filters)
	plain = {lot.item_code for lot in lots if not lot.batch_no}
	serial_stock = get_serial_stock(filters, plain)
	serial_items = {key[0] for key in serial_stock}
	serial_receipts = get_serial_first_receipts(filters, serial_items)
	movements = get_movements(filters, plain - serial_items)
	expiry = get_expiry({lot.batch_no for lot in lots if lot.batch_no})

	rows = []
	for lot, received, qty in dated_lots(lots, first_receipts, serial_stock, serial_receipts, movements):
		expiry_date = expiry.get(lot.batch_no) if lot.batch_no else None
		result = assess(received, as_on, lot.max_storage_days, lot.min_remaining_shelf_life, expiry_date)
		if not wanted(result["met"], filters.get("status")):
			continue
		rows.append(
			{
				"item_code": lot.item_code,
				"item_name": lot.item_name,
				"item_group": lot.item_group,
				"warehouse": lot.warehouse,
				"storage_location": lot.storage_location or None,
				"batch_no": lot.batch_no,
				"qty": round(flt(qty), QTY_PRECISION),
				"received_date": received,
				"days_in_stock": result["days_in_stock"],
				"max_storage_days": cint(lot.max_storage_days) or None,
				"days_over_limit": result["days_over_limit"] or None,
				"expiry_date": expiry_date,
				"remaining_shelf_life": result["remaining_shelf_life"],
				"min_remaining_shelf_life": cint(lot.min_remaining_shelf_life) or None,
				"status": result["status"],
			}
		)
	rows.sort(
		key=lambda r: (
			-(r["days_over_limit"] or 0),
			r["remaining_shelf_life"] if r["remaining_shelf_life"] is not None else 10**9,
			r["item_code"],
			r["warehouse"],
			r["batch_no"] or "",
		)
	)
	return rows


def get_columns():
	def col(fieldname, label, fieldtype="Data", width=110, options=None):
		column = {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": width}
		if options:
			column["options"] = options
		return column

	return [
		col("item_code", "Item", "Link", 140, "Item"),
		col("item_name", "Item Name", width=180),
		col("item_group", "Item Group", "Link", 130, "Item Group"),
		col("warehouse", "Warehouse", "Link", 170, "Warehouse"),
		col("storage_location", "Storage Location", "Link", 130, "Storage Location"),
		col("batch_no", "Batch No", "Link", 120, "Batch"),
		col("qty", "Qty on Hand", "Float", 100),
		col("received_date", "Received Date", "Date", 100),
		col("days_in_stock", "Days in Stock", "Int", 90),
		col("max_storage_days", "Max Storage Days", "Int", 100),
		col("days_over_limit", "Days Over Limit", "Int", 100),
		col("expiry_date", "Expiry Date", "Date", 100),
		col("remaining_shelf_life", "Remaining Shelf Life (days)", "Int", 120),
		col("min_remaining_shelf_life", "Minimum Remaining Shelf Life", "Int", 120),
		col("status", "Status", width=170),
	]


def execute(filters=None):
	return get_columns(), build_rows(filters)
