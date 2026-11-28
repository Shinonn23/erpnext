"""Pick List: pick from stock that exists, say where it is, take the earliest expiry first (README.md)."""

from __future__ import annotations

import datetime
from math import floor, isfinite

import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe.utils.nestedset import get_descendants_of

NO_EXPIRY = datetime.date.max
# Where a ledger line's stock lies: its bundle entry's Storage Location, else the ledger row's. The select, the filter
# and the group by of get_stock_lines use this one expression (UX WP2), so a bundle entry's place is the place.
LINE_LOCATION = "coalesce(nullif(sbe.storage_location, ''), sle.storage_location)"


def _staging_filter(expression):
	"""SQL: the Storage Location is not restricted (Receiving, Quality Inspection or Damage)."""
	from erpnext.stock.doctype.storage_location.storage_location import restricted_location_sql

	return f"not {restricted_location_sql(expression)}"


def _is_staging(location, warehouse=None):
	"""The location is restricted (of ``warehouse`` when given)."""
	from erpnext.stock.doctype.storage_location.storage_location import is_restricted_location

	return is_restricted_location(location, warehouse)


def _pick_fulfilment(item_code, pick_items, current=False):
	"""Actual stock outflows linked to Pick List rows, including their bundle identities."""
	if not pick_items:
		return []
	return frappe.db.sql(
		f"""
		select coalesce(dni.pick_list_item, sii.pick_list_item, sed.pick_list_item) as pick_item,
			sle.warehouse, coalesce(nullif(sbe.storage_location, ''), sle.storage_location) as storage_location,
			coalesce(nullif(sbe.batch_no, ''), sle.batch_no) as batch_no,
			coalesce(nullif(sbe.serial_no, ''), sle.serial_no) as serial_no,
			abs(coalesce(sbe.qty, sle.actual_qty)) as qty
		from `tabStock Ledger Entry` sle
		left join `tabSerial and Batch Entry` sbe on sbe.parent = sle.serial_and_batch_bundle
		left join `tabDelivery Note Item` dni on sle.voucher_type = 'Delivery Note' and dni.name = sle.voucher_detail_no
		left join `tabSales Invoice Item` sii on sle.voucher_type = 'Sales Invoice' and sii.name = sle.voucher_detail_no
		left join `tabStock Entry Detail` sed on sle.voucher_type = 'Stock Entry' and sed.name = sle.voucher_detail_no
		where sle.item_code = %(item_code)s and sle.is_cancelled = 0 and sle.actual_qty < 0
		and coalesce(dni.pick_list_item, sii.pick_list_item, sed.pick_list_item) in %(pick_items)s
		{"for update" if current else ""}
	""",
		{"item_code": item_code, "pick_items": tuple(pick_items)},
		as_dict=True,
	)


# ---------------------------------------------------------------------------
# Stock that exists, per warehouse / storage location / batch
# ---------------------------------------------------------------------------


def fefo_sort_key(line):
	"""First Expired, First Out: earliest expiry first, batches with no expiry last, then a steady order."""
	expiry = line.get("expiry_date")
	return (
		expiry is None,
		expiry or NO_EXPIRY,
		line.get("batch_no") or "",
		line.get("storage_location") or "",
		line.get("warehouse") or "",
	)


def get_stock_lines(
	item_code,
	warehouses=None,
	storage_location=None,
	batch_no=None,
	exclude_pick_list=None,
	include_expired=False,
	current=False,
):
	"""What can still be picked: one line per (warehouse, storage location, batch) with a positive balance,
	net of what the other submitted Pick Lists have picked and not yet delivered. FEFO order. Only warehouses whose
	Warehouse Type lets stock be sold or issued (the fork's ``warehouse_policy.sellable_warehouse_sql``, WP10)."""
	from erpnext.stock.warehouse_policy import sellable_warehouse_sql

	conditions = [
		"sle.item_code = %(item_code)s",
		"sle.is_cancelled = 0",
		"exists (select 1 from `tabWarehouse` w where w.name = sle.warehouse and w.disabled = 0 "
		f"and {sellable_warehouse_sql('w')})",
		_staging_filter(LINE_LOCATION),
	]
	values = {"item_code": item_code}
	if warehouses:
		conditions.append("sle.warehouse in %(warehouses)s")
		values["warehouses"] = tuple(warehouses)
	if storage_location:
		conditions.append(f"{LINE_LOCATION} = %(storage_location)s")
		values["storage_location"] = storage_location
	if batch_no:
		conditions.append("coalesce(sbe.batch_no, sle.batch_no) = %(batch_no)s")
		values["batch_no"] = batch_no

	# A batch is held either on the ledger row (old) or on its Serial and Batch Bundle entries (new).
	rows = frappe.db.sql(
		f"""
		select sle.warehouse, {LINE_LOCATION} as storage_location, coalesce(sbe.batch_no, sle.batch_no) as batch_no,
			sum(coalesce(sbe.qty, sle.actual_qty)) as qty
		from `tabStock Ledger Entry` sle
		left join `tabSerial and Batch Entry` sbe on sbe.parent = sle.serial_and_batch_bundle
		where {" and ".join(conditions)}
		group by sle.warehouse, {LINE_LOCATION}, coalesce(sbe.batch_no, sle.batch_no)
		having sum(coalesce(sbe.qty, sle.actual_qty)) > 0
		{"for update" if current else ""}
		""",
		values,
		as_dict=True,
	)
	lines = [
		{
			"warehouse": row.warehouse,
			"storage_location": row.storage_location or None,
			"batch_no": row.batch_no or None,
			"qty": flt(row.qty),
			"expiry_date": None,
		}
		for row in rows
	]

	lines = _with_batch_expiry(lines, include_expired)
	lines = subtract_picks(lines, _open_picks(item_code, exclude_pick_list, current=current))
	return sorted(lines, key=fefo_sort_key)


def _with_batch_expiry(lines, include_expired):
	batches = {line["batch_no"] for line in lines if line["batch_no"]}
	if not batches:
		return lines

	info = {
		row.name: row
		for row in frappe.get_all(
			"Batch",
			filters={"name": ("in", list(batches))},
			fields=["name", "expiry_date", "disabled"],
		)
	}
	today = frappe.utils.getdate()
	kept = []
	for line in lines:
		batch = info.get(line["batch_no"])
		if batch:
			if batch.disabled:
				continue
			line["expiry_date"] = batch.expiry_date
			if batch.expiry_date and batch.expiry_date < today and not include_expired:
				continue
		kept.append(line)
	return kept


