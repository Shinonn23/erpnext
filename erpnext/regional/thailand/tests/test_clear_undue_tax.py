"""Clear Undue Tax on a Payment Entry: the pair of tax rows that moves undue VAT to due VAT nets to zero, so it posts
no bank (paid account) lines and bank reconciliation sees only the money that moved; a refund of a credit note moves
the VAT back and makes a negative tax invoice.
Offline: frappe.db, the Thai Tax Settings and the document's storage are stand-ins. TestClearUndueTaxPosting runs
ERPNext's own Payment Entry tax code (calculate_taxes, add_tax_gl_entries, toggle_debit_credit_if_negative)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import frappe

import erpnext
from erpnext.accounts.doctype.payment_entry import payment_entry as erpnext_payment_entry
from erpnext.accounts.general_ledger import toggle_debit_credit_if_negative
from erpnext.regional.thailand.custom import custom_api
from erpnext.regional.thailand.custom.payment_entry import PaymentEntry


def _receipt():
	doc = MagicMock()
	doc.company = "CD"
	doc.payment_type = "Receive"
	doc.has_purchase_tax_invoice = 0
	doc.total_allocated_amount = 53500
	doc.references = [
		SimpleNamespace(
			reference_doctype="Sales Invoice",
			reference_name="SINV-1",
			allocated_amount=53500,
			total_amount=53500,
		)
	]
	doc.get_doc_before_save.return_value = None
	rows = []
	doc.append.side_effect = lambda table, row: rows.append(row)
	return doc, rows


class TestClearUndueTaxRows(unittest.TestCase):
	def test_the_pair_moves_the_undue_vat_to_due_vat(self):
		doc, rows = _receipt()
		settings = SimpleNamespace(sales_tax_account_undue="2135-01", sales_tax_account="2135-00")
		with (
			patch.object(custom_api.frappe, "flags", custom_api.frappe._dict()),
			patch.object(
				custom_api.frappe, "db", MagicMock(get_all=Mock(return_value=[{"account": "2135-01"}]))
			),
			patch.object(custom_api, "get_thai_tax_settings", return_value=settings),
			patch.object(custom_api, "get_undue_tax", return_value=(3500, 50000, "2135-01", "2135-00")),
		):
			custom_api.clear_invoice_undue_tax(doc, None)
		self.assertEqual([r["description"] for r in rows], [custom_api.CLEAR_UNDUE_TAX] * 2)
		self.assertEqual([r["account_head"] for r in rows], ["2135-01", "2135-00"])
		self.assertEqual([r["tax_amount"] for r in rows], [-3500, 3500])
		# an Actual charge cannot be included in the paid amount (validate_inclusive_tax)
		self.assertFalse(any(r.get("included_in_paid_amount") for r in rows))
		doc.save.assert_called_once()


class _ERPNextPaymentEntry:
	"""ERPNext's add_tax_gl_entries, reduced to what decides the bank lines: a paid-account counter-line for every
	tax row not included in the paid amount."""

	def add_tax_gl_entries(self, gl_entries):
		for d in self.taxes:
			gl_entries.append(("tax", d.account_head, d.tax_amount))
			if not d.included_in_paid_amount:
				gl_entries.append(("bank", "1113-01", -d.tax_amount))

	def get(self, key):
		return getattr(self, key)

	def precision(self, fieldname):
		return 2


class _Receipt(PaymentEntry, _ERPNextPaymentEntry):
	def __init__(self, taxes):
		self.taxes = taxes


def _row(description, account, amount):
	return SimpleNamespace(
		description=description, account_head=account, tax_amount=amount, included_in_paid_amount=0
	)


class TestClearUndueTaxGl(unittest.TestCase):
	def test_the_pair_posts_no_bank_lines(self):
		pair = [_row("Clear Undue Tax", "2135-01", -3500), _row("Clear Undue Tax", "2135-00", 3500)]
		gl = []
		_Receipt(pair).add_tax_gl_entries(gl)
		self.assertEqual(gl, [("tax", "2135-01", -3500), ("tax", "2135-00", 3500)])
		# the rows themselves stay as saved
		self.assertEqual([d.included_in_paid_amount for d in pair], [0, 0])

	def test_other_tax_rows_keep_their_bank_lines(self):
		rows = [
			_row("Clear Undue Tax", "2135-01", -3500),
			_row("Clear Undue Tax", "2135-00", 3500),
			_row("Bank charge", "5310-00", 25),
		]
		gl = []
		_Receipt(rows).add_tax_gl_entries(gl)
		self.assertEqual([line for line in gl if line[0] == "bank"], [("bank", "1113-01", -25)])

	def test_rows_that_do_not_net_to_zero_keep_their_bank_lines(self):
		rows = [_row("Clear Undue Tax", "2135-01", -3500), _row("Clear Undue Tax", "2135-00", 3000)]
		gl = []
		_Receipt(rows).add_tax_gl_entries(gl)
		self.assertEqual(len([line for line in gl if line[0] == "bank"]), 2)


SETTINGS = SimpleNamespace(
	sales_tax_account="2135-00",
	sales_tax_account_undue="2135-01",
	purchase_tax_account="1154-00",
	purchase_tax_account_undue="1154-01",
)
INCOME, EXPENSE = "4100-01", "5320-02"


class _TaxRow(frappe._dict):
	def precision(self, fieldname, parentfield=None):
		return 2


class _Payment(PaymentEntry, erpnext_payment_entry.PaymentEntry):
	"""ERPNext's Payment Entry with the Thai extension, composed as extend_doctype_class does, kept in memory."""

	def __init__(self, payment_type, party_type, reference, has_purchase_tax_invoice=0):
		self.__dict__.update(
			company="CD",
			payment_type=payment_type,
			party_type=party_type,
			party="PARTY",
			paid_from="1111-00",
			paid_to="1130-01",
			paid_from_account_currency="THB",
			paid_to_account_currency="THB",
			source_exchange_rate=1,
			target_exchange_rate=1,
			transaction_exchange_rate=1,
			cost_center="Main - CD",
			paid_amount_after_tax=abs(reference.allocated_amount),
			has_purchase_tax_invoice=has_purchase_tax_invoice,
			total_allocated_amount=reference.allocated_amount,
			references=[reference],
			taxes=[],
		)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def append(self, table, row):
		row = _TaxRow(row, idx=len(self.taxes) + 1, rate=0, included_in_paid_amount=0, currency="THB")
		self.taxes.append(row)
		return row

	def precision(self, fieldname, parentfield=None):
		return 2

	def get_doc_before_save(self):
		return None

	def save(self):
		pass

	def get_gl_dict(self, args, account_currency=None, item=None):
		return frappe._dict(args)


