"""Outgoing inspection units for a submitted sales Pick List."""

from contextlib import nullcontext
from math import isfinite

import frappe
from frappe import _
from frappe.utils import cint, flt


def eligible(doc):
	return doc.get("doctype") == "Pick List" and doc.get("purpose") == "Delivery" and not doc.get("borrowing")


def source_rows(pick):
	"""Represent a service bundle by its sold service, not its picked components."""
	rows = list(pick.get("locations") or [])
	bundle_orders = {
		row.get("sales_order_item")
		for row in rows
		if row.get("product_bundle_item") and row.get("sales_order_item")
	}
	if not bundle_orders:
		return rows
	orders = {
		row.name: row
		for row in frappe.get_all(
			"Sales Order Item",
			filters={"name": ["in", list(bundle_orders)]},
			fields=["name", "item_code", "stock_uom"],
		)
	}
	services = {
		name: row
		for name, row in orders.items()
		if not cint(frappe.get_cached_value("Item", row.item_code, "is_stock_item"))
		and frappe.db.exists("Product Bundle", {"new_item_code": row.item_code, "disabled": 0})
	}
	if not services:
		return rows
	bundle_refs = pick._get_product_bundles()
	bundle_maps = pick._get_product_bundle_qty_map(bundle_refs.values())
	result = []
	seen = set()
	for row in rows:
		order = services.get(row.get("sales_order_item")) if row.get("product_bundle_item") else None
		if not order:
			result.append(row)
			continue
		if order.name in seen or row.name != bundle_refs[order.name].pick_list_item:
			continue
		seen.add(order.name)
		qty = pick._compute_picked_qty_for_bundle(order.name, bundle_maps[order.item_code])
		values = frappe._dict(row.as_dict() if callable(getattr(row, "as_dict", None)) else row)
		values.update(
			{
				"item_code": order.item_code,
				"stock_uom": order.stock_uom,
				"picked_qty": qty,
				"stock_qty": qty,
				"serial_no": None,
				"batch_no": None,
				"serial_and_batch_bundle": None,
				"outgoing_inspection_granularity": "Batch/Lot",
				"service_bundle": 1,
			}
		)
		if row.get("outgoing_inspection_subject") != order.item_code:
			values.outgoing_inspection_requirement = None
		result.append(values)
	return result


def required(row):
	if not row.get("item_code") or flt(row.get("picked_qty")) <= 0:
		return False
	snapshot = row.get("outgoing_inspection_requirement")
	if snapshot:
		return snapshot == "Required"
	return bool(cint(frappe.get_cached_value("Item", row.item_code, "inspection_required_before_delivery")))


def units(row):
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import receipt_units

	values = frappe._dict(row.as_dict() if callable(getattr(row, "as_dict", None)) else row)
	values.stock_qty = flt(row.get("picked_qty"))
	values.transfer_qty = None
	values.purchase_inspection_granularity = (
		row.get("outgoing_inspection_granularity")
		or frappe.get_cached_value("Item", row.item_code, "outgoing_inspection_granularity")
		or "Batch/Lot"
	)
	result = receipt_units(values)
	item = frappe.get_cached_value("Item", row.item_code, ["has_serial_no", "has_batch_no"], as_dict=True)
	if item and item.has_serial_no and any(not unit["serials"] for unit in result):
		frappe.throw(
			_("Row {0}: pick the serial numbers before generating outgoing Quality Inspection.").format(
				row.idx
			)
		)
	if item and item.has_batch_no and any(not unit["batch_no"] for unit in result):
		frappe.throw(
			_("Row {0}: pick the batch before generating outgoing Quality Inspection.").format(row.idx)
		)
	return result


def generate(doc, can_inspect_item=None, system_context=nullcontext):
	"""Create missing inspection units under the caller's authorized system context."""
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		inspection_progress,
		inspections_for_row,
		new_purchase_inspection,
	)

	result = {"created": [], "draft": [], "submitted": [], "skipped": []}
	if not eligible(doc) or cint(doc.docstatus) != 1 or doc.get("status") == "Closed":
		return result
	frappe.db.get_value("Pick List", doc.name, "name", for_update=True)
	with system_context():
		for row in source_rows(doc):
			if not required(row):
				continue
			if can_inspect_item and not can_inspect_item(row.item_code):
				result["skipped"].append(
					_("Row {0}: configure the outgoing Quality Inspection team for {1}.").format(
						row.idx, row.item_code
					)
				)
				continue
			for part in inspection_progress(units(row), inspections_for_row(doc, row, lock=True)):
				if part["state"] != "missing":
					result[part["state"]].append(part["inspection"].name)
					continue
				inspection = new_purchase_inspection(doc, row, part["unit"])
				inspection.inspection_type = "Outgoing"
				inspection.remarks = _("Outgoing inspection for Pick List {0}, row {1}.").format(
					doc.name, row.idx
				)
				inspection.insert()
				result["created"].append(inspection.name)
	return result


