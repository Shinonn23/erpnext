"""The inspection units and quantities of one purchase receipt row."""

from collections import defaultdict
from math import isfinite

import frappe
from frappe import _
from frappe.utils import cint, flt


def split_units(qty, serials, batches, serial_batches, granularity):
	"""Return unique units in stock UOM; serial_batches maps each serial to its received batch."""
	qty = abs(flt(qty))
	if not isfinite(qty) or qty <= 0:
		raise ValueError("Received quantity must be positive and finite")
	serials = list(serials)
	if len(serials) != len(set(serials)):
		raise ValueError("Duplicate serial numbers in receipt row")
	if serials and abs(len(serials) - qty) > 1e-9:
		raise ValueError("Serial numbers do not match received quantity")
	if batches and (
		any(not isfinite(flt(q)) or flt(q) == 0 for q in batches.values())
		or abs(sum(abs(flt(q)) for q in batches.values()) - qty) > 1e-9
	):
		raise ValueError("Batch quantities do not match received quantity")
	if serials and batches:
		counts = defaultdict(int)
		for serial in serials:
			batch = serial_batches.get(serial)
			if batch not in batches:
				raise ValueError("Serial number does not belong to a received batch")
			counts[batch] += 1
		if any(abs(counts[batch] - abs(flt(count))) > 1e-9 for batch, count in batches.items()):
			raise ValueError("Serial numbers do not match received batch quantities")
	if serials and granularity == "Serial Number":
		return [
			{"batch_no": serial_batches.get(s), "serial_no": s, "qty": 1, "serials": [s]} for s in serials
		]
	if batches:
		return [
			{
				"batch_no": batch,
				"serial_no": None,
				"qty": abs(flt(count)),
				"serials": [s for s in serials if serial_batches.get(s) == batch],
			}
			for batch, count in batches.items()
		]
	return [{"batch_no": None, "serial_no": None, "qty": qty, "serials": serials}]


def receipt_units(row):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
	from erpnext.stock.serial_batch_bundle import get_batch_nos
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	qty = abs(flt(row.get("transfer_qty") or row.get("stock_qty") or row.get("qty")))
	bundle = row.get("serial_and_batch_bundle")
	serials = bundle_serial_nos(bundle) if bundle else get_serial_nos(row.get("serial_no") or "")
	if not cint(frappe.get_cached_value("Item", row.item_code, "has_serial_no")):
		serials = []
	batches = (
		{batch: abs(flt(entry.qty)) for batch, entry in get_batch_nos(bundle).items()}
		if bundle
		else {row.batch_no: qty}
		if row.get("batch_no")
		else {}
	)
	serial_batches = {}
	if row.get("serial_and_batch_bundle"):
		entries = frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": row.serial_and_batch_bundle},
			fields=["serial_no", "batch_no"],
			order_by="idx",
		)
		serial_batches = {entry.serial_no: entry.batch_no for entry in entries if entry.serial_no}
	elif serials and batches:
		serial_batches = {serial: row.get("batch_no") for serial in serials}
	granularity = (
		row.get("purchase_inspection_granularity")
		or frappe.get_cached_value("Item", row.item_code, "purchase_inspection_granularity")
		or "Batch/Lot"
	)
	try:
		return split_units(qty, serials, batches, serial_batches, granularity)
	except ValueError as exc:
		frappe.throw(_("Row {0} ({1}): {2}.").format(row.idx, row.item_code, str(exc)))


@frappe.whitelist()
def inspection_serial_results(name):
	"""Return received serials for an existing lot inspection without changing its draft."""
	doc = frappe.get_doc("Quality Inspection", name)
	doc.check_permission("read")
	if doc.reference_type not in ("Purchase Receipt", "Purchase Invoice", "Pick List") or not doc.purchase_unit_qty or doc.item_serial_no:
		return []
	reference = frappe.get_doc(doc.reference_type, doc.reference_name)
	reference.check_permission("read")
	if doc.reference_type == "Pick List":
		from connect_diagnostics_setup.stock.outgoing_inspection import source_rows
		rows = source_rows(reference)
	else:
		rows = reference.items
	row = next((r for r in rows if r.name == doc.child_row_reference and r.item_code == doc.item_code), None)
	if not row:
		frappe.throw(_("The source row for this inspection was not found."))
	if doc.reference_type == "Pick List":
		from connect_diagnostics_setup.stock.outgoing_inspection import units
		source_units = units(row)
	else:
		source_units = receipt_units(row)
	unit = match_unit(source_units, doc.batch_no, None)
	if not unit:
		frappe.throw(_("The inspection unit does not match the receipt."))
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	rejected = set(get_serial_nos(doc.purchase_rejected_serials or ""))
	return [{"serial_no": serial, "result": "Rejected" if serial in rejected else "Accepted"} for serial in unit["serials"]]


def freeze_purchase_inspection_granularity(doc):
	"""Keep the inspection unit chosen when a stock purchase is submitted."""
	if doc.get("docstatus") != 1 or doc.get("is_return"):
		return
	if doc.get("doctype") == "Purchase Invoice" and not doc.get("update_stock"):
		return
	for row in doc.get("items") or []:
		if row.get("item_code") and (
			getattr(doc, "_action", None) == "submit" or not row.get("purchase_inspection_granularity")
		):
			item = frappe.get_cached_value(
				"Item",
				row.item_code,
				["is_stock_item", "inspection_required_before_purchase", "purchase_inspection_granularity"],
				as_dict=True,
			)
			if item and item.get("is_stock_item"):
				row.purchase_inspection_granularity = (
					(item.get("purchase_inspection_granularity") or "Batch/Lot")
					if item.get("inspection_required_before_purchase")
					else "Not Required"
				)


