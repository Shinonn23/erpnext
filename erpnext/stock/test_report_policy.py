from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext.stock import report_policy


class TestStockReportPolicy(TestCase):
	def test_scope_intersects_restrictions_without_granting_a_broader_scope(self):
		values = {"company": {"A", "B"}, "team": ["B", "C"], "unrestricted": None}
		with (
			patch.object(frappe, "get_hooks", return_value={"warehouses": list(values)}),
			patch.object(frappe, "get_attr", side_effect=lambda path: lambda: values[path]),
		):
			self.assertEqual(report_policy.allowed_warehouses(), {"B"})
			values["team"] = []
			self.assertEqual(report_policy.allowed_warehouses(), set())

	def test_scope_without_policy_uses_readable_records_only(self):
		with (
			patch.object(frappe, "get_hooks", return_value={}),
			patch.object(frappe, "has_permission", side_effect=[False, True]),
			patch.object(frappe, "get_list", return_value=["Allowed"]) as get_list,
		):
			self.assertEqual(report_policy.allowed_groups(), set())
			get_list.assert_not_called()
			self.assertEqual(report_policy.allowed_warehouses(), {"Allowed"})
		get_list.assert_called_once_with("Warehouse", pluck="name", limit_page_length=0)

	def test_value_permissions_require_an_explicit_grant_from_every_provider(self):
		with patch.object(frappe, "get_hooks", return_value={}):
			self.assertFalse(report_policy.has_stock_value_access())
			self.assertFalse(report_policy.has_purchase_price_access())
		values = {"stock": True, "company": False}
		with (
			patch.object(frappe, "get_hooks", return_value={"stock_values": list(values)}),
			patch.object(frappe, "get_attr", side_effect=lambda path: lambda: values[path]),
		):
			self.assertFalse(report_policy.has_stock_value_access())
			values["company"] = True
			self.assertTrue(report_policy.has_stock_value_access())

	def test_freight_item_must_have_a_single_agreed_configuration(self):
		values = {"default": "Freight", "company": "Other Freight"}
		with (
			patch.object(frappe, "get_hooks", return_value={"freight_item": list(values)}),
			patch.object(frappe, "get_attr", side_effect=lambda path: lambda: values[path]),
		):
			self.assertIsNone(report_policy.freight_item_code())
			values["company"] = "Freight"
			self.assertEqual(report_policy.freight_item_code(), "Freight")