def _open_picks(item_code, exclude_pick_list=None, current=False):
	"""Picked, not yet delivered (or moved), on submitted Pick Lists that are neither Completed nor Cancelled, as
	ERPNext's own pick counts them: (warehouse, location, batch, stock UOM qty). A transfer or manufacture pick
	is released by what its Stock Entries moved (transferred_qty), a delivery pick by what was delivered."""
	conditions = [
		"pl.docstatus = 1",
		"pl.status not in ('Completed', 'Cancelled', 'Closed')",
		"pli.item_code = %(item_code)s",
	]
	values = {"item_code": item_code}
	if exclude_pick_list:
		conditions.append("pl.name != %(exclude)s")
		values["exclude"] = exclude_pick_list

	rejected = (
		"""coalesce((select sum(sed.transfer_qty) from `tabStock Entry Detail` sed
		join `tabQuality Inspection` qi on qi.name = sed.outgoing_qi
		where sed.docstatus = 1 and qi.reference_type = 'Pick List' and qi.reference_name = pli.parent
		and (sed.outgoing_pick_component = pli.name or (ifnull(sed.outgoing_pick_component, '') = '' and qi.child_row_reference = pli.name))), 0)"""
		if frappe.db.has_column("Stock Entry Detail", "outgoing_qi")
		else "0"
	)
	outstanding = f"greatest(pli.picked_qty - if(pl.purpose in ('Material Transfer', 'Material Transfer for Manufacture', 'Manufacture'), pli.transferred_qty, pli.delivered_qty) - if(pl.purpose = 'Delivery', {rejected}, 0), 0)"
	query = f"""
		select pli.warehouse, pli.storage_location, pli.batch_no,
			sum({outstanding}) as qty
		from `tabPick List Item` pli
		inner join `tabPick List` pl on pl.name = pli.parent
		where {" and ".join(conditions)}
		and (ifnull(pli.serial_and_batch_bundle, '') = '' or ifnull(pli.batch_no, '') != '')
		group by pli.warehouse, pli.storage_location, pli.batch_no
		{"for update" if current else ""}
		"""
	try:
		rows = frappe.db.sql(query, values, as_dict=True)
		bundle_rows = frappe.db.sql(
			f"""
			select pli.name as pick_item, pli.warehouse,
				coalesce(nullif(sbe.storage_location, ''), pli.storage_location) as storage_location,
				sbe.batch_no, {outstanding} as outstanding, sum(abs(sbe.qty)) as bundle_qty
			from `tabPick List Item` pli
			join `tabPick List` pl on pl.name = pli.parent
			join `tabSerial and Batch Entry` sbe on sbe.parent = pli.serial_and_batch_bundle
			where {" and ".join(conditions)} and ifnull(pli.serial_and_batch_bundle, '') != ''
			and ifnull(pli.batch_no, '') = ''
			group by pli.name, pli.warehouse, coalesce(nullif(sbe.storage_location, ''), pli.storage_location), sbe.batch_no
			{"for update" if current else ""}
		""",
			values,
			as_dict=True,
		)
		fulfilled = _pick_fulfilment(item_code, {row.pick_item for row in bundle_rows}, current=current)
	except frappe.QueryDeadlockError as exc:
		database_error = exc.__cause__ or (exc.args[0] if exc.args else None)
		if current and getattr(database_error, "args", ())[:1] == (1020,):
			frappe.throw(
				_(
					"Stock picking changed while you were working. Please reload and check the latest Pick List before trying again."
				),
				frappe.TimestampMismatchError,
			)
		raise
	used = {}
	for part in fulfilled:
		key = (part.pick_item, part.warehouse, part.storage_location or None, part.batch_no or None)
		used[key] = used.get(key, 0) + flt(part.qty)
	picks = [(row.warehouse, row.storage_location or None, row.batch_no or None, row.qty) for row in rows]
	for row in bundle_rows:
		key = (row.pick_item, row.warehouse, row.storage_location or None, row.batch_no or None)
		qty = min(flt(row.outstanding), max(flt(row.bundle_qty) - used.get(key, 0), 0))
		if qty > 1e-9:
			picks.append((row.warehouse, row.storage_location or None, row.batch_no or None, qty))
	return picks


def subtract_picks(lines, picks):
	"""Take picks off the lines. A pick that names no storage location comes off the fullest locations of its
	warehouse and batch."""
	by_key = {(line["warehouse"], line["storage_location"], line["batch_no"]): line for line in lines}
	for warehouse, location, batch, qty in picks:
		qty = flt(qty)
		if qty <= 0:
			continue

		if location:
			line = by_key.get((warehouse, location, batch or None))
			if line:
				line["qty"] = flt(line["qty"] - qty)
			continue

		same = [
			line for line in lines if line["warehouse"] == warehouse and line["batch_no"] == (batch or None)
		]
		for line in sorted(same, key=lambda line: -line["qty"]):
			taken = min(line["qty"], qty)
			line["qty"] = flt(line["qty"] - taken)
			qty = flt(qty - taken)
			if qty <= 0:
				break

	return [line for line in lines if flt(line["qty"]) > 1e-9]


def allocate_fefo(lines, required_qty):
	"""Take required_qty from the lines, earliest expiry first: (lines taken, qty left over)."""
	taken = []
	remaining = flt(required_qty)
	for line in sorted(lines, key=fefo_sort_key):
		if remaining <= 0:
			break
		qty = min(flt(line["qty"]), remaining)
		if qty <= 0:
			continue
		taken.append({**line, "qty": qty})
		remaining = flt(remaining - qty)
	return taken, remaining


# ---------------------------------------------------------------------------
# What the form asks for (whitelisted)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_pickable_stock(item_code, warehouse=None, storage_location=None, batch_no=None, pick_list=None):
	"""The lines the Pick From Stock dialog lists, earliest expiry first."""
	frappe.has_permission("Pick List", "read", throw=True)
	return get_stock_lines(
		item_code,
		warehouses=[warehouse] if warehouse else None,
		storage_location=storage_location or None,
		batch_no=batch_no or None,
		exclude_pick_list=pick_list or None,
	)


