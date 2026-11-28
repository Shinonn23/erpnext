"""Received first, inspected after (Siwat, 9 Nov 2026): the one rule for every receipt of goods.

A Purchase Receipt, a Purchase Invoice that updates stock, a sales return (Delivery Note or Sales Invoice) and a
borrowing's return Delivery Note are submitted first. There is no Quality Inspection before the submit and the system drafts none:

1. A row whose Item is inspected on receipt (the Item switch 'Inspection Required before Purchase';
   ``quality_inspection.row_switch``) is received into the Quarantine warehouse of the warehouse concerned, at its
   default (STAGING) Storage Location. ``route_receipt_rows`` (before_validate of a draft) does it for the purchase
   documents, remembering the real warehouse in ``putaway_warehouse`` ("Receive for Warehouse") and the bin the user
   chose there in ``putaway_storage_location`` (the release puts the goods there), and for a sales return, whose home
   is the warehouse of the delivery it reverses. The header's ``set_warehouse`` ("Receive for Warehouse") is kept.
   ``preview_receipt_route`` runs the same rules on an unsaved row, so the form shows before the save what the save
   stores (UX WP6, 19 Nov 2026). The receiving row is moved by one helper, the
   ERPNext fork's ``borrowing_core.receive_row_into_quarantine`` (a borrowing's return Delivery Note is received
   into quarantine by the fork itself). Any other row goes to its warehouse as before.
2. The QI team gets an "Inspect" task on the SUBMITTED document for the rows still in quarantine without a submitted
   Quality Inspection (``waiting_rows``; ``my_space/inspections.py``) and makes the inspection itself, as the user who
   presses the button (``quarantine_inspection.make_receipt_inspections``). A Quality Inspection of an unsubmitted
   receipt is refused (``refuse_unsubmitted_receipt``).
3. The Stock team takes the goods out of quarantine with a Stock Entry of its own (``quarantine_release``): allowed when
   the goods belong to rows with a submitted inspection, Accepted or Rejected.

``quarantine_due_date`` is where the policy of days will give a task its due date; it returns None for now.

A stock purchase row that needs no inspection is received directly at a compatible storage or sales counter location.
Goods rejected at the door use the separate rejected leg in a damage warehouse. Earlier receipts still at STAGING
remain eligible for put-away.
"""

from __future__ import annotations

import frappe
from erpnext.controllers.stock_controller import is_fully_rejected_purchase_row
from erpnext.stock.doctype.storage_location.storage_location import (
	DAMAGE,
	HOLDING,
	PUTAWAY_LOCATION_TYPES,
	QUALITY_INSPECTION,
	get_default_storage_location,
	get_location_type,
	is_receiving_location,
	is_stock_type_warehouse,
	preferred_purchase_location,
	typed_location,
	validate_storage_location_warehouse,
)
from frappe import _
from frappe.utils import cint, flt

from .policies import get_policy

PUTAWAY = "putaway_warehouse"
PUTAWAY_LOCATION = "putaway_storage_location"
PURCHASE_DOCTYPES = ("Purchase Receipt", "Purchase Invoice")
RETURN_DOCTYPES = ("Delivery Note", "Sales Invoice")
RECEIPT_DOCTYPES = (*PURCHASE_DOCTYPES, *RETURN_DOCTYPES)
# Quality Inspection reference types that are inspected only after their submit
# the return Asset Movement of a borrowing too (UX WP4): its assets are received before they are inspected
AFTER_SUBMIT_REFERENCES = (*PURCHASE_DOCTYPES, *RETURN_DOCTYPES, "Asset Movement")


def quarantine_due_date(doc):
	"""The date by which the QI team should have inspected the goods of ``doc`` (the task's due date). None until the
	Quarantine policy of days exists."""
	return None


def _fork():
	from erpnext.borrowing.doctype.borrowing import borrowing_core

	return borrowing_core


def is_quarantine(warehouse, company):
	return bool(warehouse) and _fork().is_quarantine_warehouse(warehouse, company)


def row_in_quarantine(doc, row):
	warehouse = row.get("warehouse")
	return is_quarantine(warehouse, doc.company) or (
		get_location_type(row.get("storage_location"), warehouse) == QUALITY_INSPECTION
	)


