# Copyright (c) 2024, Ecosoft and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import flt


class PurchaseBilling(Document):
	def validate(self):
		invoices = [i.purchase_invoice for i in self.purchase_billing_line]
		if len(invoices) > len(list(set(invoices))):
			frappe.throw(_("Please do not select same Purchase Invoice more than once!"))
		total_outstanding_amount = sum(i.outstanding_amount for i in self.purchase_billing_line)
		total_billing_amount = sum(i.grand_total for i in self.purchase_billing_line)
		self.total_outstanding_amount = total_outstanding_amount
		self.total_billing_amount = total_billing_amount
		self.set_branch_address()

	def set_branch_address(self):
		"""One billing, one company branch (each branch files its own VAT): the invoices listed must be of one branch,
		which names the billing."""
		from erpnext.regional.thailand.custom.naming_branch import billing_line_addresses

		addresses = billing_line_addresses(self)
		if len(addresses) > 1:
			frappe.throw(
				_(
					"Invoices of different company branches ({0}) cannot be on one billing. "
					"Please make a separate billing for each branch."
				).format(", ".join(sorted(addresses)))
			)
		if addresses:
			self.company_tax_address = addresses[0]


@frappe.whitelist()
def get_due_billing(supplier=None, currency=None, tax_type=None, threshold_type=None, threshold_date=None):
	if not (supplier, currency, tax_type, threshold_date):
		return {}
	filters = {
		"supplier": supplier,
		"currency": currency,
		"docstatus": 1,
		"outstanding_amount": ["!=", 0],
	}

	if tax_type:
		filters["taxes_and_charges"] = tax_type
	if threshold_type == "Due Date":
		filters["due_date"] = ["<=", threshold_date]
	if threshold_type == "Invoice Date":
		filters["posting_date"] = ["<=", threshold_date]
	if threshold_type == "Supplier Invoice Date":
		filters["bill_date"] = ["<=", threshold_date]
	invoices = frappe.get_list(
		"Purchase Invoice",
		filters=filters,
		fields=[
			"name",
			"posting_date",
			"due_date",
			"po_no",
			"grand_total",
			"outstanding_amount",
			"payment_terms_template",
		],
	)

	# Exclude invoices which are in another submitted, open Purchase Billing. A cancelled billing (docstatus 2)
	# and a closed one (all its invoices paid) hold nothing back, so cancelling a billing frees its invoices.
	PurchaseBilling = DocType("Purchase Billing")
	PurchaseBillingLine = DocType("Purchase Billing Line")

	excluded_invoices = (
		frappe.qb.from_(PurchaseBilling)
		.join(PurchaseBillingLine)
		.on(PurchaseBilling.name == PurchaseBillingLine.parent)
		.where(PurchaseBilling.docstatus == 1)
		.where(PurchaseBilling.closed == 0)
		.select(PurchaseBillingLine.purchase_invoice)
	).run()

	excluded_invoices = [d[0] for d in excluded_invoices]
	invoices = [inv for inv in invoices if inv["name"] not in excluded_invoices]
	return invoices


def update_purchase_billing_outstanding_amount(doc, method=None):
	"""Document: Payment Entry (on submit and on cancel). Refreshes the outstanding amounts of the Purchase Billing
	the entry names and of every other open one that lists an invoice the entry pays; a billing whose invoices are
	all paid is closed, and opens again when the payment is cancelled."""
	names = {doc.get("purchase_billing")} if doc.get("purchase_billing") else set()
	invoices = [
		ref.reference_name
		for ref in doc.get("references") or []
		if ref.reference_doctype == "Purchase Invoice"
	]
	if invoices:
		names |= set(
			frappe.get_all(
				"Purchase Billing Line",
				filters={"purchase_invoice": ["in", invoices], "parenttype": "Purchase Billing"},
				pluck="parent",
			)
		)
	for name in sorted(names):
		if frappe.db.get_value("Purchase Billing", name, "docstatus") != 1:
			continue
		bill = frappe.get_doc("Purchase Billing", name)
		total_outstanding_amount, changed = 0, False
		for bill_line in bill.purchase_billing_line:
			outstanding = flt(
				frappe.db.get_value("Purchase Invoice", bill_line.purchase_invoice, "outstanding_amount")
			)
			changed = changed or flt(bill_line.outstanding_amount) != outstanding
			bill_line.outstanding_amount = outstanding
			total_outstanding_amount += outstanding
		closed = 0 if total_outstanding_amount else 1
		if not changed and int(bill.closed or 0) == closed:
			continue
		bill.total_outstanding_amount = total_outstanding_amount
		bill.closed = closed
		# without the acting user's rights: whoever approves the payment may not write billings
		bill.save(ignore_permissions=True)


# the name this function had while it was not hooked to anything
update_sales_billing_outstanding_amount = update_purchase_billing_outstanding_amount
