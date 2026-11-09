"""Journal withholding validates the party before looking up its tax details. No site required."""

import unittest
from unittest.mock import patch

import frappe

from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import JournalTaxWithholding


class TestJournalWithholdingParty(unittest.TestCase):
	def setUp(self):
		translation = patch(
			"erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry._", lambda text: text
		)
		translation.start()
		self.addCleanup(translation.stop)

	def controller(self, **values):
		doc = frappe._dict(
			voucher_type="Journal Entry",
			apply_tds=1,
			tax_withholding_category="Service WHT",
			tax_withholding_entries=[],
			accounts=[frappe._dict(party_type="", party="", supplier="SUP-1")],
			precision=lambda *args: 2,
		)
		doc.update(values)
		return JournalTaxWithholding(doc)

	def test_missing_party_is_validation_error_before_tax_lookup(self):
		controller = self.controller()
		with (
			patch.object(frappe, "throw", side_effect=frappe.ValidationError) as throw,
			patch.object(controller, "calculate") as calculate,
		):
			with self.assertRaises(frappe.ValidationError):
				controller.on_validate()
			self.assertIn("Party Type and Party", throw.call_args.args[0])
			calculate.assert_not_called()

	def test_incomplete_party_is_rejected(self):
		for row in [frappe._dict(party_type="Supplier"), frappe._dict(party="SUP-1")]:
			with self.subTest(row=row), patch.object(frappe, "throw", side_effect=frappe.ValidationError):
				with self.assertRaises(frappe.ValidationError):
					self.controller(accounts=[row])._is_tax_withholding_applicable()

	def test_disabled_or_uncategorized_withholding_still_needs_no_party(self):
		for values in [{"apply_tds": 0}, {"tax_withholding_category": ""}, {"voucher_type": "Bank Entry"}]:
			with self.subTest(values=values):
				controller = self.controller(**values)
				with patch.object(controller, "calculate") as calculate:
					controller.on_validate()
					calculate.assert_not_called()

	def test_supplier_and_customer_continue_to_calculation(self):
		for party_type in ["Supplier", "Customer"]:
			with self.subTest(party_type=party_type):
				controller = self.controller(accounts=[frappe._dict(party_type=party_type, party="PARTY-1")])
				with patch.object(controller, "calculate") as calculate:
					controller.on_validate()
					calculate.assert_called_once_with()


class Row(frappe._dict):
	def precision(self, *args):
		return 2


class Journal(frappe._dict):
	def precision(self, *args):
		return 2

	def append(self, field, values):
		row = Row(values)
		if field == "tax_withholding_entries":
			row.set_status = lambda *args: None
			row.validate_adjustments = lambda: None
			row.validate_tax_withheld_amount = lambda: None
		self[field].append(row)
		return row

	def extend(self, field, values):
		for value in values:
			self.append(field, value)

	def remove(self, row):
		self.accounts.remove(row)

	def set_amounts_in_company_currency(self):
		from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
		JournalEntry.set_amounts_in_company_currency(self)

	def set_total_debit_credit(self):
		from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
		JournalEntry.set_total_debit_credit(self)

	def set_against_account(self):
		from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
		JournalEntry.set_against_account(self)


