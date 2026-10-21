# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Department, Territory and Sales Person are budgeted like Project, and only where they exist."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.accounts.doctype.accounting_dimension import accounting_dimension
from erpnext.accounts.doctype.budget import budget
from erpnext.controllers import budget_controller

ACCOUNTING_DIMENSION = "erpnext.accounts.doctype.accounting_dimension.accounting_dimension."


class TestBudgetDimensionList(unittest.TestCase):
	def budget_dimensions(self, accounting_dimensions):
		with patch.object(frappe, "get_all", return_value=accounting_dimensions):
			return accounting_dimension.get_budget_dimensions()

	def test_native_dimensions_are_listed_between_project_and_accounting_dimensions(self):
		self.assertEqual(
			self.budget_dimensions(["Branch"]),
			["Project", "Department", "Territory", "Sales Person", "Cost Center", "Branch"],
		)

	def test_department_accounting_dimension_is_not_listed_twice(self):
		self.assertEqual(
			self.budget_dimensions(["Department", "Branch"]),
			["Project", "Department", "Territory", "Sales Person", "Cost Center", "Branch"],
		)

	def test_budget_dimension_fields_are_unique_by_fieldname(self):
		custom = [
			frappe._dict(fieldname="department", document_type="Department", label="Department"),
			frappe._dict(fieldname="branch", document_type="Branch"),
		]
		with patch(ACCOUNTING_DIMENSION + "get_accounting_dimensions", return_value=custom):
			fields = accounting_dimension.get_budget_dimension_fields()
		self.assertEqual(
			[(d.get("fieldname"), d.get("document_type")) for d in fields],
			[
				("project", "Project"),
				("department", "Department"),
				("territory", "Territory"),
				("sales_person", "Sales Person"),
				("cost_center", "Cost Center"),
				("branch", "Branch"),
			],
		)

	def test_budget_form_has_a_field_per_native_dimension(self):
		definition = json.loads((Path(budget.__file__).with_name("budget.json")).read_text())
		fields = {field["fieldname"]: field for field in definition["fields"]}
		options = fields["budget_against"]["options"].split("\n")
		for fieldname, document_type in (
			("project", "Project"),
			("department", "Department"),
			("territory", "Territory"),
			("sales_person", "Sales Person"),
		):
			with self.subTest(fieldname=fieldname):
				self.assertIn(document_type, options)
				self.assertEqual(fields[fieldname]["options"], document_type)
				condition = f"eval:doc.budget_against == '{document_type}'"
				self.assertEqual(fields[fieldname]["depends_on"], condition)
				self.assertEqual(fields[fieldname]["mandatory_depends_on"], condition)
				self.assertIn(fieldname, definition["field_order"])


class TestMissingBudgetColumns(unittest.TestCase):
	"""Purchase Order and Material Request rows do not carry Territory or Sales Person."""

	def meta(self, fields):
		return SimpleNamespace(has_field=lambda fieldname: fieldname in fields)

	def test_new_controller_skips_requested_and_ordered_amount_without_the_column(self):
		controller = budget_controller.BudgetValidation.__new__(budget_controller.BudgetValidation)
		key = ("territory", "East", "Expense")
		controller.to_validate = frappe._dict({key: frappe._dict(ordered_amount=0, requested_amount=0)})
		qb = MagicMock()
		with (
			patch.object(frappe, "get_meta", return_value=self.meta({"project"})),
			patch.object(budget_controller, "qb", new=qb),
		):
			controller.get_ordered_amount(key)
			controller.get_requested_amount(key)
		qb.from_.assert_not_called()
		self.assertEqual(controller.to_validate[key].ordered_amount, 0)
		self.assertEqual(controller.to_validate[key].requested_amount, 0)

	def test_new_controller_still_queries_a_dimension_the_rows_carry(self):
		controller = budget_controller.BudgetValidation.__new__(budget_controller.BudgetValidation)
		controller.company = "Company"
		controller.document_type = "GL Map"
		controller.fy_start_date = controller.fy_end_date = "2026-10-01"
		key = ("department", "Ops", "Expense")
		controller.to_validate = frappe._dict({key: frappe._dict(ordered_amount=0)})
		qb = MagicMock()
		query = qb.from_.return_value.inner_join.return_value.on.return_value
		query.select.return_value.where.return_value.run.return_value = [frappe._dict(amount=40)]
		with (
			patch.object(frappe, "get_meta", return_value=self.meta({"department"})),
			patch.object(budget_controller, "qb", new=qb),
			patch.object(budget_controller, "Criterion", new=MagicMock()),
		):
			controller.get_ordered_amount(key)
		self.assertEqual(controller.to_validate[key].ordered_amount, 40)

	def test_legacy_controller_ignores_rows_that_cannot_carry_the_dimension(self):
		params = frappe._dict(budget_against_field="sales_person", sales_person="Alice")
		with patch.object(frappe, "get_meta", return_value=self.meta({"project"})):
			self.assertFalse(budget.has_budget_dimension_column("Purchase Order Item", params))
			self.assertEqual(budget.get_ordered_amount(params), 0)
			self.assertEqual(budget.get_requested_amount(params), 0)

	def test_legacy_controller_checks_dimensions_the_rows_have_or_that_are_not_requested(self):
		params = frappe._dict(budget_against_field="department", department="Ops")
		with patch.object(frappe, "get_meta", return_value=self.meta({"department"})):
			self.assertTrue(budget.has_budget_dimension_column("Purchase Order Item", params))
		with patch.object(frappe, "get_meta", MagicMock()) as get_meta:
			self.assertTrue(budget.has_budget_dimension_column("Purchase Order Item", frappe._dict()))
			get_meta.assert_not_called()