def validate_inspection(doc, method=None):
	if doc.get("reference_type") != "Pick List":
		return
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		validate_purchase_unit_inspection,
	)

	name = doc.get("reference_name")
	frappe.db.get_value("Pick List", name, "name", for_update=True)
	reference = frappe.get_doc("Pick List", name)
	reference.check_permission("read")
	if not eligible(reference) or reference.docstatus != 1 or doc.get("inspection_type") != "Outgoing":
		frappe.throw(_("Outgoing inspection requires a submitted sales Pick List."))
	if reference.get("status") == "Closed":
		frappe.throw(
			_("Pick List {0} is closed. Reopen it before inspecting its goods.").format(reference.name)
		)
	row = next(
		(
			row
			for row in source_rows(reference)
			if row.name == doc.get("child_row_reference") and row.item_code == doc.item_code
		),
		None,
	)
	if not row or not required(row):
		frappe.throw(_("The picked row is missing or does not require outgoing inspection."))
	values = frappe._dict(row.as_dict() if callable(getattr(row, "as_dict", None)) else row)
	values.stock_qty = flt(row.picked_qty)
	values.transfer_qty = None
	values.purchase_inspection_granularity = (
		row.get("outgoing_inspection_granularity")
		or frappe.get_cached_value("Item", row.item_code, "outgoing_inspection_granularity")
		or "Batch/Lot"
	)
	validate_purchase_unit_inspection(doc, reference, values, method)


def remaining_accepted(unit, inspection, shipped):
	"""Subtract submitted deliveries from one inspected unit without reusing serials."""
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import result_parts

	part = result_parts(unit, inspection)["accepted"]
	return remaining_part(part, shipped)


def remaining_part(part, shipped):
	used_qty = sum(flt(row.get("stock_qty") or row.get("qty")) for row in shipped)
	if not isfinite(used_qty) or used_qty < 0 or used_qty > part["qty"] + 1e-9:
		raise ValueError("Delivered quantity exceeds accepted quantity")
	remaining = part["qty"] - used_qty
	if part["serial_nos"]:
		from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

		used = [serial for row in shipped for serial in get_serial_nos(row.get("serial_no") or "")]
		if len(used) != len(set(used)) or not set(used).issubset(part["serial_nos"]) or len(used) != used_qty:
			raise ValueError("Delivered serials do not match the accepted inspection")
		serials = [serial for serial in part["serial_nos"] if serial not in used]
		if len(serials) != remaining:
			raise ValueError("Delivered serial quantity does not match the accepted inspection")
	else:
		serials = []
	return {"qty": remaining, "batch_no": part["batch_no"], "serial_nos": serials}


def shipped_for_inspection(name, exclude=None):
	"""Read submitted deliveries; the inspection row is locked by callers before this query."""
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	filters = {"quality_inspection": name, "docstatus": 1}
	if exclude:
		filters["parent"] = ["!=", exclude]
	rows = frappe.get_all(
		"Delivery Note Item", filters=filters, fields=["stock_qty", "serial_no", "serial_and_batch_bundle"]
	)
	for row in rows:
		if row.serial_and_batch_bundle:
			row.serial_no = "\n".join(bundle_serial_nos(row.serial_and_batch_bundle))
	return rows


def accepted_parts(pick_list, row, exclude=None):
	"""Return each submitted inspection's undelivered accepted quantities."""
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		inspection_progress,
		inspections_for_row,
	)

	parts = []
	for entry in inspection_progress(units(row), inspections_for_row(pick_list, row, lock=True)):
		if entry["state"] != "submitted":
			continue
		inspection = entry["inspection"]
		part = remaining_accepted(entry["unit"], inspection, shipped_for_inspection(inspection.name, exclude))
		if part["qty"] > 1e-9:
			parts.append({**part, "quality_inspection": inspection.name})
	return parts


def validate_delivery(doc, method=None):
	"""Verify each picked delivery row against locked, unconsumed inspection results."""
	if (
		doc.get("doctype") != "Delivery Note"
		or doc.get("is_return")
		or doc.get("borrowing")
		or cint(doc.get("docstatus")) != 1
	):
		return
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	selected = {}
	for item in doc.get("items") or []:
		is_stock = cint(frappe.get_cached_value("Item", item.item_code, "is_stock_item"))
		is_service_bundle = not is_stock and bool(
			frappe.db.exists("Product Bundle", {"new_item_code": item.item_code, "disabled": 0})
		)
		if not is_stock and not is_service_bundle:
			continue
		flagged = bool(
			cint(frappe.get_cached_value("Item", item.item_code, "inspection_required_before_delivery"))
		)
		if not flagged and item.get("against_pick_list") and item.get("pick_list_item"):
			flagged = (
				frappe.db.get_value("Pick List Item", item.pick_list_item, "outgoing_inspection_requirement")
				== "Required"
			)
		if not flagged:
			continue
		if (
			not item.get("against_pick_list")
			or not item.get("pick_list_item")
			or not item.get("quality_inspection")
		):
			frappe.throw(_("Row {0}: select inspected goods from a submitted Pick List.").format(item.idx))
		frappe.db.get_value("Pick List", item.against_pick_list, "name", for_update=True)
		pick = frappe.get_doc("Pick List", item.against_pick_list)
		row = next(
			(
				row
				for row in source_rows(pick)
				if row.name == item.pick_list_item and row.item_code == item.item_code
			),
			None,
		)
		if not eligible(pick) or pick.docstatus != 1 or not row or row.item_code != item.item_code:
			frappe.throw(_("Row {0}: the selected Pick List item is invalid.").format(item.idx))
		# the warehouse, place, batch and serial nos are the picked ones: pick_delivery.validate_against_pick
		if not required(row):
			frappe.throw(_("Row {0}: the picked item does not require inspection.").format(item.idx))
		parts = {p["quality_inspection"]: p for p in accepted_parts(pick, row, exclude=doc.name)}
		part = parts.get(item.quality_inspection)
		if not part or (part["batch_no"] or None) != (item.get("batch_no") or None):
			frappe.throw(_("Row {0}: no matching accepted inspection quantity remains.").format(item.idx))
		qty = flt(item.qty) * flt(item.conversion_factor or 1)
		serials = (
			bundle_serial_nos(item.serial_and_batch_bundle)
			if item.get("serial_and_batch_bundle")
			else get_serial_nos(item.get("serial_no") or "")
		)
		state = selected.setdefault(item.quality_inspection, {"qty": 0, "serials": set()})
		if row.get("service_bundle") and state["qty"]:
			frappe.throw(_("Use one delivery row per service inspection."))
		if not isfinite(qty) or qty <= 0 or state["qty"] + qty > part["qty"] + 1e-9:
			frappe.throw(_("Row {0}: quantity exceeds the remaining accepted quantity.").format(item.idx))
		if part["serial_nos"] and (
			len(serials) != qty
			or len(serials) != len(set(serials))
			or not set(serials).issubset(part["serial_nos"])
			or state["serials"].intersection(serials)
		):
			frappe.throw(_("Row {0}: serials must be accepted, undelivered and unique.").format(item.idx))
		state["qty"] += qty
		state["serials"].update(serials)