def receives_goods(doc):
	"""The document is a receipt of goods that moves stock: a Purchase Receipt, a Purchase Invoice with Update Stock, or a
	return of goods (Delivery Note, Sales Invoice with Update Stock). A purchase return is not a receipt."""
	doctype = doc.get("doctype")
	if doctype in PURCHASE_DOCTYPES:
		if cint(doc.get("is_return")):
			return False
		return doctype == "Purchase Receipt" or bool(cint(doc.get("update_stock")))
	if doctype in RETURN_DOCTYPES:
		if not cint(doc.get("is_return")):
			return False
		return doctype == "Delivery Note" or bool(cint(doc.get("update_stock")))
	return False


def is_loan_return_note(doc):
	"""The receiving Delivery Note of a stock Loan Return: the fork puts its rows into quarantine itself."""
	return bool(doc.get("doctype") == "Delivery Note" and cint(doc.get("is_borrowing_transaction")))


def needs_inspection(doc, row):
	"""The row's goods are inspected on receipt: a stock Item whose switch for this document is on."""
	if not receives_goods(doc) or not row.get("item_code") or is_fully_rejected_purchase_row(doc, row):
		return False
	switch = "inspection_required_before_purchase"
	item = frappe.get_cached_value("Item", row.item_code, [switch, "is_stock_item"], as_dict=True)
	return bool(item and cint(item.get(switch)) and cint(item.get("is_stock_item")))


def return_home(row):
	"""Where the goods of a returned row go when they are accepted: the warehouse of the delivery it reverses (the row of
	the Delivery Note or Sales Invoice it returns), else the loan row's lending warehouse."""
	for field, doctype in (
		("dn_detail", "Delivery Note Item"),
		("sales_invoice_item", "Sales Invoice Item"),
		("si_detail", "Sales Invoice Item"),
	):
		if row.get(field) and frappe.get_meta(row.doctype).has_field(field):
			home = frappe.db.get_value(doctype, row.get(field), "warehouse")
			if home:
				return home
	if row.get("borrowing_item"):
		return frappe.db.get_value("Borrowing Item", row.borrowing_item, "source_warehouse")
	return None


def home_warehouse(doc, row):
	"""The warehouse a receipt row is really for: ``putaway_warehouse`` of a purchase row received into quarantine, the
	warehouse of the delivery for a returned row received there, else the row's own warehouse. The stock guards and
	the manager grants look at this one, not at the quarantine."""
	warehouse = row.get("warehouse")
	if row.get(PUTAWAY):
		return row.get(PUTAWAY)
	if doc.get("doctype") in RETURN_DOCTYPES and is_quarantine(warehouse, doc.get("company")):
		return return_home(row) or warehouse
	return warehouse


def _no_quarantine(row):
	frappe.throw(
		_(
			"Row {0} ({1}) is inspected when it is received and goes to the Quarantine warehouse, but the company has "
			"none. Ask the Stock team to add a warehouse of type Quarantine."
		).format(row.get("idx"), row.get("item_code")),
		title=_("No Quarantine Warehouse"),
	)


def _no_bundle(row, warehouse):
	frappe.throw(
		_(
			"Row {0} ({1}) carries a Serial and Batch Bundle made for {2}. Clear the bundle (Serial No / Batch No in the "
			"row are enough), then save: the row is received into Quarantine."
		).format(row.get("idx"), row.get("item_code"), warehouse),
		title=_("Serial and Batch Bundle"),
	)


def _receive(fork, row, target, current):
	"""Move one row to ``target`` with the fork's helper, refusing a bundle and a rejected warehouse that is the same."""
	if row.get("serial_and_batch_bundle"):
		_no_bundle(row, current)
	if target == row.get("rejected_warehouse") and flt(row.get("rejected_qty")):
		frappe.throw(
			_(
				"Row {0} ({1}) is received into {2}, which is also its Rejected Warehouse. Choose another Rejected "
				"Warehouse (goods rejected at the door are not held for inspection)."
			).format(row.get("idx"), row.get("item_code"), target),
			title=_("Rejected Warehouse"),
		)
	fork.receive_row_into(row, target)


