import json

import frappe
from frappe import _
from frappe.utils import flt

try:
	from hrms.overrides.employee_payment_entry import (
		get_payment_entry_for_employee as origin_get_payment_entry_for_employee,
	)

	_hrms_installed = True
except ImportError:
	origin_get_payment_entry_for_employee = None
	_hrms_installed = False
from erpnext.regional.thailand.custom.advance_vat import (
	ADVANCE_FOR,
	ADVANCE_VAT,
	SERVICES,
	validate_advance_vat,
)
from erpnext.regional.thailand.custom.custom_api import CLEAR_UNDUE_TAX, get_thai_tax_settings
from erpnext.regional.thailand.custom.withholding import apply_receive_withholding, validate_receive_rows
from erpnext.regional.thailand.custom.withholding_cert import (
	CERT,
	existing_certificates,
)
from erpnext.regional.thailand.custom.withholding_cert import (
	linked_address as _linked_address,
)

REF_DOCTYPES = ["Purchase Invoice", "Expense Claim", "Journal Entry"]
# what a payment to a supplier / a receipt from a customer settles that names its amount before VAT
PAYABLE_DOCTYPES = ("Purchase Invoice", "Purchase Order")
RECEIVABLE_DOCTYPES = ("Sales Invoice", "Sales Order")


class PaymentEntry:
	@frappe.whitelist()
	def apply_thai_withholding(
		self, tax_withholding_category=None, base_amount=None, withholding_amount=None, based_on="Manual"
	):
		"""Withhold Tax on a receipt from a customer: deductions on the types' Receive Accounts (withholding.py)."""
		return apply_receive_withholding(
			self, tax_withholding_category, base_amount, withholding_amount, based_on
		)

	def before_submit(self):
		if self.is_petty_cash:
			is_petty_cash_paid_from = frappe.get_value(
				"Account",
				self.paid_from,
				"is_petty_cash_account",
			)
			is_petty_cash_paid_to = frappe.get_value(
				"Account",
				self.paid_to,
				"is_petty_cash_account",
			)
			if not (is_petty_cash_paid_from or is_petty_cash_paid_to):
				frappe.throw(
					_(
						"Paid From / Paid To account is not petty cash account, please unselect <b>is petty cash</b> before submit."
					)
				)

	def validate(self):
		super().validate()
		if not self.is_petty_cash and (self.petty_cash_holder or self.petty_cash_holder_name):
			self.update(
				{
					"petty_cash_holder": "",
					"petty_cash_holder_name": "",
				}
			)
		validate_receive_rows(self)
		validate_advance_vat(self)

	def add_tax_gl_entries(self, gl_entries):
		"""The Clear Undue Tax / Advance VAT Due rows move VAT from the undue to the due account and net to zero, so
		they move no money.
		ERPNext gives every tax row not included in the paid amount a paid-account counter-line, and an Actual charge
		cannot be included (validate_inclusive_tax): the pair would show on the bank as Dr and Cr lines that bank
		reconciliation adds to the payment. Build the pair's GL as included, so the bank gets only what moved."""
		pair = [
			d
			for d in self.get("taxes")
			if d.description in (CLEAR_UNDUE_TAX, ADVANCE_VAT) and not d.included_in_paid_amount
		]
		if not pair or round(sum(flt(d.tax_amount) for d in pair), self.precision("paid_amount")):
			return super().add_tax_gl_entries(gl_entries)
		for d in pair:
			d.included_in_paid_amount = 1
		try:
			return super().add_tax_gl_entries(gl_entries)
		finally:
			for d in pair:
				d.included_in_paid_amount = 0

	def get_gl_dict(self, args, account_currency=None, item=None):
		gl_dict = super().get_gl_dict(args, account_currency=account_currency, item=item)
		if item and item.doctype == "Payment Entry":
			is_petty_cash_account = frappe.get_value(
				"Account",
				gl_dict["account"],
				"is_petty_cash_account",
			)
			if is_petty_cash_account:
				gl_dict.update(
					{
						"petty_cash_holder": item.petty_cash_holder,
						"petty_cash_holder_name": item.petty_cash_holder_name,
					}
				)
		return gl_dict