def new_purchase_inspection(doc, row, unit, remarks=None):
	"""Build an unsaved incoming inspection for one exact purchase receipt unit."""
	inspection = frappe.get_doc(
		{
			"doctype": "Quality Inspection",
			"company": doc.company,
			"inspection_type": "Incoming",
			"reference_type": doc.doctype,
			"reference_name": doc.name,
			"child_row_reference": row.name,
			"item_code": row.item_code,
			"batch_no": unit["batch_no"],
			"item_serial_no": unit["serial_no"],
			"purchase_unit_qty": unit["qty"],
			"sample_size": unit["qty"],
			"quality_inspection_template": frappe.db.get_value(
				"Item", row.item_code, "quality_inspection_template"
			),
			"inspected_by": frappe.session.user,
			"remarks": remarks
			or _("Goods at the receiving location ({0} row {1})").format(doc.name, row.idx),
			"manual_inspection": 0,
		}
	)
	if not unit["serial_no"]:
		for serial in unit["serials"]:
			inspection.append("purchase_serial_results", {"serial_no": serial, "result": "Accepted"})
	if inspection.quality_inspection_template:
		inspection.get_item_specification_details()
		from erpnext.stock.doctype.quality_inspection_reading.quality_inspection_reading import (
			has_automatic_criteria,
		)

		for reading in inspection.get("readings") or []:
			reading.manual_inspection = int(not has_automatic_criteria(reading))
			reading.status = ""
	return inspection


def validate_result(unit, accepted, rejected, rejected_serials):
	accepted, rejected = flt(accepted), flt(rejected)
	rejected_serials = list(rejected_serials)
	if (
		not isfinite(accepted)
		or not isfinite(rejected)
		or accepted < 0
		or rejected < 0
		or abs(accepted + rejected - unit["qty"]) > 1e-9
	):
		raise ValueError("Accepted and rejected quantities must add up to the received quantity")
	if len(rejected_serials) != len(set(rejected_serials)) or not set(rejected_serials) <= set(
		unit["serials"]
	):
		raise ValueError("Rejected serial numbers must be unique and belong to this inspection unit")
	if unit["serials"] and len(rejected_serials) != rejected:
		raise ValueError("Rejected serial count must equal rejected quantity")
	if not unit["serials"] and rejected_serials:
		raise ValueError("This inspection unit has no serial numbers")


def match_unit(units, batch, serial):
	return next(
		(
			unit
			for unit in units
			if (unit["batch_no"] or None) == (batch or None)
			and (unit["serial_no"] or None) == (serial or None)
		),
		None,
	)


def unit_has_moved(unit, held_parts):
	"""Whether any goods of an inspection unit have left their receiving location."""
	parts = [part for part in held_parts if (part.get("batch_no") or None) == (unit["batch_no"] or None)]
	if unit["serials"]:
		held = {serial for part in parts for serial in part.get("serial_nos") or []}
		return not set(unit["serials"]).issubset(held)
	return sum(flt(part.get("qty")) for part in parts) + 1e-9 < unit["qty"]


def has_active_serial_outflow(inspection, row, unit):
	"""Find an unreversed movement even if a later transfer brought the serial back."""
	if not unit["serials"] or not inspection.get("creation"):
		return False
	location_filter = (
		"and coalesce(nullif(entry.storage_location, ''), sle.storage_location) = %(location)s"
		if row.get("storage_location")
		else ""
	)
	params = {
		"item": row.item_code,
		"warehouse": row.warehouse,
		"created": inspection.creation,
		"serials": tuple(unit["serials"]),
		"location": row.get("storage_location"),
	}
	if frappe.db.sql(
		"""select entry.serial_no from `tabStock Ledger Entry` sle
		join `tabSerial and Batch Entry` entry on entry.parent = sle.serial_and_batch_bundle
		where sle.item_code = %(item)s and sle.warehouse = %(warehouse)s and sle.is_cancelled = 0
		and sle.actual_qty < 0 and sle.creation >= %(created)s and entry.serial_no in %(serials)s
		"""
		+ location_filter
		+ " limit 1 for update",
		params,
	):
		return True
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	legacy_location = "and sle.storage_location = %(location)s" if row.get("storage_location") else ""
	legacy = frappe.db.sql(
		"""select sle.serial_no from `tabStock Ledger Entry` sle
		where sle.item_code = %(item)s and sle.warehouse = %(warehouse)s and sle.is_cancelled = 0
		and sle.actual_qty < 0 and sle.creation >= %(created)s and coalesce(sle.serial_no, '') != ''
		"""
		+ legacy_location
		+ " for update",
		params,
		as_dict=True,
	)
	return any(set(get_serial_nos(leg.serial_no)) & set(unit["serials"]) for leg in legacy)


@frappe.whitelist()
def serial_inspection_history(serial_no):
	"""Return only inspections the current user may read for this Serial Number."""
	frappe.get_doc("Serial No", serial_no).check_permission("read")
	children = frappe.get_all(
		"Purchase Inspection Serial",
		filters={
			"serial_no": serial_no,
			"parenttype": "Quality Inspection",
			"parentfield": "purchase_serial_results",
		},
		fields=["parent", "result"],
	)
	results = {entry.parent: entry.result for entry in children}
	legacy = frappe.get_list(
		"Quality Inspection",
		filters={"item_serial_no": serial_no, "docstatus": ["<", 2]},
		fields=["name", "reference_type", "reference_name", "batch_no", "status", "docstatus"],
	)
	linked = (
		frappe.get_list(
			"Quality Inspection",
			filters={"name": ["in", list(results)], "docstatus": ["<", 2]},
			fields=["name", "reference_type", "reference_name", "batch_no", "status", "docstatus"],
		)
		if results
		else []
	)
	by_name = {row.name: row for row in legacy + linked}
	for row in by_name.values():
		row["serial_result"] = results.get(row.name, row.status if row.docstatus == 1 else None)
	return list(by_name.values())


