"""Return a loan set in one step, with what the borrower used up (Siwat, 5 Nov 2026).

A Demo / Borrow request can make two loans, one of Assets and one of Stock. One dialog lists what is still out of
both; the person presses Return and the system makes the Asset Return and the Stock Return behind the scenes.

- A stock row may come back in another batch or serial number than the one lent (customers return a different lot):
  ``register_returned_lot`` creates an unknown batch (its expiry date is asked, mandatory) or serial number first. A
  fixed Asset must come back as the same Asset (a different machine is an exchange, out of scope).
- A quantity the customer used up is not returned: for a Customer Demo the system writes it off as a demo expense (a
  Custody Loan Adjustment, reason "Consumed during demo", the Company's Stock Adjustment Account); for a Customer
  Borrow / Loan it writes it off the same way and drafts a Sales Invoice to the borrower at the selling price, which
  accounts submits (the hand-off rule "Drafted Sales Invoice", for the accounts receivable designations).
- The loan closes when every item is returned or consumed (``CustodyLoan.get_submitted_status``).

The set is the loans issued from one Material Request (``loans_of_set``: the only place that knows it, so a dedicated
link can replace it).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from erpnext.loan.doctype.custody_loan.custody_loan_core import (
	loan_item_available_to_adjust,
	loan_item_available_to_receive,
)
from erpnext.loan.doctype.custody_loan.custody_loan_tracking import (
	grouped_open_rows,
	is_tracked,
	item_flags,
)

OPEN_STATUSES = ("Active", "Partially Returned")
DEMO_REQUEST = "Customer Demo"
DEMO_REASON = "Consumed during demo"


def loans_of_set(loan_name):
	"""The submitted loans issued from the same Material Request as ``loan_name``, Assets first."""
	request = frappe.db.get_value("Custody Loan", loan_name, "material_request")
	return loans_of_request(request) if request else [loan_name]


def loans_of_request(request):
	return frappe.get_all(
		"Custody Loan",
		filters={"material_request": request, "docstatus": 1},
		pluck="name",
		order_by="loan_type asc, creation asc",
	)


def _set_names(loan, material_request):
	if material_request:
		return loans_of_request(material_request)
	if not loan:
		frappe.throw(_("Name the loan or the request to return."))
	return loans_of_set(loan)


DELIVERING = ("Pending Pick", "Pending Delivery Note")
RETURN_CHECK = ("Pending Quality Inspection", "Pending Receipt")
DONE = ("Returned", "Closed", "Cancelled")


def set_status(statuses):
	"""One status for the loans of a request: delivering until the goods have left, then out, partly back, being checked
	and finally closed. ``statuses`` are the loans' own statuses."""
	statuses = [status for status in statuses if status != "Cancelled"]
	if not statuses:
		return None
	if any(status in DELIVERING for status in statuses):
		return "Being delivered"
	if any(status in RETURN_CHECK for status in statuses):
		return "Return being checked"
	if all(status in DONE for status in statuses):
		return "Closed"
	if any(status in DONE or status == "Partially Returned" for status in statuses):
		return "Partly back"
	return "Out"


@frappe.whitelist()
def request_loan_summary(material_request: str):
	"""The loans of a Demo / Borrow request as one set, for the block on the request: each loan with what is out, what
	came back and what was used up, and the combined status. Loans the user may not read are left out."""
	loans = []
	for name in loans_of_request(material_request):
		doc = frappe.get_doc("Custody Loan", name)
		if not doc.has_permission("read"):
			continue
		rows = [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"asset": row.asset,
				"issued": flt(row.issued_qty),
				"outstanding": flt(row.outstanding_qty),
				"returned": flt(row.returned_qty),
				"used_up": flt(row.adjusted_qty) + flt(row.sold_qty),
			}
			for row in doc.items
		]
		loans.append({"name": name, "loan_type": doc.loan_type, "status": doc.status, "items": rows})
	return {"loans": loans, "status": set_status([loan["status"] for loan in loans])}