@frappe.whitelist()
def get_payment_entry_for_employee(dt, dn, party_amount=None, bank_account=None, bank_amount=None):
	if not _hrms_installed:
		frappe.throw(_("hrms is required for this feature"))
	pe = origin_get_payment_entry_for_employee(
		dt, dn, party_amount=party_amount, bank_account=bank_account, bank_amount=bank_amount
	)
	doc = frappe.get_doc(dt, dn)
	# Petty Cash
	if doc.is_petty_cash:
		pe.is_petty_cash = doc.is_petty_cash
		pe.petty_cash_holder = doc.petty_cash_holder
		petty_cash_account = frappe.get_value(
			"Account",
			frappe.get_value("Petty Cash Holder", doc.petty_cash_holder, "petty_cash_account"),
			["name", "account_currency"],
			as_dict=1,
		)
		pe.paid_from = petty_cash_account.name
		pe.paid_from_account_currency = petty_cash_account.account_currency
		pe.set_exchange_rate(ref_doc=doc)
		pe.set_amounts()
	return pe


@frappe.whitelist()
def make_withholding_tax_cert(filters, doc):
	"""The manual "Create Withholding Tax Cert" (a fallback: submitting the entry makes its certificates,
	withholding_cert.py)."""
	filters = frappe.parse_json(filters)
	pay = frappe.parse_json(doc)
	# a Payment Entry, or a Journal Entry that withholds (Consider for Tax Withholding)
	voucher_type = (
		pay.get("doctype") if pay.get("doctype") in ("Payment Entry", "Journal Entry") else "Payment Entry"
	)
	frappe.get_doc(voucher_type, pay.get("name")).check_permission("read")
	frappe.has_permission("Withholding Tax Cert", "create", throw=True)
	existing = existing_certificates(voucher_type, pay.get("name"))
	if existing:
		frappe.throw(
			_("{0} {1} already has Withholding Tax Cert {2}: open it from Connections.").format(
				_(voucher_type),
				pay.get("name"),
				", ".join(frappe.utils.get_link_to_form(CERT, name) for name in existing),
			)
		)
	cert = frappe.new_doc("Withholding Tax Cert")
	if voucher_type == "Journal Entry":
		party = next(
			(r for r in pay.get("accounts") or [] if r.get("party_type") == "Supplier" and r.get("party")), {}
		)
		cert.supplier = party.get("party") or ""
	else:
		cert.supplier = pay.get("party_type") == "Supplier" and pay.get("party") or ""
	if cert.supplier:
		supplier = frappe.get_doc("Supplier", cert.supplier)
		cert.supplier_name = supplier.supplier_name
		# the primary address, else the supplier's own address (suppliers often have one address not marked primary)
		cert.supplier_address = supplier.supplier_primary_address or _linked_address(
			"Supplier", cert.supplier
		)
	cert.voucher_type = voucher_type
	cert.voucher_no = pay.get("name")
	cert.company = pay.get("company")
	cert.company_address = filters.get("company_address") or _linked_address(
		"Company", cert.company, own=True
	)
	cert.income_tax_form = filters.get("income_tax_form") or (
		cert.supplier
		and (
			"PND3"
			if frappe.db.get_value("Supplier", cert.supplier, "supplier_type") == "Individual"
			else "PND53"
		)
	)
	cert.date = filters.get("date") or pay.get("posting_date")
	category_forms = set()
	income_totals = {}
	for entry in pay.get("tax_withholding_entries") or []:
		category = entry.get("tax_withholding_category")
		category_data = (
			frappe.db.get_value(
				"Tax Withholding Category",
				category,
				["thai_type_of_income", "thai_income_tax_form"],
				as_dict=True,
			)
			if category
			else None
		)
		category_data = frappe._dict(category_data) if category_data else None
		income_type = category_data.thai_type_of_income if category_data else None
		if not income_type:
			frappe.throw(_("Set Thai Type of Income on Tax Withholding Category {0}").format(category))
		if category_data.thai_income_tax_form:
			category_forms.add(category_data.thai_income_tax_form)
		amount = abs(entry.get("withholding_amount") or 0)
		if not amount:
			continue
		# one certificate line per type of income and rate: advertising 2% and services 3% are both type 5 but
		# are separate lines on PND53, each with its own rate
		key = (income_type, frappe.utils.flt(entry.get("tax_rate")))
		line = income_totals.setdefault(key, {"tax_base": 0, "tax_amount": 0, "kinds": []})
		line["tax_base"] += abs(entry.get("taxable_amount") or 0)
		line["tax_amount"] += amount
		kind = frappe.db.get_value("Tax Withholding Category", category, "category_name") or category
		if kind not in line["kinds"]:
			line["kinds"].append(kind)
	if len(category_forms) > 1:
		frappe.throw(_("All withholding categories on one certificate must use the same Thai tax form."))
	if category_forms:
		category_form = next(iter(category_forms))
		if cert.income_tax_form and cert.income_tax_form != category_form:
			frappe.throw(_("The selected income tax form does not match the withholding category."))
		cert.income_tax_form = category_form
	for (income_type, rate), amounts in income_totals.items():
		base = amounts["tax_base"]
		cert.append(
			"withholding_tax_items",
			{
				"type_of_income": income_type,
				"description": ", ".join(amounts["kinds"]),
				"tax_base": base,
				"tax_rate": rate or (amounts["tax_amount"] / base * 100 if base else 0),
				"tax_amount": amounts["tax_amount"],
			},
		)
	return cert