def _active_picked_serials(item_code, exclude_pick_list=None, current=False):
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import _row_serial_nos

	rows = frappe.db.sql(
		f"""select pli.name as pick_item, pli.serial_no, pli.serial_and_batch_bundle
		from `tabPick List Item` pli join `tabPick List` pl on pl.name = pli.parent
		where pl.docstatus = 1 and pl.status not in ('Completed', 'Cancelled', 'Closed')
		and pli.item_code = %s and pl.name != %s and pli.picked_qty > if(pl.purpose in
		('Material Transfer', 'Material Transfer for Manufacture', 'Manufacture'),
		pli.transferred_qty, pli.delivered_qty) {"for update" if current else ""}""",
		(item_code, exclude_pick_list or ""),
		as_dict=True,
	)
	fulfilled = _pick_fulfilment(item_code, {row.pick_item for row in rows}, current=current)
	used = {
		(part.pick_item, serial.strip())
		for part in fulfilled
		for serial in (part.serial_no or "").splitlines()
		if serial.strip()
	}
	return {
		serial
		for row in rows
		for serial in _row_serial_nos(row, current=current)
		if (row.pick_item, serial) not in used
	}


@frappe.whitelist()
def locate_pick(item_code, warehouse, qty=0, batch_no=None, serial_no=None, pick_list=None):
	"""The Storage Location a row picks from, found from what it names (Siwat, 5 Nov 2026, relayed by the user: the
	location comes by itself when picking): the place holding its Serial Nos; else, for a batch, the earliest-expiry
	place holding enough of it (else the first holding any); else the only place holding the item. None when the
	place is not clear (the picker chooses)."""
	frappe.has_permission("Pick List", "read", throw=True)
	return _locate(item_code, warehouse, qty, batch_no, serial_no, pick_list)


def _locate(item_code, warehouse, qty=0, batch_no=None, serial_no=None, pick_list=None):
	if not item_code or not warehouse:
		return None
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	serials = [serial for serial in get_serial_nos(serial_no or "") if serial]
	if serials:
		# a serial elsewhere now (lent, in quarantine) has no place in this warehouse
		places = {
			_serial_location(serial, warehouse)
			if frappe.db.get_value("Serial No", serial, "warehouse") == warehouse
			else None
			for serial in serials
		}
		places.discard(None)
		places = {place for place in places if not _is_staging(place)}
		return places.pop() if len(places) == 1 else None
	lines = [
		line
		for line in get_stock_lines(
			item_code, warehouses=[warehouse], batch_no=batch_no or None, exclude_pick_list=pick_list or None
		)
		if line.get("storage_location")
	]
	if batch_no:
		enough = [line for line in lines if flt(line["qty"]) >= flt(qty)]
		chosen = (enough or lines)[:1]
		return chosen[0]["storage_location"] if chosen else None
	places = {line["storage_location"] for line in lines}
	return places.pop() if len(places) == 1 else None


def locate_draft_rows(doc, method=None):
	"""Pick List before_validate: a draft row that names its item and warehouse but no Storage Location gets the place
	the form would fill (``locate_pick``), so the saved value is what the screen shows (UX WP2). A place that is not
	clear stays empty for the picker; a row of a warehouse with no locations is left alone."""
	if int(doc.get("docstatus") or 0) != 0 or frappe.flags.in_patch or frappe.flags.in_install:
		return
	has_locations = {}
	for row in doc.get("locations") or []:
		if row.get("storage_location") or not row.get("item_code") or not row.get("warehouse"):
			continue
		if row.get("serial_and_batch_bundle") or not _item_flags(row.item_code).get("is_stock_item"):
			continue
		if not _warehouse_has_locations(row.warehouse, has_locations):
			continue
		location = _locate(
			row.item_code,
			row.warehouse,
			flt(row.get("stock_qty")) or flt(row.get("qty")),
			row.get("batch_no"),
			row.get("serial_no"),
			None if doc.is_new() else doc.name,
		)
		if location:
			row.storage_location = location


def _serial_location(serial, warehouse):
	"""The Storage Location where a Serial No last came into the warehouse."""
	row = frappe.db.sql(
		"""select sle.storage_location from `tabStock Ledger Entry` sle
		left join `tabSerial and Batch Entry` entry on entry.parent = sle.serial_and_batch_bundle
		where sle.warehouse = %(warehouse)s and sle.is_cancelled = 0 and sle.actual_qty > 0
		and (entry.serial_no = %(serial)s or sle.serial_no = %(serial)s)
		order by sle.posting_datetime desc, sle.creation desc limit 1""",
		{"serial": serial, "warehouse": warehouse},
	)
	return row[0][0] if row and row[0][0] else None


def _serial_at_staging(serial, warehouse):
	"""The serial last came into the warehouse at a STAGING location: it is not picked until put away."""
	return _is_staging(_serial_location(serial, warehouse), warehouse)


@frappe.whitelist()
def get_pickable_serials(item_code, warehouse, qty, storage_location=None, batch_no=None, pick_list=None):
	"""The first qty serial nos in stock at the warehouse / storage location (/ batch), as ERPNext picks."""
	return [
		row.serial_no
		for row in _pickable_serial_rows(item_code, warehouse, storage_location, batch_no, pick_list)
	][: int(flt(qty))]


def _pickable_serial_rows(item_code, warehouse, storage_location=None, batch_no=None, pick_list=None):
	frappe.has_permission("Pick List", "read", throw=True)
	if not item_code or not warehouse:
		return []
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_available_serial_nos

	kwargs = frappe._dict(
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"has_batch_no": frappe.get_cached_value("Item", item_code, "has_batch_no"),
			"company": frappe.get_cached_value("Warehouse", warehouse, "company"),
			"based_on": frappe.get_single_value("Stock Settings", "pick_serial_and_batch_based_on"),
			"posting_datetime": now_datetime(),
			"inventory_dimensions_dict": {"storage_location": storage_location} if storage_location else {},
		}
	)
	if batch_no:
		kwargs.update({"batches": [batch_no], "has_batch_no": True})

	rows = get_available_serial_nos(kwargs)
	if batch_no:
		rows = [row for row in rows if row.get("batch_no") == batch_no]
	blocked = _active_picked_serials(item_code, exclude_pick_list=pick_list)
	return [
		row
		for row in rows
		if row.serial_no not in blocked and not _serial_at_staging(row.serial_no, warehouse)
	]


