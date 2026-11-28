"""Loans are inspected only where Siwat's QI workbooks ask (Siwat, 4 Nov 2026, B3; 9 Nov 2026).

The Item's switches (the users', since the 5 Nov correction of the workbooks' ticks; Siwat, 6 Nov 2026): a lent item is
inspected going out and coming back (the same goods) only when its Item has that switch on:

- Stock loan out: the Loan Delivery Note, natively (ERPNext's QI gate on a Delivery Note, the same switch).
- Stock loan back: received first, inspected after (Siwat, 9 Nov 2026). The user submits the receiving Loan Return Delivery Note and puts the rows whose Item is inspected on receipt into the Quarantine warehouse of the warehouse they were lent from
  (``borrowing_core.receive_row_into_quarantine``); the QI team inspects them there from its Inspect task
  (``workflows/receipt_quarantine.py``, ``quarantine_inspection.py``).
- Asset loan out: the Borrowing itself, before delivery. Quality Inspection may reference it (``setup_loan_inspections``);
  ``validate_asset_loan_inspections`` asks for a submitted, Accepted inspection of each asset row whose Item is inspected
  before delivery; ``make_loan_inspections`` (Create > Quality Inspection) opens an unsaved inspection for the Item's QI team, who only read
  the loan.
- Asset loan back: the user makes the return Asset Movement from the issue movement (Create > Return Movement; UX WP4,
  Siwat 18 Nov 2026: no Borrowing Return) and submits it. When its Item is inspected on receipt the asset is held,
  "Awaiting Inspection", until the QI team has inspected the row of the submitted return movement
  (``assets/inspection_hold.py``); this module releases the hold when the inspection is submitted (``settle_asset_return``)
  and flags a rejected asset for repair.

The QI team members read the loans of their Items (workflows/quality_reference_access.py).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from erpnext.assets.doctype.asset_repair.reinspection import return_repair_row

from .policy import sync_request_tasks

# An Asset is inspected on the Borrowing before it leaves and on the return Asset Movement after it comes back
# (UX WP4, Siwat 18 Nov 2026: the return movement is made from the issue movement; there is no Borrowing Return).
LOAN_ROWS = {"Borrowing": "Borrowing Item", "Asset Movement": "Asset Movement Item"}
ACCEPTED_ONLY = frozenset({"Accepted"})
ANY_RESULT = frozenset({"Accepted", "Rejected"})
INSPECTION_FIELD = "quality_inspection"
VERB = {"Borrowing": "lend", "Asset Movement": "receive back"}
RETIRED_REFERENCE = "Borrowing Return"


def is_return_movement(doc):
	"""A borrowing's return Asset Movement (UX WP4, Siwat 18 Nov 2026): made from the issue movement, it brings the
	Assets back; its rows are inspected on receipt as a Borrowing Return's were."""
	return bool(
		doc
		and doc.get("doctype") == "Asset Movement"
		and doc.get("borrowing")
		and doc.get("borrowing_source_doctype") == "Borrowing"
		and doc.get("purpose") == "Receipt"
	)


def inspection_rows(doc):
	"""The rows a Quality Inspection of ``doc`` reads, each with its ``item_code``: an Asset Movement's rows name the
	Item as ``asset_item_code``, so it is copied onto the row (in memory only)."""
	if doc.get("doctype") == "Asset Movement":
		rows = list(doc.get("assets") or [])
		for row in rows:
			if not row.get("item_code"):
				row.item_code = row.get("asset_item_code")
		return rows
	return list(doc.get("items") or [])


def reference_inspection_direction(doc):
	if doc.get("doctype") == "Borrowing":
		return "Outgoing"
	if doc.get("doctype") == "Delivery Note":
		return "Incoming" if cint(doc.get("is_return")) else "Outgoing"
	if is_return_movement(doc):
		return "Incoming"


def row_switch(doc, row):
	if doc.get("doctype") == "Delivery Note":
		return (
			"inspection_required_before_purchase"
			if cint(doc.get("is_return"))
			else "inspection_required_before_delivery"
		)
	if not row.get("asset"):
		return None
	if is_return_movement(doc):
		return "inspection_required_before_purchase"
	if doc.get("doctype") == "Borrowing" and doc.get("loan_type") == "Asset":
		return "inspection_required_before_delivery"


