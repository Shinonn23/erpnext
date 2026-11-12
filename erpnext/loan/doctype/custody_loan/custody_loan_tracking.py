"""Serial / batch tracking for Stock Custody Loans.

A loan row can be picked from several batches, and its serial numbers can come from several batches. Every balance
is kept per (loan item, batch, serial): what was issued on the loan Delivery Note, what came back on received
returns, what is on the way back (returns not yet received) and what was written off by adjustments. Quantities are
in the stock UOM, as on the Serial and Batch Bundle.

Connect Diagnostics (Siwat, 5 Nov 2026, relayed by the user): a loan tracks batches and serial numbers as fully as
any stock transaction, and returns and write-offs follow them.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

TRACKING_FIELDS = (
	"custody_loan_item",
	"item_code",
	"batch_no",
	"serial_no",
	"issued_qty",
	"returned_qty",
	"pending_return_qty",
	"adjusted_qty",
	"outstanding_qty",
	"status",
	"returned_as",
)


def _serials(value):
	return [serial for serial in get_serial_nos(value or "") if serial]


def item_flags(item_code):
	return (
		frappe.get_cached_value("Item", item_code, ["has_batch_no", "has_serial_no"], as_dict=True)
		or frappe._dict()
	)


def is_tracked(item_code):
	flags = item_flags(item_code)
	return bool(flags.has_batch_no or flags.has_serial_no)


def transaction_row_entries(row):
	"""(batch, serial, stock qty) moved by one Delivery Note or Stock Entry row."""
	bundle = row.get("serial_and_batch_bundle")
	if bundle:
		entries = frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": bundle},
			fields=["batch_no", "serial_no", "qty"],
			order_by="idx",
		)
		if entries:
			return [
				(entry.batch_no or None, entry.serial_no or None, abs(flt(entry.qty))) for entry in entries
			]
	serials = _serials(row.get("serial_no"))
	if serials:
		return [(row.get("batch_no") or None, serial, 1) for serial in serials]
	return [(row.get("batch_no") or None, None, abs(flt(row.get("stock_qty") or row.get("qty"))))]


def _key(loan_item, batch_no, serial_no):
	# a serial number identifies the unit: its batch is the one it was issued from
	return (loan_item, None, serial_no) if serial_no else (loan_item, batch_no or None, None)


def issued_entries(loan):
	"""{loan item: [(batch, serial, stock qty)]} issued on the submitted loan Delivery Note."""
	issued = defaultdict(list)
	if loan.loan_type != "Stock" or not loan.delivery_note:
		return issued
	if frappe.db.get_value("Delivery Note", loan.delivery_note, "docstatus") != 1:
		return issued
	for row in frappe.get_doc("Delivery Note", loan.delivery_note).items:
		if row.custody_loan_item:
			issued[row.custody_loan_item].extend(transaction_row_entries(row))
	return issued


def conversion_factors(loan):
	"""Stock UOM per loan UOM for each loan item, from the issue Delivery Note (1 before it exists)."""
	factors = {}
	if loan.get("delivery_note"):
		for row in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": loan.delivery_note},
			fields=["custody_loan_item", "conversion_factor"],
		):
			factors[row.custody_loan_item] = flt(row.conversion_factor) or 1
	return factors


def _movement_rows(loan_name, *, exclude=None):
	"""Return and adjustment rows of the loan with what they move, split by state."""
	rows = []
	for doctype, child, qty_field in (
		("Custody Loan Return", "Custody Loan Return Item", "returned_qty"),
		("Custody Loan Adjustment", "Custody Loan Adjustment Item", "qty"),
	):
		filters = {"custody_loan": loan_name, "docstatus": 1}
		if exclude and exclude[0] == doctype:
			filters["name"] = ["!=", exclude[1]]
		parents = frappe.get_all(doctype, filters=filters, fields=["name", "status"])
		if not parents:
			continue
		state = {parent.name: parent.status for parent in parents}
		for row in frappe.get_all(
			child,
			filters={"parent": ["in", list(state)], "parenttype": doctype},
			fields=["parent", "custody_loan_item", "batch_no", "serial_no", f"{qty_field} as qty"],
		):
			if doctype == "Custody Loan Adjustment":
				kind = "adjusted" if state[row.parent] == "Submitted" else "pending"
			else:
				kind = "returned" if state[row.parent] == "Received" else "pending"
			rows.append((kind, row))
	return rows


def get_balances(loan, *, exclude=None, attribute=True):
	"""{(loan item, batch, serial): balance} for the tracked items of a Stock loan.

	exclude=(doctype, name) leaves a return or adjustment out, so the document being validated is not counted
	against itself.

	A return may bring back another batch or serial number of the same Item than the one lent (Siwat, 5 Nov 2026):
	``attribute`` counts what came back in another lot against the lent entries, earliest first, and notes the lot
	on them (``returned_as``), so the outstanding of each lent entry stays honest. A validation of a return itself
	passes ``attribute=False`` and sees the entries as they are.
	"""
	items = {row.name: row for row in loan.items if row.item_code and is_tracked(row.item_code)}
	factors = conversion_factors(loan)
	balances = {}

	def balance(key, item_code):
		if key not in balances:
			balances[key] = frappe._dict(
				custody_loan_item=key[0],
				item_code=item_code,
				batch_no=key[1],
				serial_no=key[2],
				issued_qty=0,
				returned_qty=0,
				pending_return_qty=0,
				adjusted_qty=0,
			)
		return balances[key]

	serial_batch = {}
	for loan_item, entries in issued_entries(loan).items():
		if loan_item not in items:
			continue
		for batch_no, serial_no, qty in entries:
			entry = balance(_key(loan_item, batch_no, serial_no), items[loan_item].item_code)
			entry.issued_qty += qty
			if serial_no:
				serial_batch[(loan_item, serial_no)] = batch_no
				entry.issued_batch_no = batch_no

	field = {"returned": "returned_qty", "pending": "pending_return_qty", "adjusted": "adjusted_qty"}
	for kind, row in _movement_rows(loan.name, exclude=exclude):
		if row.custody_loan_item not in items:
			continue
		item_code = items[row.custody_loan_item].item_code
		serials = _serials(row.serial_no)
		if serials:
			for serial in serials:
				balance(_key(row.custody_loan_item, None, serial), item_code)[field[kind]] += 1
		else:
			qty = flt(row.qty) * factors.get(row.custody_loan_item, 1)
			balance(_key(row.custody_loan_item, row.batch_no, None), item_code)[field[kind]] += qty

	for entry in balances.values():
		if entry.serial_no:
			entry.batch_no = serial_batch.get((entry.custody_loan_item, entry.serial_no))
		entry.returned_as = ""
		entry.other_lot = entry.issued_qty <= 0 and (entry.returned_qty > 0 or entry.pending_return_qty > 0)
	if attribute:
		attribute_other_lots(balances)
	for entry in balances.values():
		entry.outstanding_qty = max(entry.issued_qty - entry.returned_qty - entry.adjusted_qty, 0)
		entry.open_qty = max(entry.outstanding_qty - entry.pending_return_qty, 0)
		entry.status = tracking_status(entry)
	return balances


def attribute_other_lots(balances):
	"""What came back in a lot that was not lent counts against the lent entries of the same loan item, earliest lot
	first; each lent entry keeps a note of the lots that came back for it."""
	lent_by_item = defaultdict(list)
	for entry in balances.values():
		if entry.issued_qty > 0:
			lent_by_item[entry.custody_loan_item].append(entry)
	for entry in list(balances.values()):
		if not entry.other_lot:
			continue
		label = entry.serial_no or entry.batch_no or _("no batch")
		lent = sorted(
			lent_by_item.get(entry.custody_loan_item, []), key=lambda e: (e.batch_no or "", e.serial_no or "")
		)
		for field in ("returned_qty", "pending_return_qty"):
			left = entry.get(field)
			for target in lent:
				if left <= 0:
					break
				room = (
					target.issued_qty - target.returned_qty - target.adjusted_qty - target.pending_return_qty
				)
				take = min(max(room, 0), left)
				if take <= 0:
					continue
				target[field] += take
				left -= take
				note = f"{label} x {flt(take):g}" + (
					f" ({_('pending')})" if field == "pending_return_qty" else ""
				)
				target.returned_as = f"{target.returned_as}, {note}" if target.returned_as else note


def tracking_status(entry):
	if entry.issued_qty <= 0:
		return (
			"Returned in Other Lot"
			if (entry.returned_qty > 0 or entry.pending_return_qty > 0)
			else "Not Issued"
		)
	if entry.pending_return_qty > 0:
		return "Pending Return"
	if entry.outstanding_qty <= 0:
		return "Written Off" if entry.adjusted_qty and not entry.returned_qty else "Returned"
	return "With Borrower"


def tracking_rows(loan):
	"""Rows for the loan's Serial / Batch Tracking table, in loan-item order."""
	order = {row.name: row.idx for row in loan.items}
	rows = sorted(
		(entry for entry in get_balances(loan).values() if not entry.other_lot),
		key=lambda entry: (
			order.get(entry.custody_loan_item, 0),
			entry.batch_no or "",
			entry.serial_no or "",
		),
	)
	return [{field: entry.get(field) for field in TRACKING_FIELDS} for entry in rows]


