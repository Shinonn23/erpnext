# Copyright (c) 2024, FLO and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PaymentReceipt(Document):
	def validate(self):
		self.validate_payment_references()
		self.update_billing_references()
		self.update_total_paid_amount()

	def on_submit(self):
		self.check_payment_references()

	def validate_payment_references(self):
		"""A receipt lists Receive Payment Entries of its customer and company (no Sales Billing needed), each once,
		none already on another receipt, all of one company branch (each branch files its own VAT)."""
		seen = set()
		addresses = set()
		for row in self.payment_references:
			if not row.payment_entry:
				continue
			if row.payment_entry in seen:
				frappe.throw(_("Payment Entry {0} is listed more than once.").format(row.payment_entry))
			seen.add(row.payment_entry)
			pe = frappe.db.get_value(
				"Payment Entry",
				row.payment_entry,
				["docstatus", "payment_type", "party_type", "party", "company", "company_tax_address"],
				as_dict=True,
			)
			if not pe:
				frappe.throw(_("Payment Entry {0} does not exist.").format(row.payment_entry))
			if pe.docstatus == 2:
				frappe.throw(_("Payment Entry {0} is cancelled.").format(row.payment_entry))
			if pe.payment_type != "Receive" or pe.party_type != "Customer":
				frappe.throw(
					_("Payment Entry {0} is not a receipt from a customer.").format(row.payment_entry)
				)
			if pe.party != self.customer:
				frappe.throw(
					_("Payment Entry {0} is for customer {1}, not {2}.").format(
						row.payment_entry, pe.party, self.customer
					)
				)
			if self.company and pe.company != self.company:
				frappe.throw(
					_("Payment Entry {0} belongs to company {1}, not {2}.").format(
						row.payment_entry, pe.company, self.company
					)
				)
			self.company = self.company or pe.company
			if pe.company_tax_address:
				addresses.add(pe.company_tax_address)
			other = self.other_receipt_of(row.payment_entry)
			if other:
				frappe.throw(
					_("Payment Entry {0} is already on Payment Receipt {1}.").format(row.payment_entry, other)
				)
		if len(addresses) > 1:
			frappe.throw(
				_(
					"Payment Entries of different company branches ({0}) cannot be on one receipt. "
					"Please make a separate Payment Receipt for each branch."
				).format(", ".join(sorted(addresses)))
			)
		if addresses:
			(self.company_tax_address,) = addresses

	def other_receipt_of(self, payment_entry):
		"""The other Payment Receipt (not cancelled) that lists the Payment Entry, or None."""
		rows = frappe.get_all(
			"Payment Receipt Payment Reference",
			filters={
				"payment_entry": payment_entry,
				"parenttype": "Payment Receipt",
				"parent": ["!=", self.name or ""],
			},
			pluck="parent",
		)
		for parent in rows:
			if frappe.db.get_value("Payment Receipt", parent, "docstatus") != 2:
				return parent
		return None

	def check_payment_references(self):
		if not self.payment_references:
			frappe.throw(_("No Payment References found!"))
		unsubmitted_references = []
		for pr in self.payment_references:
			payment = frappe.get_doc("Payment Entry", pr.payment_entry)
			if payment.docstatus != 1:
				unsubmitted_references.append(payment.name)
		if unsubmitted_references:
			frappe.throw(_("Unsubmitted Payment References: {0}").format(", ".join(unsubmitted_references)))

	def update_billing_references(self):
		"""The invoices the receipt covers: those of its Sales Billing when it has one, else those its Payment
		Entries pay."""
		self.billing_references = []
		allocated = self.get_sum_allocated_by_invoice()
		if self.sales_billing:
			sales_billing = frappe.get_doc("Sales Billing", self.sales_billing)
			for ref in sales_billing.sales_billing_line:
				self.append(
					"billing_references",
					{
						"reference_doctype": "Sales Invoice",
						"reference_name": ref.sales_invoice,
						"due_date": ref.due_date,
						"grand_total": ref.grand_total,
						"outstanding_amount": ref.outstanding_amount,
						"allocated_amount": allocated[ref.sales_invoice]["allocated"],
					},
				)
			return
		for invoice, values in allocated.items():
			details = frappe.db.get_value(
				"Sales Invoice", invoice, ["due_date", "grand_total", "outstanding_amount"], as_dict=True
			)
			if not details:
				continue
			self.append(
				"billing_references",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": invoice,
					"due_date": details.due_date,
					"grand_total": details.grand_total,
					"outstanding_amount": details.outstanding_amount,
					"allocated_amount": values["allocated"],
				},
			)

	def get_sum_allocated_by_invoice(self):
		group = defaultdict(lambda: {"allocated": 0.0})
		for pr in self.payment_references:
			pe = frappe.get_doc("Payment Entry", pr.payment_entry)
			for pe_ref in pe.references:
				if pe_ref.reference_doctype == "Sales Invoice":
					key = pe_ref.reference_name
					group[key]["allocated"] += pe_ref.allocated_amount
		return group

	def update_total_paid_amount(self):
		self.total_paid_amount = sum(flt(r.paid_amount) for r in self.payment_references)
		self.total_invoice_amount = sum(flt(r.grand_total) for r in self.billing_references)


@frappe.whitelist()
def make_payment_receipt(source_name):
	"""A new Payment Receipt for a submitted Receive Payment Entry of a customer (the form's Create button)."""
	pe = frappe.get_doc("Payment Entry", source_name)
	pe.check_permission("read")
	if pe.docstatus != 1:
		frappe.throw(_("Submit Payment Entry {0} first.").format(pe.name))
	if pe.payment_type != "Receive" or pe.party_type != "Customer":
		frappe.throw(_("A Payment Receipt is made for a receipt from a customer."))
	frappe.has_permission("Payment Receipt", "create", throw=True)
	existing = frappe.get_all(
		"Payment Receipt Payment Reference",
		filters={"payment_entry": pe.name, "parenttype": "Payment Receipt"},
		pluck="parent",
	)
	for parent in existing:
		if frappe.db.get_value("Payment Receipt", parent, "docstatus") != 2:
			frappe.throw(_("Payment Entry {0} is already on Payment Receipt {1}.").format(pe.name, parent))
	receipt = frappe.new_doc("Payment Receipt")
	receipt.customer = pe.party
	receipt.company = pe.company
	receipt.date = pe.posting_date
	receipt.company_tax_address = pe.company_tax_address
	receipt.append("payment_references", {"payment_entry": pe.name})
	return receipt