def _loan_type(doc):
	"""Asset or Stock for a Borrowing; Asset for a return Asset Movement; None for anything else."""
	if doc.doctype == "Borrowing":
		return doc.get("loan_type")
	return "Asset" if is_return_movement(doc) else None


def _rows(doc):
	return inspection_rows(doc)


def _row_needs_inspection(doc, row):
	switch = row_switch(doc, row)
	return bool(switch and cint(frappe.get_cached_value("Item", row.get("item_code"), switch)))


def results_allowed(direction):
	"""What a submitted inspection must find to let the goods move: going out, Accepted; coming back, any result
	(Siwat: what fails on its return is received into quarantine / repair so the loan can close)."""
	return ACCEPTED_ONLY if direction == "Outgoing" else ANY_RESULT


def validate_asset_loan_inspections(doc, method=None):
	"""Before Loan delivery: each asset row whose Item is inspected before delivery has a submitted, Accepted Quality
	Inspection of that very row. A return movement asks for none: the asset is received first (9 Nov 2026)."""
	if doc.get("loan_type") != "Asset" or doc.doctype != "Borrowing":
		return
	direction = reference_inspection_direction(doc)
	for row in doc.get("items") or []:
		if not _row_needs_inspection(doc, row):
			continue
		name = row.get(INSPECTION_FIELD)
		inspection = (
			frappe.db.get_value(
				"Quality Inspection",
				name,
				[
					"docstatus",
					"status",
					"reference_type",
					"reference_name",
					"child_row_reference",
					"item_code",
					"inspection_type",
				],
				as_dict=True,
			)
			if name
			else None
		)
		if not inspection:
			frappe.throw(
				_(
					"Row {0} ({1}, {2}): the Item's Quality Inspection team inspects this asset before you {3} it (Create > Quality Inspection)."
				).format(row.idx, row.item_code, row.get("asset") or "", _(VERB[doc.doctype])),
				title=_("Inspection Required"),
			)
		if (
			inspection.reference_type != doc.doctype
			or inspection.reference_name != doc.name
			or (inspection.child_row_reference and inspection.child_row_reference != row.name)
			or inspection.item_code != row.get("item_code")
			or inspection.inspection_type != direction
		):
			frappe.throw(
				_("Row {0}: Quality Inspection {1} does not inspect this row.").format(row.idx, name),
				title=_("Inspection Required"),
			)
		if cint(inspection.docstatus) != 1 or inspection.status not in results_allowed(direction):
			frappe.throw(
				_("Row {0} ({1}): Quality Inspection {2} must be submitted and Accepted.").format(
					row.idx, row.item_code, name
				),
				title=_("Inspection Required"),
			)


def _return_movement(inspection):
	"""The submitted return Asset Movement an inspection inspects, else None."""
	if inspection.get("reference_type") != "Asset Movement" or not inspection.get("reference_name"):
		return None
	if not frappe.db.exists("Asset Movement", inspection.reference_name):
		return None
	doc = frappe.get_doc("Asset Movement", inspection.reference_name)
	return doc if is_return_movement(doc) and _can_inspect_assets(doc) else None


def settle_asset_return(inspection, method=None):
	"""Quality Inspection on_submit of an asset a return Asset Movement received back (Siwat, 9 Nov 2026): the hold on
	the asset ends, "Awaiting Inspection" (``assets/inspection_hold.py``). Accepted: the asset is lendable again.
	Rejected: the hold ends too and the asset is flagged for repair (Out of Order and a note; the custodian starts the
	repair explicitly, ``make_return_repairs``)."""
	doc = _return_movement(inspection)
	if not doc:
		return
	from erpnext.assets.inspection_hold import release_hold

	for row in _rows(doc):
		if row.name != inspection.get("child_row_reference") or not row.get("asset"):
			continue
		release_hold(row.asset, doc.name)
		if inspection.get("status") == "Rejected":
			flag_rejected_asset_return(doc, row, inspection.name)
		_notify_ready(row.asset)
	# the return itself did not change: open the hand-offs its new state asks for (repair of a rejected asset)
	sync_request_tasks(frappe.get_doc("Asset Movement", doc.name))