def sync_tracking_table(loan):
	"""Keep the loan's tracking table in step with its documents; a submitted loan is written directly."""
	if loan.loan_type != "Stock":
		return
	rows = tracking_rows(loan)
	current = [
		{field: row.get(field) for field in TRACKING_FIELDS} for row in loan.get("serial_batch_details") or []
	]
	if _signature(current) == _signature(rows):
		return
	loan.set("serial_batch_details", rows)
	if loan.docstatus != 1 or not loan.name:
		return
	frappe.db.delete("Custody Loan Serial Batch", {"parent": loan.name, "parenttype": "Custody Loan"})
	for row in loan.serial_batch_details:
		row.name = frappe.generate_hash(length=10)
		row.db_insert()


def _signature(rows):
	return [
		tuple(
			flt(row.get(field), 6) if field.endswith("_qty") else (row.get(field) or None)
			for field in TRACKING_FIELDS
		)
		for row in rows
	]


def open_entries(loan, *, exclude=None, loan_item=None):
	"""Units still with the borrower and not on a pending return: what a return or write-off may take."""
	return [
		entry
		for entry in get_balances(loan, exclude=exclude).values()
		if entry.open_qty > 0 and (not loan_item or entry.custody_loan_item == loan_item)
	]


def grouped_open_rows(loan, loan_item_row):
	"""One return/adjustment row per batch of a loan item: (batch, serial list, qty in the loan UOM)."""
	factor = conversion_factors(loan).get(loan_item_row.name, 1) or 1
	groups = {}
	for entry in open_entries(loan, loan_item=loan_item_row.name):
		group = groups.setdefault(entry.batch_no, {"batch_no": entry.batch_no, "serials": [], "qty": 0})
		if entry.serial_no:
			group["serials"].append(entry.serial_no)
			group["qty"] += 1
		else:
			group["qty"] += entry.open_qty / factor
	return sorted(groups.values(), key=lambda group: group["batch_no"] or "")


