"""Offline checks for expensing Demo stock while retaining a zero-valued tracking Asset."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.asset import asset as asset_module
from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion


class TestExpenseConversion(TestCase):
	def setUp(self):
		self.database = Mock()
		for patcher in (
			patch.object(frappe, "db", self.database),
			patch.object(frappe, "throw", side_effect=ValueError("rejected")),
			patch.object(conversion, "_", lambda text: text),
			patch.object(asset_module, "_", lambda text: text),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_expense_uses_actual_stock_value_and_rejects_nonperpetual_inventory(self):
		for perpetual in (True, False):
			with self.subTest(perpetual=perpetual):
				row = frappe._dict(name="ROW-1", amount=8500, warehouse="STOCK", idx=1)
				output = frappe._dict(
					source_item_row=row.name,
					asset_item_code="DEMO",
					asset_category="Demo",
					asset_value=8200,
					accounting_treatment="Expense Immediately",
					expense_account="Demo Expense",
					db_set=Mock(),
				)
				doc = SimpleNamespace(
					name="CONV-1",
					company="CD",
					stock_items=[row],
					assets=[output],
					use_item_inventory_account=False,
					get_inventory_account_map=lambda: {},
					get_stock_ledger_details=lambda: {row.name: [frappe._dict(stock_value_difference=-8200)]},
					get_debit_field_precision=lambda: None,
					precision=lambda _field: None,
					get_inventory_account_dict=lambda *_args: {
						"account": "Inventory",
						"account_currency": "THB",
					},
					get_company_default=lambda _field: "Original Purchase Expense",
					get_gl_dict=lambda values, *_args, **_kwargs: values,
				)
				with (
					patch.object(
						conversion.erpnext, "is_perpetual_inventory_enabled", return_value=perpetual
					),
					patch.object(conversion.erpnext, "get_default_cost_center", return_value="Main"),
					patch.object(conversion, "get_asset_category_account") as fixed_account,
				):
					if not perpetual:
						with self.assertRaises(ValueError):
							conversion.StocktoAssetConversion.get_gl_entries(doc)
						continue
					entries = conversion.StocktoAssetConversion.get_gl_entries(doc)
				fixed_account.assert_not_called()
				self.assertEqual(entries[0]["account"], "Inventory")
				self.assertEqual(entries[0]["credit"], 8200)
				self.assertEqual((entries[1]["account"], entries[1]["debit"]), ("Demo Expense", 8200))
				self.assertEqual(output.asset_value, 8200)
				output.asset_value = 8500
				with (
					patch.object(conversion.erpnext, "is_perpetual_inventory_enabled", return_value=True),
					patch.object(conversion.erpnext, "get_default_cost_center", return_value="Main"),
					self.assertRaises(ValueError),
				):
					conversion.StocktoAssetConversion.get_gl_entries(doc)

	def test_purchase_document_price_is_company_currency_per_stock_unit(self):
		purchase = frappe._dict(
			docstatus=1,
			is_return=0,
			company="CD",
			posting_date="2026-11-01",
			taxes=[],
			items=[
				frappe._dict(
					name="PR-ROW", item_code="STOCK", stock_qty=2, base_net_amount=17000, serial_no="SER-1"
				)
			],
			check_permission=Mock(),
		)
		output = frappe._dict(
			idx=1,
			source_item_row="ROW-1",
			purchase_reference_type="Purchase Receipt",
			purchase_reference="PR-1",
			purchase_reference_item="PR-ROW",
			purchase_unit_price=1,
			source_serial_no="SER-1",
		)
		doc = SimpleNamespace(
			company="CD",
			posting_date="2026-11-12",
			stock_items=[frappe._dict(name="ROW-1", item_code="STOCK")],
		)
		doc.validate_purchase_provenance = lambda output, row: (
			conversion.StocktoAssetConversion.validate_purchase_provenance(doc, output, row)
		)
		with patch.object(frappe, "get_doc", return_value=purchase):
			conversion.StocktoAssetConversion.set_purchase_unit_price(doc, output)
		self.assertEqual(output.purchase_unit_price, 8500)
		purchase.check_permission.assert_called_once_with("read")
		purchase.company = "OTHER"
		with patch.object(frappe, "get_doc", return_value=purchase), self.assertRaises(ValueError):
			conversion.StocktoAssetConversion.set_purchase_unit_price(doc, output)

	def test_expense_threshold_is_strictly_below_10000(self):
		self.database.get_value.return_value = "Customer Demo"
		for price, allowed in ((9999.99, True), (10000, False), (-1, False)):
			output = frappe._dict(
				accounting_treatment="Expense Immediately",
				purchase_unit_price=price,
				expense_account="Demo Expense",
			)
			doc = SimpleNamespace(
				assets=[output],
				material_request="MR-1",
				company="CD",
				set_purchase_unit_price=lambda _row: None,
				validate_demo_category=lambda *_args: None,
			)
			with (
				patch.object(conversion.erpnext, "is_perpetual_inventory_enabled", return_value=True),
				patch.object(frappe, "get_cached_value", return_value="THB"),
				patch.object(
					frappe,
					"get_cached_doc",
					return_value=frappe._dict(company="CD", is_group=0, disabled=0, root_type="Expense"),
				),
			):
				if allowed:
					conversion.StocktoAssetConversion.validate_accounting_treatments(doc)
				else:
					with self.assertRaises(ValueError):
						conversion.StocktoAssetConversion.validate_accounting_treatments(doc)

	def test_zero_value_exception_requires_matching_conversion_unit(self):
		output = frappe._dict(
			asset="ASSET-1",
			accounting_treatment="Expense Immediately",
			source_item_row="ROW-1",
			source_unit_index=1,
			asset_item_code="DEMO",
		)
		asset = frappe._dict(
			name="ASSET-1",
			company="CD",
			stock_to_asset_conversion="CONV-1",
			stock_to_asset_conversion_item="ROW-1",
			stock_to_asset_conversion_unit=1,
			item_code="DEMO",
			flags=frappe._dict(),
			asset_category="Demo",
			net_purchase_amount=0,
			additional_asset_cost=0,
			calculate_depreciation=0,
		)
		with patch.object(
			frappe, "get_doc", return_value=frappe._dict(docstatus=1, company="CD", assets=[output])
		):
			self.assertTrue(conversion.is_expensed_conversion_asset(asset))
			asset_module.Asset.validate_asset_values(asset)
			for field in (
				"calculate_depreciation",
				"opening_accumulated_depreciation",
				"opening_number_of_booked_depreciations",
			):
				asset[field] = 1
				with self.assertRaises(ValueError):
					asset_module.Asset.validate_asset_values(asset)
				asset[field] = 0
			asset.name = "FORGED-ASSET"
			self.assertFalse(conversion.is_expensed_conversion_asset(asset))

	def test_manual_purchase_price_requires_price_and_explanation(self):
		output = frappe._dict(
			purchase_price_source="Manual Evidence",
			purchase_unit_price=8500,
			purchase_price_reason="Original invoice reviewed",
		)
		doc = SimpleNamespace(name="CONV-1")
		conversion.StocktoAssetConversion.set_purchase_unit_price(doc, output)
		self.assertEqual(output.purchase_unit_price, 8500)
		for field, value in (("purchase_unit_price", 0), ("purchase_price_reason", "")):
			invalid = frappe._dict(output.copy())
			invalid[field] = value
			with self.assertRaises(ValueError):
				conversion.StocktoAssetConversion.set_purchase_unit_price(doc, invalid)

	def test_purchase_source_must_match_the_conversion_serial_and_batch(self):
		doc = SimpleNamespace(company="CD")
		output = frappe._dict(source_serial_no="SER-1", source_batch_no="BATCH-1")
		purchase_row = frappe._dict(serial_and_batch_bundle="BUNDLE-1")
		self.database.exists.return_value = True
		conversion.StocktoAssetConversion.validate_purchase_provenance(doc, output, purchase_row)
		self.database.exists.assert_called_once_with(
			"Serial and Batch Entry",
			{"parent": "BUNDLE-1", "is_cancelled": 0, "serial_no": "SER-1", "batch_no": "BATCH-1"},
		)
		self.database.exists.return_value = False
		with self.assertRaises(ValueError):
			conversion.StocktoAssetConversion.validate_purchase_provenance(doc, output, purchase_row)
		with self.assertRaises(ValueError):
			conversion.StocktoAssetConversion.validate_purchase_provenance(doc, frappe._dict(), purchase_row)

	def test_expense_submission_does_not_require_depreciation_plan_or_accounts(self):
		output = frappe._dict(
			asset="ASSET-1",
			asset_item_code="DEMO",
			source_item_row="ROW-1",
			asset_value=8500,
			accounting_treatment="Expense Immediately",
		)
		asset = frappe._dict(
			name="ASSET-1",
			docstatus=0,
			stock_to_asset_conversion="CONV-1",
			asset_category="Demo",
			location="Office",
			net_purchase_amount=0,
		)
		doc = SimpleNamespace(
			name="CONV-1",
			company="CD",
			material_request=None,
			pick_list=None,
			assets=[output],
			stock_items=[frappe._dict(name="ROW-1", item_code="STOCK", warehouse="Warehouse", serial_and_batch_bundle="BUNDLE", idx=1)],
			sync_draft_assets=Mock(),
			validate_bundle=Mock(),
			get_serial_costs=Mock(return_value={"SER-1": 8500}),
		)
		self.database.get_value.return_value = None
		with (
			patch.object(frappe, "get_doc", return_value=asset),
			patch.object(
				frappe, "get_cached_doc", return_value=frappe._dict(has_serial_no=1, has_batch_no=0, use_serial_no_wise_valuation=1)
			) as category,
			patch.object(frappe, "get_all", return_value=["SER-1"]),
			patch.object(conversion, "get_depreciation_accounts") as accounts,
		):
			conversion.StocktoAssetConversion.before_submit(doc)
			accounts.assert_not_called()
			self.assertEqual(category.call_count, 2)
			category.assert_called_with("Item", "STOCK")
			self.assertEqual(output.asset_value, 8500)
			output.accounting_treatment = "Capitalize"
			with self.assertRaises(ValueError):
				conversion.StocktoAssetConversion.before_submit(doc)
