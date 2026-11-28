"""Goods in quarantine: the QI team inspects them, the Stock team takes them out (Siwat, 5 Nov 2026; 9 Nov 2026).

Goods get into quarantine in two ways. A receipt (Purchase Receipt, Purchase Invoice with stock, sales return, stock Borrowing
Return) puts the rows of an Item that is inspected on receipt there (``receipt_quarantine.py``), and the Stock team's
"Quarantine Intake" (a Material Transfer into quarantine, ``operation_plan.stock_approval_route``) puts there what it
suspects. Either way the document is submitted first; nothing is inspected before.

1. The QI team of each Item (``my_space/inspections.py``) gets an "Inspect" task on the submitted document for the rows
   still in quarantine without a submitted Quality Inspection. The inspection is made by the member who presses the button,
   as that user, prefilled with the reference, the row, the Item, its template and Incoming
   (``make_receipt_inspections``; a draft that already exists is reused). Nothing is drafted by the system.
2. The member records the readings and submits the inspection, Accepted or Rejected. When every row of a team is
   inspected its task closes ("Inspected") and the Stock team gets one "Move" task for the document.
   The goods of an Intake Item with no QI team wait for nobody.
3. The Stock team makes its own Stock Entry out of quarantine to wherever it chooses; the button "Create Stock Entry"
   (``release_entry``) opens it prefilled with the inspected quantities still in quarantine: Accepted rows toward the
   warehouse they were meant for, at the Item's preferred Storage Location, Rejected rows with the target left to the
   team. ``quarantine_release.refuse_manual_move_out`` allows the move when the goods are inspected. Nobody approves it.
4. Goods moved into DAMAGE (the company's Write-Off warehouse) wait for their disposal: the Stock Managers get a "Dispose
   of the goods" task on that Stock Entry (``make_damage_disposal`` drafts the Disposal issue); the Managing Director
   decides it. Rejected purchased goods may instead go back to the supplier with a Purchase Return from quarantine.

Cancelling the document closes its tasks (``my_space/inspections.py``).

A row of a purchase document whose Item needs no inspection is received at the STAGING location of its warehouse
(the form's empty location) and the Stock team puts it away with a Stock Entry of its own: ``release_rows`` lists
those rows as ready at once, the target being the Item's preferred (never STAGING) Storage Location.
"""

from __future__ import annotations

import frappe
from erpnext.borrowing.loan_inspection import validate_loan_note_inspection as validate_loan_note_inspection
from erpnext.quality_management.inspection_teams import inspection_teams
from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
	_held,
	_row_batches,
	_row_qty,
	_row_serial_nos,
	build_release_entry,
	write_off_warehouse,
)
from frappe import _
from frappe.utils import cint, flt

from .policies import get_policy
from .quarantine_release import item_team

INTAKE_ROUTE = "Quarantine Intake"
WRITE_OFF_TYPE = "Write-Off"


def is_intake(doc):
	if not doc or doc.get("doctype") != "Stock Entry" or cint(doc.get("docstatus")) != 1:
		return False
	if doc.get("operation_approval_route") == INTAKE_ROUTE:
		return True
	if get_policy("stock_entry_route"):
		return False
	from .quarantine_release import physical_quarantine_route

	return physical_quarantine_route(doc) == INTAKE_ROUTE


def is_receipt_source(doc):
	"""A submitted receipt whose inspected goods were received into quarantine (``receipt_quarantine``)."""
	from .receipt_quarantine import is_inspected_receipt

	return is_inspected_receipt(doc)


def is_quarantine_source(doc):
	return is_intake(doc) or is_receipt_source(doc)