def _notify_ready(asset):
	"""The hold ended: tell the Demo request that waits for the asset (it was not ready while held)."""
	from .demo_reservations import _notify_ready_asset

	_notify_ready_asset(asset)


def hold_cancelled_asset_return(inspection):
	"""Quality Inspection on_cancel of a returned asset's row: it waits for its inspection again."""
	doc = _return_movement(inspection)
	if not doc:
		return
	from erpnext.assets.inspection_hold import hold_again

	for row in _rows(doc):
		if row.name == inspection.get("child_row_reference") and row.get("asset"):
			hold_again(row.asset, doc.name)


def flag_rejected_asset_return(doc, row, inspection_name):
	"""Record a failed returned asset for repair; the custodian starts its repair explicitly."""
	text = _("Received back on {0} {1} with Quality Inspection {2} Rejected: send it for repair.").format(
		_(doc.doctype), doc.name, inspection_name
	)
	asset = frappe.get_doc("Asset", row.asset)
	asset.set_status("Out of Order")
	asset.add_comment("Comment", text)
	doc.add_comment("Comment", _("Row {0} ({1}): {2}").format(row.idx, row.asset, text))


def _existing_return_repair(doc, row, exclude=None):
	filters = {"asset": row.asset, "company": doc.company, "docstatus": ["!=", 2]}
	if exclude:
		filters["name"] = ["!=", exclude]
	return frappe.db.get_value(
		"Asset Repair",
		{**filters, "return_movement": doc.name, "return_movement_item": row.name},
		"name",
		for_update=True,
	)


@frappe.whitelist(methods=["POST"])
def make_return_repairs(name: str, row_name: str | None = None, doctype=None):
	"""Map one rejected return row to an unsaved repair; the user saves it explicitly."""
	from frappe.utils import now_datetime

	doc = _follow_up_source("Asset Movement", name, "Asset Repair")
	row = return_repair_row(doc, row_name)
	if _existing_return_repair(doc, row):
		frappe.throw(_("This returned asset already has an Asset Repair. Open the linked repair instead."))
	return (
		frappe.new_doc("Asset Repair")
		.update(
			{
				"asset": row.asset,
				"company": doc.company,
				"return_movement": doc.name,
				"return_movement_item": row.name,
				"failure_date": now_datetime(),
				"repair_status": "Pending",
				"description": _("Rejected on the borrowing return {0}; Quality Inspection {1}.").format(
					doc.name, row.quality_inspection
				),
			}
		)
		.as_dict()
	)


def validate_return_repair(repair, method=None):
	"""Recheck source and duplicates on save, including two unsaved forms opened together."""
	previous = repair.get_doc_before_save()
	if (
		previous
		and previous.get("return_movement")
		and any(
			repair.get(field) != previous.get(field) for field in ("return_movement", "return_movement_item")
		)
	):
		frappe.throw(_("The source return movement and row cannot be changed."))
	if not repair.get("return_movement"):
		return
	doc = frappe.get_doc("Asset Movement", repair.return_movement)
	doc.check_permission("read")
	frappe.db.sql("select name from `tabAsset Movement` where name=%s for update", doc.name)
	doc.reload()
	row = return_repair_row(doc, repair.get("return_movement_item"))
	if repair.get("asset") != row.asset or repair.get("company") != doc.company:
		frappe.throw(_("Asset Repair must use the returned asset and its company."))
	if _existing_return_repair(doc, row, exclude=repair.name):
		frappe.throw(_("This returned asset already has an Asset Repair."))


def _can_inspect_assets(doc):
	"""A Borrowing is inspected before it is delivered; a return Asset Movement once it is submitted."""
	if doc.doctype == "Asset Movement":
		return is_return_movement(doc) and cint(doc.docstatus) == 1
	return cint(doc.docstatus) == 0 or (
		doc.doctype == "Borrowing"
		and cint(doc.docstatus) == 1
		and doc.status == "Pending Delivery"
		and (
			not doc.get("asset_movement")
			or frappe.db.get_value("Asset Movement", doc.asset_movement, "docstatus") != 1
		)
	)


