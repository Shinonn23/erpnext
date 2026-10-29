import unittest

import frappe

from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport
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
		self._check(ReceivablePayableReport, "Receivable", "customer_name")
		self._check(ReceivablePayableReport, "Payable", "supplier_name")

	def test_summary(self):
		self._check(AccountsReceivableSummary, "Receivable", "party_name")
		self._check(AccountsReceivableSummary, "Payable", "party_name")