def inspections_for_row(doc, row, lock=False):
	fields = [
		"name",
		"status",
		"docstatus",
		"batch_no",
		"item_serial_no",
		"purchase_unit_qty",
		"purchase_accepted_qty",
		"purchase_rejected_qty",
		"purchase_rejected_serials",
	]
	filters = {
		"reference_type": doc.doctype,
		"reference_name": doc.name,
		"child_row_reference": row.name,
		"docstatus": ["<", 2],
	}
	if lock:
		return frappe.qb.get_query("Quality Inspection", filters=filters, fields=fields, for_update=True).run(
			as_dict=True
		)
	return frappe.get_all("Quality Inspection", filters=filters, fields=fields, order_by="creation")


def inspection_progress(units, inspections):
	"""Match active inspections to receipt units without changing any stored result."""
	matched = {}
	legacy = [qi for qi in inspections if not flt(qi.get("purchase_unit_qty"))]
	if legacy:
		if len(inspections) != 1:
			raise ValueError("Legacy inspection cannot be safely matched to receipt units")
		matched = dict.fromkeys(range(len(units)), legacy[0])
	else:
		for qi in inspections:
			unit = match_unit(units, qi.get("batch_no"), qi.get("item_serial_no"))
			if unit is None or abs(flt(qi.get("purchase_unit_qty")) - unit["qty"]) > 1e-9:
				raise ValueError("Inspection no longer matches the received unit")
			index = units.index(unit)
			if index in matched:
				raise ValueError("More than one inspection covers the same unit")
			matched[index] = qi
	return [
		{
			"unit": unit,
			"inspection": matched.get(index),
			"state": "missing"
			if index not in matched
			else "submitted"
			if matched[index].get("docstatus") == 1
			else "draft",
		}
		for index, unit in enumerate(units)
	]


def create_purchase_unit_inspections(doc, row, progress):
	"""Create missing drafts and return accessible existing results without changing them."""
	result = {"created": [], "draft": [], "submitted": [], "skipped": []}
	for part in progress:
		frappe.db.savepoint("purchase_qi_unit")
		try:
			if part["state"] == "missing":
				inspection = new_purchase_inspection(doc, row, part["unit"])
				inspection.insert()
				result["created"].append(inspection.name)
			else:
				name = part["inspection"]["name"]
				if name not in result[part["state"]]:
					frappe.get_doc("Quality Inspection", name).check_permission("read")
					result[part["state"]].append(name)
		except (frappe.ValidationError, frappe.PermissionError) as exc:
			frappe.db.rollback(save_point="purchase_qi_unit")
			unit = part["unit"]
			label = unit.get("serial_no") or unit.get("batch_no") or str(row.idx)
			result["skipped"].append(
				_("Row {0} ({1}), unit {2}: {3}").format(row.idx, row.item_code, label, str(exc))
			)
		finally:
			frappe.db.release_savepoint("purchase_qi_unit")
	return result


def result_parts(unit, inspection):
	"""Split a submitted unit into accepted and rejected stock quantities and serials."""
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	if inspection.get("docstatus") != 1 or inspection.get("status") not in ("Accepted", "Rejected"):
		raise ValueError("Inspection is not submitted with a final result")
	if flt(inspection.get("purchase_unit_qty")):
		accepted = flt(inspection.get("purchase_accepted_qty"))
		rejected = flt(inspection.get("purchase_rejected_qty"))
		rejected_serials = get_serial_nos(inspection.get("purchase_rejected_serials") or "")
	else:
		rejected = unit["qty"] if inspection.get("status") == "Rejected" else 0
		accepted = unit["qty"] - rejected
		rejected_serials = unit["serials"] if rejected else []
	validate_result(unit, accepted, rejected, rejected_serials)
	if bool(rejected) != (inspection.get("status") == "Rejected"):
		raise ValueError("Inspection status does not match rejected quantity")
	rejected_set = set(rejected_serials)
	return {
		"accepted": {
			"qty": accepted,
			"batch_no": unit["batch_no"],
			"serial_nos": [serial for serial in unit["serials"] if serial not in rejected_set],
		},
		"rejected": {"qty": rejected, "batch_no": unit["batch_no"], "serial_nos": rejected_serials},
	}


def summarize_progress(units, inspections):
	"""Count received units and final result quantities without treating drafts as inspected."""
	progress = inspection_progress(units, inspections)
	summary = {
		"required": len(units),
		"missing": 0,
		"draft": 0,
		"submitted": 0,
		"accepted_qty": 0,
		"rejected_qty": 0,
	}
	for entry in progress:
		summary[entry["state"]] += 1
		if entry["state"] == "submitted":
			parts = result_parts(entry["unit"], entry["inspection"])
			summary["accepted_qty"] += parts["accepted"]["qty"]
			summary["rejected_qty"] += parts["rejected"]["qty"]
	return summary


def completed_row_inspection(units, inspections):
	"""Return an aggregate row result only when every required inspection is submitted."""
	progress = inspection_progress(units, inspections)
	if not progress or any(entry["state"] != "submitted" for entry in progress):
		return None
	parts = [result_parts(entry["unit"], entry["inspection"]) for entry in progress]
	return frappe._dict(
		name=progress[0]["inspection"]["name"],
		docstatus=1,
		status="Rejected" if any(part["rejected"]["qty"] > 0 for part in parts) else "Accepted",
	)