def require_own_quarantine(doc):
	"""Stock Entry before_validate of a Quarantine Intake (Siwat, 7 Nov 2026): goods are held in the quarantine of the
	warehouse they come from (``Warehouse.quarantine_warehouse``, else the company's Quarantine warehouse), and one
	intake moves into one quarantine warehouse. A row put into another quarantine, or rows of warehouses whose
	quarantines differ, are refused with the warehouse to use; make one intake for each source warehouse."""
	from erpnext.borrowing.doctype.borrowing.borrowing_core import quarantine_warehouse_for

	from .quarantine_release import zone_transfer_route

	if zone_transfer_route(doc) == INTAKE_ROUTE:
		return

	targets = {}
	for row in doc.get("items") or []:
		source = row.get("s_warehouse") or doc.get("from_warehouse")
		target = row.get("t_warehouse") or doc.get("to_warehouse")
		if not source or not target:
			continue
		expected = quarantine_warehouse_for(doc.get("company"), source) if doc.get("company") else None
		if expected and target != expected:
			frappe.throw(
				_("Row {0} ({1}): goods of {2} are held in {3}, not in {4}. Put them into {3}.").format(
					row.get("idx"), row.get("item_code"), source, expected, target
				),
				title=_("Quarantine of the Warehouse"),
			)
		targets.setdefault(target, []).append(row.get("idx"))
	if len(targets) > 1:
		frappe.throw(
			_(
				"One Quarantine Intake moves goods into one quarantine warehouse, but these rows go to {0}. "
				"Make a separate intake for the goods of each warehouse."
			).format(
				", ".join(
					f"{name} (row {', '.join(str(idx) for idx in rows)})" for name, rows in targets.items()
				)
			),
			title=_("Quarantine of the Warehouse"),
		)


def row_quarantine(doc, row):
	"""The quarantine warehouse the row's goods are in: an intake's target, a receipt's receiving warehouse."""
	if doc.get("doctype") == "Stock Entry":
		return row.get("t_warehouse") or doc.get("to_warehouse")
	return row.get("warehouse")


def row_held_location(doc, row):
	"""The exact inspection zone when goods are held inside the physical warehouse."""
	from erpnext.stock.doctype.storage_location.storage_location import QUALITY_INSPECTION, get_location_type

	field = "to_storage_location" if doc.get("doctype") == "Stock Entry" else "storage_location"
	location = row.get(field)
	return (
		location
		if location and get_location_type(location, row_quarantine(doc, row)) == QUALITY_INSPECTION
		else None
	)


def refuse_cancel_zone_inspection(inspection, method=None):
	"""A submitted movement that used or returned this result must be reversed before cancelling it."""
	doctype = inspection.get("reference_type")
	name = inspection.get("reference_name")
	row_name = inspection.get("child_row_reference")
	if (
		inspection.get("inspection_type") != "Incoming"
		or doctype
		not in ("Purchase Receipt", "Purchase Invoice", "Sales Invoice", "Delivery Note", "Stock Entry")
		or not name
		or not row_name
	):
		return
	reference = frappe.get_doc(doctype, name, for_update=True)
	row = next((line for line in reference.items if line.name == row_name), None)
	location = row_held_location(reference, row) if row else None
	if not location:
		return
	if frappe.db.exists(
		"Stock Entry Detail", {"ste_detail": row_name, "storage_location": location, "docstatus": 1}
	):
		frappe.throw(_("Cancel the stock movement before cancelling this Quality Inspection."))
	field = {"Purchase Receipt": "purchase_receipt_item", "Purchase Invoice": "purchase_invoice_item"}.get(
		doctype
	)
	if field and frappe.db.exists(
		f"{doctype} Item", {field: row_name, "qty": ["<", 0], "storage_location": location, "docstatus": 1}
	):
		frappe.throw(_("Cancel the purchase return before cancelling this Quality Inspection."))


def _member(team, doc, user=None):
	user = user or frappe.session.user
	team_member = get_policy("team_member")
	if team_member:
		return team_member(user, doc.company, team)
	return user == "Administrator" or team in inspection_teams(user, company=doc.company)


def awaiting_rows(doc):
	"""The rows of a submitted document that still wait for their first Quality Inspection while their goods are held:
	a Quarantine Intake's rows whose Item names a QI team, a receipt's rows in quarantine (``receipt_quarantine``), the
	asset rows of a borrowing's return Asset Movement held for inspection."""
	if is_intake(doc):
		return [
			row
			for row in doc.get("items") or []
			if item_team(row.get("item_code")) and not row_inspection(doc, row, submitted=True)
		]
	if doc.get("doctype") == "Asset Movement":
		asset_rows = get_policy("asset_rows")
		return asset_rows(doc) if asset_rows else []
	from .receipt_quarantine import waiting_rows

	return waiting_rows(doc)