@frappe.whitelist()
def selector_pickable_data(
	item_code, warehouse, qty, has_serial_no=0, storage_location=None, batch_no=None, pick_list=None
):
	frappe.has_permission("Pick List", "read", throw=True)
	if not item_code or not warehouse:
		return []
	qty = max(flt(qty), 0)
	if int(has_serial_no or 0):
		return _pickable_serial_rows(item_code, warehouse, storage_location, batch_no, pick_list)[: int(qty)]
	remaining, rows = qty, []
	for line in get_stock_lines(
		item_code,
		[warehouse],
		storage_location=storage_location or None,
		batch_no=batch_no or None,
		exclude_pick_list=pick_list or None,
	):
		if not line.get("batch_no") or remaining <= 0:
			continue
		take = min(flt(line["qty"]), remaining)
		rows.append({"batch_no": line["batch_no"], "qty": take})
		remaining -= take
	return rows


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def selector_serial_query(doctype, txt, searchfield, start, page_len, filters):
	filters = frappe._dict(filters or {})
	if not filters.get("item_code") or not filters.get("warehouse"):
		return []
	rows = _pickable_serial_rows(
		filters.item_code,
		filters.warehouse,
		filters.get("storage_location"),
		filters.get("batch_no"),
		filters.get("pick_list"),
	)
	match = [(row.serial_no,) for row in rows if (txt or "").lower() in row.serial_no.lower()]
	return match[int(start or 0) : int(start or 0) + int(page_len or 20)]


@frappe.whitelist()
def selector_is_pickable(
	item_code, warehouse, serial_no=None, storage_location=None, batch_no=None, pick_list=None
):
	frappe.has_permission("Pick List", "read", throw=True)
	if not item_code or not warehouse:
		return False
	if serial_no:
		return any(
			row.serial_no == serial_no
			for row in _pickable_serial_rows(item_code, warehouse, storage_location, batch_no, pick_list)
		)
	return bool(
		batch_no
		and get_stock_lines(
			item_code,
			[warehouse],
			storage_location=storage_location or None,
			batch_no=batch_no,
			exclude_pick_list=pick_list or None,
		)
	)


@frappe.whitelist()
def selector_validate_entries(item_code, warehouse, entries, storage_location=None, pick_list=None):
	frappe.has_permission("Pick List", "read", throw=True)
	entries = frappe.parse_json(entries) if isinstance(entries, str) else entries
	if (
		not item_code
		or not warehouse
		or not isinstance(entries, list)
		or not entries
		or any(not isinstance(row, dict) for row in entries)
	):
		frappe.throw(_("Choose valid goods and a warehouse before selecting serials or batches."))
	if any(row.get("serial_no") for row in entries):
		available = {
			row.serial_no: row
			for row in _pickable_serial_rows(item_code, warehouse, storage_location, pick_list=pick_list)
		}
		seen = set()
		for row in entries:
			serial = str(row.get("serial_no") or "").strip()
			item = available.get(serial)
			qty = flt(row.get("qty")) if row.get("qty") not in (None, "") else 1
			if (
				not item
				or serial in seen
				or (row.get("batch_no") and row["batch_no"] != item.get("batch_no"))
				or not isfinite(qty)
				or abs(qty) != 1
			):
				frappe.throw(
					_("Serial {0} is duplicated or not available to pick at this location.").format(
						frappe.bold(serial or "-")
					)
				)
			seen.add(serial)
			row.update(serial_no=serial, batch_no=item.get("batch_no"), qty=1)
	else:
		available = {}
		for line in get_stock_lines(
			item_code,
			[warehouse],
			storage_location=storage_location or None,
			exclude_pick_list=pick_list or None,
		):
			batch = line.get("batch_no")
			available[batch] = available.get(batch, 0) + flt(line.get("qty"))
		for row in entries:
			batch, qty = row.get("batch_no"), flt(row.get("qty"))
			if not batch or not isfinite(qty) or qty <= 0 or qty > available.get(batch, 0) + 1e-9:
				frappe.throw(
					_("Batch {0} does not have that quantity available to pick at this location.").format(
						frappe.bold(batch or "-")
					)
				)
			available[batch] -= qty
	return entries


@frappe.whitelist()
def selector_import_csv(item_code, warehouse, file_path, storage_location=None, pick_list=None):
	frappe.has_permission("Pick List", "read", throw=True)
	from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
		parse_csv_file_to_get_serial_batch,
	)
	from frappe.core.doctype.file.utils import find_file_by_url
	from frappe.utils.csvutils import read_csv_content

	file = find_file_by_url(file_path)
	if not file or file.file_type != "CSV":
		frappe.throw(_("Choose an accessible CSV file."))
	serials, batches = parse_csv_file_to_get_serial_batch(read_csv_content(file.get_content()))
	return selector_validate_entries(item_code, warehouse, serials or batches, storage_location, pick_list)