def _ref(doctype, amount):
	return SimpleNamespace(
		reference_doctype=doctype, reference_name="INV-1", allocated_amount=amount, total_amount=amount
	)


def _gl(account, debit=0, credit=0):
	return {"account": account, "debit": debit, "credit": credit}


def _report_type(doctype, account, fieldname):
	return "Profit and Loss" if account in (INCOME, EXPENSE) else "Balance Sheet"


def _post(payment, invoice_gl):
	"""Save the payment (the pair) and build its tax GL as ERPNext submits it: {account: (debit, credit)}."""
	with (
		patch.object(custom_api.frappe, "flags", frappe._dict()),
		patch.object(custom_api.frappe, "db", SimpleNamespace(get_all=Mock(return_value=invoice_gl))),
		patch.object(custom_api.frappe, "get_cached_value", side_effect=_report_type),
		patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS),
		# flt(x, precision) rounds by the System Settings' method
		patch.object(frappe, "get_system_settings", return_value=None, create=True),
		patch.object(erpnext_payment_entry, "get_account_currency", return_value="THB"),
		patch.object(erpnext, "get_company_currency", return_value="THB"),
	):
		custom_api.clear_invoice_undue_tax(payment, None)
		gl = []
		payment.add_tax_gl_entries(gl)
	toggle_debit_credit_if_negative(gl)
	return {d.account: (d.debit, d.credit) for d in gl}


# each invoice's own GL, as ERPNext stores it
SERVICE_INVOICE = [_gl("1130-01", debit=53500), _gl("2135-01", credit=3500), _gl(INCOME, credit=50000)]
CREDIT_NOTE = [_gl("1130-01", credit=10700), _gl("2135-01", debit=700), _gl(INCOME, debit=10000)]
SUPPLIER_INVOICE = [_gl("2120-01", credit=10700), _gl("1154-01", debit=700), _gl(EXPENSE, debit=10000)]
DEBIT_NOTE = [_gl("2120-01", debit=10700), _gl("1154-01", credit=700), _gl(EXPENSE, credit=10000)]