def inspectable_rows(doc, user=None):
	"""The rows of a submitted document that ``user`` may inspect: an Item of a QI team they belong to."""
	rows = []
	for row in awaiting_rows(doc):
		team = _row_team(doc, row)
		if team and _member(team, doc, user):
			rows.append(row)
	return rows


def _row_team(doc, row):
	"""The Incoming QI team of the row's Item (``quality_inspection.item_inspection_team``, with the Product Group's
	default)."""
	if doc.get("doctype") in ("Purchase Receipt", "Purchase Invoice"):
		row_team = get_policy("row_team")
		if row_team:
			return row_team(doc, row)
		warehouse = row.get("putaway_warehouse") or row.get("warehouse")
		if warehouse:
			info = frappe.db.get_value("Warehouse", warehouse, ["company", "incoming_qi_team"], as_dict=True)
			if info and info.company == doc.company and info.incoming_qi_team:
				return info.incoming_qi_team
	return item_team(row.get("item_code"))


def row_inspection(doc, row, submitted=False, lock=False):
	"""The row's Quality Inspection (not cancelled; only a submitted one with ``submitted``), else None."""
	return frappe.db.get_value(
		"Quality Inspection",
		{
			"reference_type": doc.get("doctype") or "Stock Entry",
			"reference_name": doc.name,
			"child_row_reference": row.name,
			"docstatus": 1 if submitted else ["<", 2],
		},
		["name", "status", "docstatus"],
		as_dict=True,
		for_update=lock,
	)


def new_inspection(doc, row, remarks=None, *, inspection_type="Incoming"):
	"""The unsaved Incoming Quality Inspection of one row of a submitted receipt or intake, made for the session user:
	the reference, the row, the Item, its template and the batch / quantity of the row."""
	inspection = frappe.new_doc("Quality Inspection")
	inspection.update(
		{
			"company": doc.company,
			"inspection_type": inspection_type,
			"reference_type": doc.get("doctype") or "Stock Entry",
			"reference_name": doc.name,
			"child_row_reference": row.name,
			"item_code": row.item_code,
			"batch_no": row.get("batch_no"),
			"sample_size": abs(
				flt(row.get("qty") or row.get("transfer_qty") or row.get("returned_qty") or 1)
			),
			"quality_inspection_template": frappe.db.get_value(
				"Item", row.item_code, "quality_inspection_template"
			),
			"inspected_by": frappe.session.user,
			"remarks": remarks or _("Goods in quarantine ({0} row {1})").format(doc.name, row.idx),
		}
	)
	# The draft is manual, header and readings alike (Siwat, 6 Nov 2026, UX review): no reading has been typed, so
	# nothing may be computed from the template's empty Min / Max (the draft was born Rejected). It starts Accepted; the
	# inspector only confirms or rejects it.
	inspection.manual_inspection = 1
	if inspection.quality_inspection_template:
		inspection.get_item_specification_details()
		for reading in inspection.get("readings") or []:
			reading.manual_inspection = 1
	return inspection


def _draft_inspection(doc, row, remarks=None):
	inspection = new_inspection(doc, row, remarks)
	inspection.insert()
	return inspection.name


def _inspection_document(doc):
	if not is_quarantine_source(doc):
		frappe.throw(
			_(
				"Inspect only the goods of a submitted receipt, return movement or Quarantine Intake held for inspection."
			)
		)
	if cint(doc.get("docstatus")) != 1:
		frappe.throw(
			_("Submit {0} {1} first: goods are received before they are inspected.").format(
				_(doc.doctype), doc.name
			)
		)