def prepare_delivery(doc, pick_list):
	"""Keep only undelivered accepted picked units in a generated draft."""
	if not eligible(pick_list) or doc.doctype != "Delivery Note":
		return
	rows = {row.name: row for row in source_rows(pick_list)}
	original = list(doc.items)
	for item in original:
		row = rows.get(item.get("pick_list_item"))
		if not row or row.item_code != item.item_code or not required(row):
			continue
		parts = accepted_parts(pick_list, row)
		doc.items.remove(item)
		for part in parts:
			values = item.as_dict()
			for field in ("name", "idx", "serial_and_batch_bundle"):
				values.pop(field, None)
			values.update(
				{
					"qty": part["qty"] / flt(item.conversion_factor or 1),
					"stock_qty": part["qty"],
					"batch_no": part["batch_no"],
					"serial_no": "\n".join(part["serial_nos"]),
					"use_serial_batch_fields": 1,
					"quality_inspection": part["quality_inspection"],
				}
			)
			doc.append("items", values)
	if not doc.items:
		frappe.throw(_("No inspected and accepted picked goods are available for delivery yet."))
	doc.run_method("calculate_taxes_and_totals")


def refuse_cancel(doc, method=None):
	if doc.get("reference_type") != "Pick List":
		return
	frappe.db.get_value("Pick List", doc.reference_name, "name", for_update=True)
	if frappe.db.exists(
		"Delivery Note Item", {"quality_inspection": doc.name, "docstatus": 1}
	) or frappe.db.exists("Stock Entry Detail", {"outgoing_qi": doc.name, "docstatus": 1}):
		frappe.throw(_("Reverse the delivery or rejected stock movement before cancelling this inspection."))
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		has_active_serial_outflow,
		match_unit,
	)

	pick = frappe.get_doc("Pick List", doc.reference_name)
	row = next(
		(
			row
			for row in source_rows(pick)
			if row.name == doc.child_row_reference and row.item_code == doc.item_code
		),
		None,
	)
	unit = match_unit(units(row), doc.get("batch_no"), doc.get("item_serial_no")) if row else None
	if not unit or has_active_serial_outflow(doc, row, unit):
		frappe.throw(_("Reverse the stock movement of this inspection's serials before cancelling it."))


def freeze(doc, method=None):
	if not eligible(doc):
		return
	sources = {row.name: row for row in source_rows(doc)}
	for row in doc.locations:
		source = sources.get(row.name)
		row.outgoing_inspection_requirement = "Required" if source and required(source) else "Not Required"
		row.outgoing_inspection_subject = source.item_code if source else None
		if row.outgoing_inspection_requirement == "Required":
			row.outgoing_inspection_granularity = (
				"Batch/Lot"
				if source.get("service_bundle")
				else frappe.get_cached_value("Item", row.item_code, "outgoing_inspection_granularity")
				or "Batch/Lot"
			)


def cancel_pick(doc, method=None):
	if not eligible(doc):
		return
	if frappe.db.exists(
		"Quality Inspection",
		{"reference_type": "Pick List", "reference_name": doc.name, "docstatus": ["<", 2]},
	):
		frappe.throw(_("Cancel the Pick List's Quality Inspections before cancelling the Pick List."))


def rejected_parts(pick, row, exclude=None):
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		inspection_progress,
		inspections_for_row,
		result_parts,
	)
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	parts = []
	for entry in inspection_progress(units(row), inspections_for_row(pick, row, lock=True)):
		if entry["state"] != "submitted":
			continue
		qi = entry["inspection"]
		part = result_parts(entry["unit"], qi)["rejected"]
		filters = {"outgoing_qi": qi.name, "docstatus": 1}
		if exclude:
			filters["parent"] = ["!=", exclude]
		moved = frappe.get_all(
			"Stock Entry Detail",
			filters=filters,
			fields=["transfer_qty", "serial_no", "serial_and_batch_bundle"],
		)
		for movement in moved:
			movement.stock_qty = movement.transfer_qty
			if movement.serial_and_batch_bundle:
				movement.serial_no = "\n".join(bundle_serial_nos(movement.serial_and_batch_bundle))
		remaining = remaining_part(part, moved)
		if remaining["qty"] > 1e-9:
			parts.append({**remaining, "quality_inspection": qi.name})
	return parts


