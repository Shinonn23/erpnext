import unittest
from unittest.mock import patch

import frappe

from erpnext.accounts.report.accounts_receivable.accounts_receivable import (
	ReceivablePayableReport,
	get_party_names,
)
from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (
	AccountsReceivableSummary,
)


def _columns(report_class, account_type):
	report = report_class.__new__(report_class)
	report.account_type = account_type
	report.party_naming_by = "Naming Series"
	report.filters = frappe._dict(
		based_on_payment_terms=0,
		show_gl_balance=0,
		show_future_payments=0,
		show_sales_person=0,
		sales_partner=None,
		range=None,
		ageing_based_on="Due Date",
		report_date="2026-10-29",
	)
	report.ranges = ["30", "60", "90", "120"]
	report.range = ["30", "60", "90", "120"]
	report.get_columns()
	return report.columns


class TestPartyNameColumn(unittest.TestCase):
	def _check(self, report_class, account_type, fieldname):
		columns = _columns(report_class, account_type)
		fieldnames = [c["fieldname"] for c in columns]
		idx = fieldnames.index("party")
		name_col = columns[idx + 1]
		self.assertEqual(name_col["fieldname"], fieldname)
		self.assertEqual(name_col["label"], "Party Name")
		if "party_account" in fieldnames:
			self.assertEqual(fieldnames.index("party_account"), idx + 2)

	def test_detailed(self):
		self._check(ReceivablePayableReport, "Receivable", "party_name")
		self._check(ReceivablePayableReport, "Payable", "party_name")

	def test_summary(self):
		self._check(AccountsReceivableSummary, "Receivable", "party_name")
		self._check(AccountsReceivableSummary, "Payable", "party_name")


class TestPartyNames(unittest.TestCase):
	def test_names_by_party_type(self):
		queried = []
		data = {
			"Customer": [("C1", "Customer One")],
			"Supplier": [("S1", "Supplier One")],
			"Employee": [("6609003", "Somchai")],
		}

		def fake_get_all(doctype, **kwargs):
			queried.append((doctype, kwargs["fields"][1]))
			return [r for r in data[doctype] if r[0] in kwargs["filters"]["name"][1]]

		with (
			patch("frappe.get_all", fake_get_all),
			patch("frappe.has_permission", return_value=True),
		):
			names = get_party_names(
				{"Customer": {"C1", "C2"}, "Supplier": {"S1"}, "Employee": {"6609003"}, "Other": {"X"}}
			)
		self.assertEqual(names[("Customer", "C1")], "Customer One")
		self.assertEqual(names[("Customer", "C2")], "C2")
		self.assertEqual(names[("Supplier", "S1")], "Supplier One")
		self.assertEqual(names[("Employee", "6609003")], "Somchai")
		self.assertEqual(names[("Other", "X")], "X")
		self.assertEqual(
			sorted(queried),
			[("Customer", "customer_name"), ("Employee", "employee_name"), ("Supplier", "supplier_name")],
		)

	def test_employee_name_needs_read_permission(self):
		with (
			patch("frappe.get_all", side_effect=AssertionError("must not query")),
			patch("frappe.has_permission", return_value=False),
		):
			names = get_party_names({"Employee": {"6609003"}})
		self.assertEqual(names[("Employee", "6609003")], "6609003")