def remaining_result_parts(unit, inspection, movements):
	"""Allocate a unit's outward movements against its submitted result, in posting order.

	Movements must already be attributed to this receipt unit and carry a disposition flag for
	returns or movements to a rejected-goods destination. Serialised movements identify their serials.
	"""
	parts = result_parts(unit, inspection)
	for movement in movements:
		quantity = flt(movement.get("qty"))
		serials = movement.get("serial_nos") or []
		if not isfinite(quantity) or quantity < 0:
			raise ValueError("Movement quantity cannot be negative")
		if unit["serials"]:
			if len(serials) != quantity or len(serials) != len(set(serials)):
				raise ValueError("Movement quantity must match its unique serial numbers")
			for serial in serials:
				kind = next(
					(kind for kind in ("accepted", "rejected") if serial in parts[kind]["serial_nos"]), None
				)
				if not kind or (kind == "rejected" and not movement.get("disposition")):
					raise ValueError("Movement includes an unavailable or rejected serial number")
				parts[kind]["serial_nos"].remove(serial)
				parts[kind]["qty"] -= 1
		else:
			if serials:
				raise ValueError("Movement names serial numbers for an untracked unit")
			for kind in ("rejected", "accepted") if movement.get("disposition") else ("accepted",):
				taken = min(quantity, parts[kind]["qty"])
				parts[kind]["qty"] -= taken
				quantity -= taken
			if quantity > 1e-9:
				raise ValueError("Movement exceeds the eligible inspected quantity")
	return parts


def remaining_inspection_results(units, inspections, held_parts, movements):
	"""Reconcile final inspection quantities against stock still held for this receipt row."""
	progress = inspection_progress(units, inspections)
	held = {part.get("batch_no"): part for part in held_parts}
	results = []
	for entry in progress:
		if entry["state"] != "submitted":
			continue
		unit = entry["unit"]
		batch = unit["batch_no"]
		available = held.get(batch, {})
		if unit["serials"]:
			parts = result_parts(unit, entry["inspection"])
			serials = set(available.get("serial_nos") or [])
			for part in parts.values():
				part["serial_nos"] = [serial for serial in part["serial_nos"] if serial in serials]
				part["qty"] = len(part["serial_nos"])
		else:
			parts = remaining_result_parts(
				unit,
				entry["inspection"],
				[movement for movement in movements if movement.get("batch_no") == batch],
			)
			remaining = sum(part["qty"] for part in parts.values())
			if abs(remaining - flt(available.get("qty"))) > 1e-9:
				raise ValueError("Inspected quantities do not match the remaining stock for this receipt")
		results.append({"unit": unit, "inspection": entry["inspection"], **parts})
	return results


def validate_purchase_unit_inspection(doc, reference, row, method):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	units = receipt_units(row)
	unit = match_unit(units, doc.get("batch_no"), doc.get("item_serial_no"))
	if (
		not unit
		or doc.get("item_code") != row.item_code
		or abs(flt(doc.purchase_unit_qty) - unit["qty"]) > 1e-9
	):
		frappe.throw(
			_("The inspection unit must match the received Item, Batch, Serial Number and quantity.")
		)
	if flt(doc.get("sample_size")) > unit["qty"]:
		frappe.throw(_("The sample size cannot exceed the received unit quantity."))
	for other in inspections_for_row(reference, row, lock=True):
		if other.name == doc.get("name"):
			continue
		if not flt(other.purchase_unit_qty) or match_unit([unit], other.batch_no, other.item_serial_no):
			frappe.throw(_("Quality Inspection {0} already covers this receipt unit.").format(other.name))
	if method != "before_submit":
		return
	if doc.get("status") not in ("Accepted", "Rejected") or not doc.get("readings"):
		frappe.throw(_("Choose an inspection status and add at least one reading before submitting."))
	if doc.status == "Accepted" and any(r.get("status") == "Rejected" for r in doc.get("readings") or []):
		frappe.throw(_("A Quality Inspection with a rejected reading cannot be accepted."))
	if unit["serial_no"]:
		doc.purchase_accepted_qty = 1 if doc.status == "Accepted" else 0
		doc.purchase_rejected_qty = 1 - doc.purchase_accepted_qty
		doc.purchase_rejected_serials = unit["serial_no"] if doc.purchase_rejected_qty else ""
	elif doc.get("purchase_serial_results"):
		results = doc.get("purchase_serial_results")
		serials = [row.get("serial_no") for row in results]
		if len(serials) != len(set(serials)) or set(serials) != set(unit["serials"]):
			frappe.throw(_("Serial numbers must match the received inspection unit."))
		if any(row.get("result") not in ("Accepted", "Rejected") for row in results):
			frappe.throw(_("Choose Accepted or Rejected for every serial number."))
		rejected = [row.get("serial_no") for row in results if row.get("result") == "Rejected"]
		doc.purchase_rejected_serials = "\n".join(rejected)
		doc.purchase_rejected_qty = len(rejected)
		doc.purchase_accepted_qty = len(serials) - len(rejected)
	try:
		validate_result(
			unit,
			doc.get("purchase_accepted_qty"),
			doc.get("purchase_rejected_qty"),
			get_serial_nos(doc.get("purchase_rejected_serials") or ""),
		)
	except ValueError as exc:
		frappe.throw(_(str(exc)))
	if bool(flt(doc.get("purchase_rejected_qty"))) != (doc.status == "Rejected"):
		frappe.throw(_("Use Rejected status if any quantity is rejected; otherwise use Accepted."))
	rejected = set(get_serial_nos(doc.get("purchase_rejected_serials") or ""))
	doc.set("purchase_serial_results", [])
	for serial in unit["serials"]:
		doc.append(
			"purchase_serial_results",
			{
				"serial_no": serial,
				"result": "Rejected" if serial in rejected else "Accepted",
			},
		)