@frappe.whitelist(methods=["POST"])
def make_rejected_entry(name):
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		damage_destination,
	)

	pick = frappe.get_doc("Pick List", name)
	pick.check_permission("read")
	frappe.has_permission("Stock Entry", "create", throw=True)
	frappe.db.get_value("Pick List", name, "name", for_update=True)
	if not eligible(pick) or pick.docstatus != 1:
		frappe.throw(_("Select a submitted sales Pick List."))
	entry = frappe.new_doc("Stock Entry")
	entry.purpose = "Material Transfer"
	entry.stock_entry_type = frappe.db.get_value(
		"Stock Entry Type", {"purpose": "Material Transfer", "is_standard": 1}, "name"
	)
	entry.company = pick.company
	for row in source_rows(pick):
		if row.get("service_bundle") or not required(row):
			continue
		for part in rejected_parts(pick, row):
			target, location = damage_destination(pick.company, row.warehouse, row.item_code)
			entry.append(
				"items",
				{
					"item_code": row.item_code,
					"qty": part["qty"],
					"uom": row.stock_uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": 1,
					"s_warehouse": row.warehouse,
					"storage_location": row.storage_location,
					"t_warehouse": target,
					"to_storage_location": location,
					"serial_no": "\n".join(part["serial_nos"]),
					"batch_no": part["batch_no"],
					"use_serial_batch_fields": 1,
					"outgoing_qi": part["quality_inspection"],
				},
			)
	if not entry.items:
		frappe.throw(_("No rejected picked goods remain to move."))
	return entry


def validate_rejected_transfer(doc, method=None):
	if doc.get("purpose") != "Material Transfer" or cint(doc.get("docstatus")) != 1:
		return
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
	from erpnext.stock.doctype.storage_location.storage_location import (
		DAMAGE,
		get_location_type,
		typed_location,
	)
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	selected = {}
	for item in doc.get("items") or []:
		if not item.get("outgoing_qi"):
			continue
		damage = typed_location(item.s_warehouse, item.item_code, DAMAGE)
		target = item.get("t_warehouse") or doc.get("to_warehouse")
		if damage:
			if (
				target != item.s_warehouse
				or get_location_type(item.get("to_storage_location"), target) != DAMAGE
			):
				frappe.throw(_("Move rejected picked goods to a Damage location in the same warehouse."))
		elif not target or frappe.db.get_value("Warehouse", target, "warehouse_type") != "Write-Off":
			frappe.throw(_("Move rejected picked goods to a Damage location or Write-Off warehouse."))
		qi = frappe.get_doc("Quality Inspection", item.outgoing_qi)
		if qi.reference_type != "Pick List":
			frappe.throw(_("Select an outgoing Pick List inspection."))
		frappe.db.get_value("Pick List", qi.reference_name, "name", for_update=True)
		pick = frappe.get_doc("Pick List", qi.reference_name)
		row = next((row for row in pick.locations if row.name == qi.child_row_reference), None)
		if item.get("outgoing_pick_component"):
			source = next(
				(
					r
					for r in source_rows(pick)
					if r.name == qi.child_row_reference
					and r.item_code == qi.item_code
					and r.get("service_bundle")
				),
				None,
			)
			if not eligible(pick) or pick.docstatus != 1 or pick.company != doc.company or not source:
				frappe.throw(_("The rejected service inspection is invalid."))
			options = service_component_options(pick, source)
			part = next(
				(
					p
					for p in options
					if p["quality_inspection"] == qi.name
					and p["component_row"] == item.outgoing_pick_component
					and (p["batch_no"] or None) == (item.get("batch_no") or None)
				),
				None,
			)
			if (
				not part
				or part["item_code"] != item.item_code
				or part["warehouse"] != item.s_warehouse
				or (part["storage_location"] or None) != (item.get("storage_location") or None)
				or (part["batch_no"] or None) != (item.get("batch_no") or None)
			):
				frappe.throw(_("The rejected component must match the picked goods."))
			qty = flt(item.qty) * flt(item.conversion_factor or 1)
			serials = (
				bundle_serial_nos(item.serial_and_batch_bundle)
				if item.get("serial_and_batch_bundle")
				else get_serial_nos(item.get("serial_no") or "")
			)
			state = selected.setdefault(
				(qi.name, part["item_code"]), {"qty": 0, "serials": set(), "rows": {}}
			)
			row_qty = state["rows"].get(part["component_row"], 0) + qty
			if (
				not isfinite(qty)
				or qty <= 0
				or qty > part["qty"] + 1e-9
				or row_qty > part["row_qty"] + 1e-9
				or state["qty"] + qty > part["pool_qty"] + 1e-9
			):
				frappe.throw(_("Quantity exceeds the remaining rejected component quantity."))
			if part["serial_nos"] and (
				len(serials) != qty
				or len(serials) != len(set(serials))
				or not set(serials).issubset(part["serial_nos"])
				or state["serials"].intersection(serials)
			):
				frappe.throw(_("Serials must be picked, unmoved and unique."))
			state["qty"] += qty
			state["serials"].update(serials)
			state["rows"][part["component_row"]] = row_qty
			continue
		if (
			not eligible(pick)
			or pick.docstatus != 1
			or not row
			or row.item_code != item.item_code
			or qi.item_code != row.item_code
			or pick.company != doc.company
		):
			frappe.throw(_("The rejected picked item is invalid."))
		if row.warehouse != item.s_warehouse or (row.storage_location or None) != (
			item.get("storage_location") or None
		):
			frappe.throw(_("The rejected goods must move from their picked warehouse and location."))
		part = next(
			(p for p in rejected_parts(pick, row, exclude=doc.name) if p["quality_inspection"] == qi.name),
			None,
		)
		if not part or (part["batch_no"] or None) != (item.get("batch_no") or None):
			frappe.throw(_("No matching rejected quantity remains for this inspection."))
		qty = flt(item.qty) * flt(item.conversion_factor or 1)
		serials = (
			bundle_serial_nos(item.serial_and_batch_bundle)
			if item.get("serial_and_batch_bundle")
			else get_serial_nos(item.get("serial_no") or "")
		)
		state = selected.setdefault(qi.name, {"qty": 0, "serials": set()})
		if not isfinite(qty) or qty <= 0 or state["qty"] + qty > part["qty"] + 1e-9:
			frappe.throw(_("Quantity exceeds the remaining rejected quantity."))
		if part["serial_nos"] and (
			len(serials) != qty
			or len(serials) != len(set(serials))
			or not set(serials).issubset(part["serial_nos"])
			or state["serials"].intersection(serials)
		):
			frappe.throw(_("Serials must be rejected, unmoved and unique."))
		state["qty"] += qty
		state["serials"].update(serials)