def validate_tracked_rows(doc, loan, qty_field, allow_other_lots=False):
	"""Each return/adjustment row of a tracked item must take open units: its serials must have been issued on this loan
	and not be returned, pending return or written off already. A batch-tracked item without serial numbers may come
	back in another batch of the same Item (``allow_other_lots``); a serial number never may (Siwat, 8 Nov 2026)."""
	exclude = (doc.doctype, doc.name) if doc.name else None
	balances = get_balances(loan, exclude=exclude, attribute=not allow_other_lots)
	factors = conversion_factors(loan)
	loan_items = {row.name: row for row in loan.items}
	taken = defaultdict(float)
	seen_pairs = set()
	for row in doc.items:
		loan_item = loan_items.get(row.custody_loan_item)
		if not loan_item or not is_tracked(loan_item.item_code):
			continue
		flags = item_flags(loan_item.item_code)
		qty = flt(row.get(qty_field))
		serials = _serials(row.serial_no) if flags.has_serial_no else []
		if flags.has_serial_no:
			if qty % 1 or len(serials) != cint(qty) or len(serials) != len(set(serials)):
				frappe.throw(_("Row {0}: Select one unique Serial No for each whole item").format(row.idx))
			batches = set()
			for serial in serials:
				entry = balances.get((loan_item.name, None, serial))
				# Siwat, 8 Nov 2026: a serial-tracked unit comes back as the very unit that was lent, never another
				# serial number (a unit sold earlier goes back through a Sales Return, not through the loan)
				if not entry or entry.issued_qty <= 0:
					frappe.throw(
						_(
							"Row {0}: Serial No {1} was not lent on this Loan Item. Only the serial numbers that were lent come back."
						).format(row.idx, serial)
					)
				if entry.open_qty - taken[(loan_item.name, None, serial)] < 1:
					frappe.throw(
						_("Row {0}: Serial No {1} is already returned, pending return or written off").format(
							row.idx, serial
						)
					)
				taken[(loan_item.name, None, serial)] += 1
				batches.add(entry.batch_no)
			if flags.has_batch_no:
				if len(batches) > 1:
					frappe.throw(
						_(
							"Row {0}: These Serial Nos come from different batches; use one row per batch"
						).format(row.idx)
					)
				issued_batch = next(iter(batches), None)
				if row.batch_no and row.batch_no != issued_batch:
					frappe.throw(
						_("Row {0}: The Serial Nos were issued from Batch {1}, not {2}").format(
							row.idx, issued_batch, row.batch_no
						)
					)
				row.batch_no = issued_batch
			row.serial_no = "\n".join(serials)
		else:
			if not row.batch_no:
				frappe.throw(_("Row {0}: Select the Batch No").format(row.idx))
			key = (loan_item.name, row.batch_no, None)
			entry = balances.get(key)
			if allow_other_lots and (not entry or entry.issued_qty <= 0):
				# a batch that was not lent comes back too, if it is a batch of this Item
				if frappe.db.get_value("Batch", row.batch_no, "item") != loan_item.item_code:
					frappe.throw(
						_("Row {0}: Batch {1} is not a batch of {2}").format(
							row.idx, row.batch_no, loan_item.item_code
						)
					)
			elif allow_other_lots:
				pass
			elif not entry or entry.issued_qty <= 0:
				frappe.throw(
					_("Row {0}: Batch {1} was not issued on this Loan Item").format(row.idx, row.batch_no)
				)
			stock_qty = qty * (factors.get(loan_item.name, 1) or 1)
			taken[key] += stock_qty
			if allow_other_lots:
				# the count is per Item, checked on the whole return
				pass
			elif taken[key] > entry.open_qty + 1e-9:
				frappe.throw(
					_("Row {0}: Only {1} of Batch {2} is still with the borrower").format(
						row.idx, flt(entry.open_qty), row.batch_no
					)
				)
		pair = (loan_item.name, row.batch_no or None)
		if pair in seen_pairs:
			frappe.throw(_("Row {0}: Use one row per Loan Item and Batch").format(row.idx))
		seen_pairs.add(pair)


