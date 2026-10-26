"""VAT rules of the Thai Journal Entry (owner, 22-10-2026).
A customer's service invoice, whose output VAT waits on the undue account until it is paid, is received with a Payment
Entry only: a Journal Entry that settles it is refused on validate. VAT a Journal Entry posts to output or input VAT,
either way, needs Create Tax Invoice, and input VAT the supplier's tax invoice number and date, before submit; the
month-end entry of Entry Type VAT Closing moves both balances to VAT payable / receivable and needs none, and only an
Accounts Manager submits it. Reversals and opening balances are left alone.
Offline: the Thai Tax Settings, the invoices' GL and ERPNext's own Journal Entry validation are stand-ins."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry as ERPNextJournalEntry
from erpnext.regional.thailand.custom import custom_api, journal_entry
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
			vat_payable_account="2135-02",
			vat_receivable_account="1154-02",
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

	def test_saving_a_vat_closing_entry_checks_its_accounts(self):
		journal = JournalEntry.__new__(JournalEntry)
		journal.__dict__.update(_closing(_line("2135-00", debit=70), _line("1111-00", credit=70)))
		for line in journal.accounts:
			line.update(is_petty_cash_account=0, petty_cash_holder=None, petty_cash_holder_name=None)
		with (
			patch.object(ERPNextJournalEntry, "validate"),
			patch.object(journal_entry, "validate_receive_rows"),
			self.assertRaises(frappe.ValidationError) as refused,
		):
			journal.validate()
		self.assertIn("a VAT Closing entry moves only", str(refused.exception))


def _vat_journal(*lines, **fields):
	journal = _journal(*lines, **fields)
	for idx, line in enumerate(journal.accounts, 1):
		line.idx = idx
	journal.setdefault("tax_invoice_details", [])
	return journal


def _input_vat(**fields):
	return _vat_journal(
		_line("5330-02", debit=1000),
		_line("1154-00", debit=70, **fields.pop("line", {})),
		_line("1111-00", credit=1070),
		**fields,
	)


class TestVatTaxInvoices(_Stubs, unittest.TestCase):
	def test_input_vat_without_a_tax_invoice_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_vat_tax_invoices(_input_vat())
		self.assertIn(
			"Row #2 posts a debit of 70.00 of input VAT to 1154-00 without a Purchase Tax Invoice",
			str(refused.exception),
		)

	def test_input_vat_needs_the_supplier_tax_invoice_number_and_date(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_vat_tax_invoices(_input_vat(create_tax_invoice=1))
		self.assertIn("needs the supplier's Tax Invoice Number and Tax Invoice Date", str(refused.exception))
		on_line = {"tax_invoice_number": "INV-1", "tax_invoice_date": "2026-10-22"}
		journal_entry.validate_vat_tax_invoices(_input_vat(create_tax_invoice=1, line=on_line))

	def test_the_number_and_date_may_be_on_the_tax_invoice_tab(self):
		journal = _input_vat(create_tax_invoice=1, line={"reference_detail_no": "TID-1"})
		journal.tax_invoice_details = [
			frappe._dict(name="TID-1", tax_invoice_number="INV-1", tax_invoice_date="2026-10-22")
		]
		journal_entry.validate_vat_tax_invoices(journal)

	def test_output_vat_needs_create_tax_invoice_only(self):
		sale = [_line("1130-01", debit=10700), _line("4100-01", credit=10000), _line("2135-00", credit=700)]
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_vat_tax_invoices(_vat_journal(*sale))
		self.assertIn(
			"Row #3 posts a credit of 700.00 of output VAT to 2135-00 without a Sales Tax Invoice",
			str(refused.exception),
		)
		# the Sales Tax Invoice the entry makes gives the number and date
		journal_entry.validate_vat_tax_invoices(_vat_journal(*sale, create_tax_invoice=1))

	def test_reductions_need_their_tax_invoice_too(self):
		"""A credit note or a supplier's credit note by journal: a debit on output VAT, a credit on input VAT."""
		credit_note = [
			_line("4100-01", debit=1000),
			_line("2135-00", debit=70),
			_line("1130-01", credit=1070),
		]
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_vat_tax_invoices(_vat_journal(*credit_note))
		self.assertIn("Row #2 posts a debit of 70.00 of output VAT to 2135-00", str(refused.exception))
		journal_entry.validate_vat_tax_invoices(_vat_journal(*credit_note, create_tax_invoice=1))
		on_line = {"tax_invoice_number": "CN-1", "tax_invoice_date": "2026-10-22"}
		supplier_note = [
			_line("2120-01", debit=1070),
			_line("5330-02", credit=1000),
			_line("1154-00", credit=70),
		]
		with self.assertRaises(frappe.ValidationError):
			journal_entry.validate_vat_tax_invoices(_vat_journal(*supplier_note, create_tax_invoice=1))
		supplier_note[2].update(on_line)
		journal_entry.validate_vat_tax_invoices(_vat_journal(*supplier_note, create_tax_invoice=1))

	def test_the_month_end_closing_goes_in_a_vat_closing_entry(self):
		closing = [_line("2135-00", debit=3500), _line("1154-00", credit=700), _line("2135-02", credit=2800)]
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_vat_tax_invoices(_vat_journal(*closing))
		self.assertIn("the month-end closing goes in an Entry Type VAT Closing", str(refused.exception))
		journal_entry.validate_vat_tax_invoices(_vat_journal(*closing, voucher_type="VAT Closing"))

	def test_reversals_and_opening_balances_are_left_alone(self):
		reopened = [_line("2135-00", credit=3500), _line("1154-00", debit=700), _line("2136-00", debit=2800)]
		journal_entry.validate_vat_tax_invoices(_vat_journal(*reopened, reversal_of="JV-1"))
		journal_entry.validate_vat_tax_invoices(
			_vat_journal(*reopened, voucher_type="Opening Entry", is_opening="Yes")
		)

	def test_journals_without_vat_and_companies_without_settings_pass(self):
		journal_entry.validate_vat_tax_invoices(
			_vat_journal(_line("5320-02", debit=1), _line("1111-00", credit=1))
		)
		journal_entry.validate_vat_tax_invoices(_input_vat(company="Other"))


