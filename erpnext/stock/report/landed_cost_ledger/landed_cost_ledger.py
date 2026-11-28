"""Trace one LCV through its receipt, invoices and reconciled settlements; never post entries."""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.stock.report_policy import has_purchase_price_access, has_stock_value_access


def columns():
	result = [
		{"fieldname": "section", "label": _("Section"), "fieldtype": "Data", "width": 155},
		{
			"fieldname": "voucher_type",
			"label": _("Document Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"hidden": 1,
		},
		{
			"fieldname": "voucher_no",
			"label": _("Document"),
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 140,
		},
		{"fieldname": "posting_date", "label": _("Posting Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "detail", "label": _("Details / Account"), "fieldtype": "Data", "width": 270},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 140},
		{
			"fieldname": "warehouse",
			"label": _("Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 140,
		},
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 100},
		*[
			{
				"fieldname": key,
				"label": _(label),
				"fieldtype": "Currency",
				"options": "currency",
				"width": 140,
			}
			for key, label in (
				("amount", "Allocated Landed Cost"),
				("cost_before", "Receipt Base Cost"),
				("cost_after", "Base Plus This LCV"),
				("valuation_rate", "Valuation Rate"),
				("stock_value_difference", "Stock Value Change"),
				("payment_amount", "Payment Ledger Amount"),
				("debit", "Debit"),
				("credit", "Credit"),
			)
		],
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
		},
	]
	first = ("section", "voucher_no", "detail", "amount", "debit", "credit")
	by_name = {column["fieldname"]: column for column in result}
	return [by_name[key] for key in first] + [column for column in result if column["fieldname"] not in first]


def linked_settlements(refs, company):
	"""Expand exact voucher links once, including cycles and advances reconciled through a journal."""
	payment_entries = {}
	visited = set()
	while refs - visited:
		kind, number = sorted(refs - visited)[0]
		visited.add((kind, number))
		for entry in frappe.get_all(
			"Payment Ledger Entry",
			filters={"company": company, "delinked": 0},
			or_filters={"voucher_no": number, "against_voucher_no": number},
			fields=[
				"name",
				"voucher_type",
				"voucher_no",
				"against_voucher_type",
				"against_voucher_no",
				"posting_date",
				"account",
				"party",
				"amount",
			],
		):
			if (entry.voucher_type, entry.voucher_no) != (kind, number) and (
				entry.against_voucher_type,
				entry.against_voucher_no,
			) != (kind, number):
				continue
			payment_entries[entry.name] = entry
			for pair in (
				(entry.voucher_type, entry.voucher_no),
				(entry.against_voucher_type, entry.against_voucher_no),
			):
				if pair[0] in {"Purchase Invoice", "Payment Entry", "Journal Entry"} and pair[1]:
					refs.add(pair)
	return payment_entries


def receipt_base_cost(item):
	"""Receipt valuation includes free goods; exclude LCVs so resubmitting cannot double count them."""
	quantity = item.get("stock_qty") or item.get("transfer_qty") or item.qty
	return flt(item.valuation_rate) * flt(quantity) - flt(item.get("landed_cost_voucher_amount"))


def execute(filters=None):
	name = (filters or {}).get("landed_cost_voucher")
	if not name:
		frappe.throw(_("Select a Landed Cost Voucher."))
	if (
		not frappe.has_permission("GL Entry", "read")
		or not has_purchase_price_access()
		or not has_stock_value_access()
	):
		frappe.throw(
			_("Accounting access and permission to view purchase prices and stock values are required."),
			frappe.PermissionError,
		)
	doc = frappe.get_doc("Landed Cost Voucher", name)
	doc.check_permission("read")
	currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	refs = {(row.receipt_document_type, row.receipt_document) for row in doc.purchase_receipts}
	refs |= {("Purchase Invoice", row.vendor_invoice) for row in doc.vendor_invoices if row.vendor_invoice}
	for receipt_type, receipt in list(refs):
		if receipt_type == "Purchase Receipt":
			refs |= {
				("Purchase Invoice", row.parent)
				for row in frappe.get_all(
					"Purchase Invoice Item",
					filters={"purchase_receipt": receipt, "docstatus": 1},
					fields=["parent"],
				)
			}
	# A journal can transfer the invoice debt to an agent; show every row of that journal, including the agent.
	invoices = [number for kind, number in refs if kind == "Purchase Invoice"]
	if invoices:
		refs |= {
			("Payment Entry", row.parent)
			for row in frappe.get_all(
				"Payment Entry Reference",
				filters={
					"reference_doctype": "Purchase Invoice",
					"reference_name": ["in", invoices],
					"docstatus": 1,
				},
				fields=["parent"],
			)
		}
		refs |= {
			("Journal Entry", row.parent)
			for row in frappe.get_all(
				"Journal Entry Account",
				filters={
					"reference_type": "Purchase Invoice",
					"reference_name": ["in", invoices],
					"docstatus": 1,
				},
				fields=["parent"],
			)
		}
	payment_entries = linked_settlements(refs, doc.company)
	related_docs = {}
	for kind, number in sorted(refs):
		related = frappe.get_doc(kind, number)
		related_docs[(kind, number)] = related
		related.check_permission("read")
		if related.company != doc.company:
			frappe.throw(_("Related documents must belong to the same company."))

	rows = [
		dict(
			section=_("Invoice Allocation"),
			voucher_type="Purchase Invoice",
			voucher_no=row.vendor_invoice,
			amount=flt(row.amount),
			detail=_("Amount used from this invoice"),
		)
		for row in doc.vendor_invoices
	]
	for item in doc.items:
		receipt = related_docs[(item.receipt_document_type, item.receipt_document)]
		detail_name = item.get("purchase_receipt_item") or item.get("stock_entry_item")
		detail = next(row for row in receipt.items if row.name == detail_name)
		base_cost = receipt_base_cost(detail)
		rows.append(
			dict(
				section=_("Item Allocation"),
				voucher_type=item.receipt_document_type,
				voucher_no=item.receipt_document,
				item_code=item.item_code,
				qty=item.qty,
				amount=item.applicable_charges,
				detail=_("This voucher's allocation; receipt base cost shown below"),
				cost_before=base_cost,
				cost_after=base_cost + flt(item.applicable_charges),
			)
		)
	gl_count = 0
	for kind, number in sorted(refs):
		for entry in (
			frappe.get_list(
				"Stock Ledger Entry",
				filters={
					"voucher_type": kind,
					"voucher_no": number,
					"company": doc.company,
					"is_cancelled": 0,
				},
				fields=[
					"posting_date",
					"item_code",
					"warehouse",
					"actual_qty",
					"valuation_rate",
					"stock_value_difference",
				],
				order_by="posting_datetime, creation",
				limit_page_length=0,
			)
			if kind in {"Purchase Receipt", "Purchase Invoice", "Stock Entry", "Subcontracting Receipt"}
			else []
		):
			rows.append(
				dict(
					section=_("Stock Ledger"),
					voucher_type=kind,
					voucher_no=number,
					posting_date=entry.posting_date,
					item_code=entry.item_code,
					warehouse=entry.warehouse,
					qty=entry.actual_qty,
					valuation_rate=entry.valuation_rate,
					stock_value_difference=entry.stock_value_difference,
				)
			)
	for kind, number in sorted(refs):
		for entry in frappe.get_list(
			"GL Entry",
			filters={"voucher_type": kind, "voucher_no": number, "company": doc.company, "is_cancelled": 0},
			fields=["posting_date", "account", "debit", "credit", "party"],
			order_by="posting_date, creation, name",
			limit_page_length=0,
		):
			gl_count += 1
			rows.append(
				dict(
					section=_("General Ledger"),
					voucher_type=kind,
					voucher_no=number,
					posting_date=entry.posting_date,
					detail=entry.account + (" · " + entry.party if entry.party else ""),
					debit=entry.debit,
					credit=entry.credit,
				)
			)
	for entry in sorted(payment_entries.values(), key=lambda row: (row.posting_date, row.name)):
		rows.append(
			dict(
				section=_("Payment Ledger"),
				voucher_type=entry.voucher_type,
				voucher_no=entry.voucher_no,
				posting_date=entry.posting_date,
				detail=f"{entry.account} · {entry.party} → {entry.against_voucher_no}",
				payment_amount=entry.amount,
			)
		)
	for row in rows:
		row["currency"] = currency
	message = _(
		"Receipt base cost includes free-goods allocation and excludes all LCVs; Base Plus This LCV adds only this voucher. LCV adjustments appear under receipt vouchers. Invoice and item allocations show the same cost: do not add them together. Ledger entries exclude cancellations, unlinked advances and later stock movements."
	)
	if doc.docstatus != 1:
		message = (
			_(
				"This voucher is not submitted: allocations are a preview; ledger entries are existing postings, not postings of this voucher."
			)
			+ " "
			+ message
		)
	summary = [
		{
			"label": _("Voucher Status"),
			"value": {0: _("Draft"), 1: _("Submitted"), 2: _("Cancelled")}[doc.docstatus],
			"datatype": "Data",
		},
		{
			"label": _("Allocated Landed Cost"),
			"value": doc.total_taxes_and_charges,
			"datatype": "Currency",
			"currency": currency,
		},
		{"label": _("Documents / GL Rows"), "value": f"{len(refs)} / {gl_count}", "datatype": "Data"},
	]
	return columns(), rows, message, None, summary