def _inspected_when_moved(leg, at):
	"""Was this inflow's goods inspected when the movement at ``at`` was made? A later inspection does not change what a
	past movement took. Legs without timestamps (``inspected`` only) are judged by that flag."""
	if "inspected_at" not in leg:
		return bool(leg.get("inspected"))
	return leg["inspected_at"] is not None and (at is None or leg["inspected_at"] <= at)


def allocate(inflows, outflows, movements=None):
	"""What of each receiving row is still in the warehouse: ``{row: {batch: quantity}}``. The one place that decides it.

	Every outward movement (Stock Entry, Purchase Return, Delivery Note, ...) is attributed to the rows that put the
	goods there: a movement that names its row (``linked``: made by "Create Stock Entry", or a Purchase Return against the
	receipt row) takes from that row first; the rest, and what a row could not give, is taken first-in first-out in
	posting order from the rows that came in before it, per batch (``None`` for an Item without batches). Rows whose
	inspection was submitted when the movement was made give before rows still waiting, since only inspected goods (or
	goods nothing waits for) may be moved by hand (``quarantine_release.refuse_manual_move_out``); an inspection submitted
	or cancelled later never moves a past movement to other rows. Serial numbers are not allocated here: a serial is
	exactly where the Serial No ledger says."""
	left = {}
	for leg in inflows:
		bucket = left.setdefault(leg["row"], {})
		for batch, qty in leg["batches"].items():
			bucket[batch] = bucket.get(batch, 0) + qty
	for outgoing in sorted(outflows, key=lambda leg: leg["seq"]):
		eligible = sorted(
			(leg for leg in inflows if leg["seq"] < outgoing["seq"] and _eligible_inflow(leg, outgoing)),
			key=lambda leg: (
				leg["row"] != outgoing.get("linked"),
				not _inspected_when_moved(leg, outgoing.get("at")),
				bool(outgoing.get("disposition"))
				and leg.get("inspection_status") not in ("Rejected", "Mixed"),
				leg["seq"],
			),
		)
		for batch, qty in outgoing["batches"].items():
			for leg in eligible:
				bucket = left[leg["row"]]
				if batch not in bucket:
					continue
				given = min(qty, bucket[batch])
				bucket[batch] -= given
				qty -= given
				if given > 1e-9 and movements is not None:
					movements.setdefault(leg["row"], []).append(
						{
							"seq": outgoing["seq"],
							"qty": given,
							"batch_no": batch,
							"disposition": outgoing.get("disposition", False),
						}
					)
				if qty <= 1e-9:
					break
	return left


def _eligible_inflow(inflow, outgoing):
	if "disposition" not in outgoing or not inflow.get("needs_inspection"):
		return True
	return _inspected_when_moved(inflow, outgoing.get("at")) and (
		inflow.get("inspection_status") in ("Accepted", "Mixed")
		or (outgoing["disposition"] and inflow.get("inspection_status") == "Rejected")
	)


def _row_qty(row):
	return abs(flt(row.get("transfer_qty") or row.get("stock_qty") or row.get("qty")))


def _current_rows(doctype, filters, fields, order_by=None):
	return frappe.qb.get_query(
		doctype, filters=filters, fields=fields, order_by=order_by, for_update=True
	).run(as_dict=True)


def _held(item_code, warehouse, location=None, current=False):
	if location:
		if current:
			return sum(
				flt(row.actual_qty)
				for row in _current_rows(
					"Stock Ledger Entry",
					{
						"item_code": item_code,
						"warehouse": warehouse,
						"storage_location": location,
						"is_cancelled": 0,
					},
					["actual_qty"],
				)
			)
		from erpnext.stock.doctype.storage_location.storage_location import location_stock

		return flt(location_stock(item_code, warehouse).get(location))
	return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))


def _row_serial_nos(row, current=False):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	if row.get("serial_and_batch_bundle"):
		if current:
			return [
				entry.serial_no
				for entry in _current_rows(
					"Serial and Batch Entry", {"parent": row.serial_and_batch_bundle}, ["serial_no"], "idx"
				)
				if entry.serial_no
			]
		return bundle_serial_nos(row.serial_and_batch_bundle)
	return get_serial_nos(row.get("serial_no") or "")


def _row_batches(row, current=False):
	"""{batch: qty} the intake row put into quarantine."""
	from erpnext.stock.serial_batch_bundle import get_batch_nos

	if row.get("serial_and_batch_bundle"):
		if current:
			batches = {}
			for entry in _current_rows(
				"Serial and Batch Entry", {"parent": row.serial_and_batch_bundle}, ["batch_no", "qty"]
			):
				if entry.batch_no:
					batches[entry.batch_no] = batches.get(entry.batch_no, 0) + abs(flt(entry.qty))
			return batches
		return {
			batch: abs(flt(entry.qty)) for batch, entry in get_batch_nos(row.serial_and_batch_bundle).items()
		}
	if row.get("batch_no"):
		return {row.batch_no: _row_qty(row)}
	return {}


def _bundle_batches(leg, current=False):
	"""{batch: quantity} of one ledger entry (positive): its bundle's batches, else its batch, else {None: quantity}."""
	from erpnext.stock.serial_batch_bundle import get_batch_nos

	if leg.get("serial_and_batch_bundle"):
		if current:
			batches = _row_batches(leg, current=True)
			return batches or {None: abs(flt(leg.actual_qty))}
		batches = get_batch_nos(leg.serial_and_batch_bundle)
		if batches:
			return {batch: abs(flt(entry.qty)) for batch, entry in batches.items()}
	return {leg.get("batch_no") or None: abs(flt(leg.actual_qty))}


