"""VAT rules of the Thai Journal Entry (owner, 22-10-2026).
A customer's service invoice, whose output VAT waits on the undue account until it is paid, is received with a Payment
Entry only: a Journal Entry that settles it is refused on validate.
Offline: the Thai Tax Settings, the invoices' GL and ERPNext's own Journal Entry validation are stand-ins."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry as ERPNextJournalEntry
from erpnext.regional.thailand.custom import journal_entry
from erpnext.regional.thailand.custom.journal_entry import JournalEntry

CD = "CD"
SETTINGS = SimpleNamespace(
	company_accounts=[
		SimpleNamespace(
			company=CD,
			sales_tax_account="2135-00",
			sales_tax_account_undue="2135-01",
			purchase_tax_account="1154-00",
			purchase_tax_account_undue="1154-01",
		)
	]
)
# what each invoice posted on 2135-01 (credit - debit), and whether it is a credit note
UNDUE = {"SVC-1": 700, "GOODS-1": 0, "CN-1": -700}
RETURNS = {"CN-1": 1}


def _line(account, debit=0, credit=0, reference=None, **extra):
	return frappe._dict(
		account=account,
		debit=debit,
		credit=credit,
		reference_type="Sales Invoice" if reference else None,
		reference_name=reference,
		**extra,
	)


def _journal(*lines, **fields):
	journal = frappe._dict(
		doctype="Journal Entry", company=CD, voucher_type="Journal Entry", accounts=list(lines)
	)
	journal.update(fields)
	return journal


def _receipt(invoice, amount=10700):
	return _journal(_line("1113-01", debit=amount), _line("1130-01", credit=amount, reference=invoice))


def _sql(query, values):
	return [[UNDUE.get(values[0])]]


def _throw(message, *args, **kwargs):
	raise frappe.ValidationError(message)


class _Stubs:
	def setUp(self):
		self.sql = Mock(side_effect=_sql)
		db = SimpleNamespace(sql=self.sql, get_value=lambda doctype, name, field: RETURNS.get(name, 0))
		for target in (
			patch.object(journal_entry.frappe, "throw", side_effect=_throw),
			patch.object(journal_entry.frappe, "db", db),
			patch.object(journal_entry.frappe, "get_cached_doc", return_value=SETTINGS),
			patch.object(journal_entry, "get_link_to_form", side_effect=lambda doctype, name: name),
			patch.object(
				journal_entry, "fmt_money", side_effect=lambda amount, precision=None: f"{amount:,.2f}"
			),
		):
			target.start()
			self.addCleanup(target.stop)


class TestUndueVatSettlement(_Stubs, unittest.TestCase):
	def test_a_journal_receiving_a_service_invoice_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_undue_vat_settlement(_receipt("SVC-1"))
		message = str(refused.exception)
		self.assertIn("Sales Invoice SVC-1 has output VAT of 700.00 that is not due yet (2135-01)", message)
		self.assertIn("Receive it with a Payment Entry", message)

	def test_a_part_payment_is_refused_too(self):
		with self.assertRaises(frappe.ValidationError):
			journal_entry.validate_undue_vat_settlement(_receipt("SVC-1", amount=5000))

	def test_a_goods_invoice_may_be_received_by_journal(self):
		journal_entry.validate_undue_vat_settlement(_receipt("GOODS-1"))

	def test_a_journal_refunding_a_service_credit_note_is_refused(self):
		refund = _journal(_line("1130-01", debit=10700, reference="CN-1"), _line("1111-00", credit=10700))
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_undue_vat_settlement(refund)
		self.assertIn("Refund it with a Payment Entry", str(refused.exception))

	def test_a_line_that_adds_to_what_the_invoice_is_owed_settles_nothing(self):
		journal_entry.validate_undue_vat_settlement(
			_journal(_line("1130-01", debit=10700, reference="SVC-1"), _line("1113-01", credit=10700))
		)

	def test_journals_without_invoice_lines_read_nothing(self):
		journal_entry.validate_undue_vat_settlement(
			_journal(_line("5320-02", debit=1000), _line("1111-00", credit=1000))
		)
		self.sql.assert_not_called()

	def test_system_journals_and_reversals_are_left_alone(self):
		journal_entry.validate_undue_vat_settlement(
			_journal(*_receipt("SVC-1").accounts, voucher_type="Exchange Gain Or Loss", is_system_generated=1)
		)
		journal_entry.validate_undue_vat_settlement(_journal(*_receipt("SVC-1").accounts, reversal_of="JV-1"))
		self.sql.assert_not_called()

	def test_a_company_without_thai_tax_settings_is_left_alone(self):
		journal_entry.validate_undue_vat_settlement(_journal(*_receipt("SVC-1").accounts, company="Other"))
		self.sql.assert_not_called()


class TestJournalEntryValidate(_Stubs, unittest.TestCase):
	def test_saving_the_journal_runs_the_rule(self):
		"""The Thai Journal Entry (override_doctype_class) refuses the receipt after ERPNext's own validation."""
		journal = JournalEntry.__new__(JournalEntry)
		journal.__dict__.update(_receipt("SVC-1"))
		for line in journal.accounts:
			line.update(is_petty_cash_account=0, petty_cash_holder=None, petty_cash_holder_name=None)
		with (
			patch.object(ERPNextJournalEntry, "validate") as erpnext_validate,
			patch.object(journal_entry, "validate_receive_rows"),
			self.assertRaises(frappe.ValidationError),
		):
			journal.validate()
		erpnext_validate.assert_called_once()


if __name__ == "__main__":
	unittest.main()
