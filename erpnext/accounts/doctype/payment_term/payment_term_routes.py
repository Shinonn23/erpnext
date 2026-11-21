# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""Native Payment Term route validation and advance term helpers."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

REGULAR = "Regular"
ADVANCE = "Advance"
DEPOSIT_INVOICE = "Deposit Invoice"
TERM_TYPES = {REGULAR, ADVANCE, DEPOSIT_INVOICE}


def is_advance_term(doctype: str, row) -> bool:
	"""Return True if a payment schedule row is paid before invoicing."""
	cached = (
		frappe.get_cached_value("Payment Term", row.get("payment_term"), "payment_term_type")
		if row.get("payment_term")
		else None
	)
	term_type = cached if cached in TERM_TYPES else row.get("payment_term_type")
	return term_type == ADVANCE or (
		doctype == "Purchase Order" and row.get("due_date_based_on") == "Day(s) after order date"
	)


def get_unpaid_advance_term(doc) -> str | None:
	"""Return the first unpaid advance payment_term name of a submitted multi-term order."""
	if cint(doc.docstatus) != 1 or doc.get("has_deposit"):
		return None
	schedule = sorted(doc.get("payment_schedule") or [], key=lambda row: cint(row.get("idx")))
	if len(schedule) < 2:
		return None
	term = next(
		(
			row
			for row in schedule
			if row.get("payment_term")
			and is_advance_term(doc.doctype, row)
			and flt(row.get("payment_amount")) > flt(row.get("paid_amount"))
		),
		None,
	)
	return term.payment_term if term else None


def set_order_payment_route(doc, method=None):
	"""Draft order schedules take their document route from their named Payment Terms."""
	if doc.doctype not in {"Sales Order", "Purchase Order"} or cint(doc.docstatus) == 2:
		return
	previous = doc.get_doc_before_save()
	if previous and cint(previous.docstatus) == 1:
		return
	if previous and previous.get("has_deposit") and not doc.get("payment_terms_template"):
		# Existing template-less deposits retain their agreed route; new orders use named Payment Terms.
		for field in ("has_deposit", "percent_deposit", "deposit_amount"):
			setattr(doc, field, previous.get(field))
		return
	terms = sorted(doc.get("payment_schedule") or [], key=lambda row: cint(row.get("idx")))
	kinds = {}
	for row in terms:
		name = row.get("payment_term")
		if name and name not in kinds:
			kinds[name] = frappe.get_cached_value("Payment Term", name, "payment_term_type") or REGULAR
		kind = kinds.get(name, REGULAR)
		if kind not in TERM_TYPES:
			frappe.throw(_("Payment Term {0} has an invalid Payment Term Type.").format(name))
		row.payment_term_type = kind
	has_advance = any(row.payment_term_type == ADVANCE for row in terms)
	if has_advance and (
		not all(row.get("payment_term") for row in terms)
		or len({row.payment_term for row in terms}) != len(terms)
	):
		frappe.throw(_("Every payment schedule row must name a different Payment Term when using Advance."))
	template = None
	if doc.get("payment_terms_template"):
		template = frappe.get_cached_doc("Payment Terms Template", doc.payment_terms_template)
		expected = []
		for index, row in enumerate(template.terms):
			name = row.payment_term
			if name not in kinds:
				kinds[name] = frappe.get_cached_value("Payment Term", name, "payment_term_type") or REGULAR
			if kinds[name] != REGULAR:
				expected.append((index, name, kinds[name], flt(row.invoice_portion)))
		actual = [
			(index, row.payment_term, row.payment_term_type, flt(row.invoice_portion))
			for index, row in enumerate(terms)
			if row.payment_term_type != REGULAR
		]
		if actual != expected:
			frappe.throw(
				_(
					"Refresh the Payment Terms Template; its deposit and advance terms cannot be changed on the order."
				)
			)
	if has_advance:
		if not (template and template.get("allocate_payment_based_on_payment_terms")):
			frappe.throw(
				_("Advance terms require a Payment Terms Template that allocates payment by payment terms.")
			)
		if any(flt(row.get("discount")) for row in terms):
			frappe.throw(
				_("Advance payment tasks do not support early payment discounts in the payment schedule.")
			)
	deposits = [row for row in terms if row.payment_term_type == DEPOSIT_INVOICE]
	if deposits and doc.doctype == "Purchase Order":
		frappe.throw(_("Use an Advance payment term for a Purchase Order; Deposit Invoice is for sales."))
	if doc.doctype != "Sales Order":
		return
	if (
		previous
		and previous.get("has_deposit")
		and previous.get("payment_terms_template") == doc.get("payment_terms_template")
		and not any(row.payment_term_type != REGULAR for row in terms)
		and not any(
			row.get("payment_term_type") in {ADVANCE, DEPOSIT_INVOICE}
			for row in previous.get("payment_schedule") or []
		)
	):
		# An unclassified historical draft is not a request to remove its agreed deposit.
		for field in ("has_deposit", "percent_deposit", "deposit_amount"):
			setattr(doc, field, previous.get(field))
		return
	if deposits and (len(deposits) != 1 or deposits[0] is not terms[0]):
		frappe.throw(_("Deposit Invoice must be the first and only deposit term of the Sales Order."))
	if deposits and any(row.payment_term_type == ADVANCE for row in terms):
		frappe.throw(_("A Sales Order cannot combine Deposit Invoice and Advance payment terms."))
	if deposits and not 0 < flt(deposits[0].invoice_portion) <= 100:
		frappe.throw(
			_("The Deposit Invoice term must have an Invoice Portion greater than zero and at most 100%.")
		)
	doc.has_deposit = int(bool(deposits))
	doc.percent_deposit = flt(deposits[0].invoice_portion) if deposits else 0
	doc.deposit_amount = flt(doc.get("total")) * doc.percent_deposit / 100