@frappe.whitelist()
def progress(name):
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		inspections_for_row,
		summarize_progress,
	)

	pick = frappe.get_doc("Pick List", name)
	pick.check_permission("read")
	if not eligible(pick) or pick.docstatus != 1:
		return {"rows": []}
	rows = []
	for row in source_rows(pick):
		if not required(row):
			continue
		inspections = inspections_for_row(pick, row)
		rows.append(
			{
				"idx": row.idx,
				"item_code": row.item_code,
				**summarize_progress(units(row), inspections),
				"inspections": [qi.name for qi in inspections],
				"accepted_remaining": sum(part["qty"] for part in accepted_parts(pick, row)),
				"rejected_remaining": 0
				if row.get("service_bundle")
				else sum(part["qty"] for part in rejected_parts(pick, row)),
				"service_bundle": bool(row.get("service_bundle")),
				"rejected_components": len(service_component_options(pick, row))
				if row.get("service_bundle")
				else 0,
			}
		)
	return {"rows": rows}


def rejected_moved_by_row(pick_name):
	"""Quantity of each Pick List Item (by row name) that a submitted rejected Stock Entry has moved out."""
	if not frappe.db.has_column("Stock Entry Detail", "outgoing_qi"):
		return {}
	values = frappe.db.sql(
		"""
		SELECT pli.name AS ref, SUM(sed.transfer_qty) AS qty
		FROM `tabPick List Item` pli
		JOIN `tabQuality Inspection` qi ON qi.reference_type = 'Pick List' AND qi.reference_name = pli.parent
		JOIN `tabStock Entry Detail` sed ON sed.outgoing_qi = qi.name AND sed.docstatus = 1 AND (sed.outgoing_pick_component = pli.name OR (IFNULL(sed.outgoing_pick_component, '') = '' AND qi.child_row_reference = pli.name))
		WHERE pli.parent = %(pick)s AND pli.docstatus = 1
		GROUP BY pli.name
	""",
		{"pick": pick_name},
		as_dict=True,
	)
	return {row.ref: flt(row.qty) for row in values}


def refuse_close_with_rejected(pick):
	"""Status > Close frees what the pick holds. Goods an outgoing inspection rejected and nobody moved to damage
	yet would be free to pick again, so the pick is closed only after the rejected movement (a data check)."""
	if not eligible(pick):
		return
	waiting = []
	for row in source_rows(pick):
		if not required(row):
			continue
		if row.get("service_bundle"):
			# the rejected parts of a service bundle are moved by its own Stock Entry (rejected components)
			for part in service_component_options(pick, row):
				if flt(part["qty"]) > 1e-9:
					waiting.append(
						_("Row {0}: {1}, {2} rejected component of {3}").format(
							row.idx, part["item_code"], flt(part["qty"]), row.item_code
						)
					)
			continue
		quantity = sum(part["qty"] for part in rejected_parts(pick, row))
		if quantity > 1e-9:
			waiting.append(_("Row {0}: {1}, {2} rejected").format(row.idx, row.item_code, flt(quantity)))
	if waiting:
		frappe.throw(
			_(
				"Pick List {0} cannot be closed: move the rejected goods to damage first (Stock Entry - Rejected; "
				"for a service bundle, Stock Entry - Rejected Components)."
			).format(pick.name)
			+ "<br>"
			+ "<br>".join(waiting),
			title=_("Rejected Goods Not Moved"),
		)