def _closing(*lines, **fields):
	return _vat_journal(*lines, voucher_type="VAT Closing", **fields)


PAYABLE = [_line("2135-00", debit=3500), _line("1154-00", credit=700), _line("2135-02", credit=2800)]
RECEIVABLE = [_line("2135-00", debit=700), _line("1154-00", credit=3500), _line("1154-02", debit=2800)]


class TestVatClosing(_Stubs, unittest.TestCase):
	def test_both_balances_go_to_vat_payable_or_receivable(self):
		journal_entry.validate_vat_closing(_closing(*PAYABLE))
		journal_entry.validate_vat_closing(_closing(*RECEIVABLE))
		journal_entry.validate_vat_closing(_closing(_line("2135-00", debit=70), _line("2135-02", credit=70)))

	def test_only_the_four_vat_accounts(self):
		with self.assertRaises(frappe.ValidationError) as refused:
			journal_entry.validate_vat_closing(
				_closing(_line("2135-00", debit=70), _line("1111-00", credit=70))
			)
		self.assertIn("Row #2: a VAT Closing entry moves only output VAT (2135-00)", str(refused.exception))
		with self.assertRaises(frappe.ValidationError):
			journal_entry.validate_vat_closing(
				_closing(_line("2135-02", debit=70), _line("1154-02", credit=70))
			)

	def test_no_party_and_no_tax_invoice(self):
		with self.assertRaises(frappe.ValidationError):
			journal_entry.validate_vat_closing(
				_closing(
					_line("2135-00", debit=70), _line("2135-02", credit=70, party_type="Supplier", party="RD")
				)
			)
		with self.assertRaises(frappe.ValidationError):
			journal_entry.validate_vat_closing(_closing(*PAYABLE, create_tax_invoice=1))

	def test_the_accounts_are_set_up_first(self):
		with patch.object(
			journal_entry.frappe,
			"get_cached_doc",
			return_value=SimpleNamespace(
				company_accounts=[
					SimpleNamespace(company=CD, vat_payable_account=None, vat_receivable_account=None)
				]
			),
		):
			with self.assertRaises(frappe.ValidationError) as refused:
				journal_entry.validate_vat_closing(_closing(*PAYABLE))
		self.assertIn("Set the VAT Payable Account and VAT Receivable Account", str(refused.exception))

	def test_other_entry_types_are_not_checked_here(self):
		journal_entry.validate_vat_closing(
			_vat_journal(_line("2135-00", debit=70), _line("1111-00", credit=70))
		)

	def test_only_an_accounts_manager_submits_it(self):
		with patch.object(journal_entry.frappe, "get_roles", return_value=["Accounts User"]):
			with self.assertRaises(frappe.ValidationError) as refused:
				journal_entry.validate_vat_closing_submitter(_closing(*PAYABLE))
		self.assertIn("Only an Accounts Manager submits a VAT Closing entry", str(refused.exception))
		with patch.object(
			journal_entry.frappe, "get_roles", return_value=["Accounts User", "Accounts Manager"]
		):
			journal_entry.validate_vat_closing_submitter(_closing(*PAYABLE))
		with patch.object(journal_entry.frappe, "get_roles", return_value=["Accounts User"]):
			journal_entry.validate_vat_closing_submitter(_vat_journal(*PAYABLE))