# Outward voucher -> (its item table, the field naming the receipt row it returns from or releases)
OUT_LINKS = {
	"Stock Entry": ("Stock Entry Detail", "ste_detail"),
	"Purchase Receipt": ("Purchase Receipt Item", "purchase_receipt_item"),
	"Purchase Invoice": ("Purchase Invoice Item", "purchase_invoice_item"),
}


def _ledger(item_code, warehouse, location=None, current=False, classify=False):
	"""The goods that came into and left ``warehouse`` for ``item_code``, from the Stock Ledger (not cancelled), in posting
	order: ``(inflows, outflows)``. An inflow is ``{"seq", "row", "inspected_at", "batches"}`` (``row`` is the document row
	that put the goods there, ``inspected_at`` when its Quality Inspection was submitted, else None); an outflow is
	``{"seq", "at", "linked", "batches"}`` where ``at`` is when the movement was made and ``linked`` is the receipt row a
	Stock Entry (``ste_detail``), a Purchase Return (``purchase_receipt_item``) or a debit note
	(``purchase_invoice_item``) names, else None. A document row that leaves and enters the warehouse in one go (a Storage
	Location change inside the quarantine) moves nothing out of it and is left out."""
	filters = {"item_code": item_code, "warehouse": warehouse, "is_cancelled": 0, "actual_qty": ["!=", 0]}
	if location:
		filters["storage_location"] = location
	read = _current_rows if current else frappe.get_all
	entries = read(
		"Stock Ledger Entry",
		filters=filters,
		fields=[
			"actual_qty",
			"voucher_type",
			"voucher_no",
			"voucher_detail_no",
			"batch_no",
			"serial_and_batch_bundle",
			"creation",
		],
		order_by="posting_datetime asc, creation asc",
	)
	signs = {}
	for e in entries:
		if e.voucher_detail_no:
			signs.setdefault((e.voucher_type, e.voucher_no, e.voucher_detail_no), set()).add(
				flt(e.actual_qty) > 0
			)
	entries = [
		e for e in entries if len(signs.get((e.voucher_type, e.voucher_no, e.voucher_detail_no), ())) < 2
	]
	links, dispositions = {}, {}
	for voucher_type, (child, field) in OUT_LINKS.items():
		details = [
			e.voucher_detail_no
			for e in entries
			if flt(e.actual_qty) < 0 and e.voucher_type == voucher_type and e.voucher_detail_no
		]
		if details:
			filters = {"name": ["in", details]}
			if not location and not classify:
				filters[field] = ["is", "set"]
			fields = ["name", field]
			if (location or classify) and voucher_type == "Stock Entry":
				fields.append("t_warehouse")
			for detail in read(child, filters=filters, fields=fields):
				links[detail.name] = detail.get(field)
				if (location or classify) and voucher_type == "Stock Entry":
					target_type = (
						frappe.db.get_value("Warehouse", detail.get("t_warehouse"), "warehouse_type")
						if detail.get("t_warehouse")
						else None
					)
					dispositions[detail.name] = target_type in ("Quarantine", "QA", "Write-Off")
	rows = [e.voucher_detail_no for e in entries if flt(e.actual_qty) > 0 and e.voucher_detail_no]
	inspection_rows = (
		read(
			"Quality Inspection",
			filters={"child_row_reference": ["in", rows], "docstatus": 1},
			fields=[
				"child_row_reference",
				"modified",
				*(
					["status", "purchase_unit_qty", "purchase_accepted_qty", "purchase_rejected_qty"]
					if location or classify
					else []
				),
			],
			order_by="modified desc",
		)
		if rows
		else []
	)
	inspected, statuses = {}, {}
	for qi in inspection_rows:
		row_name = qi.child_row_reference
		if row_name not in inspected or qi.modified > inspected[row_name]:
			inspected[row_name] = qi.modified
		status = qi.get("status")
		if (
			flt(qi.get("purchase_unit_qty"))
			and flt(qi.get("purchase_accepted_qty"))
			and flt(qi.get("purchase_rejected_qty"))
		):
			status = "Mixed"
		statuses.setdefault(row_name, set()).add(status)
	statuses = {row: next(iter(values)) if len(values) == 1 else "Mixed" for row, values in statuses.items()}
	inspected_item = (
		bool(cint(frappe.get_cached_value("Item", item_code, "inspection_required_before_purchase")))
		if location or classify
		else False
	)
	snapshots = {}
	if location or classify:
		for voucher_type in ("Purchase Receipt", "Purchase Invoice"):
			ids = [
				leg.voucher_detail_no
				for leg in entries
				if flt(leg.actual_qty) > 0 and leg.voucher_type == voucher_type and leg.voucher_detail_no
			]
			if ids:
				for row in read(
					f"{voucher_type} Item",
					filters={"name": ["in", ids]},
					fields=["name", "purchase_inspection_granularity"],
				):
					snapshots[row.name] = row.get("purchase_inspection_granularity")
	inflows, outflows = [], []
	for seq, leg in enumerate(entries):
		if flt(leg.actual_qty) > 0:
			inflow = {
				"seq": seq,
				"row": leg.voucher_detail_no,
				"inspected_at": inspected.get(leg.voucher_detail_no),
				"batches": _bundle_batches(leg, current=current),
			}
			if location or classify:
				inflow.update(
					inspection_status=statuses.get(leg.voucher_detail_no),
					needs_inspection=leg.voucher_type in ("Purchase Receipt", "Purchase Invoice")
					and (
						snapshots.get(leg.voucher_detail_no) != "Not Required"
						if snapshots.get(leg.voucher_detail_no)
						else inspected_item
					),
				)
			inflows.append(inflow)
		else:
			outflow = {
				"seq": seq,
				"at": leg.creation,
				"linked": links.get(leg.voucher_detail_no),
				"batches": _bundle_batches(leg, current=current),
			}
			if location or classify:
				outflow["disposition"] = leg.voucher_type in (
					"Purchase Receipt",
					"Purchase Invoice",
				) or dispositions.get(leg.voucher_detail_no, False)
			outflows.append(outflow)
	return inflows, outflows


