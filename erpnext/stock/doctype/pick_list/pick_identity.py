"""What a Pick List row picked, and whether the Delivery Note made from it delivers exactly that (UX WP2).

Two rules, one place:

- ``identity_at_location``: when the picker changes a row's Storage Location, which of the row's Batch No, Serial Nos
  and bundle the new place still holds. The form clears only what it does not hold (``pick_list.js``), so a batch or
  serial chosen first is kept when its place is filled in after it. The picker uses the same available-stock count as Auto Pick,
  with open picks deducted and restricted locations excluded.
- ``pick_mismatch``: a Delivery Note row made from a Pick List row delivers from the same warehouse and Storage
  Location, and only the batches and serial nos that row picked (a part of them for a part delivery). Used by the
  Borrowing Delivery Note check (``borrowing_core.validate_borrowing_delivery_note``) and by the app's Delivery Note
  validate hook.
"""

import frappe
from frappe import _
from frappe.utils import cstr


def _serials(text):
	"""The Serial Nos of a row's text, split as ERPNext splits them (new lines or commas)."""
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	return [serial for serial in get_serial_nos(cstr(text)) if serial]


def bundle_entries(bundle):
	if not bundle:
		return []
	return frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": bundle},
		fields=["batch_no", "serial_no", "storage_location", "qty"],
		order_by="idx",
	)


def row_parts(row):
	"""What a row names: (storage location, batch, serial) per bundle entry, else from its own fields. A bundle entry
	without a location takes the row's."""
	location = row.get("storage_location") or None
	if row.get("serial_and_batch_bundle"):
		return [
			(entry.storage_location or location, entry.batch_no or None, entry.serial_no or None)
			for entry in bundle_entries(row.serial_and_batch_bundle)
		]
	serials = _serials(row.get("serial_no"))
	batch = row.get("batch_no") or None
	if serials:
		return [(location, batch, serial) for serial in serials]
	if batch:
		return [(location, batch, None)]
	return []


def pick_mismatch(row, pick_row):
	"""Why the delivery row does not deliver what ``pick_row`` picked, or None. A part of the picked batches / serial
	nos is allowed (part delivery); another warehouse, place, batch or serial is not. A row that names no batch or
	serial yet takes them from the pick's bundle on submit (``set_serial_and_batch_bundle_from_pick_list``), unless the
	pick named them in its own fields, which the row then must carry."""
	if (row.get("warehouse") or None) != (pick_row.get("warehouse") or None):
		return _("warehouse {0} instead of {1}").format(row.get("warehouse"), pick_row.get("warehouse"))
	if (row.get("storage_location") or None) != (pick_row.get("storage_location") or None):
		return _("Storage Location {0} instead of {1}").format(
			row.get("storage_location") or "-", pick_row.get("storage_location") or "-"
		)

	picked = row_parts(pick_row)
	delivered = row_parts(row)
	if not delivered:
		if pick_row.get("serial_and_batch_bundle") or not picked:
			return None
		return _("Batch No / Serial No left empty; the pick names {0}").format(
			", ".join(part[2] or part[1] for part in picked)
		)

	if picked:
		by_serial = {part[2]: part for part in picked if part[2]}
		for location, batch, serial in delivered:
			if serial:
				part = by_serial.get(serial)
				if not part:
					return _("Serial No {0} was not picked").format(serial)
			else:
				same_batch = [part for part in picked if part[1] == batch]
				part = next((part for part in same_batch if part[0] == location), None) or next(
					iter(same_batch), None
				)
				if not part:
					return _("Batch No {0} was not picked").format(batch or "-")
			if batch and part[1] and batch != part[1]:
				return _("Batch No {0} instead of {1}").format(batch, part[1])
			if location and part[0] and location != part[0]:
				return _("Storage Location {0} instead of {1}").format(location, part[0])
		if by_serial and not any(part[2] for part in delivered):
			return _("Serial No left empty; the pick names {0}").format(", ".join(sorted(by_serial)))
		return None

	# nothing was picked by batch or serial: the delivery names none either
	return _("Batch No / Serial No {0} not on the Pick List").format(
		", ".join(part[2] or part[1] for part in delivered if part[2] or part[1])
	)


def refuse_mismatch(row, pick_row):
	reason = pick_mismatch(row, pick_row)
	if reason:
		frappe.throw(
			_("Row {0}: {1} must be delivered as picked on Pick List {2}: {3}.").format(
				row.get("idx"), frappe.bold(row.get("item_code")), row.get("against_pick_list"), reason
			),
			title=_("Not as picked"),
		)


@frappe.whitelist()
def identity_at_location(
	item_code,
	warehouse,
	storage_location,
	batch_no=None,
	serial_no=None,
	serial_and_batch_bundle=None,
	pick_list=None,
):
	"""Which picked identities the new storage location can supply after open picks are deducted."""
	frappe.has_permission("Pick List", "read", throw=True)
	from erpnext.stock.pick_stock import _pickable_serial_rows, get_stock_lines

	result = {"batch_no": True, "serial_nos": [], "bundle": True}
	if not (item_code and warehouse and storage_location):
		return result
	pick_list = pick_list or None

	def batch_there(batch):
		return bool(
			get_stock_lines(item_code, [warehouse], storage_location, batch, exclude_pick_list=pick_list)
		)

	serials_there = None

	def serial_there(serial):
		nonlocal serials_there
		if serials_there is None:
			serials_there = {
				row.serial_no
				for row in _pickable_serial_rows(item_code, warehouse, storage_location, pick_list=pick_list)
			}
		return serial in serials_there

	if batch_no:
		result["batch_no"] = batch_there(batch_no)
	result["serial_nos"] = [
		serial for serial in _serials(serial_no) if serial and serial_there(serial)
	]
	if serial_and_batch_bundle:
		result["bundle"] = all(
			(entry.storage_location or storage_location) == storage_location
			and (serial_there(entry.serial_no) if entry.serial_no else batch_there(entry.batch_no))
			for entry in bundle_entries(serial_and_batch_bundle)
		)
	return result


def validate_against_pick(doc, method=None):
	"""Delivery Note validate: a row made from a Pick List row delivers what was picked there, the same warehouse,
	Storage Location, batch and serial nos (a part of them for a part delivery), whatever the form or an import sent
	(UX WP2; the rule is the fork's ``pick_identity.pick_mismatch``, which the Borrowing Delivery Note check uses too).
	A return, and a row of a non-stock Item (a product bundle's parent), are not compared."""
	if doc.get("is_return") or frappe.flags.in_patch or frappe.flags.in_install:
		return
	rows = [row for row in doc.get("items") or [] if row.get("pick_list_item")]
	if not rows:
		return
	picked = {
		pick_row.name: pick_row
		for pick_row in frappe.get_all(
			"Pick List Item",
			filters={"name": ("in", sorted({row.pick_list_item for row in rows}))},
			fields=[
				"name",
				"parent",
				"item_code",
				"warehouse",
				"storage_location",
				"batch_no",
				"serial_no",
				"serial_and_batch_bundle",
			],
		)
	}
	for row in rows:
		pick_row = picked.get(row.pick_list_item)
		if not pick_row or not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue
		if pick_row.item_code != row.item_code:
			frappe.throw(
				_("Row {0}: {1} is not the Item picked on Pick List {2}.").format(
					row.idx, frappe.bold(row.item_code), pick_row.parent
				),
				title=_("Not as picked"),
			)
		refuse_mismatch(row, pick_row)
