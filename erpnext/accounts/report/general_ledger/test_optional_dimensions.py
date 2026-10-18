"""Reports remain usable before optional GL dimensions have been installed."""

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.report.general_ledger import general_ledger as report


class TestOptionalDimensions(unittest.TestCase):
	def test_missing_columns_are_null_in_both_queries(self):
		filters = frappe._dict(company="Company", from_date="2026-01-01", to_date="2026-12-31")
		with (
			patch.object(frappe, "db", Mock()) as database,
			patch.object(report, "get_currency", return_value={}),
			patch.object(report, "get_party_name_map", return_value={}),
			patch.object(report, "get_conditions", return_value=""),
			patch.object(report, "_build_gl_conditions_duckdb", return_value=([], [])),
			patch.object(report, "check_ledger_voucher_permissions"),
		):
			database.has_column.return_value = False
			database.sql.return_value = []
			report.get_gl_entries(filters, [])
			self.assertIn("project, NULL AS department, NULL AS territory,", database.sql.call_args.args[0])
			connection = Mock()
			connection.execute.return_value.fetchall.return_value = []
			report.get_gl_entries_duckdb(filters, [], connection)
			self.assertIn("project, NULL AS department, NULL AS territory,", connection.execute.call_args.args[0])

	def test_filter_on_missing_dimension_is_rejected(self):
		def reject(message):
			raise ValueError(message)

		filters = frappe._dict(
			company="Company", from_date="2026-01-01", to_date="2026-12-31", department='["Operations"]'
		)
		with (
			patch.object(frappe, "db", Mock()) as database,
			patch.object(report, "_", side_effect=lambda value: value),
			patch.object(frappe, "throw", side_effect=reject),
		):
			database.has_column.return_value = False
			with self.assertRaisesRegex(ValueError, "Department is not configured on GL Entry"):
				report.validate_filters(filters, {})