def inspection_documents(doc, user=None):
	"""The Quality Inspections the session user may make for ``doc``, saved ones first: [(existing name | None, unsaved
	document | None, row)]. A row whose inspection already exists (a draft; a submitted one is no longer awaited) is
	reused; the others are made for the user only when she belongs to the Item's QI team."""
	found = []
	mine = {row.name for row in inspectable_rows(doc, user)}
	for row in awaiting_rows(doc):
		if row.name not in mine:
			continue
		existing = row_inspection(doc, row)
		if existing:
			found.append((existing.name, None, row))
		else:
			found.append((None, new_inspection(doc, row), row))
	return found


@frappe.whitelist(methods=["POST"])
def make_receipt_inspections(doctype: str, name: str, child_row_reference: str | None = None):
	"""The Quality Inspections of the rows of a submitted receipt, return Asset Movement or Quarantine Intake that the user's QI
	team inspects: an existing draft is reused and a missing one is made now, in the user's name. Returns {"inspections":
	[...], "skipped": [...]} (the rows that are not an Item of her team)."""
	if doctype == "Asset Movement":
		make_asset_inspections = get_policy("make_asset_inspections")
		if make_asset_inspections:
			return make_asset_inspections(doctype, name, row_name=child_row_reference)

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	frappe.has_permission("Quality Inspection", "create", throw=True)
	_inspection_document(doc)
	frappe.db.get_value(doctype, name, "name", for_update=True)
	if doctype == "Delivery Note" and doc.get("is_borrowing_transaction"):
		if not child_row_reference:
			frappe.throw(_("Select one borrowing receipt row to inspect."))
		row = next((row for row in inspectable_rows(doc) if row.name == child_row_reference), None)
		if not row:
			frappe.throw(
				_("Select a pending receipt row assigned to your Quality Inspection team."),
				frappe.PermissionError,
			)
		existing = row_inspection(doc, row, lock=True)
		if existing:
			inspection = frappe.get_doc("Quality Inspection", existing.name)
			inspection.check_permission("read")
		else:
			inspection = new_inspection(doc, row)
			inspection.set("__islocal", 1)
		return inspection.as_dict()
	names, skipped = [], []
	mine = {row.name for row in inspectable_rows(doc)}
	if child_row_reference and child_row_reference not in mine:
		frappe.throw(
			_("Select a pending receipt row assigned to your Quality Inspection team."),
			frappe.PermissionError,
		)
	for row in awaiting_rows(doc):
		if child_row_reference and row.name != child_row_reference:
			continue
		if row.name not in mine:
			skipped.append(
				_("Row {0} ({1}): not an Item of your Quality Inspection team.").format(
					row.idx, row.item_code
				)
			)
			continue
		existing = row_inspection(doc, row, lock=True)
		names.append(existing.name if existing else _draft_inspection(doc, row))
	return {"inspections": names, "skipped": skipped}


def remaining_parts(doc, row, quarantine, current=False):
	"""What of this receiving row is still where it was received (``quarantine``): the parts a move takes, see the fork's
	``purchase_inspection_units.remaining_parts``. A row received at the STAGING location of a normal warehouse (no
	inspection, put away later) counts only what that location holds."""
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		remaining_parts as stock_remaining_parts,
	)

	from .receipt_quarantine import is_putaway_row

	location = row_held_location(doc, row)
	if not location and is_putaway_row(doc, row):
		location = row.get("storage_location")
	return stock_remaining_parts(doc, row, quarantine, current=current, location=location)


def may_decide(user=None):
	user = user or frappe.session.user
	may_move_stock = get_policy("may_move_stock")
	return may_move_stock(user) if may_move_stock else frappe.has_permission("Stock Entry", "create", user=user)


def _home(doc, row):
	"""Where an inspected row goes back to: an intake row's source, a receipt row's warehouse (a purchase row's
	``putaway_warehouse``, a returned row's delivery warehouse; a row received at STAGING is put away inside its own
	warehouse)."""
	if doc.get("doctype") == "Stock Entry":
		return row.get("s_warehouse") or doc.get("from_warehouse")
	from .receipt_quarantine import home_warehouse, is_putaway_row

	home = home_warehouse(doc, row)
	if row_held_location(doc, row):
		return row.get("warehouse")
	return home if home and (home != row.get("warehouse") or is_putaway_row(doc, row)) else None


