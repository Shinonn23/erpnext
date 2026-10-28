"""Advance VAT on a Payment Entry: the VAT of a service paid before it is invoiced (Revenue Code section 78/1: for
services the tax point is the payment).

A Payment Entry that receives an advance from a customer for SERVICES moves the VAT of the advance from the undue
output VAT account to the output VAT account on the date it is received (a Sales Tax Invoice dated that day); a
Payment Entry that pays a supplier an advance for services does the same with the input VAT (Has Purchase Tax
Invoice). The pair of tax rows is the one Clear Undue Tax makes for a paid invoice (custom_api.clear_invoice_undue_tax)
and posts no bank line (payment_entry.add_tax_gl_entries). The service invoice issued later credits the undue
account, which the pair debited, so the undue account nets to zero by itself. GOODS: a deposit is no tax point, VAT
arises on the invoice / delivery, so there is no pair.

Which advance is for services: a Sales Order / Purchase Order is judged by its taxes (a service order posts its tax to
the undue account of Thai Tax Settings, template "Thailand Tax (Service)"); an amount not allocated to any document
says it on the Payment Entry (Advance For, thai_advance_for).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

# the description of the tax rows of the advance pair
ADVANCE_VAT = "Advance VAT Due"

GOODS = "Goods"
SERVICES = "Services"
ADVANCE_FOR = "thai_advance_for"

SALES = "Sales"
PURCHASE = "Purchase"
# (payment type, party type) of the advances that carry VAT: received from a customer, paid to a supplier
SIDES = {("Receive", "Customer"): SALES, ("Pay", "Supplier"): PURCHASE}
ORDER_DOCTYPES = {SALES: "Sales Order", PURCHASE: "Purchase Order"}
TAX_ROW_DOCTYPES = {SALES: "Sales Taxes and Charges", PURCHASE: "Purchase Taxes and Charges"}


def state_is_editable(state):
	"""The workflow states in which the Payment Entry is still the preparer's (Draft, Rejected; the app prefixes
	them: Payment Entry Draft, Payment Entry Amount Rejected, Payment Entry Refund Rejected). Any later state
	(Pending Bank Verification, Verified, Pending Amount Approval ...) locks the business details."""
	if not isinstance(state, str):
		# no workflow state field (or a stand-in): the document is free
		return True
	return not state or state.endswith(("Draft", "Rejected"))


def is_editable_state(doc):
	return state_is_editable(doc.get("workflow_state"))


def advance_side(doc):
	"""SALES for an advance received from a customer, PURCHASE for one paid to a supplier, else None."""
	return SIDES.get((doc.get("payment_type"), doc.get("party_type")))


def order_undue_tax(doctype, name, undue_account):
	"""(tax, base, total) of a Sales Order / Purchase Order in company currency: the tax it posts to the undue
	account, its net total and its grand total. A zero tax means it is not a service order."""
	order = frappe.db.get_value(doctype, name, ["base_net_total", "base_grand_total"], as_dict=True)
	if not order:
		return 0, 0, 0
	rows = frappe.get_all(
		TAX_ROW_DOCTYPES[SALES if doctype == "Sales Order" else PURCHASE],
		filters={"parent": name, "parenttype": doctype, "account_head": undue_account},
		fields=["base_tax_amount_after_discount_amount"],
	)
	tax = sum(flt(row.get("base_tax_amount_after_discount_amount")) for row in rows)
	return tax, flt(order.get("base_net_total")), flt(order.get("base_grand_total"))


def _accounts(side, setting):
	"""(undue account, due account) of the side."""
	if side == SALES:
		return setting.sales_tax_account_undue, setting.sales_tax_account
	return setting.purchase_tax_account_undue, setting.purchase_tax_account


def _wants_pairs(doc, side):
	"""A payment to a supplier moves input VAT only when the supplier gave a tax invoice (Has Purchase Tax Invoice)."""
	return side == SALES or (side == PURCHASE and cint(doc.get("has_purchase_tax_invoice")))


def order_references(doc):
	"""The Sales Order / Purchase Order rows of the entry that allocate something."""
	side = advance_side(doc)
	if not side:
		return []
	return [
		ref
		for ref in doc.get("references") or []
		if ref.reference_doctype == ORDER_DOCTYPES[side] and ref.reference_name and flt(ref.allocated_amount)
	]


def advance_vat_pairs(doc, setting):
	"""[(undue account, due account, tax, base)] of the advance VAT of the entry, in company currency.

	Every order row: the tax the order posts to the undue account times the share allocated, on its net total times
	the same share. The amount not allocated to anything, when Advance For is Services: it holds the VAT at the
	tax rate of the due account, tax = total x rate / (100 + rate), base = total - tax."""
	side = advance_side(doc)
	if not side or not _wants_pairs(doc, side):
		return []
	undue, due = _accounts(side, setting)
	pairs = []
	for ref in order_references(doc):
		tax, base, total = order_undue_tax(ref.reference_doctype, ref.reference_name, undue)
		if not tax:
			# a goods order (or VAT Exempt): a deposit is not a tax point
			continue
		denominator = flt(getattr(ref, "total_amount", 0)) or total
		if not denominator:
			continue
		share = min(abs(flt(ref.allocated_amount) / denominator), 1)
		pairs.append((undue, due, flt(tax * share, 2), flt(base * share, 2)))
	unallocated = flt(doc.get("unallocated_amount"))
	if doc.get(ADVANCE_FOR) == SERVICES and unallocated > 0:
		rate_field = "source_exchange_rate" if side == SALES else "target_exchange_rate"
		total = flt(unallocated * flt(doc.get(rate_field) or 1), 2)
		rate = flt(frappe.get_cached_value("Account", due, "tax_rate"))
		if not rate:
			frappe.throw(_("Set the Tax Rate of account {0} to take the VAT out of an advance.").format(due))
		tax = flt(total * rate / (100 + rate), 2)
		pairs.append((undue, due, tax, flt(total - tax, 2)))
	return [pair for pair in pairs if pair[2]]


def validate_advance_vat(doc):
	"""A draft that receives from a customer / pays a supplier more than it allocates must say what the rest is for:
	the VAT of services falls due with the payment, that of goods with the invoice."""
	if doc.docstatus != 0 or not advance_side(doc):
		return
	# only while the preparer edits it, or on the transition out of Draft: an entry already in review (bank
	# verification, amount approval) that holds an unallocated amount must not get stuck
	loader = getattr(doc, "get_doc_before_save", None)
	previous = loader() if callable(loader) else None
	if not (is_editable_state(doc) or (previous and is_editable_state(previous))):
		return
	# the custom field is made by connect_diagnostics_setup; without it nothing can be asked
	meta = getattr(doc, "meta", None)
	if meta is None or not meta.has_field(ADVANCE_FOR):
		return
	if flt(doc.get("unallocated_amount")) <= 0:
		return
	advance_for = doc.get(ADVANCE_FOR)
	if not advance_for:
		frappe.throw(
			_("Choose Advance For (Goods or Services) for the amount not allocated to an invoice."),
			title=_("Advance For"),
		)
	if advance_for != SERVICES:
		return
	side = advance_side(doc)
	if not _wants_pairs(doc, side):
		return
	from erpnext.regional.thailand.custom.custom_api import get_thai_tax_settings

	undue, due = _accounts(side, get_thai_tax_settings(doc.company))
	if not (undue and due):
		frappe.throw(
			_(
				"Advance VAT needs the undue and the due {0} VAT accounts of company {1} in Thai Tax Settings."
			).format(_("output") if side == SALES else _("input"), doc.company),
			title=_("Advance For"),
		)
