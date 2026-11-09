"""Map a submitted Thai billing's current invoices to one unsaved native Payment Entry."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry, get_reference_details
from erpnext.regional.thailand.custom.naming_branch import BILLING_LINES


def make_billing_payment(billing_doctype, source_name):
	billing = frappe.get_doc(billing_doctype, source_name)
	billing.check_permission("read")
	frappe.has_permission("Payment Entry", ptype="create", throw=True)
	if billing.docstatus != 1 or cint(billing.closed):
		frappe.throw(_("Create a Payment Entry from a submitted, open billing."))

	table, invoice_field, invoice_doctype, address_field = BILLING_LINES[billing_doctype]
	party_field = "customer" if billing_doctype == "Sales Billing" else "supplier"
	payment_type = "Receive" if party_field == "customer" else "Pay"
	invoices, seen = [], set()
	for line in billing.get(table) or []:
		name = line.get(invoice_field)
		if not name or name in seen:
			frappe.throw(_("The billing must list each invoice once."))
		seen.add(name)
		invoice = frappe.get_doc(invoice_doctype, name)
		invoice.check_permission("read")
		if (
			invoice.docstatus != 1
			or invoice.company != billing.company
			or invoice.get(party_field) != billing.get(party_field)
			or invoice.currency != billing.currency
			or (invoice.get(address_field) or "") != (billing.get("company_tax_address") or "")
		):
			frappe.throw(
				_("Billing invoices must be submitted and match its Company, party, Currency and branch.")
			)
		if not flt(invoice.outstanding_amount):
			continue
		if invoice_doctype == "Purchase Invoice" and invoice.invoice_is_blocked():
			frappe.throw(_("Purchase Invoice {0} is on hold.").format(invoice.name))
		invoices.append(invoice)

	if sum(flt(invoice.outstanding_amount) for invoice in invoices) <= 0:
		frappe.throw(_("The billing must have a positive net outstanding amount to create a Payment Entry."))

	# A positive invoice supplies the normal Receive/Pay accounts; credit notes offset that payment.
	invoices.sort(key=lambda invoice: flt(invoice.outstanding_amount) < 0)
	parts, bank_account = [], None
	for invoice in invoices:
		part = get_payment_entry(invoice_doctype, invoice.name, bank_account=bank_account)
		if not part.references and flt(invoice.outstanding_amount) < 0:
			# Native term allocation omits negative terms; use its signed invoice reference details for credits.
			details = get_reference_details(
				invoice_doctype, invoice.name, part.party_account_currency, part.party_type, part.party
			)
			part.append(
				"references",
				{
					**details,
					"reference_doctype": invoice_doctype,
					"reference_name": invoice.name,
					"allocated_amount": details.outstanding_amount,
				},
			)
		if not part.references:
			frappe.throw(_("Invoice {0} has no payable outstanding references.").format(invoice.name))
		parts.append(part)
		if bank_account is None:
			bank_account = part.paid_to if payment_type == "Receive" else part.paid_from

	payment = parts[0]
	dimensions = ("cost_center", "project", *get_accounting_dimensions())
	amounts = {"paid_amount": 0.0, "received_amount": 0.0}
	references, deductions = [], []
	for part in parts:
		same_direction = part.payment_type == payment_type
		for left, right in (
			("paid_from", "paid_to"),
			("paid_from_account_currency", "paid_to_account_currency"),
			("source_exchange_rate", "target_exchange_rate"),
		):
			if part.get(left if same_direction else right) != payment.get(left) or part.get(
				right if same_direction else left
			) != payment.get(right):
				frappe.throw(
					_("Billing invoices must use the same payment accounts, currencies and exchange rates.")
				)
		if any(part.get(field) != payment.get(field) for field in dimensions):
			frappe.throw(_("Billing invoices must use the same accounting dimensions for one Payment Entry."))
		amounts["paid_amount"] += flt(part.paid_amount) if same_direction else -flt(part.received_amount)
		amounts["received_amount"] += flt(part.received_amount) if same_direction else -flt(part.paid_amount)
		references.extend(row.as_dict(no_default_fields=True) for row in part.references)
		deductions.extend(row.as_dict(no_default_fields=True) for row in part.deductions)

	if any(flt(value, payment.precision(field)) <= 0 for field, value in amounts.items()):
		frappe.throw(_("The billing must have a positive net payment amount."))
	payment.update(amounts)
	payment.set("references", references)
	payment.set("deductions", deductions)
	payment.set(frappe.scrub(billing_doctype), billing.name)
	payment.company_tax_address = billing.get("company_tax_address")
	payment.set_amounts()
	return payment