def _source_rows(doc):
	"""The rows of a submitted intake or receipt whose goods may be in quarantine (or at STAGING for a put-away)."""
	if doc.get("doctype") == "Stock Entry":
		return doc.get("items") or []
	from .receipt_quarantine import putaway_rows, quarantined_rows

	return [*quarantined_rows(doc), *putaway_rows(doc)]


def release_rows(doc):
	"""What the Stock team may take out of quarantine for each row of a submitted receipt or intake: [{"row", "idx",
	"item_code", "parts", "quarantine", "inspection", "status", "home", "ready"}]. ``parts`` is what of the row is still
	there (``remaining_parts``); ``putaway_location`` is the Storage Location the receipt row was meant for
	(``putaway_storage_location``), the target of its accepted goods; ``ready`` is true for a row with a submitted inspection, for an intake row whose Item
	names no QI team (nothing waits for it) and for a purchase row that needs no inspection and lies at STAGING
	(``putaway``: put away at once)."""
	from .receipt_quarantine import is_putaway_row

	rows = []
	for row in _source_rows(doc):
		putaway = is_putaway_row(doc, row)
		quarantine = row_quarantine(doc, row)
		inspection = None if putaway else row_inspection(doc, row, submitted=True)
		needs = False if putaway else (bool(item_team(row.item_code)) if is_intake(doc) else True)
		parts = remaining_parts(doc, row, quarantine) if quarantine else []
		location = row.get("storage_location") if putaway else row_held_location(doc, row)
		rows.append(
			{
				**({"storage_location": location} if location else {}),
				"inspection_zone": bool(location and not putaway),
				"row": row.name,
				"idx": row.idx,
				"item_code": row.item_code,
				"parts": parts,
				"qty": sum(part["qty"] for part in parts),
				"quarantine": quarantine,
				"inspection": inspection.name if inspection else None,
				"status": inspection.status if inspection else None,
				"home": _home(doc, row),
				# the bin the receipt row was meant for, where accepted goods go (UX WP6; the fork's build_release_entry)
				"putaway_location": None if putaway else row.get("putaway_storage_location"),
				"ready": bool(inspection) or not needs,
				"staging": putaway,
				"putaway": putaway,
			}
		)
	return rows


def rejected_purchase_parts(doc, *, current=False):
	"""Rejected goods traceable to this purchase and still in QI STAGING or in DAMAGE after its release."""
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		remaining_parts as stock_remaining_parts,
	)
	from erpnext.stock.doctype.storage_location.storage_location import DAMAGE, get_location_type

	rejected = [row for row in release_rows(doc) if row["status"] == "Rejected"]
	parts = [row for row in rejected if row["qty"] > 0]
	typed = {row["row"]: row for row in rejected if row.get("inspection_zone")}
	if not typed:
		return parts
	movements = frappe.get_all(
		"Stock Entry Detail",
		filters={"ste_detail": ["in", list(typed)], "docstatus": 1},
		fields=["name", "parent", "ste_detail"],
	)
	entries = {}
	for move in movements:
		if move.parent not in entries:
			entry = frappe.get_doc("Stock Entry", move.parent)
			entries[move.parent] = {row.name: (entry, row) for row in entry.items} if is_move(entry) else {}
		pair = entries[move.parent].get(move.name)
		if not pair:
			continue
		entry, row = pair
		source = typed[move.ste_detail]
		location = row.to_storage_location
		if (
			entry.company != doc.company
			or row.item_code != source["item_code"]
			or row.s_warehouse != source["quarantine"]
			or row.s_warehouse != row.t_warehouse
			or row.storage_location != source["storage_location"]
			or get_location_type(location, row.t_warehouse) != DAMAGE
		):
			continue
		available = stock_remaining_parts(entry, row, row.t_warehouse, current=current, location=location)
		if available:
			parts.append(
				{
					**source,
					"quarantine": row.t_warehouse,
					"storage_location": location,
					"parts": available,
					"qty": sum(part["qty"] for part in available),
				}
			)
	return parts