def validate_loan_inspection(inspection):
	"""An asset inspection belongs to an undelivered loan or a submitted return row and its direction."""
	loan = frappe.get_doc(inspection.reference_type, inspection.reference_name)
	loan.check_permission("read")
	frappe.db.sql(f"select name from `tab{loan.doctype}` where name=%s for update", loan.name)
	loan.reload()
	if _loan_type(loan) != "Asset":
		frappe.throw(_("A Stock borrowing is inspected on its Delivery Notes."))
	if not _can_inspect_assets(loan):
		frappe.throw(
			_("Submit the return Asset Movement first: goods are received before they are inspected.")
			if loan.doctype == "Asset Movement"
			else _("Inspect assets only before the borrowing is delivered.")
		)
	rows = [
		row
		for row in _rows(loan)
		if row.get("item_code") == inspection.item_code
		and (not inspection.get("child_row_reference") or row.name == inspection.child_row_reference)
	]
	if not rows:
		frappe.throw(
			_("Quality Inspection must inspect one row of {0} {1}.").format(_(loan.doctype), loan.name)
		)
	if len(rows) != 1:
		frappe.throw(_("Select one asset row for Quality Inspection."))
	inspection.child_row_reference = rows[0].name
	existing = frappe.db.get_value(
		"Quality Inspection",
		{
			"reference_type": loan.doctype,
			"reference_name": loan.name,
			"child_row_reference": rows[0].name,
			"docstatus": ["!=", 2],
			"name": ["!=", inspection.get("name") or ""],
		},
		"name",
		for_update=True,
	)
	if existing:
		frappe.throw(_("This asset row already has Quality Inspection {0}.").format(existing))
	if not any(_row_needs_inspection(loan, row) for row in rows):
		frappe.throw(
			_("Item {0} is not inspected when it is delivered, so it needs no Quality Inspection.").format(
				inspection.item_code
			)
		)
	if inspection.get("inspection_type") != reference_inspection_direction(loan):
		frappe.throw(
			_("The inspection of a {0} uses Inspection Type {1}.").format(
				_(loan.doctype), _(reference_inspection_direction(loan))
			)
		)


class LoanInspectionMixin:
	"""Quality Inspection (extend_doctype_class): native ERPNext knows no loan reference; this checks it instead."""

	def validate_inspection_required(self):
		if self.get("reference_type") in LOAN_ROWS:
			return validate_loan_inspection(self)
		return super().validate_inspection_required()

	def update_qc_reference(self, remove_reference=False):
		"""The row of a return Asset Movement names its inspection (native ERPNext reads an ``item_code`` column the
		Asset Movement Item does not have: it names the Item as ``asset_item_code``)."""
		if self.get("reference_type") == "Asset Movement" and self.get("child_row_reference"):
			frappe.db.set_value(
				"Asset Movement Item",
				self.child_row_reference,
				INSPECTION_FIELD,
				self.name if self.docstatus < 2 and not remove_reference else "",
				update_modified=False,
			)
			return
		return super().update_qc_reference(remove_reference)

	def on_submit(self):
		result = super().on_submit()
		settle_asset_return(self)
		return result

	def on_cancel(self):
		result = super().on_cancel()
		hold_cancelled_asset_return(self)
		return result


def _context(doc, row, direction):
	return frappe._dict(
		company=doc.company,
		item_code=row.get("item_code"),
		child_row_reference=row.name,
		inspection_type=direction,
		reference_type=doc.doctype,
		reference_name=doc.name,
	)


