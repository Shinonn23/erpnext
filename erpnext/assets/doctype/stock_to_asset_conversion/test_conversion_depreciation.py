"""Offline proof: every converted object uses the accountant's chosen native depreciation plan."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.asset import asset as asset_module
from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion


class DraftAsset(frappe._dict):
	def set(self, field, value):
		self[field] = value

	def set_missing_values(self):
		asset_module.Asset.set_missing_values(self)

	def is_new(self):
		return not self.name

	def insert(self, **_kwargs):
		self.name = "ASSET-1"
		self.docstatus = 0

	def save(self, **_kwargs):
		pass


class TestConversionDepreciation(TestCase):
	def setUp(self):
		self.database = Mock()
		self.database.exists.return_value = False
		self.category_books = {
			"One Period": [self.book(1)],
			"Normal": [self.book(36, salvage=10)],
		}
		for patcher in (
			patch.object(frappe, "db", self.database),
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(frappe, "get_cached_doc", side_effect=self.cached_doc),
			patch.object(frappe, "get_cached_value", side_effect=self.cached_value),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def book(self, periods, salvage=0):
		return frappe._dict(
			depreciation_method="Straight Line",
			total_number_of_depreciations=periods,
			frequency_of_depreciation=1,
			salvage_value_percentage=salvage,
			depreciation_start_date="2027-01-31",
		)

	def cached_doc(self, doctype, name):
		self.assertEqual(doctype, "Asset Category")
		return frappe._dict(finance_books=self.category_books[name])

	def cached_value(self, doctype, name, field):
		if doctype == "Item":
			return "Normal"
		self.assertEqual((doctype, field), ("Asset Category", "is_demo_category"))
		return 0

	def sync(self, value, category, existing=None, treatment="Capitalize"):
		asset = existing or DraftAsset(asset_owner="Company", flags=frappe._dict(), finance_books=[])
		output = frappe._dict(
			asset=asset.name,
			asset_item_code="ASSET-ITEM",
			source_item_row="ROW-1",
			source_unit_index=1,
			asset_category=category,
			asset_location="Warehouse",
			asset_value=value,
			accounting_treatment=treatment,
			db_set=Mock(),
		)
		doc = SimpleNamespace(
			name="CONV-1",
			assets=[output],
			stock_items=[frappe._dict(name="ROW-1", warehouse="STOCK")],
			company="CD",
			material_request=None,
			posting_date="2027-01-01",
		)
		self.database.exists.return_value = bool(existing)
		with (
			patch.object(frappe, "new_doc", return_value=asset),
			patch.object(frappe, "get_doc", return_value=asset),
		):
			conversion.StocktoAssetConversion.sync_draft_assets(doc)
		return asset

	def test_value_does_not_choose_the_category_or_depreciation_plan(self):
		for value in (1, 9999.99, 10000, 10000.01, 1000000):
			for category, periods in (("One Period", 1), ("Normal", 36)):
				with self.subTest(value=value, category=category):
					asset = self.sync(value, category)
					self.assertEqual(asset.asset_category, category)
					self.assertEqual(asset.calculate_depreciation, 1)
					self.assertEqual(asset.net_purchase_amount, value)
					self.assertEqual(asset.finance_books[0]["total_number_of_depreciations"], periods)

	def test_immediate_expense_generates_zero_value_without_depreciation(self):
		asset = self.sync(8500, "Normal", treatment="Expense Immediately")
		self.assertEqual(asset.net_purchase_amount, 0)
		self.assertEqual(asset.calculate_depreciation, 0)
		self.assertEqual(conversion.get_native_conversion_source(asset), "CONV-1")
		asset.opening_accumulated_depreciation = 1
		asset.opening_number_of_booked_depreciations = 1
		asset.additional_asset_cost = 1
		self.sync(8500, "Normal", existing=asset, treatment="Expense Immediately")
		self.assertEqual(asset.opening_accumulated_depreciation, 0)
		self.assertEqual(asset.opening_number_of_booked_depreciations, 0)
		self.assertEqual(asset.additional_asset_cost, 0)

	def test_one_period_zero_residual_uses_category_date_and_native_schedule(self):
		asset = self.sync(10000, "One Period")
		self.assertEqual(asset.finance_books[0]["expected_value_after_useful_life"], 0)
		self.assertEqual(asset.finance_books[0]["depreciation_start_date"], "2027-01-31")
		self.assertNotEqual(asset.finance_books[0]["depreciation_start_date"], asset.available_for_use_date)

	def test_category_change_replaces_plan_but_same_category_keeps_accountant_edits(self):
		asset = self.sync(10000, "Normal")
		asset.finance_books[0]["total_number_of_depreciations"] = 24
		self.sync(10000, "Normal", existing=asset)
		self.assertEqual(asset.finance_books[0]["total_number_of_depreciations"], 24)
		self.sync(10000, "One Period", existing=asset)
		self.assertEqual(asset.finance_books[0]["total_number_of_depreciations"], 1)

	def test_submission_uses_asset_lifecycle_without_a_custom_depreciation_journal(self):
		asset = Mock(is_demo_asset=0, flags=frappe._dict())
		doc = SimpleNamespace(name="CONV-1", assets=[frappe._dict(asset="ASSET-1")])
		with patch.object(frappe, "get_doc", return_value=asset), patch.object(frappe, "new_doc") as new_doc:
			conversion.StocktoAssetConversion.submit_generated_assets(doc)
		asset.submit.assert_called_once_with()
		new_doc.assert_not_called()
		self.assertEqual(conversion.get_native_conversion_source(asset), doc.name)

	def test_conversion_capitalizes_to_the_chosen_category_account(self):
		row = frappe._dict(name="ROW-1", amount=10000, warehouse="STOCK")
		output = frappe._dict(
			source_item_row=row.name,
			asset_item_code="ASSET-ITEM",
			asset_category="One Period",
			asset_value=10000,
			db_set=Mock(),
		)
		doc = SimpleNamespace(
			name="CONV-1",
			company="CD",
			stock_items=[row],
			assets=[output],
			use_item_inventory_account=False,
			get_inventory_account_map=lambda: {},
			get_stock_ledger_details=lambda: {},
			get_debit_field_precision=lambda: None,
			precision=lambda _field: None,
			get_inventory_account_dict=lambda *_args: {"account": "Inventory", "account_currency": "THB"},
			get_gl_dict=lambda values, *_args, **_kwargs: values,
		)
		with (
			patch.object(conversion.erpnext, "is_perpetual_inventory_enabled", return_value=True),
			patch.object(conversion.erpnext, "get_default_cost_center", return_value="Main"),
			patch.object(
				conversion, "get_asset_category_account", return_value="Selected Fixed Asset"
			) as account,
		):
			entries = conversion.StocktoAssetConversion.get_gl_entries(doc)
		account.assert_called_once_with("fixed_asset_account", asset_category="One Period", company="CD")
		self.assertEqual(entries[0]["credit"], 10000)
		self.assertEqual((entries[1]["account"], entries[1]["debit"]), ("Selected Fixed Asset", 10000))

	def test_serial_costs_use_each_machine_in_the_selected_warehouse(self):
		doc = SimpleNamespace(name="CONV-1", posting_date="2026-11-11", posting_time="12:00:00")
		row = frappe._dict(item_code="MACHINE", warehouse="STOCK")
		with patch.object(conversion.SerialNoValuation, "get_serial_no_wise_incoming_rate", return_value={"S1": 100, "S2": 200}) as lookup:
			rates = conversion.StocktoAssetConversion.get_serial_costs(doc, row, ["S1", "S2"])
		self.assertEqual(rates, {"S1": 100, "S2": 200})
		lookup.assert_called_once_with(["S1", "S2"])