@frappe.whitelist()
def release_status(doctype: str, name: str):
	"""Whether inspected goods of this receipt or intake are still in quarantine for the Stock team to take out."""
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if not is_quarantine_source(doc):
		return {"rows": [], "ready": False}
	rows = [row for row in release_rows(doc) if row["qty"] > 0 and row["ready"]]
	return {
		"rows": [{key: row[key] for key in ("idx", "item_code", "qty", "status")} for row in rows],
		"ready": bool(rows),
	}


def release_entry(doc, kind=None):
	"""The Stock team's Stock Entry out of quarantine (or the put-away from STAGING), unsaved and prefilled (the maker of the
	task "Move" and of the button "Create Stock Entry"): a Material Transfer with one row for each part of the inspected goods
	still there. Accepted goods (and goods that need no inspection) go toward the warehouse they were meant for, to the
	Storage Location the receipt row was meant for (``putaway_storage_location``), else the Item's preferred, never
	STAGING, Storage Location there (left to the team when there is none); Rejected goods go to the
	warehouse's DAMAGE warehouse when it has one, else the team chooses. Each row names its source row
	(``against_stock_entry`` / ``ste_detail``), so a later move counts only what is left. Nothing is submitted by the
	system."""
	rows = [row for row in release_rows(doc) if row["ready"] and row["qty"] > 0]
	if not rows:
		frappe.throw(_("Nothing inspected is left in quarantine to move."))
	# One entry never mixes the two kinds: a move out of quarantine takes the Quarantine Release route, a put-away
	# inside a warehouse the Storage Location route (the Stock team presses again for the other kind).
	if kind not in ("release", "putaway"):
		kind = "release" if any(not row["putaway"] for row in rows) else "putaway"
	rows = [row for row in rows if bool(row["putaway"]) == (kind == "putaway")]
	if not rows:
		frappe.throw(_("Nothing inspected is left in quarantine to move."))
	return build_release_entry(doc, _source_rows(doc), rows)


@frappe.whitelist()
def make_release_entry(doctype: str, name: str, kind: str | None = None):
	"""Create Stock Entry: ``release_entry`` of a submitted receipt or intake, for the form button (not saved)."""
	if not may_decide():
		frappe.throw(_("Only the Stock team takes goods out of quarantine."), frappe.PermissionError)
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	frappe.has_permission("Stock Entry", "create", throw=True)
	if not is_quarantine_source(doc):
		frappe.throw(
			_("Only the goods of a submitted receipt or Quarantine Intake are moved out of quarantine here.")
		)
	return release_entry(doc, kind).as_dict()


# -- rejected goods go back to the supplier --------------------------------------------------------------------------


@frappe.whitelist()
def rejected_goods_to_return(name: str):
	"""The rejected quantity of a submitted Purchase Receipt that no Purchase Return has taken back yet (rejected in
	quarantine, or received into its Rejected Warehouse), for the button Create > Purchase Return of rejected goods."""
	doc = frappe.get_doc("Purchase Receipt", name)
	doc.check_permission("read")
	return rejected_to_return(doc)


@frappe.whitelist()
def make_return_rejected(name: str):
	"""Create Purchase Return of the rejected goods of a submitted Purchase Receipt (not saved): the Task "Return
	rejected goods" and this button make the same document (``my_space.makers._return_rejected``)."""
	doc = frappe.get_doc("Purchase Receipt", name)
	doc.check_permission("read")
	frappe.has_permission("Purchase Receipt", "create", throw=True)
	if cint(doc.docstatus) != 1 or cint(doc.get("is_return")):
		frappe.throw(
			_("Only a submitted Purchase Receipt that is not a return has rejected goods to send back.")
		)
	return rejected_return_entry(doc).as_dict()


