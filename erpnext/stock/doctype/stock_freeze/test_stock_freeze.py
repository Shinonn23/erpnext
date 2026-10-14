# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

import frappe

from erpnext.stock.doctype.item.test_item import create_item
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.stock.stock_control import OPERATIONS, get_active_stock_freezes, stock_control_permission_field
from erpnext.tests.utils import ERPNextTestSuite


class TestStockFreeze(ERPNextTestSuite):
	def setUp(self):
		self.load_test_records("Company")
		self.item = create_item("_Test Stock Freeze Item")

	def freeze(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Stock Freeze",
				"company": "_Test Company",
				"freeze_level": "Item",
				"item_code": self.item.name,
				"reason": "Quality investigation",
				**values,
			}
		).insert()

	def test_overlapping_holds_release_independently(self):
		first, second = self.freeze(), self.freeze(reason="Independent investigation")
		self.assertEqual(
			{row.name for row in get_active_stock_freezes("_Test Company", self.item.name)},
			{first.name, second.name},
		)
		first.release("Inspection completed")
		first.reload()
		self.assertEqual(first.status, "Released")
		self.assertEqual(first.release_reason, "Inspection completed")
		self.assertEqual(first.released_by, "Administrator")
		self.assertIsNotNone(first.released_on)
		self.assertEqual(
			[row.name for row in get_active_stock_freezes("_Test Company", self.item.name)], [second.name]
		)
		second.release("Investigation closed")
		self.assertEqual(get_active_stock_freezes("_Test Company", self.item.name), [])

	def test_original_details_are_immutable(self):
		hold = self.freeze()
		hold.reason = "Changed original reason"
		with self.assertRaises(frappe.ValidationError):
			hold.save()
		hold.reload()
		self.assertEqual(hold.reason, "Quality investigation")

	def test_release_requires_reason_and_cannot_reopen_or_delete(self):
		hold = self.freeze()
		with self.assertRaises(frappe.ValidationError):
			hold.release(" ")
		hold.reload()
		self.assertEqual(hold.status, "Active")
		hold.release("Resolved")
		hold.status = "Active"
		with self.assertRaises(frappe.ValidationError):
			hold.save()
		hold.reload()
		self.assertEqual(hold.status, "Released")
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Stock Freeze", hold.name)
		self.assertTrue(frappe.db.exists("Stock Freeze", hold.name))

	def test_direct_status_edit_is_rejected(self):
		hold = self.freeze()
		hold.status = "Released"
		hold.release_reason = "Attempt to bypass Release"
		with self.assertRaises(frappe.ValidationError):
			hold.save()
		hold.reload()
		self.assertEqual(hold.status, "Active")

	def test_release_uses_persisted_target(self):
		hold = self.freeze()
		other_item = create_item("_Test Stock Freeze Caller Target")
		hold.item_code = other_item.name
		hold.release("Resolved original hold")
		hold.reload()
		self.assertEqual(hold.item_code, self.item.name)
		self.assertEqual(hold.status, "Released")
		self.assertEqual(get_active_stock_freezes("_Test Company", self.item.name), [])

	def test_unprivileged_user_cannot_release(self):
		hold = self.freeze()
		with self.set_user("Guest"):
			with self.assertRaises(frappe.PermissionError):
				hold.release("Unauthorized")
		hold.reload()
		self.assertEqual(hold.status, "Active")

	def test_qa_type_rejects_outbound_permissions(self):
		policy = frappe.get_doc(
			{
				"doctype": "Warehouse Type",
				"name": "_Test Freeze QA",
				"handling_category": "QA",
				**{
					stock_control_permission_field("Warehouse Type", operation): 0
					for operation in OPERATIONS
					if operation != "internal_transfer"
				},
				"allow_sales": 1,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			policy.insert()
		self.assertFalse(frappe.db.exists("Warehouse Type", policy.name))

	def test_policy_changes_require_a_reason(self):
		policy = frappe.get_doc(
			{
				"doctype": "Warehouse Type",
				"name": "_Test Freeze Normal",
				"handling_category": "Normal",
				"allow_sales": 0,
			}
		).insert()
		policy.allow_sales = 1
		with self.assertRaises(frappe.ValidationError):
			policy.save()
		policy.reload()
		self.assertEqual(policy.allow_sales, 0)
		policy.allow_sales = 1
		policy.policy_change_reason = "Approved sales source"
		policy.save()
		policy.reload()
		self.assertEqual(policy.allow_sales, 1)

	def test_company_activation_requires_typed_warehouses(self):
		company = frappe.get_doc("Company", "_Test Company")
		create_warehouse("_Test Freeze Untyped")
		company.enable_stock_control = 1
		with self.assertRaises(frappe.ValidationError):
			company.save()
		company.reload()
		self.assertFalse(company.enable_stock_control)
