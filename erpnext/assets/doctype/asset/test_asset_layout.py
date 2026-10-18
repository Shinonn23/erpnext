import json
import os
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.asset import asset as asset_module


class TestAssetLayout(TestCase):
	def layout(self):
		path = os.path.join(os.path.dirname(asset_module.__file__), "asset.json")
		with open(path, encoding="utf-8") as handle:
			data = json.load(handle)
		return data, {field["fieldname"]: field for field in data["fields"]}

	def test_field_order_lists_every_field_once(self):
		data, fields = self.layout()
		self.assertEqual(len(data["field_order"]), len(set(data["field_order"])))
		self.assertEqual(set(data["field_order"]), set(fields))

	def test_asset_owner_follows_asset_type_in_asset_details(self):
		data, fields = self.layout()
		order = data["field_order"]
		self.assertEqual(order[0], "asset_details_section")
		self.assertEqual(order[order.index("asset_type") + 1], "asset_owner")
		self.assertLess(order.index("asset_owner"), order.index("column_break_3"))
		self.assertNotIn("asset_owner_section", fields)
		self.assertNotIn("column_break_yeds", fields)

	def test_links_behind_the_old_supplier_and_customer_choices_are_hidden_data(self):
		_data, fields = self.layout()
		for fieldname in ("asset_owner_company", "supplier", "customer"):
			self.assertTrue(fields[fieldname]["hidden"], fieldname)

	def test_main_section_ends_before_the_source_references(self):
		# Other apps anchor their sections after the last field of this section.
		data, fields = self.layout()
		order = data["field_order"]
		self.assertEqual(order[order.index("calculate_depreciation") + 1], "tracking_details_section")
		self.assertEqual(fields["tracking_details_section"]["fieldtype"], "Section Break")

	def test_total_cost_sits_with_the_purchase_details(self):
		data, fields = self.layout()
		order = data["field_order"]
		self.assertEqual(order[order.index("total_asset_cost") + 1], "depreciation_tab")
		self.assertGreater(order.index("total_asset_cost"), order.index("purchase_details_section"))
		self.assertEqual(fields["total_asset_cost"]["fieldtype"], "Currency")

	def test_demo_flag_follows_the_asset_category_and_is_not_entered(self):
		_data, fields = self.layout()
		flag = fields["is_demo_asset"]
		self.assertTrue(flag["hidden"])
		self.assertTrue(flag["read_only"])
		self.assertFalse(flag.get("in_list_view"))
		self.assertEqual(flag["fetch_from"], "asset_category.is_demo_category")

	def test_asset_category_marks_demo_categories(self):
		path = os.path.join(os.path.dirname(asset_module.__file__), "..", "asset_category", "asset_category.json")
		with open(path, encoding="utf-8") as handle:
			data = json.load(handle)
		fields = {field["fieldname"]: field for field in data["fields"]}
		self.assertEqual(fields["is_demo_category"]["fieldtype"], "Check")
		self.assertIn("is_demo_category", data["field_order"])
		self.assertEqual(set(data["field_order"]), set(fields))

	def test_demo_flag_is_derived_from_the_category_of_a_draft_asset(self):
		for category_is_demo in (1, 0):
			asset = Mock(spec=asset_module.Asset)
			asset.docstatus = 0
			asset.asset_category = "Demo Equipment"
			asset.item_code = None
			asset.asset_owner = "Company"
			asset.asset_owner_company = "CD"
			asset.get.return_value = [object()]
			asset.is_demo_asset = 1 - category_is_demo
			with patch.object(asset_module.frappe, "get_cached_value", return_value=category_is_demo) as lookup:
				asset_module.Asset.set_missing_values(asset)
			with self.subTest(category_is_demo=category_is_demo):
				self.assertEqual(asset.is_demo_asset, category_is_demo)
				lookup.assert_called_once_with("Asset Category", "Demo Equipment", "is_demo_category")

	def test_submitted_asset_keeps_its_demo_flag(self):
		asset = Mock(spec=asset_module.Asset)
		asset.docstatus = 1
		asset.asset_category = "Furniture"
		asset.item_code = None
		asset.asset_owner = "Company"
		asset.asset_owner_company = "CD"
		asset.get.return_value = [object()]
		asset.is_demo_asset = 1
		with patch.object(asset_module.frappe, "get_cached_value", return_value=0):
			asset_module.Asset.set_missing_values(asset)
		self.assertEqual(asset.is_demo_asset, 1)

	def test_demo_status_only_shows_for_demo_assets(self):
		_data, fields = self.layout()
		self.assertEqual(fields["demo_loan_status"]["depends_on"], "eval:doc.is_demo_asset")

	def test_owner_can_filter_the_asset_list(self):
		_data, fields = self.layout()
		self.assertTrue(fields["asset_owner"]["in_standard_filter"])

	def test_manual_depreciation_entries_need_an_asset_category(self):
		# Supplier-owned equipment has no Item, so no Asset Category and no depreciation accounts to look up.
		asset = Mock(spec=asset_module.Asset)
		asset.asset_category = None
		with patch.object(asset_module, "get_depreciation_accounts", side_effect=AssertionError("looked up")) as accounts:
			self.assertEqual(asset_module.Asset.get_manual_depreciation_entries(asset), [])
		accounts.assert_not_called()

	def test_manual_depreciation_entries_still_need_configured_accounts(self):
		asset = Mock(spec=asset_module.Asset)
		asset.asset_category = "Equipment"
		asset.company = "Company"
		with patch.object(asset_module, "get_depreciation_accounts", side_effect=frappe.ValidationError("no accounts")):
			with self.assertRaises(frappe.ValidationError):
				asset_module.Asset.get_manual_depreciation_entries(asset)