@frappe.whitelist()
def loan_set_overview(loan: str | None = None, material_request: str | None = None):
	"""What is still out of the set, for the Return dialog: Asset rows and Stock rows (with the lots that were lent)."""
	names = _set_names(loan, material_request)
	overview = {"loans": [], "assets": [], "stock": [], "request_type": None, "open_loans": 0}
	for name in names:
		doc = frappe.get_doc("Custody Loan", name)
		if not doc.has_permission("read"):
			continue
		overview["request_type"] = overview["request_type"] or frappe.db.get_value(
			"Material Request", doc.material_request, "material_request_type"
		)
		if doc.status not in OPEN_STATUSES:
			continue
		overview["open_loans"] += 1
		overview["loans"].append(
			{
				"name": name,
				"loan_type": doc.loan_type,
				"borrower_type": doc.borrower_type,
				"borrower": doc.borrower,
			}
		)
		for row in doc.items:
			available = loan_item_available_to_receive(name, row)
			if available <= 0:
				continue
			common = {
				"loan": name,
				"custody_loan_item": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
			}
			if doc.loan_type == "Asset":
				overview["assets"].append({**common, "asset": row.asset})
				continue
			flags = item_flags(row.item_code)
			lots = (
				[
					{"batch_no": group["batch_no"], "serials": group["serials"], "qty": group["qty"]}
					for group in grouped_open_rows(doc, row)
				]
				if is_tracked(row.item_code)
				else []
			)
			overview["stock"].append(
				{
					**common,
					"outstanding": available,
					"uom": frappe.db.get_value("Item", row.item_code, "stock_uom"),
					"has_batch_no": cint(flags.get("has_batch_no")),
					"has_serial_no": cint(flags.get("has_serial_no")),
					"lots": lots,
				}
			)
	return overview


@frappe.whitelist(methods=["POST"])
def register_returned_lot(
	item_code: str,
	batch_no: str | None = None,
	expiry_date: str | None = None,
	manufacturing_date: str | None = None,
	serial_nos: str | None = None,
):
	"""Make a batch or serial number the loan does not know yet, so it can come back. A new batch needs its expiry date
	(its manufacturing date is optional); a batch that exists is only checked to be of this Item. Serial numbers that
	are unknown are created; one that exists is left for the return to check (in stock or on another loan: refused)."""
	frappe.has_permission("Custody Loan Return", "create", throw=True)
	flags = item_flags(item_code)
	made = {"batch": None, "serials": []}
	if batch_no:
		if not cint(flags.get("has_batch_no")):
			frappe.throw(_("{0} is not kept in batches.").format(item_code))
		existing = frappe.db.get_value("Batch", batch_no, "item")
		if existing and existing != item_code:
			frappe.throw(_("Batch {0} belongs to another Item.").format(batch_no))
		if not existing:
			if not expiry_date:
				frappe.throw(_("Give the expiry date of the new batch {0}.").format(batch_no))
			if getdate(expiry_date) < getdate(nowdate()):
				frappe.throw(_("The new batch {0} has already expired on {1}.").format(batch_no, expiry_date))
			frappe.get_doc(
				{
					"doctype": "Batch",
					"batch_id": batch_no,
					"item": item_code,
					"expiry_date": expiry_date,
					"manufacturing_date": manufacturing_date or None,
				}
			).insert(ignore_permissions=True)
			made["batch"] = batch_no
	if serial_nos:
		if not cint(flags.get("has_serial_no")):
			frappe.throw(_("{0} has no serial numbers.").format(item_code))
		for serial in [line.strip() for line in serial_nos.replace(",", "\n").split("\n") if line.strip()]:
			if frappe.db.exists("Serial No", serial):
				continue
			doc = frappe.get_doc({"doctype": "Serial No", "serial_no": serial, "item_code": item_code})
			if batch_no:
				doc.batch_no = batch_no
			doc.insert(ignore_permissions=True)
			made["serials"].append(serial)
	return made


def _rows(value):
	return frappe.parse_json(value) if isinstance(value, str) else (value or [])


@frappe.whitelist(methods=["POST"])
def return_loan_set(
	loan: str | None = None,
	material_request: str | None = None,
	assets: str | list | None = None,
	stock: str | list | None = None,
):
	"""One Return for the set. ``assets``: [{custody_loan_item}] of the Assets that came back. ``stock``: [{custody_loan_item,
	batch_no, serial_no, qty, consumed}] (a loan item may have several lines: one per returned batch; ``consumed`` is
	the used-up quantity, given on one line). Everything is checked first, then the Asset Return, the Stock Return and
	the write-off of what was consumed are made; nothing is left half done on a failure. Returns what was made."""
	frappe.has_permission("Custody Loan Return", "create", throw=True)
	names = _set_names(loan, material_request)
	assets, stock = _rows(assets), _rows(stock)
	loans = {name: frappe.get_doc("Custody Loan", name) for name in names}
	items = {row.name: (doc, row) for doc in loans.values() for row in doc.items}
	plan = _check_plan(loans, items, assets, stock)
	made = {"returns": [], "adjustments": [], "invoices": [], "waiting": [], "notes": []}
	savepoint = "return_loan_set"
	frappe.db.savepoint(savepoint)
	try:
		for name in names:
			doc = loans[name]
			if doc.loan_type == "Asset" and plan["assets"].get(name):
				_return_assets(doc, plan["assets"][name], made)
			elif doc.loan_type == "Stock" and plan["returns"].get(name):
				_return_stock(doc, plan["returns"][name], made)
		for name in names:
			if plan["consumed"].get(name):
				_consume_stock(loans[name], plan["consumed"][name], made)
		for name in names:
			frappe.get_doc("Custody Loan", name).update_status()
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	return made