def _source_warehouse(doc, row):
	"""The warehouse a purchase row was ordered for (its Purchase Order row), else the header's default; a copy of a
	routed receipt loses ``putaway_warehouse`` (no_copy) but keeps the quarantine."""
	for field in ("purchase_order_item", "po_detail"):
		if row.get(field):
			warehouse = frappe.db.get_value("Purchase Order Item", row.get(field), "warehouse")
			if warehouse:
				return warehouse
	return doc.get("set_warehouse")


def _validate_location(row, location, warehouse, allowed_types):
	if not location or not warehouse:
		return
	validate_storage_location_warehouse(location, warehouse, item_code=row.item_code)
	if frappe.get_cached_value("Storage Location", location, "location_type") not in allowed_types:
		frappe.throw(
			_("Row {0} ({1}): select a {2} location in {3}.").format(
				row.idx, row.item_code, " / ".join(_(kind) for kind in allowed_types), warehouse
			)
		)


def _quarantine_location(row, warehouse):
	location = get_default_storage_location(warehouse, row.item_code)
	if not location:
		frappe.throw(_("Configure a holding location for {0} in {1}.").format(row.item_code, warehouse))
	_validate_location(row, location, warehouse, (HOLDING,))
	return location


def _direct_purchase_location(doc, row):
	warehouse = row.get("warehouse")
	if not warehouse:
		return
	location = row.get("storage_location")
	if not location or is_receiving_location(location, warehouse):
		row.storage_location = location = preferred_purchase_location(warehouse, row.item_code)
	if not location and cint(doc.get("docstatus")) == 1:
		frappe.throw(_("Row {0}: select a Target Storage Location in {1}.").format(row.idx, warehouse))
	_validate_location(row, location, warehouse, PUTAWAY_LOCATION_TYPES)


def _route_rejected_purchase(doc, row, home):
	if flt(row.get("rejected_qty")) <= 0:
		return
	if typed_location(home, None, DAMAGE):
		row.rejected_warehouse = home
		row.rejected_storage_location = _required_zone(home, row, DAMAGE)
		return
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import write_off_warehouse

	warehouse = row.get("rejected_warehouse") or doc.get("rejected_warehouse")
	if not warehouse and home:
		warehouse = write_off_warehouse(doc.company, home)
	# An unmapped warehouse can use an explicitly selected damage warehouse in the same company.
	if not warehouse:
		if cint(doc.get("docstatus")) == 1:
			frappe.throw(_("Row {0}: select a Rejected Warehouse for the rejected quantity.").format(row.idx))
		return
	rejected = frappe.get_cached_value(
		"Warehouse", warehouse, ("company", "warehouse_type", "disabled", "is_group"), as_dict=True
	)
	if (
		not rejected
		or rejected.company != doc.company
		or rejected.warehouse_type != "Write-Off"
		or rejected.disabled
		or rejected.is_group
	):
		frappe.throw(
			_("Row {0}: select an active Write-Off warehouse of {1} for rejected goods.").format(
				row.idx, doc.company
			)
		)
	row.rejected_warehouse = warehouse
	if not row.get("rejected_storage_location"):
		row.rejected_storage_location = get_default_storage_location(warehouse, row.item_code)
	if not row.rejected_storage_location and cint(doc.get("docstatus")) == 1:
		frappe.throw(_("Row {0}: select a Rejected Storage Location in {1}.").format(row.idx, warehouse))
	_validate_location(row, row.rejected_storage_location, warehouse, (HOLDING,))


def _required_zone(warehouse, row, location_type):
	location = typed_location(warehouse, row.item_code, location_type)
	if not location:
		frappe.throw(
			_("Row {0}: no {1} location in {2} supports the storage condition of {3}.").format(
				row.idx, _(location_type), warehouse, row.item_code
			)
		)
	return location