@frappe.whitelist()
def get_open_serial_batches(custody_loan, custody_loan_item=None):
	"""Open serials and batches of a loan for the Return and Adjustment pickers, in the loan UOM."""
	loan = frappe.get_doc("Custody Loan", custody_loan)
	loan.check_permission("read")
	if loan.loan_type != "Stock":
		return []
	factors = conversion_factors(loan)
	result = []
	for entry in open_entries(loan, loan_item=custody_loan_item):
		factor = factors.get(entry.custody_loan_item, 1) or 1
		result.append(
			{
				"custody_loan_item": entry.custody_loan_item,
				"item_code": entry.item_code,
				"batch_no": entry.batch_no,
				"serial_no": entry.serial_no,
				"open_qty": 1 if entry.serial_no else flt(entry.open_qty / factor, 6),
			}
		)
	order = {row.name: row.idx for row in loan.items}
	return sorted(
		result,
		key=lambda row: (
			order.get(row["custody_loan_item"], 0),
			row["batch_no"] or "",
			row["serial_no"] or "",
		),
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def custody_loan_batch_query(doctype, txt, searchfield, start, page_len, filters):
	"""Batch picker for Return and Adjustment rows: only batches still open on that loan item."""
	filters = filters if isinstance(filters, dict) else {}
	loan_name = filters.get("custody_loan")
	if not loan_name or not frappe.db.exists("Custody Loan", loan_name):
		return []
	if not frappe.has_permission("Custody Loan", "read", loan_name):
		return []
	batches = {}
	for row in get_open_serial_batches(loan_name, filters.get("custody_loan_item")):
		if row["batch_no"] and (txt or "").lower() in row["batch_no"].lower():
			batches[row["batch_no"]] = batches.get(row["batch_no"], 0) + row["open_qty"]
	rows = [(batch, _("Open: {0}").format(flt(qty))) for batch, qty in sorted(batches.items())]
	return rows[cint(start) : cint(start) + cint(page_len)]


@frappe.whitelist()
def get_loan_item_rows(custody_loan):
	"""The loan's rows for the Return and Adjustment Item pickers: the user chooses the Item, the row link follows."""
	loan = frappe.get_doc("Custody Loan", custody_loan)
	loan.check_permission("read")
	return [
		{
			"name": row.name,
			"item_code": row.item_code,
			"item_name": row.item_name,
			"asset": row.asset,
			"outstanding_qty": flt(row.outstanding_qty),
		}
		for row in loan.items
	]


@frappe.whitelist()
def get_open_loans(borrower_type=None, borrower=None):
	"""The loans a return can be started for (submitted, still with their borrower) that the user may open, the
	soonest due first: what the Loan Return form lists once the user says who returns (customer, employee ...)."""
	filters = {"docstatus": 1, "status": ["in", ["Active", "Partially Returned"]]}
	if borrower_type:
		filters["borrower_type"] = borrower_type
	if borrower:
		filters["borrower"] = borrower
	loans = frappe.get_list(
		"Custody Loan",
		filters=filters,
		fields=["name", "loan_type", "borrower_type", "borrower", "expected_return_date", "status"],
		order_by="expected_return_date is null, expected_return_date, creation",
		limit_page_length=50,
	)
	for loan in loans:
		rows = frappe.get_all(
			"Custody Loan Item",
			filters={"parent": loan.name, "parenttype": "Custody Loan", "outstanding_qty": [">", 0]},
			fields=["item_code", "outstanding_qty"],
			order_by="idx",
		)
		loan["items"] = ", ".join(f"{row.item_code} x {flt(row.outstanding_qty):g}" for row in rows)
	return loans


@frappe.whitelist()
def get_return_items(custody_loan):
	"""The rows of a new Loan Return for a loan: what is still with the borrower, one row per batch (the same rows
	Create > Return fills)."""
	loan = frappe.get_doc("Custody Loan", custody_loan)
	loan.check_permission("read")
	return [row.as_dict() for row in loan.make_return().items]