class TestClearUndueTaxPosting(unittest.TestCase):
	def test_a_receipt_moves_the_undue_output_vat_to_output_vat(self):
		payment = _Payment("Receive", "Customer", _ref("Sales Invoice", 53500))
		self.assertEqual(_post(payment, SERVICE_INVOICE), {"2135-01": (3500, 0), "2135-00": (0, 3500)})
		self.assertEqual(
			[(d.account_head, d.tax_amount) for d in payment.taxes], [("2135-01", -3500), ("2135-00", 3500)]
		)
		self.assertEqual(payment.tax_base_amount, 50000)

	def test_refunding_a_credit_note_reverses_the_output_vat(self):
		# D-ACCOUNTS-01: the refund posted Dr 2135-01 / Cr 2135-00, the receipt's direction
		payment = _Payment("Pay", "Customer", _ref("Sales Invoice", -10700))
		self.assertEqual(_post(payment, CREDIT_NOTE), {"2135-00": (700, 0), "2135-01": (0, 700)})
		self.assertEqual(payment.tax_base_amount, 10000)

	def test_paying_a_supplier_moves_the_undue_input_vat_to_input_vat(self):
		payment = _Payment("Pay", "Supplier", _ref("Purchase Invoice", 10700), has_purchase_tax_invoice=1)
		self.assertEqual(_post(payment, SUPPLIER_INVOICE), {"1154-01": (0, 700), "1154-00": (700, 0)})
		self.assertEqual(
			[(d.account_head, d.tax_amount) for d in payment.taxes], [("1154-01", -700), ("1154-00", 700)]
		)

	def test_a_supplier_refunding_a_debit_note_reverses_the_input_vat(self):
		payment = _Payment(
			"Receive", "Supplier", _ref("Purchase Invoice", -10700), has_purchase_tax_invoice=1
		)
		self.assertEqual(_post(payment, DEBIT_NOTE), {"1154-00": (0, 700), "1154-01": (700, 0)})


def _tax_invoice_of(payment, account, debit=0, credit=0):
	"""The tax invoice create_tax_invoice_on_gl_tax makes for the payment's GL row on a due VAT account."""
	made = {}

	def create(doc, doctype, base_amount, tax_amount, voucher, split_tax_invoice=False):
		made.update(doctype=doctype, base=base_amount, tax=tax_amount)
		return [Mock()]

	gl_entry = frappe._dict(
		voucher_type="Payment Entry",
		voucher_no="PAY-1",
		company="CD",
		account=account,
		debit=debit,
		credit=credit,
		flags=frappe._dict(),
	)
	with (
		patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS),
		patch.object(custom_api.frappe, "get_doc", return_value=payment),
		patch.object(custom_api.frappe, "get_cached_value", return_value=7),
		patch.object(custom_api, "create_tax_invoice", side_effect=create),
		patch.object(custom_api, "update_voucher_tinv", side_effect=lambda doctype, voucher, tinv: tinv),
	):
		custom_api.create_tax_invoice_on_gl_tax(gl_entry, None)
	return made


def _paid(payment_type, party_type, base):
	return frappe._dict(
		doctype="Payment Entry",
		docstatus=1,
		payment_type=payment_type,
		party_type=party_type,
		tax_base_amount=base,
	)


class TestClearUndueTaxInvoice(unittest.TestCase):
	def test_a_receipt_makes_a_sales_tax_invoice(self):
		made = _tax_invoice_of(_paid("Receive", "Customer", 50000), "2135-00", credit=3500)
		self.assertEqual(made, {"doctype": "Sales Tax Invoice", "base": 50000, "tax": 3500})

	def test_a_refund_to_a_customer_makes_a_negative_sales_tax_invoice(self):
		made = _tax_invoice_of(_paid("Pay", "Customer", 10000), "2135-00", debit=700)
		self.assertEqual(made, {"doctype": "Sales Tax Invoice", "base": -10000, "tax": -700})

	def test_paying_a_supplier_makes_a_purchase_tax_invoice(self):
		made = _tax_invoice_of(_paid("Pay", "Supplier", 10000), "1154-00", debit=700)
		self.assertEqual(made, {"doctype": "Purchase Tax Invoice", "base": 10000, "tax": 700})

	def test_a_refund_from_a_supplier_makes_a_negative_purchase_tax_invoice(self):
		made = _tax_invoice_of(_paid("Receive", "Supplier", 10000), "1154-00", credit=700)
		self.assertEqual(made, {"doctype": "Purchase Tax Invoice", "base": -10000, "tax": -700})


if __name__ == "__main__":
	unittest.main()