def validate_pick_bundle(doc, method=None):
	"""A bundle edited outside the picker still cannot include already picked serials."""
	if (
		doc.get("voucher_type") != "Pick List"
		or doc.get("type_of_transaction") != "Outward"
		or int(doc.get("docstatus") or 0) == 2
		or not doc.get("entries")
	):
		return
	if not doc.get("item_code") or not doc.get("warehouse"):
		frappe.throw(_("Choose the item and warehouse before selecting serials or batches."))
	blocked = set(
		_active_picked_serials(
			doc.item_code,
			exclude_pick_list=doc.get("voucher_no"),
			current=int(doc.get("docstatus") or 0) == 1,
		)
	)
	if any(row.get("serial_no") in blocked for row in doc.entries if row.get("serial_no")):
		frappe.throw(_("A serial number in this bundle is already picked."))
	for row in doc.entries:
		location = row.get("storage_location")
		if not location and row.get("serial_no"):
			location = _serial_location(row.serial_no, doc.warehouse)
		if _is_staging(location, doc.warehouse):
			frappe.throw(
				_(
					"{0} is at the receiving location {1}; put it away with a Stock Entry before picking."
				).format(frappe.bold(doc.item_code), frappe.bold(location))
			)
	groups = {}
	for row in doc.entries:
		if row.get("serial_no") or not row.get("batch_no"):
			continue
		key = (row.get("storage_location") or None, row.batch_no)
		groups[key] = groups.get(key, 0) + abs(flt(row.get("qty")))
	for (location, batch), qty in groups.items():
		lines = get_stock_lines(
			doc.item_code,
			[doc.warehouse],
			storage_location=location,
			batch_no=batch,
			exclude_pick_list=doc.get("voucher_no") or None,
			current=int(doc.get("docstatus") or 0) == 1,
		)
		if qty > sum(flt(line["qty"]) for line in lines) + 1e-9:
			frappe.throw(_("The batch quantity in this bundle exceeds the stock available to pick."))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def storage_location_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link search of Pick List Item.storage_location: only locations holding the item (and batch) now."""
	frappe.has_permission("Pick List", "read", throw=True)
	filters = frappe._dict(filters or {})
	if not filters.get("item_code"):
		return []

	lines = get_stock_lines(
		filters.item_code,
		warehouses=[filters.warehouse] if filters.get("warehouse") else None,
		batch_no=filters.get("batch_no") or None,
		exclude_pick_list=filters.get("pick_list") or None,
	)
	totals = {}
	for line in lines:
		if line["storage_location"]:
			totals[line["storage_location"]] = totals.get(line["storage_location"], 0) + line["qty"]

	txt = (txt or "").lower()
	found = [
		(name, _("{0} available").format(f"{qty:g}")) for name, qty in totals.items() if txt in name.lower()
	]
	return found[int(start or 0) : int(start or 0) + int(page_len or 20)]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def batch_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link search of Pick List Item.batch_no: only batches in stock at the warehouse / storage location,
	earliest expiry first, each with its balance and expiry."""
	frappe.has_permission("Pick List", "read", throw=True)
	filters = frappe._dict(filters or {})
	if not filters.get("item_code"):
		return []

	lines = get_stock_lines(
		filters.item_code,
		warehouses=[filters.warehouse] if filters.get("warehouse") else None,
		storage_location=filters.get("storage_location") or None,
		exclude_pick_list=filters.get("pick_list") or None,
	)
	batches = {}
	for line in lines:
		if not line["batch_no"]:
			continue
		entry = batches.setdefault(line["batch_no"], {"qty": 0, "expiry_date": line["expiry_date"]})
		entry["qty"] += line["qty"]

	txt = (txt or "").lower()
	found = []
	for name, entry in batches.items():
		if txt not in name.lower():
			continue
		expiry = entry["expiry_date"]
		found.append((name, f"{entry['qty']:g} · EXP {expiry}" if expiry else f"{entry['qty']:g}"))
	return found[int(start or 0) : int(start or 0) + int(page_len or 20)]


# ---------------------------------------------------------------------------
# Submit: location chosen, batch chosen, and the stock is there
# ---------------------------------------------------------------------------


def _item_flags(item_code):
	return (
		frappe.get_cached_value(
			"Item", item_code, ["is_stock_item", "has_batch_no", "has_serial_no"], as_dict=True
		)
		or frappe._dict()
	)


def _warehouse_has_locations(warehouse, cache):
	if warehouse not in cache:
		cache[warehouse] = bool(frappe.db.exists("Storage Location", {"warehouse": warehouse, "disabled": 0}))
	return cache[warehouse]


def _hint(item_code, warehouse, pick_list, batch_no=None):
	lines = get_stock_lines(item_code, [warehouse], batch_no=batch_no, exclude_pick_list=pick_list)[:5]
	if not lines:
		return _("No stock is available.")
	return _("In stock: {0}").format(
		"; ".join(
			f"{line['storage_location'] or '-'} / {line['batch_no'] or '-'} = {flt(line['qty']):g}"
			for line in lines
		)
	)


def _staging_location_of_row(row):
	"""The STAGING location a picked row takes its goods from (its own, a bundle entry's, or where its serial
	numbers lie), else None."""
	warehouse = row.get("warehouse")
	if _is_staging(row.get("storage_location"), warehouse):
		return row.storage_location
	if row.get("serial_and_batch_bundle"):
		for part in frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": row.serial_and_batch_bundle},
			fields=["serial_no", "storage_location"],
		):
			place = part.storage_location or (
				_serial_location(part.serial_no, warehouse) if part.serial_no else None
			)
			if _is_staging(place, warehouse):
				return place
		return None
	if row.get("serial_no") and not row.get("storage_location"):
		from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

		for serial in get_serial_nos(row.serial_no):
			place = _serial_location(serial, warehouse)
			if _is_staging(place, warehouse):
				return place
	return None


def refuse_receiving_picks(doc, method=None):
	"""Pick List validate: a draft never holds a picked row whose goods lie at a Receiving location (the STAGING part
	of ``validate_pick_list_stock``, which still runs in full before submit). Receiving stock leaves only by a Stock
	Entry (Siwat, 17 Nov 2026; UX WP2)."""
	if frappe.flags.in_patch or int(doc.get("docstatus") or 0) != 0:
		return
	problems = []
	for row in doc.get("locations") or []:
		if flt(row.get("picked_qty")) <= 0 or not row.get("warehouse"):
			continue
		if not _item_flags(row.item_code).get("is_stock_item"):
			continue
		staged = _staging_location_of_row(row)
		if staged:
			problems.append(
				_(
					"Row {0}: {1} is at the receiving location {2}; put it away with a Stock Entry before picking."
				).format(row.idx, frappe.bold(row.item_code), frappe.bold(staged))
			)
	if problems:
		frappe.throw(problems, title=_("Pick List stock check"), as_list=True)


def refuse_save_without_warehouse(doc, method=None):
	"""Pick List validate: a draft Delivery pick of Sales Order goods with rows still to pick names the Warehouse to
	pick from (the form marks it required, ``require_warehouse``). Without it the Auto Pick would search every
	warehouse of the company (UX WP2)."""
	if frappe.flags.in_patch or frappe.flags.in_install or int(doc.get("docstatus") or 0) != 0:
		return
	if needs_warehouse_first(doc) and unpicked_needs(doc):
		frappe.throw(
			_("Choose the Warehouse to pick from, then press Auto Pick."),
			title=_("Choose the Warehouse"),
		)


