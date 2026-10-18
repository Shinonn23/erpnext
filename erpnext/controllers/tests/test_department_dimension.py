# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Purchase, stock and asset documents carry the Department that bears their cost."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.assets.doctype.asset import depreciation
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.setup.doctype.employee import employee

EMPLOYEE = "erpnext.setup.doctype.employee.employee."


class Doc(SimpleNamespace):
	set_department = AccountsController.set_department

	def get(self, key, default=None):
		return getattr(self, key, default)

	def is_new(self):
		return self.new


class TestSetDepartment(unittest.TestCase):
	def setUp(self):
		self.user_department = "Lab"
		patcher = patch(
			EMPLOYEE + "get_department_of_user", side_effect=lambda user, company: self.user_department
		)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.enterContext(patch.object(frappe, "session", new=SimpleNamespace(user="buyer@example.com")))
		self.item_meta = {"Purchase Order Item": True, "Purchase Taxes and Charges": False}
		self.enterContext(
			patch.object(
				frappe,
				"get_meta",
				side_effect=lambda doctype: SimpleNamespace(has_field=lambda field: self.item_meta[doctype]),
			)
		)

	def doc(self, doctype="Purchase Order", new=True, department=None, items=(), has_field=True):
		tables = [
			SimpleNamespace(fieldname="items", options="Purchase Order Item"),
			SimpleNamespace(fieldname="taxes", options="Purchase Taxes and Charges"),
		]
		return Doc(
			doctype=doctype,
			company="Company",
			department=department,
			new=new,
			items=[frappe._dict(department=d) for d in items],
			taxes=[frappe._dict(account="Tax")],
			meta=SimpleNamespace(has_field=lambda field: has_field, get_table_fields=lambda: tables),
		)

	def test_new_document_starts_in_the_creators_department_and_rows_follow(self):
		doc = self.doc(items=(None, "Store"))
		doc.set_department()
		self.assertEqual(doc.department, "Lab")
		self.assertEqual([row.department for row in doc.items], ["Lab", "Store"])

	def test_rows_without_a_department_field_are_left_alone(self):
		doc = self.doc(items=(None,))
		doc.set_department()
		self.assertNotIn("department", doc.taxes[0])

	def test_department_that_came_with_the_document_is_kept(self):
		doc = self.doc(department="Store", items=(None,))
		doc.set_department()
		self.assertEqual((doc.department, doc.items[0].department), ("Store", "Store"))

	def test_saved_document_is_not_given_a_department_again(self):
		doc = self.doc(new=False, items=(None,))
		doc.set_department()
		self.assertIsNone(doc.department)
		self.assertIsNone(doc.items[0].department)

	def test_creator_outside_the_company_or_without_employee_gives_no_department(self):
		self.user_department = None
		doc = self.doc(items=(None,))
		doc.set_department()
		self.assertIsNone(doc.department)
		self.assertIsNone(doc.items[0].department)

	def test_documents_where_department_means_something_else_are_untouched(self):
		for doctype in ("Asset", "Expense Claim", "Payroll Entry"):
			doc = self.doc(doctype=doctype, items=(None,))
			doc.set_department()
			self.assertIsNone(doc.department, doctype)

	def test_documents_without_the_field_are_untouched(self):
		doc = self.doc(has_field=False)
		doc.set_department()
		self.assertIsNone(doc.department)


class TestDepartmentOfUser(unittest.TestCase):
	def department(self, record):
		db = MagicMock()
		db.get_value.return_value = record
		with patch.object(frappe, "db", new=db):
			return employee.get_department_of_user("buyer@example.com", "Company")

	def test_active_employee_of_the_company(self):
		self.assertEqual(self.department(frappe._dict(department="Lab", company="Company")), "Lab")

	def test_employee_of_another_company_or_without_department_or_without_employee(self):
		self.assertIsNone(self.department(frappe._dict(department="Lab", company="Other")))
		self.assertIsNone(self.department(frappe._dict(department=None, company="Company")))
		self.assertIsNone(self.department(None))


class TestDepreciationDepartment(unittest.TestCase):
	def rows(self, department):
		asset = frappe._dict(name="ASSET-1", department=department)
		schedule = frappe._dict(depreciation_amount=100)
		return depreciation.get_credit_and_debit_entry("Accumulated", schedule, asset, "Expense", [])

	def test_both_rows_carry_the_department_the_asset_is_held_in(self):
		credit, debit = self.rows("Lab")
		self.assertEqual((credit["department"], debit["department"]), ("Lab", "Lab"))

	def test_asset_without_a_department_leaves_the_rows_unassigned(self):
		credit, debit = self.rows(None)
		self.assertNotIn("department", credit)
		self.assertNotIn("department", debit)

	def test_a_default_department_dimension_is_not_overwritten_by_an_empty_asset_department(self):
		asset = frappe._dict(name="ASSET-1", department=None)
		dimension = {
			"fieldname": "department",
			"mandatory_for_pl": 1,
			"mandatory_for_bs": 1,
			"default_dimension": "Default Department",
		}
		credit, debit = depreciation.get_credit_and_debit_entry(
			"Accumulated", frappe._dict(depreciation_amount=100), asset, "Expense", [dimension]
		)
		self.assertEqual(
			(credit["department"], debit["department"]), ("Default Department", "Default Department")
		)