def adjust_picked_totals(rows, contains_packed_items=False):
	"""A rejected item moved to damage no longer counts as picked for its order. A Closed Pick List is left out: the
	fork counts what it delivered (``least(picked, delivered)``), which the rejected goods are never part of."""
	if not frappe.db.has_column("Stock Entry Detail", "outgoing_qi"):
		return rows
	field = "product_bundle_item" if contains_packed_items else "sales_order_item"
	names = [row.get(field) for row in rows if row.get(field)]
	if not names:
		return rows
	values = frappe.db.sql(
		f"""
		SELECT pli.`{field}` AS ref, SUM(sed.transfer_qty) AS qty
		FROM `tabPick List Item` pli
		JOIN `tabPick List` pl ON pl.name = pli.parent AND pl.status != 'Closed'
		JOIN `tabQuality Inspection` qi ON qi.reference_type = 'Pick List' AND qi.reference_name = pli.parent
		JOIN `tabStock Entry Detail` sed ON sed.outgoing_qi = qi.name AND sed.docstatus = 1 AND (sed.outgoing_pick_component = pli.name OR (IFNULL(sed.outgoing_pick_component, '') = '' AND qi.child_row_reference = pli.name))
		WHERE pli.docstatus = 1 AND pli.`{field}` IN %(names)s
		GROUP BY pli.`{field}`
	""",
		{"names": names},
		as_dict=True,
	)
	moved = {row.ref: flt(row.qty) for row in values}
	for row in rows:
		row.picked_qty = max(flt(row.picked_qty) - moved.get(row.get(field), 0), 0)
	return rows


def sync_picked_after_reject(doc, method=None):
	"""Refresh Sales Order picking after a rejected movement is posted or reversed."""
	names = {row.get("outgoing_qi") for row in doc.get("items") or [] if row.get("outgoing_qi")}
	picks = (
		set(
			frappe.get_all(
				"Quality Inspection",
				filters={"name": ["in", list(names)], "reference_type": "Pick List"},
				pluck="reference_name",
			)
		)
		if names
		else set()
	)
	for name in sorted(picks):
		frappe.db.get_value("Pick List", name, "name", for_update=True)
		pick = frappe.get_doc("Pick List", name)
		if pick.docstatus == 1:
			pick.update_reference_qty()
			for source in source_rows(pick):
				if not source.get("service_bundle"):
					continue
				bundle = frappe.get_cached_doc("Product Bundle", source.item_code)
				quantities = frappe.get_all(
					"Packed Item",
					filters={
						"parenttype": "Sales Order",
						"parent_detail_docname": source.sales_order_item,
						"docstatus": 1,
					},
					fields=["item_code", "picked_qty"],
				)
				picked = {}
				for part in quantities:
					picked[part.item_code] = picked.get(part.item_code, 0) + flt(part.picked_qty)
				ratios = {}
				for part in bundle.items:
					ratios[part.item_code] = ratios.get(part.item_code, 0) + flt(part.qty)
				complete = min(
					(picked.get(item, 0) / qty for item, qty in ratios.items() if qty > 0), default=0
				)
				frappe.db.set_value(
					"Sales Order Item",
					source.sales_order_item,
					"picked_qty",
					int(complete),
					update_modified=False,
				)
			pick.update_sales_order_picking_status()


