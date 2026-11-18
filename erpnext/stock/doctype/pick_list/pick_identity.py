"""What a Pick List row picked, and whether the Delivery Note made from it delivers exactly that (UX WP2).

Two rules, one place:

- ``identity_at_location``: when the picker changes a row's Storage Location, which of the row's Batch No, Serial Nos
  and bundle the new place still holds. The form clears only what it does not hold (``pick_list.js``), so a batch or
  serial chosen first is kept when its place is filled in after it. The app overrides the method with its own count
  of pickable stock (open picks netted, Receiving locations left out).
- ``pick_mismatch``: a Delivery Note row made from a Pick List row delivers from the same warehouse and Storage
  Location, and only the batches and serial nos that row picked (a part of them for a part delivery). Used by the
  Borrowing Delivery Note check (``borrowing_core.validate_borrowing_delivery_note``) and by the app's Delivery Note
  validate hook.
"""

import frappe
from frappe import _
from frappe.utils import cstr, flt


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


def _held_qty(item_code, warehouse, storage_location, batch_no):
	row = frappe.db.sql(
		"""select sum(coalesce(sbe.qty, sle.actual_qty))
		from `tabStock Ledger Entry` sle
		left join `tabSerial and Batch Entry` sbe on sbe.parent = sle.serial_and_batch_bundle
		where sle.item_code = %(item_code)s and sle.warehouse = %(warehouse)s and sle.is_cancelled = 0
		and coalesce(nullif(sbe.storage_location, ''), sle.storage_location) = %(location)s
		and coalesce(nullif(sbe.batch_no, ''), sle.batch_no) = %(batch_no)s""",
		{"item_code": item_code, "warehouse": warehouse, "location": storage_location, "batch_no": batch_no},
	)
	return flt(row[0][0]) if row else 0


def _serial_place(serial, warehouse):
	"""Where a Serial No last came into the warehouse (None when it is not in the warehouse now)."""
	if frappe.db.get_value("Serial No", serial, "warehouse") != warehouse:
		return None
	row = frappe.db.sql(
		"""select coalesce(nullif(entry.storage_location, ''), sle.storage_location)
		from `tabStock Ledger Entry` sle
		left join `tabSerial and Batch Entry` entry on entry.parent = sle.serial_and_batch_bundle
		where sle.warehouse = %(warehouse)s and sle.is_cancelled = 0 and sle.actual_qty > 0
		and (entry.serial_no = %(serial)s or sle.serial_no = %(serial)s)
		order by sle.posting_datetime desc, sle.creation desc limit 1""",
		{"serial": serial, "warehouse": warehouse},
	)
	return row[0][0] if row and row[0][0] else None


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
	"""Which of the row's batch, serial nos and bundle the Storage Location holds: ``{"batch_no": bool,
	"serial_nos": [held], "bundle": bool}``."""
	frappe.has_permission("Pick List", "read", throw=True)
	result = {"batch_no": True, "serial_nos": [], "bundle": True}
	if not (item_code and warehouse and storage_location):
		return result
	if batch_no:
		result["batch_no"] = _held_qty(item_code, warehouse, storage_location, batch_no) > 0
	result["serial_nos"] = [
		serial for serial in _serials(serial_no) if _serial_place(serial, warehouse) == storage_location
	]
	if serial_and_batch_bundle:
		result["bundle"] = all(
			(entry.storage_location or storage_location) == storage_location
			and (
				_serial_place(entry.serial_no, warehouse) == storage_location
				if entry.serial_no
				else _held_qty(item_code, warehouse, storage_location, entry.batch_no) > 0
			)
			for entry in bundle_entries(serial_and_batch_bundle)
		)
	return result