def _usable(name, doc, row):
	if not name or not frappe.db.exists("Quality Inspection", name):
		return False
	inspection = frappe.db.get_value(
		"Quality Inspection",
		name,
		["docstatus", "status", "reference_type", "reference_name", "child_row_reference"],
		as_dict=True,
	)
	return (
		inspection.reference_type == doc.doctype
		and inspection.reference_name == doc.name
		and inspection.child_row_reference in (None, "", row.name)
		and (
			cint(inspection.docstatus) == 0
			or (
				cint(inspection.docstatus) == 1
				and inspection.status in results_allowed(reference_inspection_direction(doc))
			)
		)
	)


def _selected_row(doc, row_name):
	if not row_name:
		frappe.throw(_("Select one asset row to open its document."))
	for row in _rows(doc):
		if row.name == row_name:
			return row
	frappe.throw(_("The selected row does not belong to this borrowing document."))


def _follow_up_source(doctype, name, target):
	if doctype not in LOAN_ROWS or target not in ("Quality Inspection", "Asset Repair"):
		frappe.throw(_("Unsupported borrowing follow-up document."))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	frappe.has_permission(target, "create", throw=True)
	return doc


def _inspection_row_allowed(doc, row):
	if not _row_needs_inspection(doc, row):
		return False
	direction = reference_inspection_direction(doc)
	if not all(
		frappe.call(hook, doc=_context(doc, row, direction))
		for hook in frappe.get_hooks("borrowing_inspection_row_grants")
	):
		return False
	return not _usable(row.get(INSPECTION_FIELD), doc, row)


@frappe.whitelist()
def loan_follow_up_choices(doctype: str, name: str, target: str):
	"""Only eligible rows are offered; mapping and saving recheck the same source."""
	doc = _follow_up_source(doctype, name, target)
	rows = []
	if target == "Quality Inspection":
		if _loan_type(doc) != "Asset" or not _can_inspect_assets(doc):
			return rows
		rows = [row for row in _rows(doc) if _inspection_row_allowed(doc, row)]
	elif is_return_movement(doc) and cint(doc.docstatus) == 1:
		for row in _rows(doc):
			if not return_repair_row(doc, row.name, required=False):
				continue
			if not _existing_return_repair(doc, row):
				rows.append(row)
	return [
		{"name": row.name, "idx": row.idx, "item_code": row.get("item_code"), "asset": row.asset}
		for row in rows
	]


@frappe.whitelist(methods=["POST"])
def make_loan_inspections(doctype: str, name: str, row_name: str | None = None):
	"""Map one eligible row to an unsaved QI; no draft or row link is written here."""
	doc = _follow_up_source(doctype, name, "Quality Inspection")
	if not _can_inspect_assets(doc) or _loan_type(doc) != "Asset":
		frappe.throw(
			_(
				"Inspect assets only before the borrowing is delivered or after the return movement is submitted."
			)
		)
	row = _selected_row(doc, row_name)
	if not _inspection_row_allowed(doc, row):
		frappe.throw(
			_("This row already has an inspection or is not assigned to your Quality Inspection team.")
		)
	return (
		frappe.new_doc("Quality Inspection")
		.update(
			{
				**_context(doc, row, reference_inspection_direction(doc)),
				"sample_size": 1,
				"inspected_by": frappe.session.user,
			}
		)
		.as_dict()
	)


def _loan_note_source(name):
	doc = frappe.get_doc("Delivery Note", name)
	doc.check_permission("read")
	frappe.has_permission("Quality Inspection", "create", throw=True)
	if not doc.get("is_borrowing_transaction"):
		frappe.throw(_("Select a Borrowing Delivery Note."))
	state = frappe.db.get_value(
		"Delivery Note", name, ["docstatus", "is_return"], as_dict=True, for_update=True
	)
	if not state or cint(state.docstatus) != (1 if cint(state.is_return) else 0):
		frappe.throw(_("Inspect borrowing goods before delivery, or after the return receipt is submitted."))
	return doc


@frappe.whitelist()
def loan_note_inspection_choices(doctype: str, name: str, target=None):
	if doctype != "Delivery Note":
		frappe.throw(_("Select a Borrowing Delivery Note."))
	doc = _loan_note_source(name)
	return [
		{
			"name": row.name,
			"idx": row.idx,
			"item_code": row.item_code,
			"asset": row.get("serial_no") or row.get("batch_no") or "",
		}
		for row in doc.get("items") or []
		if _inspection_row_allowed(doc, row)
	]


