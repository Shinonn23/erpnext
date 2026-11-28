"""Goods leave quarantine with the Stock team's own Stock Entry once they are inspected (Siwat, 9 Nov 2026).

Goods that wait for an inspection are in a Quarantine warehouse: a receipt puts the rows of an inspected Item there
(``receipt_quarantine.py``) and the Stock team's "Quarantine Intake" puts there what it suspects (``quarantine_inspection.py``).
The QI team inspects them. The Stock team then takes them out with a Stock Entry (Material Transfer) it makes itself,
to wherever it chooses: a Storage Location, DAMAGE, another warehouse. The system does not make or submit it.

A move out of quarantine is allowed when every quantity it takes belongs to a receipt row with a submitted Quality
Inspection, Accepted or Rejected, or to goods nothing waits for (``refuse_manual_move_out``, checked on save and again on
submit; for an Item with batches or serial numbers the batches and serial numbers it takes must not be those that wait). It is refused, naming the rows
still waiting, otherwise. A move to a non-quarantine warehouse of the same company (no transit) takes the route
"Quarantine Release": the Stock User submits it, nobody approves it again (``waive_outgoing_role`` lifts the Warehouse
Type's outgoing role for it only; any other move out of quarantine keeps it and takes the Stock route). A Quarantine
Intake or Release has no Add to Transit, Requires Shipment or Additional Costs (``refuse_quarantine_extras``, UX WP9).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from .policies import get_policy

ROUTE = "Quarantine Release"


def zone_transfer_route(doc):
	"""Recognize transfers to or from an inspection zone in the same physical warehouse."""
	from erpnext.stock.doctype.storage_location.storage_location import QUALITY_INSPECTION, get_location_type

	rows = doc.get("items") or []
	if (
		doc.get("purpose") != "Material Transfer"
		or not rows
		or doc.get("add_to_transit")
		or doc.get("outgoing_stock_entry")
	):
		return None
	routes = set()
	for row in rows:
		source = row.get("s_warehouse") or doc.get("from_warehouse")
		target = row.get("t_warehouse") or doc.get("to_warehouse")
		in_inspection = get_location_type(row.get("storage_location"), source) == QUALITY_INSPECTION
		to_inspection = get_location_type(row.get("to_storage_location"), target) == QUALITY_INSPECTION
		if (
			in_inspection == to_inspection
			or not source
			or source != target
			or frappe.db.get_value("Warehouse", source, "company") != doc.get("company")
		):
			return None
		routes.add(ROUTE if in_inspection else "Quarantine Intake")
	return routes.pop() if len(routes) == 1 else None


def item_team(item_code):
	"""Quarantined goods belong to the incoming inspection team, else their Product Group's default."""
	team_of_item = get_policy("item_team")
	if team_of_item:
		return team_of_item(item_code)
	if frappe.get_meta("Item").has_field("incoming_qi_team"):
		return frappe.get_cached_value("Item", item_code, "incoming_qi_team")


def is_quarantine_release(doc, warehouse_info, quarantine):
	"""Every row leaves a quarantine warehouse for a non-quarantine one of the same company (no transit)."""
	if (
		not doc.get("company")
		or not doc.get("items")
		or doc.get("add_to_transit")
		or doc.get("outgoing_stock_entry")
	):
		return False
	for row in doc.items:
		source = row.get("s_warehouse") or doc.get("from_warehouse")
		target = row.get("t_warehouse") or doc.get("to_warehouse")
		if (
			source not in quarantine
			or not target
			or target in quarantine
			or any(
				not warehouse_info.get(name) or warehouse_info[name].get("company") != doc.company
				for name in (source, target)
			)
		):
			return False
	return True


def _stock_qty(row):
	return abs(flt(row.get("transfer_qty") or row.get("stock_qty") or row.get("qty")))


