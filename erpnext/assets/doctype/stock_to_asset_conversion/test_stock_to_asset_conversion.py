import unittest
from unittest.mock import Mock, patch

import frappe
from frappe.utils import flt, get_last_day, today

from erpnext.accounts.doctype.account.test_account import create_account
from erpnext.accounts.doctype.budget.budget import BudgetError
from erpnext.accounts.general_ledger import is_immutable_ledger_enabled
from erpnext.accounts.utils import get_fiscal_year
from erpnext.assets.doctype.asset.test_asset import create_fixed_asset_item
from erpnext.assets.doctype.stock_to_asset_conversion.stock_to_asset_conversion import (
	create_immediate_depreciation_entry,
	get_accounting_treatment,
	get_default_accounting_treatment,
	normalize_accounting_treatment,
)
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse, get_warehouse_account
from erpnext.stock.utils import get_stock_balance
from erpnext.tests.utils import ERPNextTestSuite


class TestStockToAssetConversion(unittest.TestCase):
	def test_below_threshold_is_expensed_without_depreciation(self):
		self.assertEqual(get_accounting_treatment(99.99, 100), ("Expense", 0))

	def test_at_or_above_threshold_is_capitalized_and_depreciated(self):
		self.assertEqual(get_accounting_treatment(100, 100), ("Capitalized", 1))
		self.assertEqual(get_accounting_treatment(100.01, 100), ("Capitalized", 1))

	def test_threshold_defaults_to_expense_below_and_scheduled_at_or_above(self):
		self.assertEqual(get_default_accounting_treatment(99.99, 100), "Expense")
		self.assertEqual(get_default_accounting_treatment(100, 100), "Scheduled Depreciation")
		self.assertEqual(get_default_accounting_treatment(100.01, 100), "Scheduled Depreciation")

	def test_zero_threshold_defaults_to_scheduled_depreciation(self):
		self.assertEqual(get_default_accounting_treatment(0.01, 0), "Scheduled Depreciation")

	def test_legacy_capitalized_treatment_maps_to_scheduled_depreciation(self):
		self.assertEqual(normalize_accounting_treatment("Capitalized"), "Scheduled Depreciation")

	def test_immediate_depreciation_journal_is_generated_from_approved_conversion(self):
		asset = Mock(net_purchase_amount=120, company="Company", stock_to_asset_conversion="Conversion")
		asset.name = "Asset"
		asset.precision.return_value = None
		journal = Mock(accounts=[frappe._dict(), frappe._dict()], flags=frappe._dict())
		conversion = frappe._dict(name="Conversion", docstatus=1, company="Company")
		with (
			patch("erpnext.assets.doctype.asset.asset.make_journal_entry", return_value=journal) as make_journal,
			patch("frappe.get_doc", return_value=conversion) as get_doc,
		):
			result = create_immediate_depreciation_entry(asset, "2026-10-12")
		make_journal.assert_called_once_with("Asset")
		get_doc.assert_called_once_with("Stock to Asset Conversion", "Conversion")
		self.assertIs(result, journal)
		journal.mark_as_system_generated.assert_called_once_with()
		self.assertIn("Conversion", journal.journal_entry_reason)
		self.assertEqual(journal.posting_date, "2026-10-12")
		self.assertEqual(journal.accounts[0].debit, 120)
		self.assertEqual(journal.accounts[0].debit_in_account_currency, 120)
		self.assertEqual(journal.accounts[1].credit, 120)
		self.assertEqual(journal.accounts[1].credit_in_account_currency, 120)
		journal.insert.assert_called_once_with()
		journal.submit.assert_called_once_with()

	def test_immediate_depreciation_rejects_unapproved_or_other_company_conversion(self):
		asset = Mock(net_purchase_amount=120, company="Company", stock_to_asset_conversion="Conversion")
		asset.name = "Asset"
		asset.precision.return_value = None
		for docstatus, company in ((0, "Company"), (1, "Other Company")):
			with self.subTest(docstatus=docstatus, company=company):
				journal = Mock()
				with (
					patch("erpnext.assets.doctype.asset.asset.make_journal_entry", return_value=journal),
					patch("frappe.get_doc", return_value=frappe._dict(docstatus=docstatus, company=company)),
					patch("frappe.throw", side_effect=frappe.ValidationError) as throw,
				):
					with self.assertRaises(frappe.ValidationError):
						create_immediate_depreciation_entry(asset, "2026-10-12")
				journal.insert.assert_not_called()
				journal.submit.assert_not_called()
				throw.assert_called_once()
				self.assertIn("submitted Stock to Asset Conversion", throw.call_args.args[0])


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
		company.asset_capitalization_threshold = 100
		company.low_value_asset_expense_account = self.depreciation_account
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

	def assert_conversion_reversed(self, conversion, *, expense_account=None):
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
		debit_account = (
			(expense_account or self.depreciation_account)
			if conversion.assets[0].accounting_treatment == "Expense"
			else self.fixed_asset_account
		)
		self.assert_gl(
			conversion.doctype, conversion.name, {self.inventory_account: 0, debit_account: 0}, cancelled=True
		)
		for output in conversion.assets:
			self.assertEqual(frappe.db.get_value("Asset", output.asset, "docstatus"), 2)
			if output.journal_entry:
				self.assertEqual(frappe.db.get_value("Journal Entry", output.journal_entry, "docstatus"), 2)
				self.assert_gl(
					"Journal Entry",
					output.journal_entry,
					{self.depreciation_account: 0, self.accumulated_depreciation_account: 0},
					cancelled=True,
				)
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

	def exercise_accounting_treatment(self, treatment):
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
			self.assertEqual(asset.stock_to_asset_accounting_treatment, "Scheduled Depreciation")
			asset.stock_to_asset_accounting_treatment = treatment
			asset.save()
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
		debit_account = self.depreciation_account if treatment == "Expense" else self.fixed_asset_account
		self.assert_gl(
			conversion.doctype, conversion.name, {self.inventory_account: -240, debit_account: 240}
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
			self.assertEqual(asset.stock_to_asset_accounting_treatment, treatment)
			self.assertEqual(
				asset.value_after_depreciation,
				0 if treatment in ("Expense", "Immediate Depreciation") else self.unit_value,
			)
			self.assertEqual(asset.calculate_depreciation, int(treatment == "Scheduled Depreciation"))
			self.assertFalse(
				frappe.db.exists("GL Entry", {"voucher_type": "Asset", "voucher_no": asset.name})
			)
			schedules = frappe.get_all(
				"Asset Depreciation Schedule", filters={"asset": asset.name, "docstatus": 1}, pluck="name"
			)
			if treatment == "Scheduled Depreciation":
				self.assertEqual(len(schedules), 1)
				schedule = frappe.get_doc("Asset Depreciation Schedule", schedules[0])
				self.assertEqual(schedule.company, self.company)
				self.assertEqual(schedule.depreciation_method, "Straight Line")
				self.assertEqual(
					flt(sum(row.depreciation_amount for row in schedule.depreciation_schedule), 2),
					self.unit_value,
				)
				self.assertTrue(all(not row.journal_entry for row in schedule.depreciation_schedule))
			else:
				self.assertEqual(schedules, [])
				self.assertEqual(len(asset.finance_books), 0)
			if treatment == "Immediate Depreciation":
				journal = frappe.get_doc("Journal Entry", output.journal_entry)
				self.assertEqual(journal.docstatus, 1)
				self.assertEqual(asset.stock_to_asset_depreciation_journal_entry, journal.name)
				self.assertTrue(
					all(
						row.reference_type == "Asset" and row.reference_name == asset.name
						for row in journal.accounts
					)
				)
				self.assert_gl(
					"Journal Entry",
					journal.name,
					{
						self.depreciation_account: self.unit_value,
						self.accumulated_depreciation_account: -self.unit_value,
					},
				)
			else:
				self.assertFalse(output.journal_entry)
		conversion.cancel()
		self.assert_conversion_reversed(conversion)

	def test_expense_conversion_full_loop(self):
		self.exercise_accounting_treatment("Expense")

	def test_hold_value_conversion_full_loop(self):
		self.exercise_accounting_treatment("Hold Value")

	def test_scheduled_depreciation_conversion_full_loop(self):
		self.exercise_accounting_treatment("Scheduled Depreciation")

	def test_immediate_depreciation_conversion_full_loop(self):
		self.exercise_accounting_treatment("Immediate Depreciation")

	def test_follow_up_movement_blocks_conversion_cancellation(self):
		conversion = self.make_conversion().insert()
		for output in conversion.assets:
			asset = frappe.get_doc("Asset", output.asset)
			asset.stock_to_asset_accounting_treatment = "Hold Value"
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

	def test_editable_conversion_preview_updates_existing_draft_asset(self):
		conversion = self.make_conversion(qty=1).insert()
		conversion.reload()
		asset_name = conversion.assets[0].asset
		target_location = (
			frappe.get_doc(
				{
					"doctype": "Location",
					"location_name": f"Preview location {self.fixture_id}",
				}
			)
			.insert()
			.name
		)

		conversion.assets[0].accounting_treatment = "Hold Value"
		conversion.assets[0].asset_location = target_location
		conversion.save()
		conversion.reload()
		asset = frappe.get_doc("Asset", asset_name)
		self.assertEqual(conversion.assets[0].asset, asset_name)
		self.assertEqual(asset.docstatus, 0)
		self.assertEqual(asset.stock_to_asset_accounting_treatment, "Hold Value")
		self.assertEqual(asset.location, target_location)
		self.assertEqual(asset.calculate_depreciation, 0)
		self.assertEqual(asset.value_after_depreciation, self.unit_value)
		self.assertEqual(conversion.assets[0].accounting_treatment, "Hold Value")
		self.assertEqual(conversion.assets[0].asset_location, target_location)

		# When the conversion row has not changed, edits on the linked Draft Asset
		# must survive the next conversion save and appear in its preview.
		asset.stock_to_asset_accounting_treatment = "Expense"
		asset.location = self.location
		asset.save()
		conversion.save()
		conversion.reload()
		asset.reload()
		self.assertEqual(conversion.assets[0].asset, asset_name)
		self.assertEqual(conversion.assets[0].accounting_treatment, "Expense")
		self.assertEqual(conversion.assets[0].asset_location, self.location)
		self.assertEqual(asset.stock_to_asset_accounting_treatment, "Expense")
		self.assertEqual(asset.location, self.location)
		self.assertEqual(asset.value_after_depreciation, 0)
		self.assertEqual(
			frappe.get_all(
				"Asset",
				filters={
					"stock_to_asset_conversion": conversion.name,
				},
				pluck="name",
			),
			[asset_name],
		)
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty)
		for doctype in ("Stock Ledger Entry", "GL Entry"):
			self.assertFalse(
				frappe.db.exists(
					doctype,
					{
						"voucher_type": conversion.doctype,
						"voucher_no": conversion.name,
					},
				)
			)
		self.assertFalse(
			frappe.db.exists(
				"Asset Movement",
				{
					"reference_doctype": conversion.doctype,
					"reference_name": conversion.name,
				},
			)
		)

	def test_department_budget_rejects_expense_conversion_overrun(self):
		# Department is a real registered dimension supplied by native bootstrap.
		# A unique expense account isolates this budget from all other expenses.
		self.assertTrue(
			frappe.db.exists(
				"Accounting Dimension",
				{
					"document_type": "Department",
					"disabled": 0,
				},
			)
		)
		expense_account = create_account(
			account_name=f"Conversion budget expense {self.fixture_id}",
			company=self.company,
			account_type="Depreciation",
			parent_account=frappe.db.get_value("Account", self.depreciation_account, "parent_account"),
		)
		company = frappe.get_doc("Company", self.company)
		company.low_value_asset_expense_account = expense_account
		company.exception_budget_approver_role = None
		company.save()
		fiscal_year = get_fiscal_year(self.posting_date, company=self.company)[0]
		budget = frappe.get_doc(
			{
				"doctype": "Budget",
				"company": self.company,
				"budget_against": "Department",
				"department": "_Test Department - _TC",
				"account": expense_account,
				"budget_amount": self.unit_value,
				"from_fiscal_year": fiscal_year,
				"to_fiscal_year": fiscal_year,
				"distribution_frequency": "Monthly",
				"distribute_equally": 1,
				"applicable_on_booking_actual_expenses": 1,
				"action_if_annual_budget_exceeded": "Stop",
				"action_if_accumulated_monthly_budget_exceeded": "Ignore",
			}
		).insert()
		budget.submit()
		conversion = self.make_conversion().insert()
		for output in conversion.assets:
			asset = frappe.get_doc("Asset", output.asset)
			asset.stock_to_asset_accounting_treatment = "Expense"
			asset.save()
		conversion.save()
		conversion.reload()
		asset_names = [row.asset for row in conversion.assets]
		savepoint = f"conversion_budget_{self.fixture_id}"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaises(BudgetError):
				conversion.submit()
		finally:
			frappe.db.rollback(save_point=savepoint)
		conversion.reload()
		self.assertEqual(conversion.docstatus, 0)
		self.assertEqual([row.asset for row in conversion.assets], asset_names)
		self.assertTrue(all(frappe.db.get_value("Asset", name, "docstatus") == 0 for name in asset_names))
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty)
		for doctype in ("GL Entry", "Stock Ledger Entry"):
			self.assertFalse(
				frappe.db.exists(
					doctype,
					{
						"voucher_type": conversion.doctype,
						"voucher_no": conversion.name,
					},
				)
			)
		self.assertFalse(
			frappe.db.exists(
				"Asset Movement",
				{
					"reference_doctype": conversion.doctype,
					"reference_name": conversion.name,
				},
			)
		)

		within_budget = self.make_conversion(qty=1).insert()
		asset = frappe.get_doc("Asset", within_budget.assets[0].asset)
		asset.stock_to_asset_accounting_treatment = "Expense"
		asset.save()
		within_budget.submit()
		within_budget.reload()
		asset.reload()
		self.assertEqual((within_budget.docstatus, within_budget.status), (1, "Submitted"))
		self.assertEqual(asset.docstatus, 1)
		self.assertEqual(asset.stock_to_asset_conversion, within_budget.name)
		self.assertEqual(asset.stock_to_asset_accounting_treatment, "Expense")
		self.assertEqual((asset.net_purchase_amount, asset.value_after_depreciation), (self.unit_value, 0))
		self.assertEqual(get_stock_balance(self.stock_item, self.warehouse), self.received_qty - 1)
		self.assert_gl(
			within_budget.doctype,
			within_budget.name,
			{self.inventory_account: -self.unit_value, expense_account: self.unit_value},
			entry_count=2,
		)
		self.assertEqual(
			set(
				frappe.get_all(
					"GL Entry",
					filters={
						"voucher_type": within_budget.doctype,
						"voucher_no": within_budget.name,
					},
					pluck="department",
				)
			),
			{"_Test Department - _TC"},
		)
		within_budget.cancel()
		self.assert_conversion_reversed(within_budget, expense_account=expense_account)
		budget.reload()
		self.assertEqual(budget.docstatus, 1)

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
		for output in conversion.assets:
			output.accounting_treatment = "Hold Value"
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
			self.assertEqual((asset.docstatus, asset.stock_to_asset_accounting_treatment), (1, "Hold Value"))
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