def validate_pick_list_stock(doc, method=None):
	"""Before submit, every row that picks something names a storage location (where the warehouse has any)
	and a batch (where the item has one), and the stock is there now at that place."""
	if frappe.flags.in_patch:
		return

	problems = []
	needed = {}
	has_locations = {}
	for row in doc.get("locations") or []:
		if flt(row.get("picked_qty")) <= 0 or not row.get("warehouse"):
			continue

		item = _item_flags(row.item_code)
		if not item.get("is_stock_item"):
			continue

		if _warehouse_has_locations(row.warehouse, has_locations) and not row.get("storage_location"):
			problems.append(
				_("Row {0}: choose the Storage Location to pick {1} from. {2}").format(
					row.idx,
					frappe.bold(row.item_code),
					_hint(row.item_code, row.warehouse, doc.name, row.get("batch_no")),
				)
			)
			continue

		staged = _staging_location_of_row(row)
		if staged:
			problems.append(
				_(
					"Row {0}: {1} is at the receiving location {2}; put it away with a Stock Entry before picking."
				).format(row.idx, frappe.bold(row.item_code), frappe.bold(staged))
			)
			continue

		if item.get("has_batch_no") and not (row.get("batch_no") or row.get("serial_and_batch_bundle")):
			problems.append(
				_("Row {0}: choose the Batch No of {1}. {2}").format(
					row.idx,
					frappe.bold(row.item_code),
					_hint(row.item_code, row.warehouse, doc.name),
				)
			)
			continue

		if row.get("serial_and_batch_bundle"):
			parts = frappe.get_all(
				"Serial and Batch Entry",
				filters={"parent": row.serial_and_batch_bundle},
				fields=["batch_no", "serial_no", "qty", "storage_location"],
			)
			if not parts:
				problems.append(
					_("Row {0}: the Serial and Batch Bundle has no stock to pick.").format(row.idx)
				)
			if abs(sum(abs(flt(part.qty)) for part in parts) - flt(row.picked_qty)) > 1e-9:
				problems.append(
					_("Row {0}: picked quantity must match its Serial and Batch Bundle.").format(row.idx)
				)
			for part in parts:
				key = (
					row.item_code,
					row.warehouse,
					part.get("storage_location") or row.get("storage_location") or None,
					part.batch_no or None,
				)
				entry = needed.setdefault(key, {"qty": 0, "rows": [], "serials": set()})
				entry["qty"] += abs(flt(part.qty))
				entry["rows"].append(row.idx)
				if part.serial_no:
					entry["serials"].add(part.serial_no)
			continue

		key = (row.item_code, row.warehouse, row.get("storage_location") or None, row.get("batch_no") or None)
		entry = needed.setdefault(key, {"qty": 0, "rows": [], "serials": set()})
		entry["qty"] += flt(row.picked_qty)
		entry["rows"].append(row.idx)
		if item.get("has_serial_no"):
			from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import _row_serial_nos

			entry["serials"].update(_row_serial_nos(row))

	for (item_code, warehouse, location, batch_no), entry in needed.items():
		if int(doc.get("docstatus") or 0) == 1:
			frappe.db.get_value(
				"Bin", {"item_code": item_code, "warehouse": warehouse}, "name", for_update=True
			)
		lines = get_stock_lines(
			item_code,
			[warehouse],
			storage_location=location,
			batch_no=batch_no,
			exclude_pick_list=doc.name,
			include_expired=True,
			current=int(doc.get("docstatus") or 0) == 1,
		)
		available = sum(line["qty"] for line in lines)
		if flt(entry["qty"] - available) > 1e-9:
			problems.append(
				_("Row {0}: {1} of {2} picked from {3}{4}, but only {5} is available there. {6}").format(
					", ".join(str(idx) for idx in entry["rows"]),
					f"{entry['qty']:g}",
					frappe.bold(item_code),
					frappe.bold(location or warehouse),
					f" ({batch_no})" if batch_no else "",
					f"{available:g}",
					_hint(item_code, warehouse, doc.name, batch_no),
				)
			)

	if problems:
		frappe.throw(problems, title=_("Pick List stock check"), as_list=True)


# ---------------------------------------------------------------------------
# Auto pick: earliest expiry first, with the storage location
# ---------------------------------------------------------------------------


def _claimed_by_other_rows(doc, skip, item_code=None):
	"""What the rows outside the auto pick already hold in this Pick List (of `item_code` only when given: a place
	shared by many Items, like STAGING, is not used up by the row of another Item)."""
	claims = []
	for row in doc.get("locations") or []:
		if id(row) in skip or (item_code and row.get("item_code") != item_code):
			continue
		qty = flt(row.get("picked_qty")) or flt(row.get("stock_qty"))
		if qty > 0:
			claims.append(
				(row.get("warehouse"), row.get("storage_location") or None, row.get("batch_no") or None, qty)
			)
	return claims


def pick_warehouses(doc, rows):
	"""Where the auto pick may take stock from, as ERPNext decides it: the Warehouse field and what is under
	it, else every warehouse of the company that is not a rejected one (a work order keeps the warehouses
	ERPNext chose for its items)."""
	from erpnext.stock.doctype.pick_list.pick_list import get_rejected_warehouses

	if doc.get("parent_warehouse"):
		return [doc.parent_warehouse, *get_descendants_of("Warehouse", doc.parent_warehouse)]

	if doc.get("work_order"):
		return sorted({row.warehouse for row in rows})

	# the rows name their warehouse: the stock is taken there (Siwat, 5 Nov 2026, relayed by the user)
	named = sorted({row.warehouse for row in rows if row.get("warehouse")})
	if named and all(row.get("warehouse") for row in rows):
		return [name for name in named if _usable_warehouse(name)] or named

	warehouses = frappe.get_all(
		"Warehouse", filters={"company": doc.company, "is_group": 0, "disabled": 0}, pluck="name"
	)
	if not doc.get("consider_rejected_warehouses"):
		rejected = set(get_rejected_warehouses())
		warehouses = [name for name in warehouses if name not in rejected]
	return [name for name in warehouses if _usable_warehouse(name)]


