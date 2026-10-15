import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_reconciliation import stock_reconciliation as reconciliation


class TestStockReconciliationBalancePermissions(unittest.TestCase):
	def test_internal_validation_can_compute_balance_without_write_permission(self):
		item = frappe._dict(
			item_code="Counted Item",
			warehouse="Count Warehouse",
			qty=8,
			valuation_rate=10,
			precision=lambda field: 2,
		)
		dimensions = {"inventory_dimension": "Count Dimension"}
		doc = SimpleNamespace(
			items=[item],
			flags=frappe._dict(ignore_permissions=True),
			stock_count="Stock Count",
			posting_date="2026-10-15",
			posting_time="12:00:00",
			company="Count Company",
			purpose="Stock Reconciliation",
			get_inventory_dimensions_dict=Mock(return_value=dimensions),
		)
		doc.calculate_difference_amount = (
			reconciliation.StockReconciliation.calculate_difference_amount.__get__(doc)
		)
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError) as permission,
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding"),
			patch.object(
				frappe, "get_cached_value", return_value=frappe._dict(has_serial_no=0, has_batch_no=0)
			),
			patch.object(
				frappe, "db", SimpleNamespace(get_single_value=Mock(return_value=0)), create=True
			),
			patch.object(reconciliation, "get_stock_balance", return_value=(5, 10)) as balance,
		):
			reconciliation.StockReconciliation.remove_items_with_no_change(doc)

		permission.assert_not_called()
		balance.assert_called_once_with(
			item.item_code,
			item.warehouse,
			doc.posting_date,
			doc.posting_time,
			with_valuation_rate=True,
			with_serial_no=False,
			inventory_dimensions_dict=dimensions,
		)
		doc.get_inventory_dimensions_dict.assert_called_once_with(item, include_empty=True)
		self.assertEqual(doc.items, [item])
		self.assertEqual(item.current_qty, 5)
		self.assertEqual(item.current_valuation_rate, 10)
		self.assertEqual(doc.difference_amount, 30)

	def test_public_api_rejects_permission_before_reading_balance(self):
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError) as permission,
			patch.object(frappe, "get_cached_value") as cached_value,
			patch.object(reconciliation, "get_stock_balance") as balance,
		):
			with self.assertRaises(frappe.PermissionError):
				reconciliation.get_stock_balance_for("Item", "Warehouse", "2026-10-15", "12:00:00")

		permission.assert_called_once_with("Stock Reconciliation", "write", throw=True)
		cached_value.assert_not_called()
		balance.assert_not_called()

	def test_public_api_forwards_every_argument_and_preserves_result(self):
		args = ("Item", "Warehouse", "2026-10-15", "12:00:00")
		kwargs = {
			"batch_no": "Batch",
			"with_valuation_rate": False,
			"inventory_dimensions_dict": {"inventory_dimension": "Dimension"},
			"row": {"batch_no": "Batch", "use_serial_batch_fields": 1},
			"company": "Company",
		}
		result = {"qty": 5, "rate": 10, "serial_nos": None, "use_serial_batch_fields": 1}
		with (
			patch.object(frappe, "has_permission", return_value=True) as permission,
			patch.object(reconciliation, "_get_stock_balance_for", return_value=result) as compute,
		):
			self.assertIs(reconciliation.get_stock_balance_for(*args, **kwargs), result)

		permission.assert_called_once_with("Stock Reconciliation", "write", throw=True)
		compute.assert_called_once_with(*args, **kwargs)

	def test_only_public_balance_function_is_whitelisted(self):
		self.assertIn(reconciliation.get_stock_balance_for, frappe.whitelisted)
		self.assertNotIn(reconciliation._get_stock_balance_for, frappe.whitelisted)