class TestDirectJournalWithholding(unittest.TestCase):
	def setUp(self):
		from contextlib import ExitStack

		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(patch(
			"erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry._", lambda text: text
		))
		self.stack.enter_context(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
		self.stack.enter_context(patch.object(frappe, "get_system_settings", return_value="Commercial Rounding"))
		self.stack.enter_context(patch(
			"erpnext.regional.thailand.custom.journal_entry.company_tax_accounts",
			return_value=frappe._dict(purchase_tax_account="VAT"),
		))
		self.stack.enter_context(patch.object(frappe, "get_cached_value", side_effect=self.cached_value))
		self.supplier = self.stack.enter_context(patch.object(frappe, "get_lazy_doc"))
		self.supplier.return_value.is_blocked_for.return_value = False
		self.party_validation = self.stack.enter_context(patch(
			"erpnext.accounts.party.validate_party_frozen_disabled"
		))
		self.stack.enter_context(patch(
			"erpnext.accounts.report.general_ledger.general_ledger.get_account_type_map",
			return_value={"VAT": "Tax", "Bank": "Bank", "Bank2": "Bank", "WHT": ""},
		))
		self.stack.enter_context(patch("erpnext.accounts.utils.get_account_currency", return_value="THB"))
		self.stack.enter_context(patch("erpnext.setup.utils.get_exchange_rate", return_value=1))
		self.category = frappe._dict(
			name="Service WHT", account_head="WHT", tax_rate=3, tax_id="TAX-ID",
			disable_cumulative_threshold=1, single_threshold=0, ldc_unutilized_amount=0,
		)
		self.stack.enter_context(patch.object(
			JournalTaxWithholding, "_get_category_details",
			side_effect=lambda: {self.category.name: self.category.copy()},
		))
		self.stack.enter_context(patch.object(JournalTaxWithholding, "_get_unused_threshold", return_value=0))
		self.stack.enter_context(patch.object(
			JournalTaxWithholding, "_get_historical_entries", return_value=[],
		))
		self.doc = Journal(
			doctype="Journal Entry", name="JE-1", company="Company", posting_date="2026-11-09",
			voucher_type="Journal Entry", create_tax_invoice=1, apply_tds=1, cost_center="Main",
			tax_withholding_category="Service WHT", tax_withholding_entries=[], tax_invoice_details=[],
			accounts=[self.row("Expense", debit=10000), self.row("VAT", debit=700, supplier="SUP-1"),
				self.row("Bank", credit=10700)],
		)

	def cached_value(self, doctype, name, field, **kwargs):
		if doctype == "Account":
			if field == "is_petty_cash_account":
				return 0
			return "Bank" if name.startswith("Bank") else ""
		return "THB"

	def row(self, account, debit=0, credit=0, **values):
		return Row(account=account, debit=debit, credit=credit, debit_in_account_currency=debit,
			credit_in_account_currency=credit, exchange_rate=1, **values)

	def save(self):
		JournalTaxWithholding(self.doc).on_validate()

	def assert_amounts(self, wht, bank):
		self.assertEqual(sum(row.credit for row in self.doc.accounts if row.account == "WHT"), wht)
		bank_row = next(row for row in self.doc.accounts if row.account == "Bank")
		self.assertEqual(bank_row.credit, bank)
		self.assertFalse(bank_row.party_type)
		self.assertFalse(bank_row.party)
		self.assertEqual(self.doc.difference, 0)

	def test_native_calculation_repeated_save_and_rate_category_change(self):
		self.save()
		self.assert_amounts(300, 10400)
		self.assertEqual(self.doc.tax_withholding_entries[0].party, "SUP-1")
		self.assertEqual(self.doc.tax_withholding_entries[0].taxable_amount, 10000)
		self.party_validation.assert_called_with("Company", "Supplier", "SUP-1")
		self.save()
		self.assert_amounts(300, 10400)
		self.assertEqual(len(self.doc.accounts), 4)
		self.category.name = "New WHT"
		self.category.account_head = "WHT2"
		self.category.tax_rate = 2
		self.doc.tax_withholding_category = "New WHT"
		self.save()
		self.assertFalse(any(row.account == "WHT" for row in self.doc.accounts))
		self.assertEqual(self.doc.accounts[-1].credit, 200)
		self.assertEqual(self.doc.accounts[2].credit, 10500)
		self.assertEqual(self.doc.difference, 0)

	def test_changed_expense_base(self):
		self.save()
		self.doc.accounts[0].update(debit=20000, debit_in_account_currency=20000)
		self.doc.accounts[2].update(credit=20400, credit_in_account_currency=20400)
		self.save()
		self.assert_amounts(600, 20100)

	def test_confirmed_manual_row_is_recalculated_once_by_native_controller(self):
		self.doc.accounts.append(self.row("WHT", credit=250, is_tax_withholding_account=1))
		self.doc.accounts[2].update(credit=10450, credit_in_account_currency=10450)
		self.save()
		self.assert_amounts(300, 10400)
		self.save()
		self.assert_amounts(300, 10400)
		self.assertEqual(len(self.doc.accounts), 4)

	def test_manual_mode_preserves_ledger_amounts_without_native_withholding_records(self):
		self.doc.apply_tds = 0
		self.doc.accounts.append(self.row("WHT", credit=250))
		self.doc.accounts[2].update(credit=10450, credit_in_account_currency=10450)
		self.save()
		self.assertEqual(self.doc.accounts[-1].credit, 250)
		self.assertFalse(self.doc.accounts[-1].is_tax_withholding_account)
		self.assertEqual(self.doc.accounts[2].credit, 10450)
		self.assertEqual(self.doc.tax_withholding_entries, [])

	def test_disabled_or_removed_category_restores_bank(self):
		for field, value in [("apply_tds", 0), ("tax_withholding_category", "")]:
			with self.subTest(field=field):
				self.doc.apply_tds = 1
				self.doc.tax_withholding_category = "Service WHT"
				self.save()
				self.doc[field] = value
				self.save()
				self.assert_amounts(0, 10700)
				self.assertEqual(self.doc.tax_withholding_entries, [])

	def test_threshold_change_removes_auto_row_and_restores_bank(self):
		self.save()
		self.category.single_threshold = 20000
		self.save()
		self.assert_amounts(0, 10700)
		self.assertEqual(self.doc.tax_withholding_entries[0].withholding_amount, 0)

	def test_supplier_from_linked_tax_invoice_and_conflict(self):
		self.doc.accounts[1].supplier = None
		self.doc.accounts[1].reference_detail_no = "DETAIL-1"
		self.doc.tax_invoice_details = [Row(name="DETAIL-1", supplier="SUP-1")]
		self.save()
		self.assert_amounts(300, 10400)
		self.doc.accounts[1].supplier = "SUP-2"
		with self.assertRaises(frappe.ValidationError):
			self.save()

	def test_missing_ambiguous_supplier_and_credits_are_rejected(self):
		for extra in [self.row("VAT", debit=7, supplier="SUP-2"), self.row("Bank2", credit=10),
			self.row("WHT", credit=300)]:
			with self.subTest(account=extra.account):
				self.doc.accounts.append(extra)
				with self.assertRaises(frappe.ValidationError):
					self.save()
				self.doc.accounts.remove(extra)
		self.doc.accounts[1].supplier = None
		with self.assertRaises(frappe.ValidationError):
			self.save()

	def test_reversed_payment_reports_debit_credit_instead_of_missing_supplier(self):
		for row in self.doc.accounts:
			row.debit, row.credit = row.credit, row.debit
			row.debit_in_account_currency, row.credit_in_account_currency = (
				row.credit_in_account_currency, row.debit_in_account_currency
			)
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError) as throw:
			with self.assertRaises(frappe.ValidationError):
				self.save()
			self.assertIn("debits the expense and input VAT", throw.call_args.args[0])
		self.assertEqual(self.doc.accounts[1].supplier, "SUP-1")
		self.assertEqual(self.doc.tax_withholding_entries, [])

	def test_blocked_disabled_and_frozen_supplier_rejected(self):
		self.supplier.return_value.is_blocked_for.return_value = True
		with self.assertRaises(frappe.ValidationError):
			self.save()
		self.supplier.return_value.is_blocked_for.assert_called_with("Payments")
		self.supplier.return_value.is_blocked_for.return_value = False
		for message in ["disabled", "frozen"]:
			with self.subTest(message=message):
				self.party_validation.side_effect = frappe.ValidationError(message)
				with self.assertRaises(frappe.ValidationError):
					self.save()

	def test_disabling_both_flags_cleans_up(self):
		self.save()
		self.doc.apply_tds = 0
		self.doc.create_tax_invoice = 0
		self.save()
		self.assert_amounts(0, 10700)
		self.assertEqual(self.doc.tax_withholding_entries, [])

	def test_cancellation_does_not_resolve_current_vat_settings(self):
		self.save()
		self.doc.docstatus = 2
		with patch("erpnext.regional.thailand.custom.journal_entry.company_tax_accounts") as settings:
			JournalTaxWithholding(self.doc)
			settings.assert_not_called()

	def test_explicit_supplier_and_customer_native_amount_updates(self):
		self.doc.create_tax_invoice = 0
		self.doc.accounts[2].update(account="Payable", party_type="Supplier", party="SUP-1")
		self.save()
		self.assertEqual(self.doc.accounts[2].credit, 10400)
		self.assertEqual(self.doc.accounts[-1].credit, 300)
		self.assertEqual(self.doc.difference, 0)
		self.doc.accounts = [self.row("Receivable", debit=10700, party_type="Customer", party="CUS-1"),
			self.row("Income", credit=10000), self.row("VAT", credit=700)]
		self.doc.tax_withholding_entries = []
		self.save()
		self.assertEqual(self.doc.accounts[0].debit, 11000)
		self.assertEqual(self.doc.accounts[-1].credit, 300)
		self.assertEqual(self.doc.difference, 0)

	def test_direct_supplier_correction_replaces_generated_identity(self):
		self.save()
		self.doc.accounts[1].supplier = "SUP-2"
		self.save()
		self.assert_amounts(300, 10400)
		self.assertEqual(self.doc.tax_withholding_entries[0].party, "SUP-2")

	def test_override_rejects_supplier_change_before_calculation(self):
		self.save()
		self.doc.override_tax_withholding_entries = 1
		self.doc.accounts[1].supplier = "SUP-2"
		with patch.object(JournalTaxWithholding, "calculate") as calculate:
			with self.assertRaises(frappe.ValidationError):
				self.save()
			calculate.assert_not_called()
		self.assertEqual(self.doc.tax_withholding_entries[0].party, "SUP-1")
		self.assert_amounts(300, 10400)

	def test_override_validates_every_nonzero_entry_identity(self):
		self.save()
		self.doc.override_tax_withholding_entries = 1
		for party_type, party in [("Customer", "SUP-1"), (None, "SUP-1"), ("Supplier", None),
			("Supplier", "SUP-2")]:
			for amount in [300, -300]:
				with self.subTest(party_type=party_type, party=party, amount=amount):
					entry = self.doc.append("tax_withholding_entries", dict(
						self.doc.tax_withholding_entries[0], party_type=party_type, party=party,
						withholding_amount=amount, withholding_name="OTHER-JE",
					))
					with self.assertRaises(frappe.ValidationError):
						self.save()
					self.doc.tax_withholding_entries.remove(entry)

	def test_override_preserves_matching_manual_amount_and_ignores_zero_identity(self):
		self.save()
		self.doc.override_tax_withholding_entries = 1
		entry = self.doc.tax_withholding_entries[0]
		entry.withholding_amount = 250
		self.doc.append("tax_withholding_entries", dict(
			entry, party_type="Customer", party="OTHER", withholding_amount=0,
		))
		self.save()
		self.assert_amounts(250, 10450)
		self.assertIs(self.doc.tax_withholding_entries[0], entry)
		self.assertEqual(entry.party_type, "Supplier")
		self.assertEqual(entry.party, "SUP-1")
		self.assertEqual(entry.withholding_amount, 250)

	def test_disabled_withholding_clears_override_entries_with_stale_identity(self):
		self.save()
		self.doc.override_tax_withholding_entries = 1
		self.doc.accounts[1].supplier = "SUP-2"
		self.doc.apply_tds = 0
		self.save()
		self.assert_amounts(0, 10700)
		self.assertEqual(self.doc.tax_withholding_entries, [])

	def test_settlement_currency_conversion(self):
		self.doc.accounts[2].exchange_rate = 2
		self.doc.accounts[2].credit_in_account_currency = 5350
		self.save()
		self.assert_amounts(300, 10400)
		self.assertEqual(self.doc.accounts[2].credit_in_account_currency, 5200)
		self.save()
		self.assert_amounts(300, 10400)

	def test_configured_input_vat_excluded_even_without_tax_account_type(self):
		with patch("erpnext.accounts.report.general_ledger.general_ledger.get_account_type_map",
			return_value={"Bank": "Bank"}):
			self.save()
		self.assert_amounts(300, 10400)

	def test_recalculated_tax_row_matches_account_flag_before_submit(self):
		from erpnext.regional.thailand.custom import journal_entry

		for row in self.doc.accounts:
			row.is_petty_cash_account = 0
		for _ in range(2):
			self.save()
			with (
				patch.object(frappe, "get_value", return_value=0),
				patch.object(journal_entry, "validate_vat_closing_submitter"),
				patch.object(journal_entry, "validate_vat_tax_invoices"),
				patch(
					"erpnext.accounts.doctype.journal_entry.journal_entry.JournalEntry.before_submit"
				),
			):
				doc = object.__new__(journal_entry.JournalEntry)
				doc.accounts = self.doc.accounts
				doc.before_submit()
			self.assertEqual(self.doc.accounts[-1].is_petty_cash_account, 0)
			self.assert_amounts(300, 10400)