def _chosen_location(row, warehouse):
	"""The Storage Location the user chose for ``row`` in ``warehouse`` before it is received into Quarantine: kept in
	``putaway_storage_location`` so the release puts the goods there. A Receiving (STAGING) location, the warehouse's
	default the fork filled, is not a choice."""
	location = row.get("storage_location")
	if not location or frappe.db.get_value("Storage Location", location, "warehouse") != warehouse:
		return None
	return None if is_receiving_location(location) else location


def route_receipt_rows(doc, method=None):
	"""Purchase Receipt, Purchase Invoice, Delivery Note and Sales Invoice before_validate of a draft receipt (and of its submit): each row
	whose Item is inspected on receipt is received into the Quarantine warehouse of its warehouse at that warehouse's
	default Storage Location, and a row whose Item no longer is goes back to its warehouse. Idempotent (a row already
	in quarantine is left, and keeps its ``putaway_warehouse``). A stock Loan Return's note is the fork's."""
	# docstatus 1 here is the submit itself (Frappe runs before_validate with it set): a draft saved before its Item was
	# flagged is routed then, so the native gate finds the row in quarantine
	if doc.get("doctype") in PURCHASE_DOCTYPES and cint(doc.get("is_return")):
		# Purchase Invoice source fields can be populated by a later before_validate hook.
		if doc.doctype == "Purchase Receipt":
			validate_hold_purchase_return(doc)
		return
	if (
		cint(doc.get("docstatus")) not in (0, 1)
		or not receives_goods(doc)
		or is_loan_return_note(doc)
		or doc.flags.get("in_quarantine_routing")
	):
		return
	fork = _fork()
	purchase = doc.doctype in PURCHASE_DOCTYPES
	for row in doc.get("items") or []:
		warehouse = row.get("warehouse")
		inspected = needs_inspection(doc, row)
		quarantined = is_quarantine(warehouse, doc.company)
		home = (
			(row.get(PUTAWAY) or _source_warehouse(doc, row) or return_home(row))
			if quarantined
			else warehouse
		)
		if quarantined and is_quarantine(home, doc.company):
			home = None
		purchase_stock = (
			purchase
			and row.get("item_code")
			and (inspected or cint(frappe.get_cached_value("Item", row.item_code, "is_stock_item")))
		)
		if purchase_stock:
			_route_rejected_purchase(doc, row, home or warehouse)
			if is_fully_rejected_purchase_row(doc, row):
				if quarantined and home and not is_quarantine(home, doc.company):
					row.warehouse = home
					row.storage_location = row.get(PUTAWAY_LOCATION)
					row.putaway_warehouse = None
					row.putaway_storage_location = None
				continue
			if home and not is_stock_type_warehouse(home):
				frappe.throw(_("Row {0}: select a stock warehouse to receive these goods.").format(row.idx))
		if inspected and typed_location(home, None, QUALITY_INSPECTION):
			if home != warehouse and row.get("serial_and_batch_bundle"):
				_no_bundle(row, warehouse)
			if purchase:
				chosen = row.get(PUTAWAY_LOCATION) if row.get(PUTAWAY) == home else None
				if (
					not chosen
					and get_location_type(row.get("storage_location"), home) in PUTAWAY_LOCATION_TYPES
				):
					chosen = _chosen_location(row, home)
				_validate_location(row, chosen, home, PUTAWAY_LOCATION_TYPES)
				row.putaway_warehouse = home
				row.putaway_storage_location = chosen
			row.warehouse = home
			row.storage_location = _required_zone(home, row, QUALITY_INSPECTION)
			continue
		if (
			not inspected
			and not quarantined
			and get_location_type(row.get("storage_location"), warehouse) == QUALITY_INSPECTION
		):
			row.storage_location = row.get(PUTAWAY_LOCATION) or preferred_purchase_location(
				warehouse, row.item_code
			)
		if not purchase and get_location_type(row.get("storage_location"), warehouse) == DAMAGE:
			frappe.throw(
				_("Select a receiving or storage location for returned Item {0}.").format(row.item_code)
			)
		if inspected and warehouse and not quarantined:
			quarantine = fork.quarantine_warehouse_for(doc.company, warehouse)
			if not quarantine:
				_no_quarantine(row)
			chosen = _chosen_location(row, warehouse) if purchase else None
			_receive(fork, row, quarantine, warehouse)
			if purchase:
				row.putaway_warehouse = warehouse
				row.putaway_storage_location = chosen
		elif inspected and quarantined and purchase:
			# a quarantine chosen by hand must be the one of the warehouse the row is for
			home = row.get(PUTAWAY) or _source_warehouse(doc, row)
			if home and not is_quarantine(home, doc.company):
				expected = fork.quarantine_warehouse_for(doc.company, home)
				if expected and expected != warehouse:
					_receive(fork, row, expected, warehouse)
				row.putaway_warehouse = home
		elif not inspected and quarantined:
			# only a purchase row the system routed goes back; a quarantine a user chose on a return stays
			home = row.get(PUTAWAY) if purchase else None
			if home and not is_quarantine(home, doc.company):
				_receive(fork, row, home, warehouse)
				if row.get(PUTAWAY_LOCATION):
					# back to the bin the user chose before the routing
					row.storage_location = row.get(PUTAWAY_LOCATION)
			if purchase and home:
				row.putaway_warehouse = None
				row.putaway_storage_location = None
		elif purchase and row.get(PUTAWAY) and not quarantined:
			# the user chose a real warehouse after the routing: nothing is held for the old one any more
			row.putaway_warehouse = None
			row.putaway_storage_location = None
		if inspected and is_quarantine(row.get("warehouse"), doc.company):
			row.storage_location = _quarantine_location(row, row.warehouse)
			if purchase:
				_validate_location(row, row.get(PUTAWAY_LOCATION), row.get(PUTAWAY), PUTAWAY_LOCATION_TYPES)
		elif purchase_stock:
			_direct_purchase_location(doc, row)
	# the header's set_warehouse ("Receive for Warehouse") stays: it names the warehouse the goods are for, which the rows
	# held in Quarantine remember in putaway_warehouse (the fork's reset_default_field_value reads that one)