def waiting_goods(item_code, warehouse):
	"""[{"label", "qty", "batches", "serials"}] of the goods of ``item_code`` that wait in quarantine ``warehouse`` for
	their first Quality Inspection: the rows of submitted receipts (Purchase Receipt, Purchase Invoice with stock, return
	of goods; the Item is inspected on receipt) and of submitted Quarantine Intakes (the Item names a QI team) that have
	no submitted inspection. Only what of the row is still there counts (``remaining_parts``: a row whose goods left by a
	Purchase Return or a move waits for nothing). Quantities in the stock UOM, label 'PR-0001 row 2', ``batches``
	{batch: qty} and ``serials`` the tracked parts."""
	from .quarantine_inspection import is_intake, remaining_parts

	candidates = []
	if cint(frappe.get_cached_value("Item", item_code, "inspection_required_before_purchase")):
		for doctype, filters in (
			("Purchase Receipt", {"is_return": 0}),
			("Purchase Invoice", {"is_return": 0, "update_stock": 1}),
			("Delivery Note", {"is_return": 1}),
			("Sales Invoice", {"is_return": 1, "update_stock": 1}),
		):
			rows = frappe.get_all(
				f"{doctype} Item",
				filters={
					"item_code": item_code,
					"warehouse": warehouse,
					"docstatus": 1,
					"parenttype": doctype,
				},
				fields=["name", "parent", "idx", "quality_inspection"],
			)
			if not rows:
				continue
			parents = set(
				frappe.get_all(
					doctype,
					filters={"name": ["in", sorted({row.parent for row in rows})], "docstatus": 1, **filters},
					pluck="name",
				)
			)
			candidates.extend((doctype, row) for row in rows if row.parent in parents)
	if item_team(item_code):
		rows = frappe.get_all(
			"Stock Entry Detail",
			filters={"item_code": item_code, "t_warehouse": warehouse, "docstatus": 1},
			fields=["name", "parent", "idx", "quality_inspection"],
		)
		uses_approval_route = bool(get_policy("stock_entry_route"))
		intakes = (
			set(
				frappe.get_all(
					"Stock Entry",
					filters={
						"name": ["in", sorted({row.parent for row in rows})],
						"docstatus": 1,
						**({"operation_approval_route": "Quarantine Intake"} if uses_approval_route else {}),
					},
					pluck="name",
				)
			)
			if rows
			else set()
		)
		if not uses_approval_route:
			intakes = {name for name in intakes if is_intake(frappe.get_doc("Stock Entry", name))}
		candidates.extend(("Stock Entry", row) for row in rows if row.parent in intakes)
	found = []
	documents = {}
	for doctype, row in candidates:
		if _inspected(row.quality_inspection):
			continue
		doc = documents.get((doctype, row.parent)) or frappe.get_doc(doctype, row.parent)
		documents[(doctype, row.parent)] = doc
		source = next((line for line in doc.get("items") or [] if line.name == row.name), None)
		parts = remaining_parts(doc, source, warehouse) if source else []
		if not parts:
			continue
		batches = {}
		for part in parts:
			if part.get("batch_no"):
				batches[part["batch_no"]] = batches.get(part["batch_no"], 0) + flt(part["qty"])
		found.append(
			{
				"label": f"{row.parent} row {row.idx}",
				"qty": sum(flt(part["qty"]) for part in parts),
				"batches": batches,
				"serials": {serial for part in parts for serial in part.get("serial_nos") or []},
			}
		)
	return found


def _inspected(inspection):
	"""Whether the row has a submitted Quality Inspection."""
	return bool(inspection) and frappe.db.get_value("Quality Inspection", inspection, "docstatus") == 1


def _asked_parts(row):
	"""({batch: qty}, {serials}) a Stock Entry row takes out; empty when the row names none."""
	from .quarantine_inspection import _row_batches, _row_serial_nos

	return _row_batches(row), set(_row_serial_nos(row))


def uninspected_moves(doc):
	"""[(item_code, warehouse, quantity to move, [labels of the rows still waiting])] for the rows of a Stock Entry that take
	out of a quarantine warehouse goods that still wait for their Quality Inspection. An Item without batch or serial
	number: what the warehouse holds less what still waits is free to move. A batch or serial Item: the batches and
	serial numbers the entry takes must not be those that wait (the quantity of a batch less what waits of it)."""
	asked = {}
	for row in doc.get("items") or []:
		source = row.get("s_warehouse") or doc.get("from_warehouse")
		if not source or not row.get("item_code"):
			continue
		entry = asked.setdefault((row.item_code, source), {"qty": 0, "batches": {}, "serials": set()})
		entry["qty"] += _stock_qty(row)
		batches, serials = _asked_parts(row)
		for batch, qty in batches.items():
			entry["batches"][batch] = entry["batches"].get(batch, 0) + flt(qty)
		entry["serials"] |= serials
	refused = []
	for (item_code, source), want in sorted(asked.items()):
		if not _is_quarantine_warehouse(source, doc.get("company")):
			continue
		waiting = waiting_goods(item_code, source)
		if not waiting:
			continue
		blocked = _blocked_by_tracking(waiting, want, source, item_code)
		if blocked is not None:
			if blocked:
				refused.append((item_code, source, want["qty"], blocked))
			continue
		held = flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": source}, "actual_qty"))
		free = held - sum(flt(entry["qty"]) for entry in waiting)
		if want["qty"] > max(free, 0) + 1e-9:
			refused.append((item_code, source, want["qty"], [entry["label"] for entry in waiting]))
	return refused