def _journal_tax_invoice(account, debit=0, credit=0, reversal_of=None):
	"""The tax invoice create_tax_invoice_on_gl_tax makes for a journal's GL row on a due VAT account."""
	made = {}
	journal = frappe._dict(
		doctype="Journal Entry", docstatus=1, reversal_of=reversal_of, create_tax_invoice=1
	)
	detail = frappe._dict(doctype="Journal Entry Tax Invoice Detail", tax_base_amount=1000)

	def get_doc(doctype, name):
		return detail if doctype == "Journal Entry Tax Invoice Detail" else journal

	def create(doc, doctype, base_amount, tax_amount, voucher, split_tax_invoice=False):
		made.update(doctype=doctype, base=base_amount, tax=tax_amount)
		return [Mock()]

	gl_entry = frappe._dict(
		voucher_type="Journal Entry",
		voucher_no="JV-1",
		voucher_detail_no="TID-1",
		company=CD,
		account=account,
		debit=debit,
		credit=credit,
		flags=frappe._dict(),
	)
	with (
		patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS.company_accounts[0]),
		patch.object(custom_api.frappe, "get_doc", side_effect=get_doc),
		patch.object(custom_api.frappe, "get_cached_value", return_value=7),
		patch.object(custom_api, "create_tax_invoice", side_effect=create),
		patch.object(custom_api, "update_voucher_tinv", side_effect=lambda doctype, voucher, tinv: tinv),
	):
		custom_api.create_tax_invoice_on_gl_tax(gl_entry, None)
	return made


class TestJournalTaxInvoiceKind(unittest.TestCase):
	def test_the_account_names_the_tax_invoice_and_the_side_its_sign(self):
		cases = {
			("2135-00", 0, 70): ("Sales Tax Invoice", 1000, 70),
			("2135-00", 70, 0): ("Sales Tax Invoice", -1000, -70),
			("1154-00", 70, 0): ("Purchase Tax Invoice", 1000, 70),
			("1154-00", 0, 70): ("Purchase Tax Invoice", -1000, -70),
		}
		for (account, debit, credit), (doctype, base, tax) in cases.items():
			with self.subTest(account=account, debit=debit):
				made = _journal_tax_invoice(account, debit, credit)
				self.assertEqual(made, {"doctype": doctype, "base": base, "tax": tax})

	def test_a_reversal_still_makes_the_negative_one(self):
		made = _journal_tax_invoice("1154-00", credit=70, reversal_of="JV-0")
		self.assertEqual(made, {"doctype": "Purchase Tax Invoice", "base": -1000, "tax": -70})