def validate_hold_purchase_return(doc, method=None):
	if not cint(doc.get("is_return")) or (
		doc.doctype == "Purchase Invoice" and not cint(doc.get("update_stock"))
	):
		return
	from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
		_row_batches,
		_row_serial_nos,
	)

	from .quarantine_inspection import rejected_purchase_parts, row_inspection

	held = []
	for row in doc.get("items") or []:
		for amount, warehouse_field, location_field in (
			("qty", "warehouse", "storage_location"),
			("rejected_qty", "rejected_warehouse", "rejected_storage_location"),
		):
			if not flt(row.get(amount)):
				continue
			location = row.get(location_field)
			warehouse = row.get(warehouse_field)
			kind = get_location_type(location, warehouse)
			if kind in (QUALITY_INSPECTION, DAMAGE):
				held.append((row, amount, location, warehouse, kind))
	if not held:
		return
	if not doc.get("return_against"):
		frappe.throw(_("Return goods from a hold zone against their original purchase document."))
	if not frappe.db.get_value(doc.doctype, doc.return_against, "name", for_update=True):
		frappe.throw(_("The original purchase document no longer exists."))
	original = frappe.get_doc(doc.doctype, doc.return_against)
	original_rows = {row.name: row for row in original.items}
	link = "purchase_receipt_item" if doc.doctype == "Purchase Receipt" else "purchase_invoice_item"
	remaining = None
	used = {}
	for row, amount, location, warehouse, kind in held:
		source = original_rows.get(row.get(link))
		if not source or source.item_code != row.item_code:
			frappe.throw(_("Row {0}: select the original item row for this return.").format(row.idx))
		if kind == DAMAGE and (amount == "rejected_qty" or row.get("return_qty_from_rejected_warehouse")):
			allowed = (
				source.get("rejected_warehouse") == warehouse
				and source.get("rejected_storage_location") == location
			)
		else:
			inspected = row_inspection(original, source, submitted=True, lock=True)
			if remaining is None:
				remaining = rejected_purchase_parts(original, current=True)
			serials = set(_row_serial_nos(row, current=True))
			key = (source.name, warehouse, location)
			matching = [
				part
				for available in remaining
				if (available["row"], available["quarantine"], available.get("storage_location")) == key
				for part in available["parts"]
			]
			qty = abs(flt(row.get(amount)) * (flt(row.get("conversion_factor")) or 1))
			batches = _row_batches(row, current=True) or {None: qty}
			previous_batches, previous_serials = used.get(key, ({}, set()))
			available_batches = {}
			for part in matching:
				batch = part.get("batch_no") or None
				available_batches[batch] = available_batches.get(batch, 0) + part["qty"]
			cumulative = {
				batch: previous_batches.get(batch, 0) + batches.get(batch, 0)
				for batch in previous_batches.keys() | batches.keys()
			}
			known_serials = {serial for part in matching for serial in part.get("serial_nos") or []}
			allowed = bool(
				inspected
				and inspected.status == "Rejected"
				and all(
					count <= available_batches.get(batch, 0) + 1e-9 for batch, count in cumulative.items()
				)
				and (not known_serials or (len(serials) == qty and serials <= known_serials))
				and not previous_serials.intersection(serials)
			)
			used[key] = (cumulative, previous_serials | serials)
		if not allowed:
			frappe.throw(
				_(
					"Row {0}: the returned quantity is not a rejected quantity of this purchase at {1}."
				).format(row.idx, location)
			)


