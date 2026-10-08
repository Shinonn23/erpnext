import json

import frappe
from frappe import _

try:
	from hrms.overrides.employee_payment_entry import (
		get_payment_entry_for_employee as origin_get_payment_entry_for_employee,
	)

	_hrms_installed = True
except ImportError:
	origin_get_payment_entry_for_employee = None
	_hrms_installed = False
from erpnext.regional.thailand.custom.custom_api import get_thai_tax_settings

REF_DOCTYPES = ["Purchase Invoice", "Expense Claim", "Journal Entry"]


class PaymentEntry:
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
def get_payment_entry_for_employee(
	dt, dn, party_amount=None, bank_account=None, bank_amount=None
):
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
	filters = frappe.parse_json(filters)
	pay = frappe.parse_json(doc)
	cert = frappe.new_doc("Withholding Tax Cert")
	cert.supplier = pay.get("party_type") == "Supplier" and pay.get("party") or ""
	if cert.supplier:
		supplier = frappe.get_doc("Supplier", cert.supplier)
		cert.supplier_name = supplier.supplier_name
		cert.supplier_address = supplier.supplier_primary_address
	cert.voucher_type = "Payment Entry"
	cert.voucher_no = pay.get("name")
	cert.company = pay.get("company")
	cert.company_address = filters.get("company_address")
	cert.income_tax_form = filters.get("income_tax_form")
	cert.date = filters.get("date")
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
		income_totals.setdefault(income_type, {"tax_base": 0, "tax_amount": 0})
		income_totals[income_type]["tax_base"] += abs(entry.get("taxable_amount") or 0)
		income_totals[income_type]["tax_amount"] += amount
	if len(category_forms) > 1:
		frappe.throw(_("All withholding categories on one certificate must use the same Thai tax form."))
	if category_forms:
		category_form = next(iter(category_forms))
		if cert.income_tax_form and cert.income_tax_form != category_form:
			frappe.throw(_("The selected income tax form does not match the withholding category."))
		cert.income_tax_form = category_form
	for income_type, amounts in income_totals.items():
		base = amounts["tax_base"]
		cert.append(
			"withholding_tax_items",
			{
				"type_of_income": income_type,
				"tax_base": base,
				"tax_rate": amounts["tax_amount"] / base * 100 if base else 0,
				"tax_amount": amounts["tax_amount"],
			},
		)
	return cert


@frappe.whitelist()
def get_payment_entry_tax_withholding_categories(doc):
	"""Build core withholding-category rows from item-level invoice defaults."""
	payment = frappe.parse_json(doc)
	if payment.get("payment_type") not in ("Pay", "Receive"):
		return []

	allowed_doctype = "Purchase Invoice" if payment.payment_type == "Pay" else "Sales Invoice"
	category_totals = {}
	for reference in payment.get("references") or []:
		if reference.reference_doctype != allowed_doctype or not reference.reference_name:
			continue

		invoice = frappe.get_doc(allowed_doctype, reference.reference_name)
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
					if allowed_doctype == "Purchase Invoice"
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