def rejected_in_quarantine(doc):
	"""The quantity (stock UOM) of a submitted Purchase Receipt whose Quality Inspection is Rejected and that is still in
	quarantine: purchasing sends it back to the supplier (Siwat, 9 Nov 2026)."""
	if doc.doctype != "Purchase Receipt" or cint(doc.docstatus) != 1 or cint(doc.get("is_return")):
		return 0
	return sum(row["qty"] for row in rejected_purchase_parts(doc))


def rejected_to_return(doc):
	"""The rejected quantity (stock UOM) of a submitted Purchase Receipt that no Purchase Return has taken back yet: what
	was rejected in quarantine, plus what was received into its Rejected Warehouse (the return against the rejected
	warehouse, ``make_purchase_return_against_rejected_warehouse``, or a plain return)."""
	if doc.doctype != "Purchase Receipt" or cint(doc.docstatus) != 1 or cint(doc.get("is_return")):
		return 0
	quarantined = rejected_in_quarantine(doc)
	rejected = sum(
		flt(row.get("rejected_qty")) * flt(row.get("conversion_factor") or 1)
		for row in doc.get("items") or []
	)
	if rejected <= 0:
		return quarantined
	returned = frappe.db.sql(
		"""SELECT COALESCE(SUM(-IF(detail.return_qty_from_rejected_warehouse = 1, detail.qty, detail.rejected_qty)
			* detail.conversion_factor), 0)
		FROM `tabPurchase Receipt Item` detail JOIN `tabPurchase Receipt` receipt ON receipt.name = detail.parent
		WHERE receipt.return_against = %s AND receipt.is_return = 1 AND receipt.docstatus = 1""",
		(doc.name,),
	)[0][0]
	return max(rejected - flt(returned), 0) + quarantined


def rejected_return_entry(doc):
	"""Return rejected goods: the Purchase Return of the rows of a Purchase Receipt whose Quality Inspection is Rejected and
	whose goods are still in quarantine (Siwat, 9 Nov 2026), cut down to what is left there; else, for a receipt that
	rejected goods at the door (a Rejected Warehouse), the native return against the rejected warehouse."""
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
		make_purchase_return,
		make_purchase_return_against_rejected_warehouse,
	)

	left = rejected_purchase_parts(doc)
	if not left:
		return make_purchase_return_against_rejected_warehouse(doc.name)
	made = make_purchase_return(doc.name)
	kept = []
	for source in made.items:
		for held in left:
			if source.get("purchase_receipt_item") != held["row"]:
				continue
			for part in held["parts"]:
				row = frappe.copy_doc(source, ignore_no_copy=True)
				factor = row.get("conversion_factor") or 1
				row.qty = -part["qty"] / factor
				row.stock_qty = -part["qty"]
				row.received_qty = row.qty
				row.rejected_qty = 0
				row.warehouse = held["quarantine"]
				row.storage_location = held.get("storage_location") or source.get("storage_location")
				row.rejected_warehouse = None
				row.rejected_storage_location = None
				row.return_qty_from_rejected_warehouse = 0
				row.serial_and_batch_bundle = None
				row.batch_no = part.get("batch_no")
				row.serial_no = "\n".join(part.get("serial_nos") or [])
				row.use_serial_batch_fields = int(bool(row.batch_no or row.serial_no))
				kept.append(row)
	made.set("items", [])
	for row in kept:
		made.append("items", row)

	if not kept:
		frappe.throw(frappe._("No rejected goods linked to this receipt remain to return."))
	made.run_method("calculate_taxes_and_totals")
	return made


# -- goods moved to DAMAGE -------------------------------------------------------------------------------------------


def is_move(doc):
	"""A submitted Stock Entry that moves goods (a Material Transfer): its rows into DAMAGE wait for their disposal,
	whatever route the move took (the Stock team's own Stock Entry out of quarantine included)."""
	return (
		doc is not None
		and doc.get("doctype") == "Stock Entry"
		and cint(doc.get("docstatus")) == 1
		and doc.get("purpose") == "Material Transfer"
	)


