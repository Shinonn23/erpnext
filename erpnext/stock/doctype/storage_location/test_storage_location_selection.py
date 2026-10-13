# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import inspect
import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.item.item import Item
from erpnext.stock.doctype.putaway_rule.putaway_rule import apply_putaway_rule
from erpnext.stock.doctype.stock_settings.stock_settings import is_putaway_rule_enabled
from erpnext.stock.doctype.storage_location.storage_location import (
	storage_location_query,
	validate_item_storage_condition,
	validate_storage_location,
)

MODULE = "erpnext.stock.doctype.storage_location.storage_location"


class TestStorageLocationSelection(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch(f"{MODULE}._", side_effect=lambda text: text))
		self.enterContext(
			patch("frappe.throw", side_effect=lambda text, *args, **kwargs: self.fail_with(text))
		)
		self.db = self.enterContext(patch("frappe.db", Mock()))

	@staticmethod
	def fail_with(text):
		raise frappe.ValidationError(text)

	def test_unclassified_item_keeps_existing_location_behavior(self):
		with patch("frappe.get_cached_value", return_value=None), patch("frappe.get_cached_doc") as location:
			validate_item_storage_condition("Shelf", "Item")
			location.assert_not_called()

	def test_location_can_support_multiple_conditions(self):
		location = frappe._dict(
			supported_storage_conditions=[
				frappe._dict(storage_condition="Ambient"),
				frappe._dict(storage_condition="Cold"),
			]
		)
		with patch("frappe.get_cached_value", return_value="Cold"), patch(
			"frappe.get_cached_doc", return_value=location
		):
			validate_item_storage_condition("Shelf", "Item")
			location.supported_storage_conditions.pop()
			with self.assertRaisesRegex(frappe.ValidationError, "does not support Storage Condition Cold"):
				validate_item_storage_condition("Shelf", "Item")

	def test_empty_location_condition_does_not_mean_all_conditions(self):
		with patch("frappe.get_cached_doc", return_value=frappe._dict(supported_storage_conditions=[])):
			with self.assertRaisesRegex(frappe.ValidationError, "does not support"):
				validate_item_storage_condition("Shelf", "Item", "Cold")

	def test_unsaved_item_condition_takes_precedence_during_master_validation(self):
		with patch("frappe.get_cached_value") as get_item:
			validate_item_storage_condition("Shelf", "Item", "")
			get_item.assert_not_called()

	def test_ledger_rejects_incompatible_location_but_preserves_cancellation(self):
		entry = frappe._dict(
			warehouse="Warehouse", storage_location="Shelf", item_code="Item", is_cancelled=0
		)
		with patch(
			"frappe.get_cached_value", return_value=frappe._dict(warehouse="Warehouse", disabled=0)
		), patch(
			f"{MODULE}.validate_item_storage_condition", side_effect=frappe.ValidationError("Incompatible")
		) as validate:
			with self.assertRaisesRegex(frappe.ValidationError, "Incompatible"):
				validate_storage_location(entry)
			validate.assert_called_once_with("Shelf", "Item")
			validate.reset_mock()
			entry.is_cancelled = 1
			validate_storage_location(entry)
			validate.assert_not_called()

	def query(self, filters):
		return inspect.unwrap(storage_location_query)("Storage Location", "Shelf", "name", 0, 20, filters)

	def test_landed_cost_recreation_keeps_historical_condition_only_for_trusted_entry(self):
		entry = frappe._dict(
			warehouse="Warehouse", storage_location="Shelf", item_code="Item", is_cancelled=0
		)
		with patch(
			"frappe.get_cached_value", return_value=frappe._dict(warehouse="Warehouse", disabled=0)
		), patch(
			f"{MODULE}.validate_item_storage_condition", side_effect=frappe.ValidationError("Incompatible")
		) as validate, patch("frappe.flags", frappe._dict(warehouse_policy_lcv_entry=entry)):
			validate_storage_location(entry)
			validate.assert_not_called()
			with self.assertRaisesRegex(frappe.ValidationError, "Incompatible"):
				validate_storage_location(frappe._dict(entry))

	def test_picker_cannot_override_persisted_item_requirement(self):
		warehouse = frappe._dict(is_group=0, disabled=0)
		item = frappe._dict(storage_condition="Cold")
		with patch("frappe.get_cached_doc", side_effect=[warehouse, item]), patch(
			"frappe.has_permission", return_value=True
		), patch(f"{MODULE}.get_match_cond", return_value=""):
			self.query({"warehouse": "Warehouse", "item_code": "Item", "storage_condition": ""})
			self.assertEqual(self.db.sql.call_args.args[1]["condition"], "Cold")
			self.assertEqual(self.db.sql.call_args.args[1]["item_code"], "Item")

	def test_no_warehouse_does_not_return_other_company_locations(self):
		self.assertEqual(self.query({"item_code": "Item"}), [])
		self.db.sql.assert_not_called()

	def test_company_and_warehouse_permissions_apply_before_search(self):
		with patch("frappe.has_permission", side_effect=[True, False]), patch(
			"frappe.get_cached_doc", return_value=frappe._dict(is_group=0, disabled=0)
		):
			self.assertEqual(self.query({"warehouse": "Other Company Warehouse"}), [])
			self.db.sql.assert_not_called()

	def test_location_permission_is_required_for_direct_endpoint_call(self):
		with patch("frappe.has_permission", return_value=False):
			with self.assertRaisesRegex(frappe.ValidationError, "Not permitted"):
				self.query({"warehouse": "Warehouse"})
			self.db.sql.assert_not_called()

	def test_duplicate_item_preferences_are_rejected(self):
		item = frappe._dict(
			name="Item",
			storage_condition="",
			preferred_storage_locations=[
				frappe._dict(idx=1, warehouse="Warehouse", storage_location="Shelf"),
				frappe._dict(idx=2, warehouse="Warehouse", storage_location="Shelf"),
			],
		)
		with patch(f"{MODULE}.validate_storage_location_warehouse"), patch(
			"erpnext.stock.doctype.item.item._", side_effect=lambda text: text
		):
			with self.assertRaisesRegex(frappe.ValidationError, "cannot be repeated"):
				Item.validate_storage_preferences(item)

	def test_putaway_setting_preserves_existing_sites(self):
		for value, expected in [(None, True), (1, True), ("1", True), (0, False), ("0", False)]:
			self.db.get_singles_dict.return_value = {} if value is None else {"enable_putaway_rule": value}
			self.assertEqual(is_putaway_rule_enabled(), expected)

	def test_disabled_putaway_rejects_backend_call(self):
		with patch(
			"erpnext.stock.doctype.putaway_rule.putaway_rule.is_putaway_rule_enabled", return_value=False
		), patch("erpnext.stock.doctype.putaway_rule.putaway_rule._", side_effect=lambda text: text):
			with self.assertRaisesRegex(frappe.ValidationError, "Putaway Rules are disabled"):
				apply_putaway_rule("Stock Entry", [], "Company", purpose="Material Transfer")