def _check_plan(loans, items, assets, stock):
	"""Group the dialog's lines per loan and refuse what cannot be: another loan's row, an Asset that is not the lent
	one, more than is out."""
	plan = {"assets": {}, "returns": {}, "consumed": {}}
	for line in assets:
		doc, row = items.get(line.get("custody_loan_item"), (None, None))
		if not doc or doc.loan_type != "Asset" or not row.asset:
			frappe.throw(_("{0} is not an Asset row of this loan set.").format(line.get("custody_loan_item")))
		if line.get("asset") and line["asset"] != row.asset:
			frappe.throw(
				_("{0} was lent, not {1}: a different machine is an exchange, not a return.").format(
					row.asset, line["asset"]
				)
			)
		if loan_item_available_to_receive(doc.name, row) < 1:
			frappe.throw(_("Asset {0} is already returned.").format(row.asset))
		plan["assets"].setdefault(doc.name, []).append(row)
	totals = {}
	for line in stock:
		doc, row = items.get(line.get("custody_loan_item"), (None, None))
		if not doc or doc.loan_type != "Stock":
			frappe.throw(_("{0} is not a Stock row of this loan set.").format(line.get("custody_loan_item")))
		qty, consumed = flt(line.get("qty")), flt(line.get("consumed"))
		if qty < 0 or consumed < 0:
			frappe.throw(_("Quantities cannot be negative."))
		totals[row.name] = totals.get(row.name, 0) + qty + consumed
		if qty > 0:
			plan["returns"].setdefault(doc.name, []).append(
				{
					"custody_loan_item": row.name,
					"item_code": row.item_code,
					"returned_qty": qty,
					"batch_no": line.get("batch_no") or None,
					"serial_no": line.get("serial_no") or None,
				}
			)
		if consumed > 0:
			plan["consumed"].setdefault(doc.name, {})
			plan["consumed"][doc.name][row.name] = plan["consumed"][doc.name].get(row.name, 0) + consumed
	for name, total in totals.items():
		doc, row = items[name]
		if total - loan_item_available_to_receive(doc.name, row) > 1e-9:
			frappe.throw(
				_("{0}: {1} returned and used up is more than the {2} still out.").format(
					row.item_code, flt(total), flt(loan_item_available_to_receive(doc.name, row))
				)
			)
	return plan


def _return_assets(loan, rows, made):
	doc = frappe.new_doc("Custody Loan Return")
	doc.company = loan.company
	doc.custody_loan = loan.name
	doc.loan_type = "Asset"
	for row in rows:
		doc.append(
			"items",
			{
				"custody_loan_item": row.name,
				"item_code": row.item_code,
				"returned_qty": 1,
				"asset": row.asset,
				"target_location": row.source_location,
			},
		)
	doc.insert()
	made["returns"].append(doc.name)
	savepoint = "return_loan_set_asset_submit"
	frappe.db.savepoint(savepoint)
	try:
		doc.submit()
	except frappe.ValidationError:
		# an inspection is asked first: the QI team is asked and the system submits the return after it
		frappe.db.rollback(save_point=savepoint)
		frappe.clear_last_message()
		made["waiting"].append(doc.name)


def _return_stock(loan, lines, made):
	doc = frappe.new_doc("Custody Loan Return")
	doc.company = loan.company
	doc.custody_loan = loan.name
	doc.loan_type = "Stock"
	for line in lines:
		doc.append("items", line)
	doc.insert()
	doc.submit()
	made["returns"].append(doc.name)


