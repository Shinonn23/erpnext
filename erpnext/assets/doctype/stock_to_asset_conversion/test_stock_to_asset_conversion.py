import frappe
from frappe.utils import flt, get_last_day, today

from erpnext.accounts.general_ledger import is_immutable_ledger_enabled
from erpnext.assets.doctype.asset.test_asset import create_fixed_asset_item
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse, get_warehouse_account
from erpnext.stock.utils import get_stock_balance
from erpnext.tests.utils import ERPNextTestSuite


class TestStockToAssetConversionIntegration(ERPNextTestSuite):
	company = "_Test Company"
	fixed_asset_account = "_Test Fixed Asset - _TC"
	depreciation_account = "_Test Depreciations - _TC"
	accumulated_depreciation_account = "_Test Accumulated Depreciations - _TC"
	unit_value = 120
	received_qty = 3

	def setUp(self):
		super().setUp()
		self.fixture_id = frappe.generate_hash(length=8)
		self.posting_date = today()
		company = frappe.get_doc("Company", self.company)
		company.enable_perpetual_inventory = 1
		company.stock_adjustment_account = "_Test Account Stock Adjustment - _TC"
		company.save()
		self.addCleanup(frappe.clear_cache, doctype="Company")
		self.location = (
			frappe.get_doc({"doctype": "Location", "location_name": f"Conversion source {self.fixture_id}"})
			.insert()
			.name
		)
		warehouse_name = f"Conversion stock {self.fixture_id}"
		self.inventory_account = get_warehouse_account(warehouse_name, self.company)
		self.warehouse = create_warehouse(
			warehouse_name,
			properties={"asset_location": self.location, "account": self.inventory_account},
		)
		self.storage_location = (
			frappe.get_doc(
				{
					"doctype": "Storage Location",
					"warehouse": self.warehouse,
					"location_code": f"Conversion shelf {self.fixture_id}",
				}
			)
			.insert()
			.name
		)
		warehouse = frappe.get_doc("Warehouse", self.warehouse)
		warehouse.default_storage_location = self.storage_location
		warehouse.save()
		category = frappe.get_doc(
			{
				"doctype": "Asset Category",
				"asset_category_name": f"Conversion category {self.fixture_id}",
				"finance_books": [
					{
						"depreciation_method": "Straight Line",
						"total_number_of_depreciations": 3,
						"frequency_of_depreciation": 1,
						"depreciation_start_date": get_last_day(self.posting_date),
						"salvage_value_percentage": 0,
					}
				],
				"accounts": [
					{
						"company_name": self.company,
						"fixed_asset_account": self.fixed_asset_account,
						"accumulated_depreciation_account": self.accumulated_depreciation_account,
						"depreciation_expense_account": self.depreciation_account,
					}
				],
			}
		).insert()
		self.asset_item = create_fixed_asset_item(
			item_code=f"Conversion asset {self.fixture_id}", asset_category=category.name
		).name
		self.stock_item = make_item(
			properties={
				"is_stock_item": 1,
				"stock_uom": "Nos",
				"demo_asset_item_code": self.asset_item,
			}
		).name
		self.receipt = make_stock_entry(
			company=self.company,
			item_code=self.stock_item,
			qty=self.received_qty,
			to_warehouse=self.warehouse,
			rate=self.unit_value,
			expense_account=company.stock_adjustment_account,
			posting_date=self.posting_date,
			posting_time="00:00:00",
		)

	def make_conversion(self, qty=2):
		return frappe.get_doc(
			{
				"doctype": "Stock to Asset Conversion",
				"company": self.company,
				"posting_date": self.posting_date,
				"posting_time": "09:00:00",
				"stock_items": [{"item_code": self.stock_item, "warehouse": self.warehouse, "qty": qty}],
			}
		)

	def assert_gl(self, voucher_type, voucher_no, expected, *, cancelled=False, entry_count=None):
		entries = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": voucher_type, "voucher_no": voucher_no},
			fields=["account", "debit", "credit", "company", "is_cancelled"],
		)
		self.assertTrue(entries)
		amounts = {}
		for entry in entries:
			self.assertEqual(entry.company, self.company)
			self.assertEqual(entry.is_cancelled, int(cancelled and not is_immutable_ledger_enabled()))
			amounts[entry.account] = flt(amounts.get(entry.account, 0) + entry.debit - entry.credit, 2)
		self.assertEqual(amounts, expected)
		self.assertEqual(flt(sum(entry.debit - entry.credit for entry in entries), 2), 0)
		if not cancelled:
			# No merge: one inventory credit and one debit for each generated Asset.
			self.assertEqual(
				len(entries), entry_count or (3 if voucher_type == "Stock to Asset Conversion" else 2)
			)

	def assert_conversion_reversed(self, conversion):
		conversion.reload()
		self.assertEqual((conversion.docstatus, conversion.status), (2, "Cancelled"))
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty)
		entries = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_type": conversion.doctype, "voucher_no": conversion.name},
			fields=["actual_qty", "stock_value_difference", "is_cancelled"],
		)
		self.assertTrue(entries)
		self.assertTrue(all(entry.is_cancelled for entry in entries))
		self.assertEqual(sum(entry.actual_qty for entry in entries), 0)
		# Native cancellation retains the historical valuation difference on the
		# inactive original SLE; prove restoration from the resulting stock state.
		self.assertFalse(
			frappe.db.exists(
				"Stock Ledger Entry",
				{
					"voucher_type": conversion.doctype,
					"voucher_no": conversion.name,
					"is_cancelled": 0,
				},
			)
		)
		self.assertEqual(
			get_stock_balance(self.stock_item, self.warehouse, with_valuation_rate=True),
			(self.received_qty, self.unit_value),
		)
		self.assertEqual(
			get_stock_balance(
				self.stock_item,
				self.warehouse,
				inventory_dimensions_dict={"storage_location": self.storage_location},
			),
			self.received_qty,
		)
		stock_bin = frappe.db.get_value(
			"Bin",
			{
				"item_code": self.stock_item,
				"warehouse": self.warehouse,
			},
			["actual_qty", "valuation_rate", "stock_value"],
			as_dict=True,
		)
		self.assertEqual(
			(stock_bin.actual_qty, stock_bin.valuation_rate, stock_bin.stock_value),
			(self.received_qty, self.unit_value, self.received_qty * self.unit_value),
		)
		self.assert_gl(
			conversion.doctype,
			conversion.name,
			{self.inventory_account: 0, self.fixed_asset_account: 0},
			cancelled=True,
		)
		for output in conversion.assets:
			self.assertEqual(frappe.db.get_value("Asset", output.asset, "docstatus"), 2)
		self.assertFalse(
			frappe.db.exists(
				"Asset Movement",
				{
					"reference_doctype": conversion.doctype,
					"reference_name": conversion.name,
					"docstatus": 1,
				},
			)
		)
		self.assertFalse(
			frappe.db.exists(
				"Asset Depreciation Schedule",
				{
					"asset": ["in", [row.asset for row in conversion.assets]],
					"docstatus": 1,
				},
			)
		)

	def exercise_conversion(self):
		conversion = self.make_conversion().insert()
		conversion.reload()
		self.assertEqual((conversion.docstatus, conversion.status), (0, "Draft"))
		self.assertEqual(conversion.total_value, 2 * self.unit_value)
		self.assertEqual(conversion.stock_items[0].storage_location, self.storage_location)
		self.assertEqual(len(conversion.assets), 2)
		asset_names = [row.asset for row in conversion.assets]
		self.assertEqual(len(set(asset_names)), 2)
		for output in conversion.assets:
			asset = frappe.get_doc("Asset", output.asset)
			self.assertEqual(asset.docstatus, 0)
			self.assertEqual(asset.stock_to_asset_conversion, conversion.name)
			self.assertEqual(asset.stock_to_asset_conversion_item, conversion.stock_items[0].name)
			self.assertEqual(asset.stock_to_asset_conversion_unit, output.source_unit_index)
			self.assertEqual(asset.calculate_depreciation, 1)
		conversion.save()
		conversion.reload()
		self.assertEqual([row.asset for row in conversion.assets], asset_names)
		self.assertEqual(
			set(
				frappe.get_all("Asset", filters={"stock_to_asset_conversion": conversion.name}, pluck="name")
			),
			set(asset_names),
		)
		self.assertFalse(
			frappe.db.exists("GL Entry", {"voucher_type": conversion.doctype, "voucher_no": conversion.name})
		)
		self.assertFalse(
			frappe.db.exists(
				"Stock Ledger Entry", {"voucher_type": conversion.doctype, "voucher_no": conversion.name}
			)
		)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty)
		conversion.submit()
		conversion.reload()
		self.assertEqual((conversion.docstatus, conversion.status), (1, "Submitted"))
		self.assertEqual([row.asset for row in conversion.assets], asset_names)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty - 2)
		entries = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_type": conversion.doctype, "voucher_no": conversion.name},
			fields=[
				"item_code",
				"warehouse",
				"voucher_detail_no",
				"storage_location",
				"actual_qty",
				"stock_value_difference",
				"is_cancelled",
			],
		)
		self.assertEqual(len(entries), 1)
		entry = entries[0]
		self.assertEqual(entry.storage_location, self.storage_location)
		self.assertEqual(
			(entry.item_code, entry.warehouse, entry.voucher_detail_no),
			(self.stock_item, self.warehouse, conversion.stock_items[0].name),
		)
		self.assertEqual((entry.actual_qty, entry.stock_value_difference, entry.is_cancelled), (-2, -240, 0))
		self.assert_gl(
			conversion.doctype, conversion.name, {self.inventory_account: -240, self.fixed_asset_account: 240}
		)
		movements = frappe.get_all(
			"Asset Movement",
			filters={
				"reference_doctype": conversion.doctype,
				"reference_name": conversion.name,
			},
			pluck="name",
		)
		self.assertEqual(len(movements), 2)
		self.assertEqual(
			{frappe.get_doc("Asset Movement", name).assets[0].asset for name in movements}, set(asset_names)
		)
		for name in movements:
			movement = frappe.get_doc("Asset Movement", name)
			self.assertEqual(
				(movement.docstatus, movement.purpose, movement.company), (1, "Receipt", self.company)
			)
			self.assertEqual(movement.assets[0].target_location, self.location)
		for output in conversion.assets:
			asset = frappe.get_doc("Asset", output.asset)
			self.assertEqual(
				(asset.docstatus, asset.item_code, asset.company, asset.location),
				(1, self.asset_item, self.company, self.location),
			)
			self.assertEqual(asset.net_purchase_amount, self.unit_value)
			self.assertEqual(asset.source_warehouse, self.warehouse)
			self.assertEqual(
				asset.value_after_depreciation,
				self.unit_value,
			)
			self.assertEqual(asset.calculate_depreciation, 1)
			self.assertFalse(
				frappe.db.exists("GL Entry", {"voucher_type": "Asset", "voucher_no": asset.name})
			)
			schedules = frappe.get_all(
				"Asset Depreciation Schedule", filters={"asset": asset.name, "docstatus": 1}, pluck="name"
			)
			self.assertEqual(len(schedules), 1)
			schedule = frappe.get_doc("Asset Depreciation Schedule", schedules[0])
			self.assertEqual(schedule.company, self.company)
			self.assertEqual(schedule.depreciation_method, "Straight Line")
			self.assertEqual(
				flt(sum(row.depreciation_amount for row in schedule.depreciation_schedule), 2),
				self.unit_value,
			)
			self.assertTrue(all(not row.journal_entry for row in schedule.depreciation_schedule))
		conversion.cancel()
		self.assert_conversion_reversed(conversion)

	def test_scheduled_depreciation_conversion_full_loop(self):
		self.exercise_conversion()

	def test_follow_up_movement_blocks_conversion_cancellation(self):
		conversion = self.make_conversion().insert()
		for output in conversion.assets:
			asset = frappe.get_doc("Asset", output.asset)
			asset.save()
		conversion.submit()
		target = (
			frappe.get_doc({"doctype": "Location", "location_name": f"Conversion target {self.fixture_id}"})
			.insert()
			.name
		)
		movement = frappe.get_doc(
			{
				"doctype": "Asset Movement",
				"company": self.company,
				"purpose": "Transfer",
				"transaction_date": f"{self.posting_date} 10:00:00",
				"assets": [
					{
						"asset": conversion.assets[0].asset,
						"source_location": self.location,
						"target_location": target,
					}
				],
			}
		).insert()
		movement.submit()
		self.assertEqual(frappe.db.get_value("Asset", conversion.assets[0].asset, "location"), target)
		# Emulate the request owner's rollback, including on_cancel side effects.
		savepoint = f"conversion_cancel_{self.fixture_id}"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "active Asset Movement"):
				conversion.cancel()
		finally:
			frappe.db.rollback(save_point=savepoint)
		conversion.reload()
		self.assertEqual(conversion.docstatus, 1)
		self.assertTrue(
			all(frappe.db.get_value("Asset", row.asset, "docstatus") == 1 for row in conversion.assets)
		)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), 1)
		self.assert_gl(
			conversion.doctype, conversion.name, {self.inventory_account: -240, self.fixed_asset_account: 240}
		)
		movement.reload()
		movement.cancel()
		self.assertEqual(frappe.db.get_value("Asset", conversion.assets[0].asset, "location"), self.location)
		conversion.cancel()
		self.assert_conversion_reversed(conversion)

	def assert_invalid_conversion(self, qty, message):
		baseline = {
			doctype: frappe.db.count(doctype)
			for doctype in (
				"Stock to Asset Conversion",
				"Asset",
				"Asset Movement",
				"GL Entry",
				"Stock Ledger Entry",
			)
		}
		savepoint = f"conversion_invalid_{self.fixture_id}"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, message):
				self.make_conversion(qty=qty).insert()
		finally:
			frappe.db.rollback(save_point=savepoint)
		self.assertEqual({doctype: frappe.db.count(doctype) for doctype in baseline}, baseline)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty)

	def test_conversion_rejects_zero_quantity(self):
		self.assert_invalid_conversion(0, "positive whole number")

	def test_conversion_rejects_fractional_quantity(self):
		self.assert_invalid_conversion(1.5, "positive whole number")

	def test_conversion_rejects_insufficient_stock(self):
		self.assert_invalid_conversion(self.received_qty + 1, "exceeds available stock")

	def test_conversion_rejects_missing_asset_mapping(self):
		item = frappe.get_doc("Item", self.stock_item)
		item.demo_asset_item_code = None
		item.save()
		self.assert_invalid_conversion(1, "Set Asset Item Code")

	def test_accountant_selects_each_objects_category_and_native_plan(self):
		conversion = self.make_conversion(qty=1).insert()
		original_category = frappe.get_doc("Asset Category", conversion.assets[0].asset_category)
		category = frappe.copy_doc(original_category)
		category.asset_category_name = f"One period {self.fixture_id}"
		category.finance_books[0].total_number_of_depreciations = 1
		category.finance_books[0].salvage_value_percentage = 0
		category.insert()
		conversion.assets[0].asset_category = category.name
		conversion.save()
		asset = frappe.get_doc("Asset", conversion.assets[0].asset)
		self.assertEqual(asset.asset_category, category.name)
		self.assertEqual(asset.calculate_depreciation, 1)
		self.assertEqual(asset.finance_books[0].total_number_of_depreciations, 1)
		self.assertEqual(asset.finance_books[0].expected_value_after_useful_life, 0)
		self.assertEqual(
			frappe.get_cached_value("Item", self.asset_item, "asset_category"), original_category.name
		)
		asset.finance_books[0].total_number_of_depreciations = 6
		asset.save()
		conversion.save()
		asset.reload()
		self.assertEqual(asset.finance_books[0].total_number_of_depreciations, 6)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty)

	def test_asset_conversion_pick_list_full_loop(self):
		pick_list = frappe.get_doc(
			{
				"doctype": "Pick List",
				"company": self.company,
				"pick_list_type": "Asset Conversion",
				"purpose": "Material Transfer",
				"pick_manually": 1,
				"locations": [
					{
						"item_code": self.stock_item,
						"warehouse": self.warehouse,
						"storage_location": self.storage_location,
						"qty": 2,
						"stock_qty": 2,
						"picked_qty": 2,
						"uom": "Nos",
						"stock_uom": "Nos",
						"conversion_factor": 1,
					}
				],
			}
		).insert()
		pick_list.submit()
		pick_list.reload()
		self.assertEqual(pick_list.docstatus, 1)
		self.assertFalse(pick_list.material_request)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty)

		conversion = frappe.get_doc(pick_list.make_stock_to_asset_conversion())
		conversion.reload()
		self.assertEqual(conversion.docstatus, 0)
		self.assertEqual(conversion.pick_list, pick_list.name)
		self.assertFalse(conversion.material_request)
		self.assertEqual(len(conversion.stock_items), 1)
		row = conversion.stock_items[0]
		self.assertEqual(
			(row.item_code, row.warehouse, row.storage_location, row.qty),
			(self.stock_item, self.warehouse, self.storage_location, 2),
		)
		self.assertEqual(conversion.total_value, 2 * self.unit_value)
		asset_names = [output.asset for output in conversion.assets]
		self.assertEqual(len(set(asset_names)), 2)
		repeated = frappe.get_doc(pick_list.make_stock_to_asset_conversion())
		self.assertEqual(repeated.name, conversion.name)
		self.assertEqual([output.asset for output in repeated.assets], asset_names)
		self.assertEqual(frappe.db.count("Stock to Asset Conversion", {"pick_list": pick_list.name}), 1)
		self.assertEqual(
			set(
				frappe.get_all(
					"Asset",
					filters={
						"stock_to_asset_conversion": conversion.name,
					},
					pluck="name",
				)
			),
			set(asset_names),
		)
		conversion.save()
		conversion.submit()
		conversion.reload()
		pick_list.reload()
		self.assertEqual((conversion.docstatus, conversion.status), (1, "Submitted"))
		self.assertEqual((pick_list.docstatus, pick_list.status), (1, "Completed"))
		self.assertEqual([output.asset for output in conversion.assets], asset_names)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), 1)
		self.assert_gl(
			conversion.doctype, conversion.name, {self.inventory_account: -240, self.fixed_asset_account: 240}
		)
		entries = frappe.get_all(
			"Stock Ledger Entry",
			filters={
				"voucher_type": conversion.doctype,
				"voucher_no": conversion.name,
				"is_cancelled": 0,
			},
			fields=["voucher_detail_no", "storage_location", "actual_qty", "stock_value_difference"],
		)
		self.assertEqual(len(entries), 1)
		self.assertEqual(
			(
				entries[0].voucher_detail_no,
				entries[0].storage_location,
				entries[0].actual_qty,
				entries[0].stock_value_difference,
			),
			(row.name, self.storage_location, -2, -240),
		)
		for asset_name in asset_names:
			asset = frappe.get_doc("Asset", asset_name)
			self.assertEqual((asset.docstatus, asset.calculate_depreciation), (1, 1))
			self.assertEqual(
				(asset.net_purchase_amount, asset.value_after_depreciation),
				(self.unit_value, self.unit_value),
			)
			self.assertEqual(asset.location, self.location)
			self.assertEqual(asset.stock_to_asset_conversion, conversion.name)
		conversion.cancel()
		self.assert_conversion_reversed(conversion)
		pick_list.reload()
		self.assertEqual((pick_list.docstatus, pick_list.status), (2, "Cancelled"))
