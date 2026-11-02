"""Offline select-only Asset picker and bounded physical metadata contracts."""

import inspect
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.material_request import material_request as requests


class TestAssetRequestDetails(TestCase):
	def setUp(self):
		self.asset = frappe._dict(
			name="ASSET-A",
			asset_name="Demo A",
			asset_serial_number="SERIAL-A",
			company="CD",
			item_code="MODEL",
			docstatus=1,
			status="Submitted",
			location="Stock Room",
			is_demo_asset=1,
			demo_loan_status="Available",
			custodian=None,
			gross_purchase_amount=99999,
			finance_books=["PRIVATE"],
		)
		self.item = frappe._dict(is_fixed_asset=1, disabled=0, stock_uom="Unit")
		self.denied = set()
		self.db = Mock()
		self.db.get_value.side_effect = lambda dt, name, fields, **kwargs: (
			self.asset if dt == "Asset" else self.item
		)
		self.db.exists.return_value = True
		for context in (
			patch.object(frappe, "db", self.db),
			patch.object(frappe, "has_permission", side_effect=self.permission),
			patch.object(frappe, "throw", side_effect=ValueError),
			patch.object(requests, "_", lambda text: text),
		):
			context.start()
			self.addCleanup(context.stop)

	def permission(self, doctype, ptype, doc=None, throw=False, **kwargs):
		if ptype != "select" or (doctype, doc) in self.denied:
			raise frappe.PermissionError
		return True

	def details(self, **kwargs):
		return requests.get_asset_request_details(
			asset_name="ASSET-A",
			company=kwargs.pop("company", "CD"),
			material_request_type=kwargs.pop("material_request_type", "Customer Demo"),
			**kwargs,
		)

	def test_select_only_requester_receives_only_physical_fields(self):
		result = self.details(item_code="MODEL")
		self.assertEqual(
			set(result),
			{"name", "asset_name", "asset_serial_number", "item_code", "company", "location", "stock_uom"},
		)
		self.assertEqual((result["item_code"], result["stock_uom"]), ("MODEL", "Unit"))
		frappe.has_permission.assert_any_call("Company", "select", doc="CD", throw=True)
		frappe.has_permission.assert_any_call("Asset", "select", doc="ASSET-A", throw=True)
		frappe.has_permission.assert_any_call("Item", "select", doc="MODEL", throw=True)
		self.assertNotIn("gross_purchase_amount", result)
		self.assertNotIn("custodian", result)

	def test_denied_company_or_asset_user_permission_prevents_lookup(self):
		for denied in (("Company", "CD"), ("Asset", "ASSET-A")):
			self.denied = {denied}
			self.db.get_value.reset_mock()
			with self.subTest(denied=denied), self.assertRaises(frappe.PermissionError):
				self.details()
			self.db.get_value.assert_not_called()

	def test_model_company_and_demo_mismatches_are_rejected(self):
		with self.assertRaises(ValueError):
			self.details(item_code="OTHER-MODEL")
		with self.assertRaises(ValueError):
			self.details(company="UB")
		self.asset.is_demo_asset = 0
		with self.assertRaises(ValueError):
			self.details()

	def test_unsubmitted_or_unusable_asset_is_rejected(self):
		for field, value in (("docstatus", 0), ("status", "Sold"), ("status", "Out of Order")):
			with patch.dict(self.asset, {field: value}):
				with self.subTest(field=field, value=value), self.assertRaises(ValueError):
					self.details()

	def test_waiting_lookup_allows_loan_or_repair_without_claiming_ready(self):
		self.asset.demo_loan_status = "With Customer"
		self.asset.status = "Out of Order"
		self.assertEqual(self.details(acquisition_method="Wait Return")["name"], "ASSET-A")
		with self.assertRaises(ValueError):
			self.details(acquisition_method="Ready Asset")

	def test_asset_transfer_preserves_employee_custody_scope_without_disclosing_custodian(self):
		self.asset.custodian = "EMPLOYEE"
		result = self.details(material_request_type="Asset Transfer")
		self.assertNotIn("custodian", result)
		with self.assertRaises(ValueError):
			self.details(material_request_type="Customer Borrow / Loan")

	def test_item_select_and_enabled_fixed_asset_checks_still_apply(self):
		self.denied.add(("Item", "MODEL"))
		with self.assertRaises(frappe.PermissionError):
			self.details()
		self.denied.clear()
		for field, value in (("is_fixed_asset", 0), ("disabled", 1)):
			with patch.dict(self.item, {field: value}):
				with self.subTest(field=field), self.assertRaises(ValueError):
					self.details()

	def test_bulk_picker_keeps_native_record_filtering_for_select_users(self):
		asset = frappe._dict(name="ASSET-A", item_code="MODEL")
		with (
			patch.object(requests, "get_available_assets", return_value=[asset]) as available,
			patch.object(frappe, "get_list", return_value=[asset]) as scoped,
			patch.object(frappe, "get_cached_value", return_value="Unit"),
		):
			self.assertEqual(requests.get_assets_for_material_request("CD", "Stock Room"), [asset])
		available.assert_called_once_with(None, "CD", location="Stock Room", exclude_custodian=False)
		self.assertNotIn("ignore_permissions", scoped.call_args.kwargs)
		self.assertEqual(
			scoped.call_args.kwargs["fields"],
			["name", "asset_name", "asset_serial_number", "item_code", "location"],
		)
		frappe.has_permission.assert_any_call("Company", "select", doc="CD", throw=True)

	def test_grid_picker_uses_select_and_preserves_native_match_condition(self):
		self.db.sql.return_value = [["ASSET-A", "Demo A", "MODEL", "Stock Room"]]
		with patch("erpnext.controllers.queries.get_match_cond", return_value=" and NATIVE_SCOPE") as match:
			result = inspect.unwrap(requests.asset_query)(
				"Asset",
				"A",
				"name",
				0,
				20,
				{"company": "CD", "material_request_type": "Customer Demo", "item_code": "MODEL"},
			)
		self.assertEqual(result[0][0], "ASSET-A")
		match.assert_called_once_with("Asset")
		query, values = self.db.sql.call_args.args
		self.assertIn("NATIVE_SCOPE", query)
		self.assertIn("tabAsset.is_demo_asset=1", query)
		self.assertEqual((values["company"], values["item_code"]), ("CD", "MODEL"))
		frappe.has_permission.assert_any_call("Asset", "select", throw=True)
		frappe.has_permission.assert_any_call("Company", "select", doc="CD", throw=True)

	def test_denied_company_stops_grid_picker_before_sql(self):
		self.denied.add(("Company", "CD"))
		with self.assertRaises(frappe.PermissionError):
			inspect.unwrap(requests.asset_query)(
				"Asset", "A", "name", 0, 20, {"company": "CD", "material_request_type": "Customer Demo"}
			)
		self.db.sql.assert_not_called()
