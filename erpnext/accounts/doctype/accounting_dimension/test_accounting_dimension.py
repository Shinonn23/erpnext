# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt
import frappe

from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.tests.utils import ERPNextTestSuite


class TestAccountingDimension(ERPNextTestSuite):
	def test_budget_options_follow_dimension_enablement(self):
		dimension = frappe.get_doc("Accounting Dimension", "Location")
		dimension.disabled = 0
		dimension.save()
		options = frappe.get_meta("Budget").get_field("budget_against").options.split("\n")
		self.assertEqual(options.count("Location"), 1)
		self.assertEqual(frappe.get_meta("Budget").get_field("project").options, "Project")

		dimension.disabled = 1
		dimension.save()
		options = frappe.get_meta("Budget").get_field("budget_against").options.split("\n")
		self.assertNotIn("Location", options)

		dimension.disabled = 0
		dimension.save()
		options = frappe.get_meta("Budget").get_field("budget_against").options.split("\n")
		self.assertEqual(options.count("Location"), 1)

	def test_standard_budget_dimensions_are_always_offered_once(self):
		# Department is also an Accounting Dimension in the fixtures; it must not be listed twice
		for enabled in (1, 0):
			dimension = frappe.get_doc("Accounting Dimension", "Department")
			dimension.disabled = 0 if enabled else 1
			dimension.save()
			options = frappe.get_meta("Budget").get_field("budget_against").options.split("\n")
			for document_type in ("Project", "Department", "Territory", "Sales Person"):
				self.assertEqual(options.count(document_type), 1, (document_type, enabled))
		dimension.disabled = 0
		dimension.save()

	def test_department_dimension_does_not_duplicate_the_standard_budget_field(self):
		self.assertFalse(frappe.db.exists("Custom Field", {"dt": "Budget", "fieldname": "department"}))
		self.assertEqual(frappe.get_meta("Budget").get_field("department").options, "Department")

	def test_project_is_a_builtin_budget_dimension(self):
		dimension = frappe.get_doc({"doctype": "Accounting Dimension", "document_type": "Project"})
		with self.assertRaises(frappe.ValidationError):
			dimension.insert()

	def test_dimension_against_sales_invoice(self):
		si = create_sales_invoice(do_not_save=1)

		si.location = "Block 1"
		si.append(
			"items",
			{
				"item_code": "_Test Item",
				"warehouse": "_Test Warehouse - _TC",
				"qty": 1,
				"rate": 100,
				"income_account": "Sales - _TC",
				"expense_account": "Cost of Goods Sold - _TC",
				"cost_center": "_Test Cost Center - _TC",
				"department": "_Test Department - _TC",
				"location": "Block 1",
			},
		)

		si.save()
		si.submit()

		gle = frappe.get_doc("GL Entry", {"voucher_no": si.name, "account": "Sales - _TC"})

		self.assertEqual(gle.get("department"), "_Test Department - _TC")

	def test_dimension_against_journal_entry(self):
		je = make_journal_entry("Sales - _TC", "Sales Expenses - _TC", 500, save=False)
		je.accounts[0].update({"department": "_Test Department - _TC"})
		je.accounts[1].update({"department": "_Test Department - _TC"})

		je.accounts[0].update({"location": "Block 1"})
		je.accounts[1].update({"location": "Block 1"})

		je.save()
		je.submit()

		gle = frappe.get_doc("GL Entry", {"voucher_no": je.name, "account": "Sales - _TC"})
		gle1 = frappe.get_doc("GL Entry", {"voucher_no": je.name, "account": "Sales Expenses - _TC"})
		self.assertEqual(gle.get("department"), "_Test Department - _TC")
		self.assertEqual(gle1.get("department"), "_Test Department - _TC")

	def test_child_table_not_allowed_as_dimension(self):
		dimension = frappe.get_doc({"doctype": "Accounting Dimension", "document_type": "Sales Team"})
		self.assertRaises(frappe.ValidationError, dimension.insert)

	def test_single_doctype_not_allowed_as_dimension(self):
		dimension = frappe.get_doc({"doctype": "Accounting Dimension", "document_type": "Selling Settings"})
		self.assertRaises(frappe.ValidationError, dimension.insert)

	def test_non_scalar_dimension_value_skipped_in_gl_dict(self):
		si = create_sales_invoice(do_not_save=1)

		si.department = "_Test Department - _TC"
		self.assertEqual(si.get_gl_dict({}).get("department"), "_Test Department - _TC")

		si.department = ["_Test Department - _TC"]
		self.assertNotIn("department", si.get_gl_dict({}))

	def test_mandatory(self):
		location = frappe.get_doc("Accounting Dimension", "Location")
		for default in location.dimension_defaults:
			if default.company == "_Test Company":
				default.mandatory_for_bs = True
				# A configured default would correctly satisfy the mandatory dimension.
				default.default_dimension = None
		location.save()

		si = create_sales_invoice(do_not_save=1)
		si.append(
			"items",
			{
				"item_code": "_Test Item",
				"warehouse": "_Test Warehouse - _TC",
				"qty": 1,
				"rate": 100,
				"income_account": "Sales - _TC",
				"expense_account": "Cost of Goods Sold - _TC",
				"cost_center": "_Test Cost Center - _TC",
				"location": "",
			},
		)

		si.save()
		self.assertFalse(si.location)
		savepoint = "mandatory_dimension_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaises(frappe.ValidationError):
				si.submit()
		finally:
			frappe.db.rollback(save_point=savepoint)
		si.reload()
		self.assertEqual(si.docstatus, 0)
		self.assertFalse(
			frappe.db.exists("GL Entry", {"voucher_type": "Sales Invoice", "voucher_no": si.name})
		)
		si.location = "Block 1"
		for row in si.items:
			row.location = "Block 1"
		si.save()
		si.submit()
		si.reload()
		self.assertEqual(si.docstatus, 1)
		self.assertEqual(
			frappe.db.get_value(
				"GL Entry",
				{"voucher_type": "Sales Invoice", "voucher_no": si.name, "account": si.debit_to},
				"location",
			),
			"Block 1",
		)
		si.cancel()
