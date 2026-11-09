"""Employee service withholding uses native calculation without protected HR data or a site."""

import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import frappe

from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry
from erpnext.accounts.doctype.tax_withholding_category.tax_withholding_category import get_tax_id_for_party
from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import PaymentTaxWithholding
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

	def precision(self, *args):
		return 2

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
			paid_from="Bank",
			tax_withholding_category="Service 3%", tax_withholding_categories=[], taxes=[],
			tax_withholding_entries=[], paid_amount=5000, received_amount=5000, base_paid_amount=5000,
			unallocated_amount=0, references=[],
			source_exchange_rate=1, target_exchange_rate=1, paid_to_account_currency="THB",
			paid_from_account_currency="THB", cost_center="Main", _item_wise_tax_details=[],
		)

	def save(self):
		PaymentTaxWithholding(self.doc).on_validate()
		self.doc.set_amounts_after_tax()

	def test_native_calculation_repeated_save_and_rate_change(self):
		for unused in range(2):
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
		from pypika import MySQLQuery
		from pypika import Table
		from erpnext.accounts.report.tax_withholding_details.tax_withholding_details import TaxWithholdingDetailsReport

		report = TaxWithholdingDetailsReport()
		with patch.object(frappe, "qb", MagicMock(DocType=lambda name: Table("tab" + name), from_=MySQLQuery.from_)):
			sql = str(report.get_party_query("Employee", {"EMP-1"}))
		self.assertIn("employee_name", sql)
		for field in ["tax_id", "national_id_number", "bank_ac_no", "personal_email", "current_address"]:
			self.assertNotIn(field, sql)