def loan_fallback_warehouses(doc, warehouses):
	"""A Borrowing Pick List whose warehouse has too little: the warehouses it named, then every other usable
	warehouse of the company (never a rejected, Loan, Quarantine, Write-Off ... one)."""
	others = frappe.get_all(
		"Warehouse",
		filters={"company": doc.company, "is_group": 0, "disabled": 0},
		pluck="name",
		order_by="name",
	)
	return [*warehouses, *(name for name in others if name not in warehouses and _usable_warehouse(name))]


@frappe.request_cache
def _usable_warehouse(warehouse):
	"""Whether the auto pick may take stock there: its Warehouse Type allows a sale or an issue (a Stock warehouse),
	never a Borrowed, Quarantine, Lost, Found, Write-Off, Frozen or Transit one. The one answer of the fork's
	``warehouse_policy.is_sellable_warehouse`` (WP10); a warehouse without a type counts as stock."""
	from erpnext.stock.warehouse_policy import is_sellable_warehouse

	return is_sellable_warehouse(warehouse)


def _serial_picks(doc, item_code, warehouses, rows, claimed, replacing=None, respect_location=True):
	"""(lines, True): the serial numbers to pick, grouped per (warehouse, storage location, batch), earliest batch
	expiry first. A serial already on another row of this list or on an undelivered submitted Pick List is left."""
	need = int(sum(flt(row.stock_qty) for row in rows))
	if need <= 0 or not warehouses:
		return [], True
	busy = set(claimed) | _active_picked_serials(item_code, exclude_pick_list=doc.name)
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import _row_serial_nos

	for row in doc.get("locations") or []:
		if row.get("item_code") == item_code and row not in rows and id(row) not in (replacing or set()):
			busy.update(_row_serial_nos(row))
	candidates = frappe.db.sql(
		"""select serial.name, serial.warehouse, serial.batch_no, batch.expiry_date
		from `tabSerial No` serial left join `tabBatch` batch on batch.name = serial.batch_no
		where serial.item_code = %s and serial.warehouse in %s and serial.status = 'Active'
		and (batch.name is null or ((batch.expiry_date is null or batch.expiry_date >= curdate()) and batch.disabled = 0))
		order by batch.expiry_date is null, batch.expiry_date, serial.batch_no, serial.name""",
		(item_code, tuple(warehouses)),
		as_dict=True,
	)
	groups = {}
	allowed_locations = (
		{row.get("storage_location") for row in rows if row.get("storage_location")}
		if respect_location
		else set()
	)
	for serial in candidates:
		if serial.name in busy:
			continue
		key = (serial.warehouse, _serial_location(serial.name, serial.warehouse), serial.batch_no or None)
		if _is_staging(key[1], serial.warehouse):
			continue
		if allowed_locations and key[1] not in allowed_locations:
			continue
		groups.setdefault(key, []).append(serial.name)
		need -= 1
		if not need:
			break
	return [
		{
			"warehouse": key[0],
			"storage_location": key[1],
			"batch_no": key[2],
			"qty": len(names),
			"serial_nos": names,
		}
		for key, names in groups.items()
	], True


def apply_fefo_locations(doc, include_located=False):
	"""Re-spread the rows the auto pick just made (not picked yet, no storage location) over the stock that
	exists, earliest expiry first, one row per storage location and batch. The parts of a Product Bundle are
	picked like any Item (each Packed Item keeps its own rows, which the Delivery Note takes onto its Packing
	List). Rows the user fixed stay as they are."""
	groups = {}
	for row in doc.get("locations") or []:
		if flt(row.get("picked_qty")) > 0 or (row.get("storage_location") and not include_located):
			continue
		if not row.get("item_code") or not row.get("warehouse"):
			continue

		item = _item_flags(row.item_code)
		if not item.get("is_stock_item"):
			continue

		key = (
			row.item_code,
			row.get("uom"),
			row.get("sales_order"),
			row.get("sales_order_item"),
			row.get("material_request"),
			row.get("material_request_item"),
			row.get("product_bundle_item"),
			row.get("storage_location"),
		)
		groups.setdefault(key, []).append(row)

	if not groups:
		return

	rebuilt = []
	claimed = []  # (warehouse, location, batch, qty) of the groups already worked out, and the Item they are of
	claimed_items = []
	claimed_serials = set()
	# the rows ERPNext just made, of every group, are what this rebuild replaces: they hold no stock while the new
	# rows are worked out, so no group is short because of the old rows of another (nor of its own)
	replaced = {id(row) for rows in groups.values() for row in rows}
	for rows in groups.values():
		first = rows[0]
		serials = None
		if _item_flags(first.item_code).get("has_serial_no"):
			# a serial item takes real serial numbers, each from where it is (earliest batch expiry first)
			warehouses = pick_warehouses(doc, rows)
			taken, serials = _serial_picks(doc, first.item_code, warehouses, rows, claimed_serials, replaced)
			if doc.get("borrowing") and sum(line["qty"] for line in taken) < sum(
				flt(row.stock_qty) for row in rows
			):
				# a loan's default warehouse is short: the serial numbers come from any usable warehouse
				taken, serials = _serial_picks(
					doc,
					first.item_code,
					loan_fallback_warehouses(doc, warehouses),
					rows,
					claimed_serials,
					replaced,
					respect_location=False,
				)
		else:
			warehouses = pick_warehouses(doc, rows)
			need = sum(flt(row.stock_qty) for row in rows)
			others_claimed = _claimed_by_other_rows(doc, replaced, first.item_code) + [
				claim for item_code, claim in claimed_items if item_code == first.item_code
			]
			lines = get_stock_lines(
				first.item_code,
				warehouses,
				storage_location=first.get("storage_location") or None,
				exclude_pick_list=doc.name,
			)
			lines = subtract_picks(lines, others_claimed)
			taken, left = allocate_fefo(lines, need)
			if doc.get("borrowing") and left > 0:
				# a loan's default warehouse is short (Siwat, 5 Nov 2026): the rest comes from any other usable
				# warehouse; the Borrowing row follows the warehouse most of it was picked from
				more = [name for name in loan_fallback_warehouses(doc, warehouses) if name not in warehouses]
				if more:
					extra = subtract_picks(
						get_stock_lines(first.item_code, more, exclude_pick_list=doc.name), others_claimed
					)
					added, _left = allocate_fefo(extra, left)
					taken = [*taken, *added]
		if not taken:
			for row in rows:
				row.serial_no = None
				row.batch_no = None
				row.storage_location = None
				row._auto_pick_unavailable = True
			continue

		conversion = flt(first.get("conversion_factor")) or 1
		uom = first.get("uom")
		whole = frappe.get_cached_value("UOM", uom, "must_be_whole_number") if uom else 0
		template = first.as_dict()
		template.update({"name": None, "idx": None})

		new_rows = []
		for line in taken:
			stock_qty = line["qty"]
			qty = stock_qty / conversion
			if whole:
				qty = floor(qty)
				stock_qty = qty * conversion
				if not stock_qty:
					continue
			if serials:
				claimed_serials.update(line["serial_nos"])
			new_rows.append(
				{
					**template,
					"warehouse": line["warehouse"],
					"storage_location": line["storage_location"],
					"batch_no": line["batch_no"],
					"qty": qty,
					"stock_qty": stock_qty,
					"picked_qty": 0,
					"serial_no": "\n".join(line["serial_nos"]) if serials else None,
					"serial_and_batch_bundle": None,
					"use_serial_batch_fields": 1,
				}
			)
			claimed.append((line["warehouse"], line["storage_location"], line["batch_no"], stock_qty))
			claimed_items.append((first.item_code, claimed[-1]))

		if new_rows:
			rebuilt.append((rows, new_rows))

	for old_rows, new_rows in rebuilt:
		for row in old_rows:
			doc.remove(row)
		for new_row in new_rows:
			doc.append("locations", new_row)

	for idx, row in enumerate(doc.get("locations") or [], start=1):
		row.idx = idx


