import frappe
from frappe import _
from frappe.utils import flt, fmt_money, get_link_to_form

from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry

from .payment_entry import reconcile_undue_tax_gls
from .withholding import apply_receive_withholding, validate_receive_rows


class JournalEntry(JournalEntry):
	@frappe.whitelist()
	def apply_thai_withholding(
		self, tax_withholding_category=None, base_amount=None, withholding_amount=None, based_on="Manual"
	):
		"""Withhold Tax on a journal that credits a customer: debit lines on the types' Receive Accounts
		(withholding.py)."""
		return apply_receive_withholding(
			self, tax_withholding_category, base_amount, withholding_amount, based_on
		)

	def before_submit(self):
		super().before_submit()
		for line in self.accounts:
			is_petty_cash_account = frappe.get_value(
				"Account",
				line.account,
				"is_petty_cash_account",
			)
			if line.is_petty_cash_account != is_petty_cash_account:
				frappe.throw(
					_(
						"<b>Is Petty Cash Account</b> in account and journal entry account not same, please check it before submit."
					)
				)

	def validate(self):
		super().validate()
		for line in self.accounts:
			if not line.is_petty_cash_account and (
				line.petty_cash_holder or line.petty_cash_holder_name
			):
				line.update(
					{
						"petty_cash_holder": "",
						"petty_cash_holder_name": "",
					}
				)
		validate_receive_rows(self)
		validate_undue_vat_settlement(self)

	def get_gl_dict(self, args, account_currency=None, item=None):
		gl_dict = super().get_gl_dict(args, account_currency=account_currency, item=item)
		if item and item.doctype == "Journal Entry Account":
			gl_dict.update(
				{
					"petty_cash_holder": item.petty_cash_holder,
					"petty_cash_holder_name": item.petty_cash_holder_name,
				}
			)
		return gl_dict


def reconcile_undue_tax(jv, method):
	"""If bs_reconcile is installed, reconcile undue tax gls"""
	if jv.for_payment:
		pay = frappe.get_doc("Payment Entry", jv.for_payment)
		vouchers = [jv.name, pay.name] + [r.reference_name for r in pay.references]
		reconcile_undue_tax_gls(vouchers, pay.company)


def company_tax_accounts(company):
	"""The company's row of Thai Tax Settings (None for a company that has none)."""
	settings = frappe.get_cached_doc("Thai Tax Settings")
	return next((row for row in settings.company_accounts if row.company == company), None)


def validate_undue_vat_settlement(doc):
	"""Owner decision 22-10-2026: a customer's service invoice is received with a Payment Entry only. Its output VAT
	waits on the undue account (Thailand Tax (Service)) until the payment moves it to output VAT and issues the Sales
	Tax Invoice; a Journal Entry that settles the invoice would leave the VAT undue and issue no tax invoice."""
	# exchange gain or loss and common party journals are made by the system; a reversal restores what it undoes
	if doc.get("is_system_generated") or doc.get("reversal_of"):
		return
	settled = {}
	for row in doc.get("accounts"):
		if row.reference_type == "Sales Invoice" and row.reference_name:
			settled[row.reference_name] = (
				settled.get(row.reference_name, 0) + flt(row.credit) - flt(row.debit)
			)
	if not settled:
		return
	accounts = company_tax_accounts(doc.company)
	if not (accounts and accounts.sales_tax_account_undue):
		return
	for invoice, amount in settled.items():
		is_return = frappe.db.get_value("Sales Invoice", invoice, "is_return")
		# a credit lowers what an invoice is owed, a debit what a credit note owes back
		if (-amount if is_return else amount) <= 0:
			continue
		undue = undue_output_vat(invoice, accounts.sales_tax_account_undue)
		if abs(undue) < 0.005:
			continue
		if is_return:
			message = _(
				"Credit Note {0} reverses output VAT of {1} that is not due yet ({2}). Refund it with a Payment "
				"Entry, which reverses the output VAT and issues the Sales Tax Invoice. A Journal Entry would leave "
				"the VAT undue and issue no tax invoice."
			)
		else:
			message = _(
				"Sales Invoice {0} has output VAT of {1} that is not due yet ({2}): a service invoice's VAT is due "
				"when it is paid. Receive it with a Payment Entry, which moves the VAT to output VAT and issues the "
				"Sales Tax Invoice. A Journal Entry would leave the VAT undue and issue no tax invoice."
			)
		frappe.throw(
			message.format(
				get_link_to_form("Sales Invoice", invoice),
				fmt_money(abs(undue), precision=2),
				accounts.sales_tax_account_undue,
			),
			title=_("Use a Payment Entry"),
		)


def undue_output_vat(invoice, undue_account):
	"""What the invoice posted on the undue output VAT account: credit - debit (negative for a credit note)."""
	result = frappe.db.sql(
		"""select sum(credit - debit) from `tabGL Entry`
		where voucher_type = 'Sales Invoice' and voucher_no = %s and account = %s and is_cancelled = 0""",
		(invoice, undue_account),
	)
	return flt(result[0][0]) if result else 0
