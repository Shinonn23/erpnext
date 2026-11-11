"""Employee service withholding uses native calculation without protected HR data or a site."""

import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import frappe

from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry
from erpnext.accounts.doctype.tax_withholding_category.tax_withholding_category import get_tax_id_for_party
from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import (
	PaymentTaxWithholding,
	PurchaseTaxWithholding,
	SalesTaxWithholding,
)
from erpnext.regional.thailand.custom.payment_entry import (
	get_payment_entry_tax_withholding_categories,
	get_payment_withholding_base,
)


class Row(frappe._dict):
	def precision(self, *args):
		return 2

	def set(self, key, value):
		self[key] = value


class Payment(frappe._dict):
	apply_taxes = PaymentEntry.apply_taxes
	initialize_taxes = PaymentEntry.initialize_taxes
	determine_exclusive_rate = PaymentEntry.determine_exclusive_rate
	calculate_taxes = PaymentEntry.calculate_taxes
	get_current_tax_amount = PaymentEntry.get_current_tax_amount
	set_amounts_after_tax = PaymentEntry.set_amounts_after_tax
	add_bank_gl_entries = PaymentEntry.add_bank_gl_entries
	add_tax_gl_entries = PaymentEntry.add_tax_gl_entries
	get_party_account_for_taxes = PaymentEntry.get_party_account_for_taxes

	def precision(self, *args):
		return 2

	def get_gl_dict(self, values, account_currency=None, item=None):
		return Row(values)

	def append(self, field, values):
		row = Row(values, idx=len(self[field]) + 1)
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
		self.taxes.remove(row)


