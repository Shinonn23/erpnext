# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import patch

import frappe

from erpnext.accounts.report.budget_variance_report import budget_variance_report as report

MODULE = "erpnext.accounts.report.budget_variance_report.budget_variance_report."
DIMENSIONS = [
	frappe._dict(fieldname=fieldname, document_type=document_type)
	for fieldname, document_type in (
		("project", "Project"),
		("department", "Department"),
		("territory", "Territory"),
		("sales_person", "Sales Person"),
	)
]


class TestBudgetVarianceReportDefaults(unittest.TestCase):
	def set_default(self, filters, budgeted=()):
		with (
			patch(MODULE + "get_budget_dimension_fields", return_value=DIMENSIONS),
			patch.object(frappe, "get_all", return_value=list(budgeted)) as get_all,
		):
			report.set_default_budget_against(filters)
		return get_all

	def test_empty_budget_against_uses_first_budgeted_dimension(self):
		# the standard "Budget Variance" dashboard chart sends budget_against = ""
		for filters in (frappe._dict(budget_against=""), frappe._dict()):
			self.set_default(filters, budgeted=["Sales Person", "Territory"])
			self.assertEqual(filters.budget_against, "Territory")

	def test_empty_budget_against_without_budgets_uses_first_dimension(self):
		filters = frappe._dict(budget_against="")
		self.set_default(filters)
		self.assertEqual(filters.budget_against, "Project")

	def test_default_only_looks_at_the_selected_company(self):
		get_all = self.set_default(frappe._dict(company="Company"))
		self.assertEqual(
			get_all.call_args.kwargs["filters"], {"docstatus": 1, "company": "Company"}
		)

	def test_selected_budget_against_is_kept(self):
		filters = frappe._dict(budget_against="Department")
		get_all = self.set_default(filters)
		self.assertEqual(filters.budget_against, "Department")
		get_all.assert_not_called()

	def test_every_budget_dimension_is_a_valid_choice(self):
		with patch(MODULE + "get_budget_dimension_fields", return_value=DIMENSIONS):
			for dimension in DIMENSIONS:
				report.validate_budget_dimensions(frappe._dict(budget_against=dimension.document_type))
			with patch.object(frappe, "throw", side_effect=ValueError), patch(MODULE + "_", str):
				with self.assertRaises(ValueError):
					report.validate_budget_dimensions(frappe._dict(budget_against="Customer"))
