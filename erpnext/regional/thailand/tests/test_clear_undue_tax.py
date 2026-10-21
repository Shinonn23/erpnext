"""Clear Undue Tax on a Payment Entry: the pair of tax rows that moves undue VAT to due VAT nets to zero, so it posts
no bank (paid account) lines and bank reconciliation sees only the money that moved.
Offline: frappe.db, the Thai Tax Settings, the document and ERPNext's own GL builder are stand-ins."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

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


if __name__ == "__main__":
	unittest.main()
