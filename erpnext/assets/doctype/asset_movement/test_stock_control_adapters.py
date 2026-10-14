# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.asset_movement.asset_movement import AssetMovement


class TestStockControlAssetAdapters(unittest.TestCase):
	def setUp(self):
		policy = patch(
			"erpnext.stock.stock_control.get_stock_control_policy",
			return_value=frappe._dict(handling_category="Normal"),
		)
		policy.start()
		self.addCleanup(policy.stop)
		locks = patch("erpnext.stock.stock_control.lock_stock_control_policies")
		locks.start()
		self.addCleanup(locks.stop)

	def movement(self, purpose="Transfer", request=None, change_custodian=0):
		return SimpleNamespace(
			company="Test Company",
			purpose=purpose,
			material_request_name=request,
			lock_material_request=Mock(),
			lock_stock_control_assets=Mock(),
			is_inbound_return=Mock(return_value=False),
			customer=None,
			assets=[frappe._dict(asset="Asset 1", target_location="QA", change_custodian=change_custodian)],
			get=lambda field: "QA release" if field == "stock_control_release_reason" else None,
		)

	def test_customer_request_transfer_is_not_internal_transfer(self):
		for request_type, operation in (("Customer Demo", "demo"), ("Customer Borrow / Loan", "loan")):
			with (
				self.subTest(request_type=request_type),
				patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
				patch("frappe.db", Mock(get_value=Mock(return_value=request_type))),
				patch("frappe.get_doc", return_value=frappe._dict(name="Asset 1", location="Main")),
				patch("erpnext.stock.stock_control.validate_asset_operation") as validate,
			):
				AssetMovement.before_submit(self.movement(request="Request 1"))
				self.assertEqual(validate.call_args.args[1], operation)

	def test_manual_issue_and_transfer_have_distinct_permissions(self):
		for purpose, operation in (
			("Transfer", "internal_transfer"),
			("Issue", "issue"),
			("Transfer and Issue", "issue"),
			("Reassignment", "issue"),
		):
			with (
				self.subTest(purpose=purpose),
				patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
				patch("frappe.get_doc", return_value=frappe._dict(name="Asset 1", location="Main")),
				patch("erpnext.stock.stock_control.validate_asset_operation") as validate,
			):
				AssetMovement.before_submit(self.movement(purpose=purpose, change_custodian=1))
				self.assertEqual(validate.call_args.args[1], operation)
				self.assertEqual(validate.call_args.kwargs["release_reason"], "QA release")

	def test_verified_inbound_receipt_does_not_block_return(self):
		with (
			patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
			patch("frappe.get_doc", return_value=frappe._dict(name="Asset 1", location="Main")),
			patch("erpnext.stock.stock_control.validate_asset_operation") as validate,
			patch("erpnext.stock.stock_control.lock_stock_control_policies"),
		):
			movement = self.movement(purpose="Receipt")
			movement.is_inbound_return.return_value = True
			AssetMovement.before_submit(movement)
			validate.assert_not_called()

	def test_department_only_reassignment_does_not_dispatch_asset(self):
		with (
			patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
			patch("frappe.get_doc", return_value=frappe._dict(name="Asset 1", location="Main")),
			patch("erpnext.stock.stock_control.validate_asset_operation") as validate,
		):
			AssetMovement.before_submit(self.movement(purpose="Reassignment"))
			validate.assert_not_called()

	def test_all_provenance_items_lock_before_assets(self):
		movement = self.movement()
		movement.assets.append(frappe._dict(asset="Asset 2"))
		events = []
		assets = {
			"Asset 1": frappe._dict(name="Asset 1", item_code="Asset Item 1"),
			"Asset 2": frappe._dict(name="Asset 2", item_code="Asset Item 2"),
		}
		with (
			patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
			patch("frappe.get_doc", side_effect=lambda doctype, name: assets[name]),
			patch(
				"erpnext.stock.stock_control.get_asset_stock_control_items",
				side_effect=lambda asset: {asset.item_code, "Original Stock Item"},
			),
			patch(
				"erpnext.stock.stock_control.lock_stock_control_policies",
				side_effect=lambda **kwargs: events.append("policies"),
			),
			patch(
				"erpnext.stock.stock_control.lock_stock_control_items",
				side_effect=lambda *args: events.append("items_assets"),
			) as lock,
		):
			AssetMovement.lock_stock_control_assets(movement)
			self.assertEqual(events, ["policies", "items_assets"])
			lock.assert_called_once_with(
				{"Asset Item 1", "Asset Item 2", "Original Stock Item"}, ["Asset 1", "Asset 2"]
			)

	def test_scrap_denied_before_disposal_date_changes(self):
		from erpnext.assets.doctype.asset.depreciation import scrap_asset

		asset = Mock()
		with (
			patch("frappe.has_permission"),
			patch("frappe.get_doc", return_value=asset),
			patch("erpnext.stock.stock_control.validate_asset_operation", side_effect=RuntimeError("Frozen")),
		):
			with self.assertRaisesRegex(RuntimeError, "Frozen"):
				scrap_asset("Asset 1")
			asset.db_set.assert_not_called()

	def test_split_denied_before_quantity_or_finance_changes(self):
		from erpnext.assets.doctype.asset.asset import split_asset

		asset = Mock()
		with (
			patch("frappe.get_doc", return_value=asset),
			patch("frappe.flags", frappe._dict()),
			patch(
				"erpnext.stock.stock_control.validate_asset_operation", side_effect=RuntimeError("Frozen")
			) as validate,
			patch("erpnext.assets.doctype.asset.asset.process_asset_split") as split,
		):
			with self.assertRaisesRegex(RuntimeError, "Frozen"):
				split_asset("Asset 1", 1)
			validate.assert_called_once_with(asset, "manufacture")
			split.assert_not_called()

	def test_forged_receipt_cannot_move_frozen_stock_out_of_qa(self):
		with (
			patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
			patch("frappe.get_doc", return_value=frappe._dict(name="Asset 1", location="QA")),
			patch(
				"erpnext.stock.stock_control.get_stock_control_policy",
				return_value=frappe._dict(handling_category="QA"),
			),
			patch(
				"erpnext.stock.stock_control.validate_asset_operation", side_effect=RuntimeError("Frozen")
			) as validate,
		):
			movement = self.movement(purpose="Receipt")
			movement.assets[0].target_location = "Main"
			with self.assertRaisesRegex(RuntimeError, "Frozen"):
				AssetMovement.before_submit(movement)
			self.assertEqual(validate.call_args.args[1], "internal_transfer")

	def test_even_verified_return_from_qa_requires_release_gate(self):
		with (
			patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
			patch("frappe.get_doc", return_value=frappe._dict(name="Asset 1", location="QA")),
			patch(
				"erpnext.stock.stock_control.get_stock_control_policy",
				return_value=frappe._dict(handling_category="QA"),
			),
			patch(
				"erpnext.stock.stock_control.validate_asset_operation",
				side_effect=RuntimeError("Release required"),
			),
		):
			movement = self.movement(purpose="Receipt")
			movement.is_inbound_return.return_value = True
			movement.assets[0].target_location = "Main"
			with self.assertRaisesRegex(RuntimeError, "Release required"):
				AssetMovement.before_submit(movement)

	def test_initial_same_location_receipt_is_registration(self):
		with (
			patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
			patch("frappe.get_doc", return_value=frappe._dict(name="Asset 1", location="QA")),
			patch("erpnext.stock.stock_control.validate_asset_operation") as validate,
		):
			movement = self.movement(purpose="Receipt")
			AssetMovement.before_submit(movement)
			validate.assert_not_called()

	def test_receipt_new_custodian_requires_issue_permission(self):
		with (
			patch("erpnext.stock.stock_control.is_stock_control_enabled", return_value=True),
			patch(
				"frappe.get_doc",
				return_value=frappe._dict(name="Asset 1", location="QA", custodian="Old Employee"),
			),
			patch(
				"erpnext.stock.stock_control.validate_asset_operation",
				side_effect=RuntimeError("Issue denied"),
			) as validate,
		):
			movement = self.movement(purpose="Receipt")
			movement.assets[0].to_employee = "New Employee"
			with self.assertRaisesRegex(RuntimeError, "Issue denied"):
				AssetMovement.before_submit(movement)
			validate.assert_called_once_with(unittest.mock.ANY, "issue", lock=False)