def damage_rows(doc):
	"""[(row, warehouse, qty)] of the goods a Move put in a Write-Off (DAMAGE) warehouse that the warehouse still holds,
	the quantity in the stock UOM, never more than the warehouse holds of the Item (shared by the rows in order)."""
	if not is_move(doc):
		return []
	from erpnext.stock.doctype.storage_location.storage_location import DAMAGE, get_location_type

	rows, taken = [], {}
	for row in doc.get("items") or []:
		warehouse = row.get("t_warehouse") or doc.get("to_warehouse")
		location = row.get("to_storage_location")
		if location and get_location_type(location, warehouse) == DAMAGE:
			from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import remaining_parts

			parts = remaining_parts(doc, row, warehouse, location=location)
			if parts:
				rows.append((row, warehouse, sum(part["qty"] for part in parts)))
			continue
		if not warehouse or frappe.db.get_value("Warehouse", warehouse, "warehouse_type") != WRITE_OFF_TYPE:
			continue
		key = (row.item_code, warehouse)
		left = _held(*key) - taken.get(key, 0)
		qty = min(flt(row.get("transfer_qty") or row.qty), left)
		if qty > 0:
			taken[key] = taken.get(key, 0) + qty
			rows.append((row, warehouse, qty))
	return rows


@frappe.whitelist()
def damage_status(stock_entry: str):
	"""Whether goods of this Move still wait in DAMAGE for their disposal (the form shows its button only then)."""
	doc = frappe.get_doc("Stock Entry", stock_entry)
	doc.check_permission("read")
	return {"remaining": bool(damage_rows(doc))}


@frappe.whitelist()
def make_damage_disposal(stock_entry: str):
	"""The Disposal issue of the goods a Move put in DAMAGE, prefilled and not saved: a Material Issue of Issue Type
	Disposal from the Write-Off warehouse, one row for each batch (serial numbers kept)."""
	if not may_decide():
		frappe.throw(_("Only the Stock team disposes of the goods in DAMAGE."), frappe.PermissionError)
	doc = frappe.get_doc("Stock Entry", stock_entry)
	doc.check_permission("read")
	frappe.has_permission("Stock Entry", "create", throw=True)
	return disposal_entry(doc).as_dict()


def disposal_entry(doc):
	"""The unsaved Disposal issue of the goods Move ``doc`` put in DAMAGE (no permission check: the caller checks, or it is
	the system drafting the hand-off ``ho_damage_dispose``)."""
	rows = damage_rows(doc)
	if not rows:
		frappe.throw(_("Nothing of this Move is left in DAMAGE to dispose of."))
	items = []
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import remaining_parts
	from erpnext.stock.doctype.storage_location.storage_location import DAMAGE, get_location_type

	for row, warehouse, qty in rows:
		stock_uom = row.get("stock_uom") or frappe.db.get_value("Item", row.item_code, "stock_uom")
		location = row.get("to_storage_location")
		if location and get_location_type(location, warehouse) == DAMAGE:
			parts = remaining_parts(doc, row, warehouse, location=location)
		else:
			batches = _row_batches(row) or {None: qty}
			serials = _row_serial_nos(row)
			parts = [
				{
					"batch_no": batch,
					"qty": min(quantity, qty),
					"serial_nos": serials[: int(min(quantity, qty))],
				}
				for batch, quantity in batches.items()
			]
		for part in parts:
			batch = part["batch_no"]
			serials = part["serial_nos"]
			items.append(
				{
					"item_code": row.item_code,
					"qty": part["qty"],
					"uom": stock_uom,
					"stock_uom": stock_uom,
					"conversion_factor": 1,
					"s_warehouse": warehouse,
					"storage_location": location,
					"use_serial_batch_fields": 1 if (batch or serials) else 0,
					"batch_no": batch,
					"serial_no": "\n".join(serials) if serials else None,
				}
			)
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"stock_entry_type": frappe.db.get_value(
				"Stock Entry Type", {"purpose": "Material Issue", "is_standard": 1}, "name"
			),
			"purpose": "Material Issue",
			"company": doc.company,
			"operation_issue_type": "Disposal",
			"remarks": _("Disposal of the goods moved to DAMAGE by {0}").format(doc.name),
		}
	)
	for item in items:
		entry.append("items", item)
	return entry