def unpicked_needs(doc, available_only=False):
	"""Stock qty each stock item still has to find, from the rows nothing is picked on."""
	needs = {}
	for row in doc.get("locations") or []:
		if (
			flt(row.get("picked_qty")) > 0
			or not row.get("item_code")
			or (available_only and row.get("_auto_pick_unavailable"))
		):
			continue
		if _item_flags(row.item_code).get("is_stock_item"):
			needs[row.item_code] = needs.get(row.item_code, 0) + flt(row.get("stock_qty"))
	return needs


def warn_short_in_warehouse(doc, needs):
	"""With a Warehouse chosen, say which warehouse could not cover the item (ERPNext only says "any of the
	warehouses"). Without one the ERPNext message is right as it stands."""
	if not doc.get("parent_warehouse"):
		return

	found = unpicked_needs(doc, available_only=True)
	for item_code, need in needs.items():
		short = flt(need - found.get(item_code, 0))
		if short > 0:
			frappe.msgprint(
				_("{0} of {1} is not in stock under the Warehouse {2}.").format(
					f"{short:g}", frappe.bold(item_code), frappe.bold(doc.parent_warehouse)
				),
				title=_("Insufficient Stock in Warehouse"),
				indicator="orange",
			)


def needs_warehouse_first(doc):
	"""A Pick List for a Sales Order with no Warehouse chosen: ERPNext would pick from every warehouse of
	the company the moment it is created (the order's own Warehouse is usually empty). It waits for it. Pick
	Manually is not read: the fork keeps it on for every draft (``keep_manual_picking``)."""
	return bool(
		doc.get("purpose") == "Delivery"
		and not doc.get("parent_warehouse")
		and any(row.get("sales_order") for row in doc.get("locations") or [])
	)


def forget_picks(doc):
	"""Pressing Auto Pick again picks afresh: what an earlier press marked as picked is open again, so the rows are
	spread over the stock that exists now. A list picked by scanning keeps what was scanned."""
	if doc.get("docstatus") == 0 and not doc.get("scan_mode"):
		for row in doc.get("locations") or []:
			row.picked_qty = 0


def mark_picked(doc):
	"""The Auto Pick took each of these rows from where the stock is: its Picked Qty is the whole quantity (the
	picker checks the rows and submits; nothing is typed). A list picked by scanning is picked by the scan."""
	if doc.get("docstatus") != 0 or doc.get("scan_mode"):
		return
	for row in doc.get("locations") or []:
		if (
			row.get("item_code")
			and not row.get("product_bundle_item")
			and not row.get("_auto_pick_unavailable")
			and flt(row.get("stock_qty")) > 0
		):
			row.picked_qty = flt(row.stock_qty)


def check_close_scope(doc):
	"""Close and Reopen free or hold stock of the pick's warehouses, so they follow the posting scope that saving and
	cancelling the Pick List follow (hooks: validate, before_cancel). The system path (a closed Sales Order closes its
	picks, ``flags.ignore_permissions``) is the order's decision and is not scoped."""
	flags = getattr(doc, "flags", None)
	if flags is not None and flags.get("ignore_permissions"):
		return
	for guard in frappe.get_hooks("pick_list_close_guards"):
		frappe.get_attr(guard)(doc)


class PickListFefoMixin:
	def close(self):
		from erpnext.stock.outgoing_inspection import refuse_close_with_rejected

		check_close_scope(self)
		if self.can_be_closed():
			refuse_close_with_rejected(self)
		super().close()

	def reopen(self):
		check_close_scope(self)
		super().reopen()

	@frappe.whitelist(methods=["POST"])
	def set_item_locations(self, save=False):
		if needs_warehouse_first(self):
			if unpicked_needs(self):
				# the save would be refused for the same reason (refuse_save_without_warehouse): say it once
				frappe.msgprint(
					_("Choose the Warehouse to pick from, then press Auto Pick."),
					title=_("Choose the Warehouse"),
					indicator="blue",
				)
				return
			if save:
				self.save()
			return

		forget_picks(self)
		needs = unpicked_needs(self)
		previous = frappe.flags.get("pick_list_custom_shortage_warning")
		frappe.flags.pick_list_custom_shortage_warning = bool(self.get("parent_warehouse"))
		try:
			super().set_item_locations(save=False)
		finally:
			frappe.flags.pick_list_custom_shortage_warning = previous
		apply_fefo_locations(self, include_located=True)
		warn_short_in_warehouse(self, needs)
		mark_picked(self)
		if save:
			self.save()