class TestClearingJournal(_Stubs, unittest.TestCase):
	"""The journal a payment's Clear Undue Tax button makes (make_clear_vat_journal_entry) ticks Create Tax Invoice;
	it submits once the supplier's tax invoice number and date are in."""

	def make(self):
		payment = frappe._dict(
			name="PAY-1",
			company=CD,
			party_type="Supplier",
			party="SUP-1",
			company_tax_address="CD-Billing",
			references=[
				frappe._dict(
					reference_doctype="Purchase Invoice",
					reference_name="PINV-1",
					allocated_amount=10700,
					total_amount=10700,
				)
			],
			check_permission=Mock(),
		)
		journal = frappe._dict(accounts=[])
		journal.append = lambda table, row: journal.accounts.append(
			frappe._dict(row, idx=len(journal.accounts) + 1)
		)
		invoice_gl = [
			{"account": "2120-01", "debit": 0, "credit": 10700},
			{"account": "1154-01", "debit": 700, "credit": 0},
			{"account": "5320-02", "debit": 10000, "credit": 0},
		]
		with (
			patch.object(custom_api.frappe, "get_doc", return_value=payment),
			patch.object(custom_api.frappe, "has_permission", return_value=True),
			patch.object(custom_api.frappe, "new_doc", return_value=journal),
			patch.object(custom_api.frappe, "db", SimpleNamespace(get_all=Mock(return_value=invoice_gl))),
			patch.object(
				custom_api.frappe,
				"get_cached_value",
				side_effect=lambda doctype, name, field: "Profit and Loss"
				if name == "5320-02"
				else "Balance Sheet",
			),
			patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS.company_accounts[0]),
		):
			journal = custom_api.make_clear_vat_journal_entry("Payment Entry", "PAY-1")
		journal.update(
			doctype="Journal Entry", company=CD, voucher_type="Journal Entry", tax_invoice_details=[]
		)
		return journal

	def test_the_clearing_journal_submits_with_the_supplier_tax_invoice(self):
		journal = self.make()
		self.assertEqual(journal.create_tax_invoice, 1)
		self.assertEqual(
			[
				(line.account, line.debit_in_account_currency or 0, line.credit_in_account_currency or 0)
				for line in journal.accounts
			],
			[("1154-01", 0, 700), ("1154-00", 700, 0)],
		)
		for line in journal.accounts:
			line.update(
				debit=line.debit_in_account_currency or 0, credit=line.credit_in_account_currency or 0
			)
		with self.assertRaises(frappe.ValidationError):
			journal_entry.validate_vat_tax_invoices(journal)
		journal.accounts[1].update(tax_invoice_number="IV-1", tax_invoice_date="2026-10-22")
		journal_entry.validate_vat_tax_invoices(journal)


class _DetailRow(SimpleNamespace):
	"""A Journal Entry Tax Invoice Detail row: no company field."""

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestJournalSalesTaxInvoice(unittest.TestCase):
	def test_the_sales_tax_invoice_of_a_journal_takes_its_number_and_company(self):
		"""A journal's voucher is its Tax Invoice Detail row; the settings come from the tax invoice's company."""
		detail = _DetailRow(company_tax_address="CD-Office", save=Mock())
		tinv = SimpleNamespace(
			name="CD00000-STI6910009", date="2026-10-22", company=CD, voucher_type="Journal Entry"
		)
		tinv.save = Mock()
		settings = SimpleNamespace(create_sales_taxinv_on_zero_tax=0, get=lambda field: 0)
		with patch.object(custom_api, "get_thai_tax_settings", return_value=settings) as thai_tax_settings:
			custom_api.update_voucher_tinv("Sales Tax Invoice", detail, tinv)
		thai_tax_settings.assert_called_once_with(CD)
		self.assertEqual(
			(detail.tax_invoice_number, detail.tax_invoice_date), ("CD00000-STI6910009", "2026-10-22")
		)
		self.assertEqual(tinv.company_tax_address, "CD-Office")
		detail.save.assert_called_once()


class TestJournalEntrySubmit(_Stubs, unittest.TestCase):
	def test_submitting_the_journal_runs_the_rule(self):
		"""The Thai Journal Entry checks the tax invoices after ERPNext's own before_submit."""
		journal = JournalEntry.__new__(JournalEntry)
		journal.__dict__.update(_input_vat())
		for line in journal.accounts:
			line.is_petty_cash_account = 0
		with (
			patch.object(ERPNextJournalEntry, "before_submit") as erpnext_before_submit,
			patch.object(journal_entry.frappe, "get_value", return_value=0),
			self.assertRaises(frappe.ValidationError),
		):
			journal.before_submit()
		erpnext_before_submit.assert_called_once()

	def test_submitting_a_vat_closing_entry_needs_an_accounts_manager(self):
		journal = JournalEntry.__new__(JournalEntry)
		journal.__dict__.update(_closing(*[frappe._dict(line) for line in PAYABLE]))
		for line in journal.accounts:
			line.is_petty_cash_account = 0
		with (
			patch.object(ERPNextJournalEntry, "before_submit"),
			patch.object(journal_entry.frappe, "get_value", return_value=0),
			patch.object(journal_entry.frappe, "get_roles", return_value=["Accounts User"]),
			self.assertRaises(frappe.ValidationError),
		):
			journal.before_submit()


if __name__ == "__main__":
	unittest.main()