def service_component_options(pick, source):
	"""Parts a stock user may identify as damaged for a rejected service bundle."""
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		inspection_progress,
		inspections_for_row,
		receipt_units,
		result_parts,
	)
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	if not source.get("service_bundle") or not required(source):
		return []
	bundle = frappe.get_cached_doc("Product Bundle", source.item_code)
	ratios = {}
	for part in bundle.items:
		ratios[part.item_code] = ratios.get(part.item_code, 0) + flt(part.qty)
	components = [
		row
		for row in pick.locations
		if row.get("product_bundle_item") and row.get("sales_order_item") == source.get("sales_order_item")
	]
	result = []
	for entry in inspection_progress(units(source), inspections_for_row(pick, source, lock=True)):
		if entry["state"] != "submitted":
			continue
		qi = entry["inspection"]
		rejected = result_parts(entry["unit"], qi)["rejected"]["qty"]
		if rejected <= 0:
			continue
		all_moved = frappe.get_all(
			"Stock Entry Detail",
			filters={"outgoing_qi": qi.name, "docstatus": 1},
			fields=[
				"outgoing_pick_component",
				"item_code",
				"transfer_qty",
				"batch_no",
				"serial_no",
				"serial_and_batch_bundle",
			],
		)
		delivered = frappe.db.sql(
			"""SELECT p.item_code, p.warehouse, p.storage_location, p.batch_no, SUM(ABS(p.qty)) qty
			FROM `tabPacked Item` p JOIN `tabDelivery Note Item` d ON d.name = p.parent_detail_docname
			WHERE p.parenttype = 'Delivery Note' AND p.docstatus = 1 AND d.against_pick_list = %s AND d.so_detail = %s
			GROUP BY p.item_code, p.warehouse, p.storage_location, p.batch_no""",
			(pick.name, source.sales_order_item),
			as_dict=True,
		)
		delivered_by_key = {
			(d.item_code, d.warehouse, d.storage_location, d.batch_no): flt(d.qty) for d in delivered
		}
		for row in components:
			ratio = ratios.get(row.item_code, 0)
			if ratio <= 0:
				continue
			moved_for_item = sum(flt(x.transfer_qty) for x in all_moved if x.item_code == row.item_code)
			moved_here = [x for x in all_moved if x.outgoing_pick_component == row.name]
			remaining = min(
				rejected * ratio - moved_for_item,
				flt(row.picked_qty) - sum(flt(x.transfer_qty) for x in moved_here),
			)
			if remaining <= 1e-9:
				continue
			values = frappe._dict(row.as_dict())
			values.stock_qty = row.picked_qty
			values.transfer_qty = None
			values.purchase_inspection_granularity = "Batch/Lot"
			from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

			for unit in receipt_units(values):
				key = (row.item_code, row.warehouse, row.storage_location, unit["batch_no"])
				delivered_qty = min(flt(unit["qty"]), delivered_by_key.get(key, 0))
				delivered_by_key[key] = delivered_by_key.get(key, 0) - delivered_qty
				moved_batch = [m for m in moved_here if (m.batch_no or None) == (unit["batch_no"] or None)]
				moved_serials = set()
				for moved in moved_batch:
					moved_serials.update(
						bundle_serial_nos(moved.serial_and_batch_bundle)
						if moved.serial_and_batch_bundle
						else get_serial_nos(moved.serial_no or "")
					)
				available_serials = [
					serial
					for serial in unit["serials"]
					if serial not in moved_serials
					and frappe.db.get_value("Serial No", serial, "warehouse") == row.warehouse
				]
				batch_remaining = min(
					remaining,
					flt(unit["qty"]) - delivered_qty - sum(flt(m.transfer_qty) for m in moved_batch),
				)
				if unit["serials"]:
					batch_remaining = min(batch_remaining, len(available_serials))
				if batch_remaining > 1e-9:
					result.append(
						{
							"quality_inspection": qi.name,
							"component_row": row.name,
							"item_code": row.item_code,
							"warehouse": row.warehouse,
							"storage_location": row.storage_location,
							"batch_no": unit["batch_no"],
							"stock_uom": row.stock_uom,
							"qty": batch_remaining,
							"row_qty": remaining,
							"pool_qty": rejected * ratio - moved_for_item,
							"serial_nos": available_serials,
						}
					)
	return result


@frappe.whitelist()
def rejected_service_components(name):
	pick = frappe.get_doc("Pick List", name)
	pick.check_permission("read")
	frappe.has_permission("Stock Entry", "create", throw=True)
	if not eligible(pick) or pick.docstatus != 1:
		frappe.throw(_("Select a submitted sales Pick List."))
	return [
		part
		for row in source_rows(pick)
		if row.get("service_bundle")
		for part in service_component_options(pick, row)
	]


@frappe.whitelist(methods=["POST"])
def make_service_rejected_entry(name, selections):
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		damage_destination,
	)
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	pick = frappe.get_doc("Pick List", name)
	pick.check_permission("read")
	frappe.has_permission("Stock Entry", "create", throw=True)
	frappe.db.get_value("Pick List", name, "name", for_update=True)
	if not eligible(pick) or pick.docstatus != 1:
		frappe.throw(_("Select a submitted sales Pick List."))
	options = {
		(part["quality_inspection"], part["component_row"], part["batch_no"] or None): part
		for part in rejected_service_components(name)
	}
	entry = frappe.new_doc("Stock Entry")
	entry.purpose = "Material Transfer"
	entry.stock_entry_type = frappe.db.get_value(
		"Stock Entry Type", {"purpose": "Material Transfer", "is_standard": 1}, "name"
	)
	entry.company = pick.company
	used = {}
	used_rows = {}
	used_serials = set()
	for selected in frappe.parse_json(selections) or []:
		option = options.get(
			(
				selected.get("quality_inspection"),
				selected.get("component_row"),
				selected.get("batch_no") or None,
			)
		)
		qty = flt(selected.get("qty"))
		serials = get_serial_nos(selected.get("serial_no") or "")
		if not option or not isfinite(qty) or qty <= 0 or qty > option["qty"] + 1e-9:
			frappe.throw(_("Select a valid rejected component and quantity."))
		row_key = (option["quality_inspection"], option["component_row"])
		used_rows[row_key] = used_rows.get(row_key, 0) + qty
		if used_rows[row_key] > option["row_qty"] + 1e-9 or used_serials.intersection(serials):
			frappe.throw(_("Selected components or serial numbers exceed the available quantity."))
		used_serials.update(serials)
		key = (option["quality_inspection"], option["item_code"])
		used[key] = used.get(key, 0) + qty
		if used[key] > option["pool_qty"] + 1e-9:
			frappe.throw(_("Selected component quantities exceed the rejected service quantity."))
		if option["serial_nos"] and (
			len(serials) != qty
			or len(serials) != len(set(serials))
			or not set(serials).issubset(option["serial_nos"])
		):
			frappe.throw(_("Select unique serial numbers picked for this component."))
		target, location = damage_destination(pick.company, option["warehouse"], option["item_code"])
		entry.append(
			"items",
			{
				"item_code": option["item_code"],
				"qty": qty,
				"uom": option["stock_uom"],
				"stock_uom": option["stock_uom"],
				"conversion_factor": 1,
				"s_warehouse": option["warehouse"],
				"storage_location": option["storage_location"],
				"t_warehouse": target,
				"to_storage_location": location,
				"serial_no": "\n".join(serials),
				"batch_no": option["batch_no"],
				"use_serial_batch_fields": 1,
				"outgoing_qi": option["quality_inspection"],
				"outgoing_pick_component": option["component_row"],
			},
		)
	if not entry.items:
		frappe.throw(_("Select at least one damaged component."))
	return entry