def _consume_stock(loan, consumed, made):
	"""Write off what was used up (the lent lots still out, earliest first); a Borrow to a Customer is also invoiced."""
	request_type = frappe.db.get_value("Material Request", loan.material_request, "material_request_type")
	demo = request_type == DEMO_REQUEST
	invoice = None
	if not demo and loan.borrower_type == "Customer":
		invoice = _draft_invoice(loan, consumed, made)
	account = frappe.db.get_value("Company", loan.company, "stock_adjustment_account")
	if not account:
		frappe.throw(
			_("Set the Stock Adjustment Account of {0} to write off what was used up.").format(loan.company)
		)
	reason = (
		_(DEMO_REASON)
		if demo
		else (
			_("Consumed by the borrower, invoiced on {0}").format(invoice)
			if invoice
			else _("Consumed by the borrower")
		)
	)
	adjustment = frappe.new_doc("Custody Loan Adjustment")
	adjustment.company = loan.company
	adjustment.custody_loan = loan.name
	adjustment.adjustment_account = account
	adjustment.reason = reason
	fresh = frappe.get_doc("Custody Loan", loan.name)
	for row in fresh.items:
		left = flt(consumed.get(row.name))
		if left <= 0:
			continue
		if loan_item_available_to_adjust(loan.name, row) + 1e-9 < left:
			frappe.throw(
				_("{0}: only {1} is left to write off.").format(
					row.item_code, flt(loan_item_available_to_adjust(loan.name, row))
				)
			)
		if not is_tracked(row.item_code):
			adjustment.append(
				"items", {"custody_loan_item": row.name, "item_code": row.item_code, "qty": left}
			)
			continue
		for group in grouped_open_rows(fresh, row):
			take = min(flt(group["qty"]), left)
			if take <= 0:
				continue
			serials = group["serials"][: int(take)] if group["serials"] else []
			adjustment.append(
				"items",
				{
					"custody_loan_item": row.name,
					"item_code": row.item_code,
					"qty": take,
					"batch_no": group["batch_no"],
					"serial_no": "\n".join(serials) or None,
				},
			)
			left -= take
			if left <= 0:
				break
		if left > 1e-9:
			frappe.throw(_("{0}: the lots still out do not cover what was used up.").format(row.item_code))
	adjustment.flags.ignore_permissions = True
	adjustment.insert()
	adjustment.submit()
	made["adjustments"].append(adjustment.name)


def _draft_invoice(loan, consumed, made):
	"""A draft Sales Invoice to the borrower for what was consumed, at the selling price, for accounts to submit. It is
	not a loan transaction (no stock moves: the write-off above takes the goods out). None when it cannot be drafted;
	the note says so and the write-off still happens."""
	from erpnext.stock.get_item_details import get_item_details

	savepoint = "return_loan_set_invoice"
	frappe.db.savepoint(savepoint)
	try:
		invoice = frappe.new_doc("Sales Invoice")
		invoice.company = loan.company
		invoice.customer = loan.borrower
		invoice.update_stock = 0
		invoice.set_missing_values()
		invoice.customer_address = loan.recipient_address
		invoice.shipping_address_name = loan.recipient_address
		invoice.contact_person = loan.recipient_contact
		invoice.remarks = _("Used up by the borrower on Custody Loan {0}").format(loan.name)
		for row in loan.items:
			qty = flt(consumed.get(row.name))
			if qty <= 0:
				continue
			uom = frappe.db.get_value("Item", row.item_code, "stock_uom")
			details = get_item_details(
				frappe._dict(
					doctype="Sales Invoice",
					item_code=row.item_code,
					customer=loan.borrower,
					company=loan.company,
					qty=qty,
					uom=uom,
					conversion_rate=invoice.conversion_rate or 1,
					currency=invoice.currency,
					selling_price_list=invoice.selling_price_list,
					price_list=invoice.selling_price_list,
					price_list_currency=invoice.price_list_currency,
					plc_conversion_rate=invoice.plc_conversion_rate or 1,
					transaction_date=invoice.posting_date,
				),
				doc=invoice,
			)
			invoice.append(
				"items",
				{**details, "item_code": row.item_code, "qty": qty, "uom": uom, "conversion_factor": 1},
			)
		invoice.flags.ignore_permissions = True
		invoice.insert()
		# the system's draft: accounts receivable finds it in My Tasks ("Drafted Sales Invoice")
		frappe.db.set_value("Sales Invoice", invoice.name, "owner", "Administrator", update_modified=False)
		made["invoices"].append(invoice.name)
		return invoice.name
	except Exception as error:
		frappe.db.rollback(save_point=savepoint)
		frappe.clear_last_message()
		made["notes"].append(
			_(
				"The Sales Invoice for what was used up could not be drafted ({0}): accounts invoices it."
			).format(str(error)[:200])
		)
		return None