PREVIEW_PARENT_FIELDS = (
	"company",
	"is_return",
	"update_stock",
	"is_borrowing_transaction",
	"set_warehouse",
	"rejected_warehouse",
)
PREVIEW_ROW_FIELDS = (
	"name",
	"idx",
	"item_code",
	"warehouse",
	"storage_location",
	"rejected_warehouse",
	"rejected_storage_location",
	"qty",
	"rejected_qty",
	"serial_and_batch_bundle",
	PUTAWAY,
	PUTAWAY_LOCATION,
	"purchase_order_item",
	"po_detail",
	"dn_detail",
	"sales_invoice_item",
	"si_detail",
	"borrowing_item",
)


@frappe.whitelist()
def preview_receipt_route(doctype: str, row, parent=None):
	"""What the save of a draft receipt stores for one row, before the save (UX WP6, 19 Nov 2026): the same rules as
	``route_receipt_rows`` (and the fork's default Storage Location of an empty incoming row) on a copy of the unsaved
	row. ``{"warehouse", "storage_location", "putaway_warehouse", "putaway_storage_location", "quarantine": bool,
	"message"}``; ``message`` says why the save would refuse the row. Read only: nothing is saved."""
	if doctype not in RECEIPT_DOCTYPES:
		frappe.throw(_("No receipt routing preview for {0}.").format(doctype))
	if not (frappe.has_permission(doctype, "create") or frappe.has_permission(doctype, "write")):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	row = frappe.parse_json(row) or {}
	parent = frappe.parse_json(parent) or {}
	doc = frappe._dict({key: parent.get(key) for key in PREVIEW_PARENT_FIELDS})
	doc.update(doctype=doctype, docstatus=0, flags=frappe._dict())
	line = frappe._dict({key: row.get(key) for key in PREVIEW_ROW_FIELDS})
	line.doctype = frappe.get_meta(doctype).get_field("items").options
	doc["items"] = [line]
	before = {key: line.get(key) for key in ("warehouse", "storage_location")}
	try:
		if doc.doctype in PURCHASE_DOCTYPES:
			for field, warehouse_field in (
				("storage_location", "warehouse"),
				("rejected_storage_location", "rejected_warehouse"),
			):
				if line.get(field) and line.get(warehouse_field):
					owner = frappe.db.get_value("Storage Location", line[field], "warehouse")
					if owner != line[warehouse_field]:
						line[field] = None
		_default_location(doc, line)
		route_receipt_rows(doc)
	except frappe.ValidationError as error:
		# the save would refuse the row: the form keeps what the user typed and says why
		if frappe.local.message_log:
			frappe.local.message_log.pop()
		return {**before, "quarantine": False, "message": str(error)}
	return {
		"warehouse": line.get("warehouse"),
		"storage_location": line.get("storage_location"),
		"rejected_warehouse": line.get("rejected_warehouse"),
		"rejected_storage_location": line.get("rejected_storage_location"),
		PUTAWAY: line.get(PUTAWAY),
		PUTAWAY_LOCATION: line.get(PUTAWAY_LOCATION),
		"quarantine": bool(receives_goods(doc) and row_in_quarantine(doc, line)),
		"inspection_zone": get_location_type(line.get("storage_location"), line.get("warehouse"))
		== QUALITY_INSPECTION,
		"damage_zone": bool(
			typed_location(line.get("putaway_warehouse") or line.get("warehouse"), None, DAMAGE)
		),
		"message": "",
	}