@frappe.whitelist()
def get_payment_withholding_base(doc):
	"""Withholding base of a payment to a supplier, before VAT: each Purchase Invoice / Purchase Order paid contributes
	its net total (company currency) times the share allocated; an unallocated (advance) amount counts as it is, or,
	when Advance For says Services, without the VAT it holds (the VAT of a service advance is not withheld on)."""
	payment = frappe.parse_json(doc)
	base = 0
	for reference in payment.get("references") or []:
		if reference.get("reference_doctype") not in PAYABLE_DOCTYPES or not reference.get("reference_name"):
			continue
		source = frappe.get_doc(reference.get("reference_doctype"), reference.get("reference_name"))
		source.check_permission("read")
		total = frappe.utils.flt(source.grand_total)
		if total:
			base += source.base_net_total * abs(frappe.utils.flt(reference.get("allocated_amount")) / total)
	unallocated = frappe.utils.flt(payment.get("unallocated_amount")) * frappe.utils.flt(
		payment.get("target_exchange_rate") or 1
	)
	if unallocated and payment.get(ADVANCE_FOR) == SERVICES:
		settings = get_thai_tax_settings(payment.get("company"))
		rate = frappe.utils.flt(frappe.get_cached_value("Account", settings.purchase_tax_account, "tax_rate"))
		unallocated = unallocated * 100 / (100 + rate)
	base += unallocated
	return frappe.utils.flt(base, 2)