def validate_deposit_invoice_amount(doc, method=None):
	"""New deposits follow a submitted order; typed routes use its agreed schedule amount."""
	if not doc.get("is_deposit_invoice") or doc.get("is_return"):
		return
	orders = {row.get("sales_order") for row in doc.get("items") or [] if row.get("sales_order")}
	previous = doc.get_doc_before_save()
	if (
		not orders
		and previous
		and previous.get("is_deposit_invoice")
		and not any(row.get("sales_order") for row in previous.get("items") or [])
	):
		# Keep saved standalone history usable; new deposits require their order's payment route.
		return
	if len(orders) != 1:
		frappe.throw(_("A Deposit Invoice must reference one submitted Sales Order requiring a deposit."))
	for name in orders:
		order = frappe.get_doc("Sales Order", name)
		if cint(order.docstatus) != 1 or not order.get("has_deposit"):
			frappe.throw(_("A Deposit Invoice must reference one submitted Sales Order requiring a deposit."))
		terms = [
			row
			for row in order.get("payment_schedule") or []
			if row.get("payment_term_type") == DEPOSIT_INVOICE
		]
		if not terms:
			if any(row.get("payment_term_type") == ADVANCE for row in order.get("payment_schedule") or []):
				frappe.throw(_("An Advance payment term cannot create a Deposit Invoice."))
			continue
		frappe.db.get_value("Sales Order", name, "name", for_update=True)
		duplicates = frappe.db.sql(
			"""select invoice.name from `tabSales Invoice` invoice
			inner join `tabSales Invoice Item` item on item.parent = invoice.name
			where item.sales_order = %s and invoice.name != %s
			and invoice.docstatus < 2 and invoice.is_deposit_invoice = 1
			and coalesce(invoice.is_return, 0) = 0 limit 1 for update""",
			(name, doc.get("name") or ""),
		)
		if duplicates:
			frappe.throw(_("Sales Order {0} already has an active Deposit Invoice.").format(name))
		precision = doc.precision("total")
		expected = flt(flt(order.total) * flt(terms[0].invoice_portion) / 100, precision)
		if flt(doc.total, precision) != expected:
			frappe.throw(
				_(
					"The Deposit Invoice amount must be {0}, as specified by the Sales Order payment term."
				).format(expected)
			)


def validate_submitted_payment_route(doc, method=None):
	"""Submitted typed orders retain the deposit route agreed in their frozen schedule."""
	if doc.doctype != "Sales Order" or cint(doc.docstatus) != 1:
		return
	previous = doc.get_doc_before_save()
	if not previous:
		return
	terms = previous.get("payment_schedule") or []
	if not any(row.get("payment_term_type") in {ADVANCE, DEPOSIT_INVOICE} for row in terms):
		return
	deposits = [row for row in terms if row.get("payment_term_type") == DEPOSIT_INVOICE]
	portion = flt(deposits[0].invoice_portion) if deposits else 0
	expected = {
		"has_deposit": int(bool(deposits)),
		"percent_deposit": portion,
		"deposit_amount": flt(previous.get("total")) * portion / 100,
	}
	for field, value in expected.items():
		precision = doc.precision(field)
		if flt(doc.get(field), precision) != flt(value, precision):
			frappe.throw(_("The submitted Sales Order payment route and deposit amount cannot be changed."))