def _blocked_by_tracking(waiting, want, source, item_code):
	"""The labels of the waiting rows whose serial numbers or batches the entry takes; [] when it takes none of what
	waits; None when the entry names no serial number or batch (the quantity rule decides)."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	if want["serials"]:
		return [entry["label"] for entry in waiting if entry["serials"] & want["serials"]]
	if want["batches"]:
		labels = []
		for batch, qty in want["batches"].items():
			rows = [entry for entry in waiting if entry["batches"].get(batch)]
			if not rows:
				continue
			free = flt(get_batch_qty(batch_no=batch, warehouse=source)) - sum(
				entry["batches"][batch] for entry in rows
			)
			if qty > max(free, 0) + 1e-9:
				labels.extend(entry["label"] for entry in rows)
		return labels
	return None


def _is_quarantine_warehouse(warehouse, company):
	from erpnext.stock.warehouse_policy import warehouse_is_quarantine

	return warehouse_is_quarantine(warehouse)


def refuse_staging_target(doc, method=None):
	"""Stock Entry validate: a Material Transfer into a normal (stock) warehouse puts its goods at a storage location,
	never at a Receiving location (Siwat, 17 Nov 2026: Receiving stock is never picked, so the move would only hide the
	goods). It covers every such transfer (WP1, 18 Nov 2026): out of quarantine, between two normal warehouses and a
	put-away inside one; a put-away to the location the goods already lie at is refused too. A draft is saved with an
	orange warning naming the rows (the fork no longer fills an empty target, so the form shows what the save stores);
	the submit refuses it. A draft the task made with the location left to the team (``flags.save_incomplete``) is not
	warned about. Goods moved into a holding warehouse (quarantine, damage ...) keep its bin."""
	from erpnext.stock.doctype.storage_location.storage_location import (
		is_receiving_location,
		is_stock_type_warehouse,
	)

	if (
		getattr(doc, "doctype", None) != "Stock Entry"
		or doc.get("purpose") != "Material Transfer"
		or cint(doc.get("docstatus")) not in (0, 1)
		or (doc.get("flags") or {}).get("save_incomplete")
	):
		return
	refused = []
	for row in doc.get("items") or []:
		source = row.get("s_warehouse") or doc.get("from_warehouse")
		target = row.get("t_warehouse") or doc.get("to_warehouse")
		location = row.get("to_storage_location")
		if not source or not target or not location or not is_stock_type_warehouse(target):
			continue
		if is_receiving_location(location, target) or (
			source == target and location == row.get("storage_location")
		):
			refused.append(f"{row.get('idx')}: {row.get('item_code')} -> {location}")
	if not refused:
		return
	message = (
		_(
			"Goods cannot be moved to a receiving location or to the location they already lie at: choose the "
			"storage location they are put away to (To Storage Location) on each row."
		)
		+ "<br>"
		+ "<br>".join(refused)
	)
	if cint(doc.get("docstatus")) == 1:
		frappe.throw(message, title=_("Choose the Storage Location"))
	frappe.msgprint(message, title=_("Choose the Storage Location"), indicator="orange")


INTERNAL_ROUTES = ("Quarantine Intake", ROUTE)


def refuse_quarantine_extras(doc, method=None):
	"""Stock Entry validate (UX WP9, 19 Nov 2026): a Quarantine Intake or a Quarantine Release moves goods between a
	warehouse and its quarantine inside the company, so it has no transit leg (Add to Transit), no Shipment and no
	Additional Costs; a draft that sets one is refused, naming it. The route is judged as if Add to Transit were off
	(a transit move otherwise takes the Stock route and would never be recognised)."""
	if (
		getattr(doc, "doctype", None) != "Stock Entry"
		or doc.get("purpose") != "Material Transfer"
		or cint(doc.get("docstatus")) not in (0, 1)
	):
		return
	transit = doc.get("add_to_transit")
	extras = [
		label
		for label, present in (
			(_("Add to Transit"), cint(transit)),
			(_("Requires Shipment"), cint(doc.get("requires_shipment"))),
			(_("Additional Costs"), bool(doc.get("additional_costs"))),
		)
		if present
	]
	if not extras:
		return
	doc.add_to_transit = 0
	try:
		stock_entry_route = get_policy("stock_entry_route")
		route = stock_entry_route(doc) if stock_entry_route else physical_quarantine_route(doc)
	finally:
		doc.add_to_transit = transit
	if route not in INTERNAL_ROUTES:
		return
	frappe.throw(
		_("A {0} moves goods inside the company: clear {1}.").format(_(route), ", ".join(extras)),
		title=_("Not Part of a Quarantine Move"),
	)


def physical_quarantine_route(doc):
	if doc.get("purpose") != "Material Transfer" or doc.get("add_to_transit"):
		return None
	if route := zone_transfer_route(doc):
		return route
	if not doc.get("company") or not doc.get("items") or doc.get("outgoing_stock_entry"):
		return None
	warehouses = {
		name
		for row in doc.items
		for name in (
			row.get("s_warehouse") or doc.get("from_warehouse"),
			row.get("t_warehouse") or doc.get("to_warehouse"),
		)
		if name
	}
	info = {
		name: frappe.db.get_value("Warehouse", name, ["warehouse_type", "company"], as_dict=True)
		for name in warehouses
	}
	from erpnext.stock.warehouse_policy import is_quarantine_type

	quarantine = {name for name, row in info.items() if row and is_quarantine_type(row.warehouse_type)}
	if is_quarantine_release(doc, info, quarantine):
		return ROUTE
	if all(
		(source := row.get("s_warehouse") or doc.get("from_warehouse")) in warehouses
		and source not in quarantine
		and (target := row.get("t_warehouse") or doc.get("to_warehouse")) in quarantine
		and all(info.get(name) and info[name].company == doc.company for name in (source, target))
		for row in doc.items
	):
		return "Quarantine Intake"


def refuse_manual_move_out(doc, method=None):
	"""Stock Entry before_validate: a Stock Entry takes goods out of a Quarantine warehouse only when they are inspected
	(see the module note). The message names the rows that still wait for the QI team. Moves into quarantine and the
	Stock Count's own posting are not touched. It runs before the warehouse type's own outgoing-role check."""
	from erpnext.stock.doctype.stock_count.stock_count import is_count_posting

	# docstatus 1 is the submit itself (Frappe runs before_validate with it set): checked again, so a draft that was valid
	# when it was saved cannot take goods that have arrived since
	if (
		getattr(doc, "doctype", None) != "Stock Entry"
		or cint(doc.get("docstatus")) not in (0, 1)
		or is_count_posting(doc)
	):
		return
	if not get_policy("stock_entry_route") and physical_quarantine_route(doc) == "Quarantine Intake":
		from .quarantine_inspection import require_own_quarantine

		require_own_quarantine(doc)
	validate_quarantine_zones(doc)
	refused = uninspected_moves(doc)
	if not refused:
		return
	lines = [
		_("{0} x {1} cannot leave {2} yet; waiting for the Quality Inspection: {3}.").format(
			flt(quantity), frappe.bold(item_code), source, ", ".join(labels)
		)
		for item_code, source, quantity, labels in refused
	]
	frappe.throw(
		"<br>".join(lines)
		+ "<br>"
		+ _("The QI team inspects the goods first; then move them out with a Stock Entry."),
		frappe.PermissionError,
		title=_("Goods Not Inspected"),
	)


def validate_quarantine_zones(doc, method=None):
	from erpnext.stock.doctype.stock_count.stock_count import is_count_posting
	from erpnext.stock.doctype.storage_location.storage_location import (
		DAMAGE,
		LOST,
		PUTAWAY_LOCATION_TYPES,
		QUALITY_INSPECTION,
		get_location_type,
		stock_source_locations,
	)

	from .quarantine_inspection import remaining_parts, row_inspection

	if is_count_posting(doc):
		return
	is_stock_write_off = get_policy("is_stock_write_off")
	route = None
	allocated = {}
	for row in doc.get("items") or []:
		source = row.get("s_warehouse") or doc.get("from_warehouse")
		target = row.get("t_warehouse") or doc.get("to_warehouse")
		location = row.get("storage_location")
		source_type = get_location_type(location, source)
		target_type = get_location_type(row.get("to_storage_location"), target)
		if (
			source
			and row.get("serial_and_batch_bundle")
			and any(
				place != location and get_location_type(place, source) in (QUALITY_INSPECTION, DAMAGE, LOST)
				for place in stock_source_locations(row)
			)
		):
			frappe.throw(_("Use the actual restricted location of the tracked stock as its source."))
		if target_type == LOST or (source_type == LOST and not (is_stock_write_off and is_stock_write_off(doc))):
			frappe.throw(
				_("Only Stock Count or a Stock Write-Off can move stock into or out of a Lost location.")
			)
		if source_type == DAMAGE and not (
			doc.get("purpose") == "Material Issue" and doc.get("operation_issue_type") == "Disposal"
		):
			frappe.throw(_("Stock in a Damage location may only be disposed of or returned to its supplier."))
		if QUALITY_INSPECTION not in (source_type, target_type):
			continue
		route = route or zone_transfer_route(doc)
		if not route:
			frappe.throw(
				_("Use a separate Material Transfer within the same warehouse for Quality Inspection.")
			)
		if route == "Quarantine Intake":
			if not item_team(row.item_code):
				frappe.throw(_("Set an incoming Quality Inspection team for Item {0}.").format(row.item_code))
			continue
		# A release must identify its submitted receipt or intake row. The source ledger entry also proves the location.
		if not row.get("ste_detail"):
			frappe.throw(_("Create the quarantine release from its submitted receipt or quarantine intake."))
		origin = frappe.db.get_value(
			"Stock Ledger Entry",
			{
				"voucher_detail_no": row.ste_detail,
				"warehouse": source,
				"storage_location": location,
				"item_code": row.item_code,
				"is_cancelled": 0,
				"actual_qty": [">", 0],
				"voucher_type": [
					"in",
					("Purchase Receipt", "Purchase Invoice", "Delivery Note", "Sales Invoice", "Stock Entry"),
				],
			},
			["voucher_type", "voucher_no"],
			as_dict=True,
		)
		if not origin:
			frappe.throw(_("The item was not received into this inspection location on the selected row."))
		submitting = cint(doc.get("docstatus")) == 1
		reference = frappe.get_doc(origin.voucher_type, origin.voucher_no, for_update=submitting)
		original_row = next((line for line in reference.items if line.name == row.ste_detail), None)
		if not original_row or reference.docstatus != 1 or reference.company != doc.company:
			frappe.throw(_("Select a submitted receipt or quarantine intake of this company."))
		inspection = row_inspection(reference, original_row, submitted=True, lock=submitting)
		if not inspection or inspection.status not in ("Accepted", "Rejected"):
			frappe.throw(_("Submit the source row's Quality Inspection before moving these goods."))
		if target_type or submitting:
			expected = (DAMAGE,) if inspection.status == "Rejected" else PUTAWAY_LOCATION_TYPES
			if target_type not in expected:
				frappe.throw(
					_("Goods with result {0} must go to {1}.").format(inspection.status, " / ".join(expected))
				)
		parts = {
			part["batch_no"]: part
			for part in remaining_parts(reference, original_row, source, current=submitting)
		}
		batches, serials = _asked_parts(row)
		if not batches:
			batches = {None: _stock_qty(row)}
		held_serials = {serial for part in parts.values() for serial in part.get("serial_nos") or []}
		if (
			abs(sum(batches.values()) - _stock_qty(row)) > 1e-9
			or serials - held_serials
			or (held_serials and len(serials) != _stock_qty(row))
		):
			frappe.throw(_("Release only the quantities and serial numbers still held for the selected row."))
		for batch, quantity in batches.items():
			part = parts.get(batch) or {}
			used = allocated.setdefault((row.ste_detail, batch), {"qty": 0, "serials": set()})
			used["qty"] += quantity
			held_serials = set(part.get("serial_nos") or [])
			part_serials = serials & held_serials
			if (
				used["qty"] > flt(part.get("qty")) + 1e-9
				or (held_serials and (len(part_serials) != quantity or used["serials"] & part_serials))
				or (serials and not held_serials)
			):
				frappe.throw(
					_("Release only the quantities and serial numbers still held for the selected row.")
				)
			used["serials"].update(part_serials)


def prepare_and_validate_stock_entry(doc, method=None):
	"""Keep application-specific bundle permission preparation before the final custody check."""
	prepare = get_policy("prepare_stock_entry_submit")
	if prepare:
		prepare(doc, method)
	validate_quarantine_zones(doc, method)