@frappe.whitelist()
def get_payment_entry_tax_withholding_categories(doc):
	"""Build core withholding-category rows from item-level invoice (and order) defaults."""
	payment = frappe.parse_json(doc)
	if payment.get("payment_type") not in ("Pay", "Receive"):
		return []

	# the invoices, and the orders an advance is paid on, of the side
	allowed_doctypes = PAYABLE_DOCTYPES if payment.payment_type == "Pay" else RECEIVABLE_DOCTYPES
	category_totals = {}
	for reference in payment.get("references") or []:
		if reference.reference_doctype not in allowed_doctypes or not reference.reference_name:
			continue
		allowed_doctype = reference.reference_doctype

		invoice = frappe.get_doc(allowed_doctype, reference.reference_name)
		invoice.check_permission("read")
		if invoice.company != payment.company:
			frappe.throw(_("All references must belong to the selected company."))
		if invoice.docstatus != 1:
			continue

		total_amount = frappe.utils.flt(reference.get("total_amount"))
		allocated_amount = frappe.utils.flt(reference.get("allocated_amount"))
		if not total_amount or not allocated_amount:
			continue
		allocation_ratio = abs(allocated_amount / total_amount)

		for row in invoice.items:
			category = row.get("tax_withholding_category")
			if not (row.get("apply_tds") and category):
				item = frappe.get_cached_doc("Item", row.item_code)
				category = (
					item.purchase_tax_withholding_category
					if allowed_doctype in PAYABLE_DOCTYPES
					else item.sales_tax_withholding_category
				)
			if not category:
				continue

			base_amount = row.get("base_net_amount") or row.get("base_amount") or 0
			category_totals[category] = category_totals.get(category, 0) + base_amount * allocation_ratio

	return [
		{"tax_withholding_category": category, "taxable_amount": amount}
		for category, amount in sorted(category_totals.items())
	]


def reconcile_undue_tax(doc, method):
	"""If bs_reconcile is installed, unreconcile undue tax gls"""
	vouchers = [doc.name] + [r.reference_name for r in doc.references]
	reconcile_undue_tax_gls(vouchers, doc.company)


def reconcile_undue_tax_gls(vouchers, company, unreconcile=False):
	"""Only if bs_reconcile app is install, reconcile/unreconcile undue tax gl entries"""
	if "bs_reconcile" not in frappe.get_installed_apps():
		return
	try:
		from bs_reconcile.balance_sheet_reconciliation import utils
	except ImportError:
		pass
	tax = get_thai_tax_settings(company)
	undue_taxes = [tax.purchase_tax_account_undue, tax.sales_tax_account_undue]
	gl_entries = utils.get_gl_entries_by_vouchers(vouchers)
	undue_tax_gls = list(filter(lambda x: x.account in undue_taxes, gl_entries))
	if unreconcile:
		utils.unreconcile_gl(undue_tax_gls)
	else:
		utils.reconcile_gl(undue_tax_gls)


def update_sales_billing_outstanding_amount(doc, method):
	# Document: Payment Entry
	total_outstanding_amount = 0
	if not doc.sales_billing:
		return
	bill = frappe.get_doc("Sales Billing", doc.sales_billing)
	for bill_line in bill.sales_billing_line:
		invoice = frappe.get_doc("Sales Invoice", bill_line.sales_invoice)
		bill_line.outstanding_amount = invoice.outstanding_amount
		total_outstanding_amount += invoice.outstanding_amount
	bill.total_outstanding_amount = total_outstanding_amount
	# Status closed
	bill.closed = 0 if bill.total_outstanding_amount else 1
	bill.save()


@frappe.whitelist()
def get_outstanding_reference_documents(args, validate=False):
	from erpnext.accounts.doctype.payment_entry.payment_entry import (
		get_outstanding_reference_documents as erpnext_get_outstanding,
	)

	data = erpnext_get_outstanding(args, validate)
	# Filter by Sales billing / Purchase Billing
	args = frappe._dict(json.loads(args))
	invoices = []
	if args.sales_billing:
		sales_billing = frappe.get_doc("Sales Billing", args.sales_billing)
		invoices = [x.sales_invoice for x in sales_billing.sales_billing_line]
	elif args.purchase_billing:
		purchase_billing = frappe.get_doc("Purchase Billing", args.purchase_billing)
		invoices = [x.purchase_invoice for x in purchase_billing.purchase_billing_line]
	if invoices:
		data = filter(lambda x: x.get("voucher_no") in invoices, data)
	return data
