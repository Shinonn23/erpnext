"""General Ledger standard dimensions, without site fixtures or database writes."""

import unittest
from datetime import date
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.report.general_ledger import general_ledger as report


class TestStandardDimensions(unittest.TestCase):
	def setUp(self):
		self.addCleanup(patch.stopall)
		patch.object(report, "_", side_effect=lambda value: value).start()
		patch.object(frappe, "get_single_value", return_value=None).start()
		patch.object(frappe, "get_cached_value", return_value=None).start()
		patch.object(report, "get_accounting_dimensions", return_value=[]).start()
		patch.object(frappe, "db", Mock()).start()
		patch("frappe.desk.reportview.build_match_conditions", return_value="").start()
		self.filters = frappe._dict(
			company="Company",
			from_date="2026-01-01",
			to_date="2026-12-31",
			include_dimensions=1,
			categorize_by="Categorize by Voucher (Consolidated)",
		)

	def test_filters_are_parsed_and_bound_in_both_backends(self):
		self.filters.update(
			department='["Operations"]', territory='["West", "East"]', sales_person='["Alice"]'
		)
		report.validate_filters(self.filters, {})
		self.assertEqual(self.filters.department, ["Operations"])
		self.assertEqual(self.filters.territory, ["West", "East"])
		self.assertEqual(self.filters.sales_person, ["Alice"])
		conditions = report.get_conditions(self.filters)
		self.assertIn("department in %(department)s", conditions)
		self.assertIn("territory in %(territory)s", conditions)
		self.assertIn("sales_person in %(sales_person)s", conditions)
		conditions, params = report._build_gl_conditions_duckdb(self.filters)
		self.assertIn("department IN (?)", conditions)
		self.assertIn("territory IN (?, ?)", conditions)
		self.assertIn("sales_person IN (?)", conditions)
		self.assertEqual(params[-4:], ["Operations", "West", "East", "Alice"])
		self.assertNotIn("Operations", " ".join(conditions))

	def test_optional_filters_leave_both_backends_unrestricted(self):
		self.assertNotIn("department", report.get_conditions(self.filters))
		conditions, _ = report._build_gl_conditions_duckdb(self.filters)
		self.assertNotIn("territory", " ".join(conditions))

	def test_columns_follow_include_dimensions(self):
		patch.object(report, "get_company_currency", return_value="USD").start()
		for include_dimensions in (0, 1):
			self.filters.include_dimensions = include_dimensions
			columns = {column["fieldname"]: column for column in report.get_columns(self.filters)}
			for fieldname in ("project", "department", "territory", "sales_person"):
				self.assertEqual(fieldname in columns, bool(include_dimensions))
				if include_dimensions:
					self.assertEqual(columns[fieldname]["options"], fieldname.replace("_", " ").title())

	def test_consolidation_keeps_each_dimension_and_combines_matching_rows(self):
		for include_dimensions, expected_count in ((1, 5), (0, 1)):
			self.filters.include_dimensions = include_dimensions
			entries = [
				frappe._dict(
					posting_date=date(2026, 1, 1),
					voucher_type="Journal Entry",
					voucher_no="JV-1",
					account="Cash",
					department=department,
					territory=territory,
					sales_person=sales_person,
					debit=10,
					credit=0,
					debit_in_account_currency=10,
					credit_in_account_currency=0,
				)
				for department, territory, sales_person in (
					("Operations", "West", "Alice"),
					("Operations", "East", "Bob"),
					("Finance", "West", "Alice"),
					(None, None, None),
					("Operations", "West", "Bob"),
					("Operations", "West", "Alice"),
				)
			]
			totals, result = report.get_accountwise_gle(self.filters, [], entries, {})
			self.assertEqual(len(result), expected_count)
			self.assertEqual(totals.total.debit, 60)
			if include_dimensions:
				self.assertEqual(result[0].debit, 20)
				self.assertEqual(result[-1].sales_person, "Bob")

	def test_unmigrated_columns_are_null_and_cannot_be_filtered(self):
		frappe.db.has_column.return_value = False
		self.assertEqual(
			report._standard_dimension_selects(),
			["project", "NULL AS department", "NULL AS territory", "NULL AS sales_person"],
		)
		throw = patch.object(frappe, "throw", side_effect=ValueError).start()
		self.filters.sales_person = '["Alice"]'
		with self.assertRaises(ValueError):
			report.validate_filters(self.filters, {})
		throw.assert_called_once_with("Sales Person is not configured on GL Entry")

	def test_both_queries_select_standard_dimensions(self):
		patch.object(report, "get_currency", return_value={}).start()
		patch.object(report, "get_party_name_map", return_value={}).start()
		permission_check = patch.object(report, "check_ledger_voucher_permissions").start()
		patch.object(report, "get_conditions", return_value="").start()
		frappe.db.sql.return_value = []
		report.get_gl_entries(self.filters, [])
		query = frappe.db.sql.call_args.args[0]
		self.assertIn("cost_center, project, department, territory, sales_person,", query)
		conn = Mock()
		row = [None] * 23
		row[8:13] = ["Main - CD", None, "Operations", "West", "Alice"]
		conn.execute.return_value.fetchall.return_value = [row]
		entries = report.get_gl_entries_duckdb(self.filters, [], conn)
		self.assertEqual(entries[0].cost_center, "Main - CD")
		self.assertEqual(entries[0].department, "Operations")
		self.assertEqual(entries[0].territory, "West")
		self.assertEqual(entries[0].sales_person, "Alice")
		query = conn.execute.call_args.args[0]
		self.assertIn("cost_center, project, department, territory, sales_person,", query)
		self.assertEqual(permission_check.call_count, 2)