def _default_location(doc, row):
	"""The fork fills an empty Storage Location of an incoming stock row with its warehouse's default on save, before
	the routing (``storage_location.set_default_storage_locations``, the first before_validate): the preview does too."""
	if row.get("storage_location") or not row.get("warehouse") or not row.get("item_code"):
		return
	if not receives_goods(doc) or not cint(frappe.get_cached_value("Item", row.item_code, "is_stock_item")):
		return
	from erpnext.stock.doctype.storage_location.storage_location import default_row_location

	row.storage_location = default_row_location(doc, row, "storage_location", row.warehouse, row.item_code)


def refuse_unsubmitted_receipt(doc, method=None):
	"""Quality Inspection validate: a receipt is inspected after it is submitted (Siwat, 9 Nov 2026). An inspection of a
	Purchase Receipt, Purchase Invoice, stock or asset Loan Return, sales return or Quarantine Intake that is not
	submitted is refused. A cancelling inspection is left alone."""
	if cint(doc.get("docstatus")) != 0 or doc.get("reference_type") not in (
		*AFTER_SUBMIT_REFERENCES,
		"Stock Entry",
	):
		return
	name = doc.get("reference_name")
	if not name or not frappe.db.exists(doc.reference_type, name):
		return
	values = frappe.db.get_value(
		doc.reference_type,
		name,
		["docstatus", *[f for f in ("is_return", "operation_approval_route") if _has(doc.reference_type, f)]],
		as_dict=True,
	)
	if doc.reference_type in RETURN_DOCTYPES and not cint(values.get("is_return")):
		return
	if doc.reference_type == "Stock Entry" and values.get("operation_approval_route") != "Quarantine Intake":
		if cint(values.docstatus) == 1 or get_policy("stock_entry_route"):
			return
		from .quarantine_release import physical_quarantine_route

		if physical_quarantine_route(frappe.get_doc("Stock Entry", name)) != "Quarantine Intake":
			return
	if cint(values.docstatus) != 1:
		frappe.throw(
			_("Submit {0} {1} first: goods are received before they are inspected.").format(
				_(doc.reference_type), name
			),
			title=_("Receipt Not Submitted"),
		)


def _has(doctype, fieldname):
	return frappe.get_meta(doctype).has_field(fieldname)


# -- the rows of a submitted receipt that wait in, or may leave, quarantine -----------------------------------------------


def is_inspected_receipt(doc):
	"""A submitted receipt whose goods are received into quarantine to be inspected."""
	return bool(doc is not None and cint(doc.get("docstatus")) == 1 and receives_goods(doc))


def quarantined_rows(doc):
	"""The rows of a submitted receipt that were received into a Quarantine warehouse because their Item is inspected."""
	if not is_inspected_receipt(doc) or doc.flags.get("in_delete"):
		return []
	return [
		row
		for row in doc.get("items") or []
		if row_in_quarantine(doc, row)
		and not is_fully_rejected_purchase_row(doc, row)
		and (
			get_location_type(row.get("storage_location"), row.get("warehouse")) == QUALITY_INSPECTION
			or needs_inspection(doc, row)
		)
	]


def is_putaway_row(doc, row):
	"""A row of a submitted purchase document that needs no inspection and lies at the STAGING location of its (normal)
	warehouse: the Stock team puts it away (an Item that is a stock Item, never a row in quarantine)."""
	if doc.get("doctype") not in PURCHASE_DOCTYPES or not row.get("item_code") or not row.get("warehouse"):
		return False
	if not receives_goods(doc) or is_quarantine(row.get("warehouse"), doc.get("company")):
		return False
	if not cint(frappe.get_cached_value("Item", row.item_code, "is_stock_item")) or needs_inspection(
		doc, row
	):
		return False
	return bool(row.get("storage_location")) and is_receiving_location(row.storage_location, row.warehouse)


