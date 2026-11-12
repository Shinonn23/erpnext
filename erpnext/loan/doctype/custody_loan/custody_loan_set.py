"""Read a request's mixed Asset / Stock loan set and open explicit linked Return documents.

Each loan has its own Return, reviewed and submitted by the user. Creating a Return never posts stock,
Asset movements, consumption adjustments or invoices. The native Return lifecycle owns those posting rules.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from erpnext.loan.doctype.custody_loan.custody_loan_core import (
	loan_item_available_to_receive,
	set_loan_transaction_source,
)
from erpnext.loan.doctype.custody_loan.custody_loan_tracking import (
	conversion_factors,
	grouped_open_rows,
	is_tracked,
	item_flags,
)

OPEN_STATUSES = ("Active", "Partially Returned")
DEMO_REQUEST = "Customer Demo"


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


DELIVERING = ("Pending Pick", "Pending Delivery")
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


@frappe.whitelist()
def make_return(custody_loan: str):
	"""Open the native linked Return without saving or submitting it."""
	return frappe.get_doc("Custody Loan", custody_loan).make_return()


@frappe.whitelist(methods=["POST"])
def return_loan_set(
	loan: str | None = None,
	material_request: str | None = None,
	assets: str | list | None = None,
	stock: str | list | None = None,
):
	"""Reject retired one-click posting, including stale browser sessions."""
	frappe.throw(_("Create a Custody Loan Return, review its items, then Save and Submit it."))


@frappe.whitelist(methods=["POST"])
def create_consumption_invoice(adjustment: str):
	"""Accounts explicitly starts billing for one submitted borrower consumption adjustment."""
	frappe.has_permission("Sales Invoice", "create", throw=True)
	frappe.db.sql("select name from `tabCustody Loan Adjustment` where name=%s for update", adjustment)
	source = frappe.get_doc("Custody Loan Adjustment", adjustment)
	source.check_permission("read")
	loan = frappe.get_doc("Custody Loan", source.custody_loan)
	loan.check_permission("read")
	request_type = frappe.db.get_value("Material Request", loan.material_request, "material_request_type")
	if (
		source.docstatus != 1
		or loan.loan_type != "Stock"
		or loan.borrower_type != "Customer"
		or request_type != "Customer Borrow / Loan"
		or source.reason != "Consumed by the borrower"
	):
		frappe.throw(_("Create a consumption invoice from a submitted borrower consumption adjustment."))
	existing = frappe.db.get_value(
		"Sales Invoice",
		{
			"custody_loan_source_doctype": source.doctype,
			"custody_loan_source_name": source.name,
			"docstatus": ["<", 2],
		},
		"name",
	)
	if existing:
		invoice = frappe.get_doc("Sales Invoice", existing)
		invoice.check_permission("read")
		return invoice.as_dict()
	if not source.stock_entry or frappe.db.get_value("Stock Entry", source.stock_entry, "docstatus") != 1:
		frappe.throw(_("Submit the write-off Stock Entry before creating its consumption invoice."))
	consumed = {}
	for row in source.items:
		consumed[row.custody_loan_item] = consumed.get(row.custody_loan_item, 0) + flt(row.qty)
	return _make_consumption_invoice(loan, consumed, source).as_dict()


def _make_consumption_invoice(loan, consumed, source):
	from erpnext.stock.get_item_details import get_item_details

	invoice = frappe.new_doc("Sales Invoice")
	invoice.company = loan.company
	invoice.customer = loan.borrower
	invoice.update_stock = 0
	invoice.set_missing_values()
	invoice.customer_address = loan.recipient_address
	invoice.shipping_address_name = loan.recipient_address
	invoice.contact_person = loan.recipient_contact
	invoice.remarks = _("Used up by the borrower on Custody Loan {0}").format(loan.name)
	set_loan_transaction_source(invoice, loan, source)
	# Billing still follows its own Sales Invoice workflow; a stock movement's authority cannot approve it.
	invoice.flags.custody_operation_token = None
	factors = conversion_factors(loan)
	for row in loan.items:
		qty = flt(consumed.get(row.name)) * factors.get(row.name, 1)
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
			{
				**details,
				"item_code": row.item_code,
				"custody_loan_item": row.name,
				"qty": qty,
				"uom": uom,
				"conversion_factor": 1,
			},
		)
	return invoice
