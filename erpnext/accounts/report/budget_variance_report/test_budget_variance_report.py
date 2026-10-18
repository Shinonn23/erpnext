# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import patch

import frappe

from erpnext.accounts.report.budget_variance_report import budget_variance_report as report

DIMENSIONS = "erpnext.accounts.report.budget_variance_report.budget_variance_report.get_dimensions"


class TestBudgetVarianceReportDefaults(unittest.TestCase):
	def test_empty_budget_against_uses_first_dimension(self):
		# the standard "Budget Variance" dashboard chart sends budget_against = ""
		dimensions = [frappe._dict(document_type="Department"), frappe._dict(document_type="Project")]
		for filters in (frappe._dict(budget_against=""), frappe._dict()):
			with patch(DIMENSIONS, return_value=(dimensions, {})):
				report.set_default_budget_against(filters)
			self.assertEqual(filters.budget_against, "Department")

	def test_empty_budget_against_falls_back_to_project(self):
		filters = frappe._dict(budget_against="")
		with patch(DIMENSIONS, return_value=([frappe._dict(document_type="Project")], {})):
			report.set_default_budget_against(filters)
		self.assertEqual(filters.budget_against, "Project")

	def test_selected_budget_against_is_kept(self):
		filters = frappe._dict(budget_against="Project")
		with patch(DIMENSIONS) as get_dimensions:
			report.set_default_budget_against(filters)
		self.assertEqual(filters.budget_against, "Project")
		get_dimensions.assert_not_called()