class TestEmployeePaymentWithholding(unittest.TestCase):
	def setUp(self):
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(patch.object(frappe, "get_system_settings", return_value="Commercial Rounding"))
		self.stack.enter_context(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
		self.stack.enter_context(patch(
			"erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry._", lambda text: text
		))
		self.stack.enter_context(patch("erpnext.regional.thailand.custom.payment_entry._", lambda text: text))
		self.employee = self.stack.enter_context(patch.object(
			frappe, "get_cached_value", return_value=frappe._dict(status="Active", company="Company")
		))
		self.employee.side_effect = lambda doctype, name, *args, **kwargs: (
			("Bank" if name == "Bank" else "Payable") if doctype == "Account" else self.employee.return_value
		)
		for name in ("validate_taxes_and_charges", "validate_inclusive_tax"):
			self.stack.enter_context(patch("erpnext.accounts.doctype.payment_entry.payment_entry." + name))
		self.category = frappe._dict(name="Service 3%", tax_rate=3, account_head="WHT", ldc_unutilized_amount=0)
		self.stack.enter_context(patch.object(
			PaymentTaxWithholding, "_get_category_details",
			side_effect=lambda: {self.category.name: self.category.copy()},
		))
		self.doc = Payment(
			doctype="Payment Entry", name="PE-1", company="Company", posting_date="2026-11-09",
			party_type="Employee", party="EMP-1", payment_type="Pay", apply_tds=1,
			paid_from="Bank", paid_to="Payable", company_currency="THB",
			transaction_currency="THB", transaction_exchange_rate=1,
			tax_withholding_category="Service 3%", tax_withholding_categories=[], taxes=[],
			tax_withholding_entries=[], paid_amount=5000, received_amount=5000, base_paid_amount=5000, base_received_amount=5000,
			unallocated_amount=0, references=[],
			source_exchange_rate=1, target_exchange_rate=1, paid_to_account_currency="THB",
			paid_from_account_currency="THB", cost_center="Main", _item_wise_tax_details=[],
		)

	def save(self):
		PaymentTaxWithholding(self.doc).on_validate()
		self.doc.set_amounts_after_tax()

	def test_native_calculation_repeated_save_and_rate_change(self):
		for _unused in range(2):
			self.save()
			self.assertEqual(len(self.doc.taxes), 1)
			self.assertEqual(self.doc.taxes[0].tax_amount, 150)
			self.assertEqual(self.doc.taxes[0].add_deduct_tax, "Deduct")
			self.assertEqual(self.doc.paid_amount_after_tax, 4850)
			entry = self.doc.tax_withholding_entries[0]
			self.assertEqual((entry.party_type, entry.party, entry.taxable_amount), ("Employee", "EMP-1", 5000))
		self.category.tax_rate = 2
		self.save()
		self.assertEqual(self.doc.taxes[0].tax_amount, 100)
		self.assertEqual(self.doc.paid_amount_after_tax, 4900)
		self.employee.assert_called_with("Employee", "EMP-1", ["status", "company"], as_dict=True)

	def assert_payment_gl(self, net_base):
		entries = []
		bank = self.doc.get_party_account_for_taxes()
		currencies = {
			"WHT": "THB",
			bank: (
				self.doc.paid_from_account_currency
				if self.doc.payment_type == "Pay"
				else self.doc.paid_to_account_currency
			),
		}
		with patch(
			"erpnext.accounts.doctype.payment_entry.payment_entry.get_account_currency",
			side_effect=currencies.get,
		):
			self.doc.add_bank_gl_entries(entries)
			self.doc.add_tax_gl_entries(entries)
		bank_entries = [row for row in entries if row.account == bank]
		credit, debit = ("credit", "debit") if self.doc.payment_type == "Pay" else ("debit", "credit")

		def net(field):
			return sum((row.get(credit + field) or 0) - (row.get(debit + field) or 0) for row in bank_entries)

		rate = (
			self.doc.source_exchange_rate if self.doc.payment_type == "Pay" else self.doc.target_exchange_rate
		)
		self.assertEqual(net(""), net_base)
		self.assertEqual(net("_in_account_currency"), net_base / rate)
		self.assertEqual(net("_in_transaction_currency"), net_base / self.doc.transaction_exchange_rate)
		withheld = next(row for row in entries if row.account == "WHT")
		self.assertEqual(withheld.credit, 150)
		self.assertEqual(withheld.credit_in_transaction_currency, 150 / self.doc.transaction_exchange_rate)

	def test_foreign_currency_employee_tax_net_and_gl_remain_consistent_on_resave(self):
		for source_rate, target_rate, bank_currency, party_currency in (
			(1, 2, "THB", "USD"),
			(2, 1, "USD", "THB"),
			(2, 4, "USD", "EUR"),
		):
			with self.subTest(bank=bank_currency, party=party_currency):
				self.doc.update(
					paid_amount=5000 / source_rate,
					received_amount=5000 / target_rate,
					source_exchange_rate=source_rate,
					target_exchange_rate=target_rate,
					paid_from_account_currency=bank_currency,
					paid_to_account_currency=party_currency,
					transaction_currency=bank_currency if source_rate != 1 else party_currency,
					transaction_exchange_rate=source_rate if source_rate != 1 else target_rate,
				)
				for _unused in range(2):
					self.save()
					self.assertEqual(len(self.doc.taxes), 1)
					self.assertEqual(self.doc.tax_withholding_entries[0].withholding_amount, 150)
					self.assertEqual(self.doc.taxes[0].tax_amount, 150)
					self.assertEqual(self.doc.taxes[0].base_tax_amount, 150)
					self.assertEqual(self.doc.paid_amount_after_tax, 4850 / source_rate)
					self.assertEqual(self.doc.received_amount_after_tax, 4850 / target_rate)
					self.assertEqual(self.doc.base_paid_amount_after_tax, 4850)
					self.assertEqual(self.doc.base_received_amount_after_tax, 4850)
					self.assertEqual(self.doc.received_amount, 5000 / target_rate)
					self.assert_payment_gl(4850)

	def test_foreign_currency_supplier_and_customer_keep_company_currency_tax_rows(self):
		for party_type, payment_type, net_base in (("Supplier", "Pay", 4850), ("Customer", "Receive", 5150)):
			with self.subTest(party_type=party_type):
				self.doc.update(
					party_type=party_type,
					payment_type=payment_type,
					unallocated_amount=2500,
					paid_amount=2500,
					received_amount=2500,
					source_exchange_rate=2,
					target_exchange_rate=2,
					paid_from_account_currency="USD",
					paid_to_account_currency="USD",
					transaction_currency="USD",
					transaction_exchange_rate=2,
				)
				self.save()
				self.assertEqual(self.doc.tax_withholding_entries[0].withholding_amount, 150)
				self.assertEqual(self.doc.taxes[0].tax_amount, 150)
				self.assertEqual(self.doc.base_paid_amount_after_tax, net_base)
				self.assertEqual(self.doc.paid_amount_after_tax, net_base / 2)
				self.assert_payment_gl(net_base)

	def test_invoice_tax_rows_still_convert_to_invoice_currency(self):
		for controller_type, doctype in (
			(PurchaseTaxWithholding, "Purchase Invoice"),
			(SalesTaxWithholding, "Sales Invoice"),
		):
			with self.subTest(doctype=doctype):
				self.doc.update(
					doctype=doctype, supplier="SUP-1", customer="CUST-1", conversion_rate=2, taxes=[]
				)
				self.doc.calculate_taxes_and_totals = lambda: None
				self.doc.tax_withholding_entries = [
					Row(
						withholding_name=self.doc.name,
						tax_withholding_category=self.category.name,
						withholding_amount=150,
					)
				]
				controller = controller_type(self.doc)
				controller.category_details = {self.category.name: self.category}
				controller.update_tax_rows()
				self.assertEqual(self.doc.taxes[0].tax_amount, 75)

	def test_net_fields_preserve_base_precision_and_ignore_included_taxes(self):
		self.doc.update(
			paid_amount=1000,
			received_amount=3000,
			base_paid_amount=3000,
			base_received_amount=3000,
			source_exchange_rate=3,
			target_exchange_rate=1,
		)
		self.doc.set_amounts_after_tax()
		self.assertEqual((self.doc.paid_amount_after_tax, self.doc.base_paid_amount_after_tax), (1000, 3000))
		self.doc.taxes = [
			Row(tax_amount=100, base_tax_amount=100, add_deduct_tax="Deduct"),
			Row(tax_amount=25, base_tax_amount=25, add_deduct_tax="Deduct", included_in_paid_amount=1),
		]
		self.doc.set_amounts_after_tax()
		self.assertEqual(self.doc.paid_amount_after_tax, 966.67)
		self.assertEqual(self.doc.base_paid_amount_after_tax, 2900)
		self.assertEqual(self.doc.received_amount_after_tax, 2900)
		self.assertEqual(self.doc.base_received_amount_after_tax, 2900)
		self.doc.taxes.append(Row(tax_amount=10, base_tax_amount=10, add_deduct_tax="Add"))
		self.doc.set_amounts_after_tax()
		self.assertEqual((self.doc.paid_amount_after_tax, self.doc.base_paid_amount_after_tax), (970, 2910))

	def test_net_account_amount_rounds_each_tax_like_the_bank_ledger(self):
		self.doc.update(
			paid_amount=1000,
			received_amount=3000,
			base_paid_amount=3000,
			base_received_amount=3000,
			source_exchange_rate=3,
			target_exchange_rate=1,
			paid_from_account_currency="USD",
			transaction_currency="USD",
			transaction_exchange_rate=3,
		)
		self.doc.taxes = [
			Row(account_head="WHT", tax_amount=0.01, base_tax_amount=0.01, add_deduct_tax="Deduct")
			for _unused in range(2)
		]
		self.doc.set_amounts_after_tax()
		entries = []
		with patch(
			"erpnext.accounts.doctype.payment_entry.payment_entry.get_account_currency",
			side_effect=lambda account: "THB" if account == "WHT" else "USD",
		):
			self.doc.add_bank_gl_entries(entries)
			self.doc.add_tax_gl_entries(entries)
		bank_net = sum(
			(row.credit_in_account_currency or 0) - (row.debit_in_account_currency or 0)
			for row in entries
			if row.account == "Bank"
		)
		self.assertEqual(self.doc.paid_amount_after_tax, bank_net)
		self.assertEqual(self.doc.paid_amount_after_tax, 1000)
		self.assertEqual(self.doc.base_paid_amount_after_tax, 2999.98)

	def test_receipt_bank_tax_uses_received_currency_precision(self):
		self.doc.update(
			payment_type="Receive", party_type="Customer", paid_from="Receivable", paid_to="Bank",
			paid_amount=10000, received_amount=100, base_paid_amount=3200, base_received_amount=3200,
			source_exchange_rate=0.32, target_exchange_rate=32,
			paid_from_account_currency="JPY", paid_to_account_currency="USD",
			transaction_currency="JPY", transaction_exchange_rate=0.32,
		)
		self.doc.taxes = [Row(
			account_head="WHT", tax_amount=150, base_tax_amount=150, add_deduct_tax="Deduct",
		)]
		entries = []
		with (
			patch.object(Payment, "precision", lambda self, field, *args: 0 if field in {
				"paid_amount", "paid_amount_after_tax",
			} else 2),
			patch(
				"erpnext.accounts.doctype.payment_entry.payment_entry.get_account_currency",
				side_effect=lambda account: "THB" if account == "WHT" else "USD",
			),
		):
			self.doc.set_amounts_after_tax()
			self.doc.add_bank_gl_entries(entries)
			self.doc.add_tax_gl_entries(entries)
		bank_net = sum(
			(row.debit_in_account_currency or 0) - (row.credit_in_account_currency or 0)
			for row in entries if row.account == "Bank"
		)
		self.assertEqual(self.doc.received_amount_after_tax, 95.31)
		self.assertEqual(bank_net, self.doc.received_amount_after_tax)
		self.assertEqual(self.doc.base_received_amount_after_tax, 3050)

	def test_receive_missing_category_inactive_and_other_company_refused(self):
		for values in [{"payment_type": "Receive"}, {"tax_withholding_category": ""}]:
			with self.subTest(values=values):
				original = self.doc.copy()
				self.doc.update(values)
				with self.assertRaises(frappe.ValidationError):
					self.save()
				self.doc.update(original)
		for details in [frappe._dict(status="Left", company="Company"),
			frappe._dict(status="Active", company="Other")]:
			self.employee.return_value = details
			with self.assertRaises(frappe.ValidationError):
				self.save()

	def test_tax_id_never_reads_employee_identity(self):
		with patch.object(frappe, "db", MagicMock()) as db:
			self.assertIsNone(get_tax_id_for_party.__wrapped__("Employee", "EMP-1"))
			db.get_value.assert_not_called()
			for party_type in ["Supplier", "Customer"]:
				get_tax_id_for_party.__wrapped__(party_type, "PARTY-1")
				db.get_value.assert_called_with(party_type, "PARTY-1", "tax_id")

	def test_employee_cannot_override_tax_entry_identity_or_pay_from_a_payable_account(self):
		for values in ({"override_tax_withholding_entries": 1}, {"paid_from": "Payable"}):
			with self.subTest(values=values):
				original = self.doc.copy()
				self.doc.update(values)
				with self.assertRaises(frappe.ValidationError):
					self.save()
				self.doc.update(original)

	def test_missing_employee_gives_validation_error(self):
		self.employee.return_value = None
		with self.assertRaises(frappe.ValidationError):
			self.save()

	def test_employee_base_uses_gross_and_disallows_invoice_categories(self):
		with patch.object(frappe, "get_doc") as get_doc:
			self.assertEqual(get_payment_withholding_base(self.doc), 5000)
			get_doc.assert_not_called()
			with self.assertRaises(frappe.ValidationError):
				get_payment_entry_tax_withholding_categories(self.doc)
			get_doc.assert_not_called()

	def test_employee_base_converts_party_currency(self):
		self.doc.received_amount = 2500
		self.doc.target_exchange_rate = 2
		self.assertEqual(get_payment_withholding_base(self.doc), 5000)
		controller = PaymentTaxWithholding(self.doc)
		controller.category_details = {self.category.name: self.category.copy()}
		controller._update_taxable_amounts()
		self.assertEqual(controller.category_details[self.category.name].taxable_amount, 5000)

	def test_reimbursement_advance_and_payroll_references_refused(self):
		for doctype in ["Employee Advance", "Expense Claim", "Payroll Entry", "Salary Slip"]:
			with self.subTest(doctype=doctype):
				self.doc.references = [Row(reference_doctype=doctype, allocated_amount=5000)]
				with self.assertRaises(frappe.ValidationError):
					self.save()

	def test_report_employee_name_query_has_no_protected_hr_fields(self):
		from pypika import MySQLQuery, Table

		from erpnext.accounts.report.tax_withholding_details.tax_withholding_details import (
			TaxWithholdingDetailsReport,
		)

		report = TaxWithholdingDetailsReport()
		with patch.object(frappe, "qb", MagicMock(DocType=lambda name: Table("tab" + name), from_=MySQLQuery.from_)):
			sql = str(report.get_party_query("Employee", {"EMP-1"}))
		self.assertIn("employee_name", sql)
		for field in ["tax_id", "national_id_number", "bank_ac_no", "personal_email", "current_address"]:
			self.assertNotIn(field, sql)