@frappe.whitelist(methods=["POST"])
def make_loan_note_inspection(doctype: str, name: str, row_name: str):
	"""An outgoing or incoming stock loan QI is reviewed before it is saved."""
	from erpnext.stock.quarantine.quarantine_inspection import new_inspection

	if doctype != "Delivery Note":
		frappe.throw(_("Select a Borrowing Delivery Note."))
	doc = _loan_note_source(name)
	row = _selected_row(doc, row_name)
	if not _inspection_row_allowed(doc, row):
		frappe.throw(
			_("This row already has an inspection or is not assigned to your Quality Inspection team.")
		)
	inspection = new_inspection(
		doc,
		row,
		remarks=_("Borrowing {0} row {1}").format(doc.name, row.idx),
		inspection_type=reference_inspection_direction(doc),
	)
	inspection.set("__islocal", 1)
	return inspection.as_dict()


@frappe.whitelist()
def make_quality_inspections(company: str, doctype: str, docname: str, items, inspection_type: str):
	"""The native bulk endpoint must not persist follow-ups for a loan."""
	from erpnext.controllers.stock_controller import make_quality_inspections as native_make

	if doctype in LOAN_ROWS:
		frappe.throw(_("Use Create > Quality Inspection to review the unsaved borrowing inspection."))
	if doctype == "Delivery Note":
		source = frappe.get_doc(doctype, docname)
		source.check_permission("read")
		if source.get("is_borrowing_transaction") or source.get("borrowing"):
			frappe.throw(_("Use Create > Quality Inspection to review the unsaved borrowing inspection."))
	return native_make(company, doctype, docname, items, inspection_type)


def _fork():
	from erpnext.borrowing.doctype.borrowing import borrowing_core

	return borrowing_core


def quarantine_warehouse(company, near=None):
	"""The Quarantine warehouse that holds the goods of warehouse ``near`` (Siwat, 7 Nov 2026: each warehouse has its
	own, ``Warehouse.quarantine_warehouse``), else the company's one (type Quarantine or QA, or named so), else None.
	The lookup is the fork's (``borrowing_core.quarantine_warehouse_for``); a fork without the link keeps the first
	quarantine warehouse of the company."""
	if not company:
		return None
	return _fork().quarantine_warehouse_for(company, near)


def validate_loan_note_inspection(doc, method=None):
	"""Loan Delivery Note QIs keep one active result per row and the native movement timing."""
	if doc.get("reference_type") != "Delivery Note" or not doc.get("reference_name"):
		return
	reference = frappe.get_doc("Delivery Note", doc.reference_name)
	if not reference.get("is_borrowing_transaction"):
		return
	reference.check_permission("read")
	state = frappe.db.get_value(
		"Delivery Note", reference.name, ["docstatus", "is_return"], as_dict=True, for_update=True
	)
	if not state or cint(state.docstatus) != (1 if cint(state.is_return) else 0):
		frappe.throw(_("Inspect borrowing goods before delivery, or after the return receipt is submitted."))
	rows = [
		row
		for row in reference.get("items") or []
		if row.item_code == doc.get("item_code")
		and (not doc.get("child_row_reference") or row.name == doc.child_row_reference)
	]
	if len(rows) != 1:
		frappe.throw(_("Select one matching borrowing Delivery Note row to inspect."))
	doc.child_row_reference = rows[0].name
	if doc.get("inspection_type") != ("Incoming" if cint(state.is_return) else "Outgoing"):
		frappe.throw(_("The inspection direction must match the borrowing Delivery Note."))
	duplicate = frappe.db.get_value(
		"Quality Inspection",
		{
			"reference_type": "Delivery Note",
			"reference_name": reference.name,
			"child_row_reference": rows[0].name,
			"docstatus": ["!=", 2],
			"name": ["!=", doc.get("name") or ""],
		},
		"name",
		for_update=True,
	)
	if duplicate:
		frappe.throw(_("This borrowing receipt row already has Quality Inspection {0}.").format(duplicate))