def remaining_parts(doc, row, quarantine, current=False, location=None):
	"""What of this receiving row is still in quarantine, as the parts a move takes: [{"qty", "batch_no",
	"serial_nos"}], quantities in the stock UOM. Counted for this row only, not the warehouse's whole stock of the Item
	(another row's goods stay): its serial numbers still in quarantine, else its batches or quantity less what the
	movements out of the quarantine took from it (``allocate``: the Stock team's own Stock Entry, a Purchase Return or a
	delivery out counts like a move made by "Create Stock Entry"), never more than quarantine holds. The task, the headline
	of the receipt and the release check all ask this."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	has_serial, has_batch = frappe.db.get_value("Item", row.item_code, ["has_serial_no", "has_batch_no"]) or (
		0,
		0,
	)
	if cint(has_serial):
		serials = _row_serial_nos(row, current=True) if current else _row_serial_nos(row)
		if not serials:
			return []
		read = _current_rows if current else frappe.get_all
		held = read(
			"Serial No",
			filters={"name": ["in", serials], "warehouse": quarantine},
			fields=["name", "batch_no"],
		)
		if location:
			present = {
				r.serial_no
				for r in frappe.db.sql(
					"""select entry.serial_no
					from `tabStock Ledger Entry` sle
					join `tabSerial and Batch Entry` entry on entry.parent = sle.serial_and_batch_bundle
					where sle.warehouse = %(warehouse)s and sle.is_cancelled = 0
						and entry.serial_no in %(serials)s
						and coalesce(nullif(entry.storage_location, ''), sle.storage_location) = %(location)s
					group by entry.serial_no having sum(entry.qty) > 0"""
					+ (" for update" if current else ""),
					{"warehouse": quarantine, "serials": tuple(serials), "location": location},
					as_dict=True,
				)
			}
			held = [serial for serial in held if serial.name in present]
		order = {serial: index for index, serial in enumerate(serials)}
		by_batch = {}
		for serial in sorted(held, key=lambda serial: order.get(serial.name, 0)):
			by_batch.setdefault(serial.batch_no or None, []).append(serial.name)
		return [
			{"qty": float(len(names)), "batch_no": batch, "serial_nos": names}
			for batch, names in by_batch.items()
		]
	if current:
		ledger = _ledger(row.item_code, quarantine, location, current=True)
	elif location:
		ledger = _ledger(row.item_code, quarantine, location)
	else:
		ledger = _ledger(row.item_code, quarantine)
	left = allocate(*ledger).get(row.name, {})
	if cint(has_batch):
		parts = []
		for batch, qty in left.items():
			if not batch:
				continue
			kwargs = {"inventory_dimensions_dict": {"storage_location": location}} if location else {}
			if not current:
				qty = min(qty, flt(get_batch_qty(batch_no=batch, warehouse=quarantine, **kwargs)))
			if qty > 1e-9:
				parts.append({"qty": qty, "batch_no": batch, "serial_nos": None})
		return parts
	if current:
		held = _held(row.item_code, quarantine, location, current=True)
	elif location:
		held = _held(row.item_code, quarantine, location)
	else:
		held = _held(row.item_code, quarantine)
	qty = min(sum(left.values()), held)
	return [{"qty": qty, "batch_no": None, "serial_nos": None}] if qty > 1e-9 else []


def purchase_release_parts(doc, row, current=False, location=None):
	"""The inspected quantities still at this receipt row's receiving location."""
	units = receipt_units(row)
	inspections = inspections_for_row(doc, row, lock=current)
	held = remaining_parts(doc, row, row.warehouse, current=current, location=location)
	movements = {}
	if not any(unit["serials"] for unit in units):
		allocate(*_ledger(row.item_code, row.warehouse, location, current=current, classify=True), movements)
	try:
		return remaining_inspection_results(units, inspections, held, movements.get(row.name, []))
	except ValueError as exc:
		frappe.throw(_("Row {0} ({1}): {2}.").format(row.idx, row.item_code, str(exc)))


def write_off_warehouse(company, source_warehouse=None):
	"""Return the source warehouse's configured damage warehouse, when it is valid."""
	if not company:
		return None
	if not source_warehouse:
		matches = frappe.get_all("Warehouse", filters={"company": company, "warehouse_type": "Write-Off", "is_group": 0, "disabled": 0}, pluck="name", limit_page_length=2)
		return matches[0] if len(matches) == 1 else None
	if not frappe.db.has_column("Warehouse", "damage_warehouse"):
		return None
	source = frappe.db.get_value("Warehouse", source_warehouse, ["company", "damage_warehouse"], as_dict=True)
	if not source or source.company != company or not source.damage_warehouse:
		return None
	target = frappe.db.get_value("Warehouse", source.damage_warehouse, ["company", "warehouse_type", "is_group", "disabled"], as_dict=True)
	if not target or target.company != company or target.warehouse_type != "Write-Off" or target.is_group or target.disabled:
		return None
	return source.damage_warehouse