def allocate_service_components(doc):
	"""Allocate the picked physical parts for each accepted service delivery."""
	if doc.get("is_return"):
		return
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import receipt_units
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serial_nos

	for parent in doc.get("items") or []:
		if not parent.get("against_pick_list") or not parent.get("quality_inspection"):
			continue
		qi = frappe.get_cached_value(
			"Quality Inspection", parent.quality_inspection, ["reference_type", "item_code"], as_dict=True
		)
		if not qi or qi.reference_type != "Pick List" or qi.item_code != parent.item_code:
			continue
		frappe.db.get_value("Pick List", parent.against_pick_list, "name", for_update=True)
		pick = frappe.get_doc("Pick List", parent.against_pick_list)
		source = next(
			(
				r
				for r in source_rows(pick)
				if r.name == parent.pick_list_item
				and r.item_code == parent.item_code
				and r.get("service_bundle")
			),
			None,
		)
		if not source:
			continue
		templates = {}
		for packed in list(doc.packed_items):
			if packed.parent_detail_docname == parent.name:
				templates.setdefault(packed.item_code, packed.as_dict())
				doc.packed_items.remove(packed)
		bundle = frappe.get_cached_doc("Product Bundle", parent.item_code)
		for part in bundle.items:
			needed = flt(parent.stock_qty) * flt(part.qty)
			template = templates.get(part.item_code)
			if not template:
				frappe.throw(_("Missing packed component {0}.").format(part.item_code))
			previous = frappe.db.sql(
				"""SELECT p.qty, p.warehouse, p.storage_location, p.batch_no, p.serial_no, p.serial_and_batch_bundle
				FROM `tabPacked Item` p JOIN `tabDelivery Note Item` d ON d.name=p.parent_detail_docname
				WHERE p.parenttype='Delivery Note' AND p.docstatus=1 AND d.against_pick_list=%s
				AND d.so_detail=%s AND p.item_code=%s AND p.parent!=%s""",
				(pick.name, parent.so_detail, part.item_code, doc.name or ""),
				as_dict=True,
			)
			used = {}
			used_serials = set()
			for old in previous:
				key = (old.warehouse, old.storage_location, old.batch_no)
				used[key] = used.get(key, 0) + abs(flt(old.qty))
				used_serials.update(
					bundle_serial_nos(old.serial_and_batch_bundle)
					if old.serial_and_batch_bundle
					else get_serial_nos(old.serial_no or "")
				)
			for row in pick.locations:
				if needed <= 1e-9:
					break
				if (
					row.sales_order_item != source.sales_order_item
					or row.item_code != part.item_code
					or not row.product_bundle_item
				):
					continue
				values = frappe._dict(row.as_dict())
				values.stock_qty = row.picked_qty
				values.transfer_qty = None
				values.purchase_inspection_granularity = "Batch/Lot"
				movements = frappe.get_all(
					"Stock Entry Detail",
					filters={"outgoing_pick_component": row.name, "docstatus": 1},
					fields=["transfer_qty", "batch_no", "serial_no", "serial_and_batch_bundle"],
				)
				for unit in receipt_units(values):
					key = (row.warehouse, row.storage_location, unit["batch_no"])
					moved = [m for m in movements if (m.batch_no or None) == (unit["batch_no"] or None)]
					moved_qty = sum(flt(m.transfer_qty) for m in moved)
					moved_serials = set()
					for m in moved:
						moved_serials.update(
							bundle_serial_nos(m.serial_and_batch_bundle)
							if m.serial_and_batch_bundle
							else get_serial_nos(m.serial_no or "")
						)
					available = max(flt(unit["qty"]) - moved_qty, 0)
					already = min(available, used.get(key, 0))
					used[key] = used.get(key, 0) - already
					available -= already
					serials = [
						s
						for s in unit["serials"]
						if s not in used_serials
						and s not in moved_serials
						and frappe.db.get_value("Serial No", s, "warehouse") == row.warehouse
					]
					if unit["serials"]:
						available = min(available, len(serials))
					qty = min(needed, available)
					if qty <= 1e-9:
						continue
					if unit["serials"] and qty != int(qty):
						frappe.throw(_("Serialised components require whole quantities."))
					values = dict(template)
					for field in ("name", "idx", "creation", "modified", "owner", "modified_by", "docstatus"):
						values.pop(field, None)
					values.update(
						{
							"qty": qty,
							"warehouse": row.warehouse,
							"storage_location": row.storage_location,
							"batch_no": unit["batch_no"],
							"serial_no": "\n".join(serials[: int(qty)]),
							"serial_and_batch_bundle": None,
							"use_serial_batch_fields": 1,
						}
					)
					doc.append("packed_items", values)
					used_serials.update(serials[: int(qty)])
					needed -= qty
			if needed > 1e-9:
				frappe.throw(
					_("Not enough picked and undelivered {0} remains for this service.").format(
						part.item_code
					)
				)