def putaway_rows(doc):
	"""The rows of a submitted purchase receipt to be put away from STAGING (no inspection needed)."""
	if (
		not is_inspected_receipt(doc)
		or doc.get("doctype") not in PURCHASE_DOCTYPES
		or (doc.get("flags") or {}).get("in_delete")
	):
		return []
	return [row for row in doc.get("items") or [] if is_putaway_row(doc, row)]


def waiting_rows(doc):
	"""The rows of a submitted receipt still in quarantine with no submitted Quality Inspection (the QI team's work)."""
	rows = quarantined_rows(doc)
	if not rows:
		return []
	from .quarantine_inspection import row_inspection

	linked = {row.get("quality_inspection") for row in rows if row.get("quality_inspection")}
	submitted = (
		set(frappe.get_all("Quality Inspection", filters={"name": ["in", sorted(linked)], "docstatus": 1}, pluck="name"))
		if linked
		else set()
	)
	return [
		row
		for row in rows
		if (
			not row_inspection(doc, row, submitted=True)
			if get_location_type(row.get("storage_location"), row.get("warehouse")) == QUALITY_INSPECTION
			else row.get("quality_inspection") not in submitted
		)
	]


def inspected_rows(doc):
	"""The rows of a submitted receipt in quarantine whose Quality Inspection is submitted (Accepted or Rejected): the
	goods the Stock team may take out."""
	waiting = {row.name for row in waiting_rows(doc)}
	return [row for row in quarantined_rows(doc) if row.name not in waiting]


@frappe.whitelist()
def receipt_quarantine_status(doctype: str, name: str):
	"""What the form of a receipt says about its goods in quarantine: ``{"stage": "draft" | "waiting" | "inspected" |
	"released" | None, "waiting": [...], "rows": [...], "quarantine": [...], "due": date | None}``. Rows are
	``{"idx", "item_code"}``. A draft says which rows will be received into quarantine; a submitted receipt which rows
	wait for the QI team, or that all are inspected and the Stock team moves them out with a Stock Entry."""
	if doctype not in (*RECEIPT_DOCTYPES, "Asset Movement") or not name:
		return {"stage": None}
	if not frappe.db.exists(doctype, name):
		return {"stage": None}
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	def describe(rows):
		return [
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"warehouse": row.get("warehouse"),
				"storage_location": row.get("storage_location"),
			}
			for row in rows
		]

	if doctype == "Asset Movement":
		# an asset the return movement received waits for its inspection (assets/inspection_hold.py)
		asset_rows = get_policy("asset_rows")
		rows = asset_rows(doc) if asset_rows else []
		return {"stage": "waiting" if rows else None, "rows": describe(rows), "waiting": describe(rows)}
	if not receives_goods(doc):
		return {"stage": None}
	if cint(doc.docstatus) == 0:
		rows = [
			row
			for row in doc.get("items") or []
			if needs_inspection(doc, row) and row_in_quarantine(doc, row)
		]
		return {
			"stage": "draft" if rows else None,
			"rows": describe(rows),
			"quarantine": sorted({row.warehouse for row in rows}),
		}
	if cint(doc.docstatus) != 1:
		return {"stage": None}
	waiting = waiting_rows(doc)
	from .quarantine_inspection import remaining_parts

	left = [row for row in inspected_rows(doc) if remaining_parts(doc, row, row.warehouse)]
	putaway = [row for row in putaway_rows(doc) if remaining_parts(doc, row, row.warehouse)]
	rows = waiting or left or putaway
	stage = "waiting" if waiting else "inspected" if left else "putaway" if putaway else None
	if stage is None and quarantined_rows(doc):
		stage = "released"
	return {
		"stage": stage,
		"rows": describe(rows),
		"waiting": describe(waiting),
		"quarantine": sorted({row.warehouse for row in rows}),
		"due": quarantine_due_date(doc),
	}