def _preferred_location(item_code, warehouse):
	"""The Item's preferred Storage Location in ``warehouse`` (the first of its list that belongs there), else the
	warehouse's default one that fits its Storage Condition; None when there is none."""
	if not warehouse:
		return None
	for row in frappe.get_all(
		"Item Storage Location",
		filters={"parent": item_code, "parenttype": "Item"},
		fields=["storage_location"],
		order_by="idx",
	):
		if (
			row.storage_location
			and frappe.db.get_value("Storage Location", row.storage_location, "warehouse") == warehouse
		):
			return row.storage_location
	from erpnext.stock.doctype.storage_location.storage_location import get_default_storage_location

	return get_default_storage_location(warehouse, item_code)


def build_release_entry(doc, sources, rows):
	"""Build an unsaved transfer for already selected inspection results."""
	items = []
	incomplete = False
	for row in rows:
		source = next(item for item in sources if item.name == row["row"])
		stock_uom = source.get("stock_uom") or frappe.db.get_value("Item", row["item_code"], "stock_uom")
		rejected = row["status"] == "Rejected"
		target = (
			write_off_warehouse(doc.company, row.get("home") or row.get("quarantine"))
			if rejected
			else row["home"]
		)
		from_location = (
			source.get("to_storage_location")
			if doc.get("doctype") == "Stock Entry"
			else source.get("storage_location")
		)
		if not target:
			incomplete = True
		target_location = _preferred_location(row["item_code"], target) if target else None
		if row.get("staging"):
			if (
				not target_location
				or (not rejected and is_staging_location(target_location, target))
				or (target == row["quarantine"] and target_location == from_location)
			):
				target_location = None
				incomplete = True
		for part in row["parts"]:
			items.append(
				{
					"item_code": row["item_code"],
					"qty": part["qty"],
					"uom": stock_uom,
					"stock_uom": stock_uom,
					"conversion_factor": 1,
					"s_warehouse": row["quarantine"],
					"t_warehouse": target,
					"storage_location": from_location,
					"to_storage_location": target_location,
					"use_serial_batch_fields": 1 if (part["batch_no"] or part["serial_nos"]) else 0,
					"batch_no": part["batch_no"],
					"serial_no": "\n".join(part["serial_nos"] or []),
					"against_stock_entry": doc.name if doc.get("doctype") == "Stock Entry" else None,
					"ste_detail": row["row"],
				}
			)
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"stock_entry_type": frappe.db.get_value(
				"Stock Entry Type", {"purpose": "Material Transfer", "is_standard": 1}, "name"
			),
			"purpose": "Material Transfer",
			"company": doc.company,
			"remarks": (
				_("Put away from the receiving location ({0}), after incoming inspection")
				if any(row.get("staging") for row in rows)
				else _("Out of quarantine ({0}), after the Quality Inspection")
			).format(doc.name),
		}
	)
	for item in items:
		entry.append("items", item)
	# A target left to the Stock team cannot pass the save checks yet: the draft is stored as it is (the task path) and
	# complete when the team chooses it.
	entry.flags.save_incomplete = incomplete
	return entry


def is_staging_location(location, warehouse):
	if not location or not warehouse:
		return False
	values = frappe.db.get_value(
		"Storage Location", location, ["warehouse", "location_code", "disabled"], as_dict=True
	)
	return bool(
		values
		and values.warehouse == warehouse
		and not cint(values.disabled)
		and (values.location_code == "STAGING" or (values.location_code or "").startswith("STAGING-"))
	)


def _blocked_by_tracking(waiting, want, source, item_code, location=None, current=False):
	"""The labels of the waiting rows whose serial numbers or batches the entry takes; [] when it takes none of what
	waits; None when the entry names no serial number or batch (the quantity rule decides)."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	if want.get("unknown_serials"):
		return [entry["label"] for entry in waiting if entry["serials"]]
	linked = [entry for entry in waiting if entry.get("row") in want.get("rows", set())]
	if linked:
		if want["serials"]:
			return [entry["label"] for entry in linked if entry["serials"] & want["serials"]]
		if want["batches"]:
			return [
				entry["label"]
				for entry in linked
				if any(
					qty > entry.get("free_batches", {}).get(batch, 0) + 1e-9
					for batch, qty in want["batches"].items()
					if entry["batches"].get(batch)
				)
			]
		return [entry["label"] for entry in linked if want["qty"] > entry.get("free_qty", 0) + 1e-9]
	if want["serials"]:
		return [entry["label"] for entry in waiting if entry["serials"] & want["serials"]]
	if want["batches"]:
		labels = []
		for batch, qty in want["batches"].items():
			rows = [entry for entry in waiting if entry["batches"].get(batch)]
			if not rows:
				continue
			kwargs = {"inventory_dimensions_dict": {"storage_location": location}} if location else {}
			if current:
				inflows, outflows = _ledger(item_code, source, location, current=True)
				held = sum(row["batches"].get(batch, 0) for row in inflows) - sum(
					row["batches"].get(batch, 0) for row in outflows
				)
			else:
				held = flt(get_batch_qty(batch_no=batch, warehouse=source, **kwargs))
			free = held - sum(entry["batches"][batch] for entry in rows)
			if qty > max(free, 0) + 1e-9:
				labels.extend(entry["label"] for entry in rows)
		return labels
	return None


def reconciliation_outgoing(row):
	"""The stock removed by a reconciliation, including replaced serial numbers."""
	current = frappe._dict(
		serial_and_batch_bundle=row.get("current_serial_and_batch_bundle"),
		serial_no=row.get("current_serial_no"),
	)
	current_serials = set(_row_serial_nos(current))
	if current_serials:
		removed = current_serials - set(_row_serial_nos(row))
		qty = float(len(removed))
	else:
		removed = set()
		qty = max(0, flt(row.get("current_qty")) - flt(row.get("qty")))
	return qty, ({row.get("batch_no"): qty} if row.get("batch_no") and qty else {}), removed
