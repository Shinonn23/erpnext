# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, today

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.stock_ledger_entry.stock_ledger_entry import InventoryDimensionNegativeStockError
from erpnext.stock.doctype.storage_location.storage_location import (
	get_default_storage_location,
	setup_storage_location_dimension,
	set_default_storage_locations,
	validate_storage_location,
	warehouse_query,
)
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.stock.report.stock_balance.stock_balance import execute as stock_balance
from erpnext.tests.utils import ERPNextTestSuite


class TestStorageLocation(ERPNextTestSuite):
	def setUp(self):
		super().setUp()
		setup_storage_location_dimension()
		self.warehouse = create_warehouse("_Test Storage Location Warehouse")
		self.other_warehouse = create_warehouse("_Test Storage Location Other Warehouse")
		# These cases start from a Warehouse without the automatic STAGING default.
		for warehouse in (self.warehouse, self.other_warehouse):
			frappe.db.set_value("Warehouse", warehouse, "default_storage_location", None)
			frappe.clear_document_cache("Warehouse", warehouse)
		self.locations = []
		for code in ("A-01", "A-02"):
			self.locations.append(frappe.get_doc({
				"doctype": "Storage Location", "warehouse": self.warehouse,
				"location_code": code + frappe.generate_hash(length=6),
			}).insert())

	def test_staging_location_follows_the_warehouse(self):
		name = create_warehouse("_Test Staging " + frappe.generate_hash(length=6))
		self.assertEqual(
			frappe.db.get_value("Warehouse", name, "default_storage_location"), f"{name} / STAGING"
		)
		renamed = frappe.rename_doc("Warehouse", name, name.replace("_Test Staging", "_Test Renamed"), force=True)
		self.assertEqual(
			frappe.db.get_value("Warehouse", renamed, "default_storage_location"), f"{renamed} / STAGING"
		)
		frappe.delete_doc("Warehouse", renamed)
		self.assertFalse(frappe.db.exists("Storage Location", {"warehouse": renamed}))

	def test_storage_condition_picks_a_compatible_location_and_warehouse(self):
		suffix = frappe.generate_hash(length=6)
		condition = frappe.get_doc({"doctype": "Storage Condition", "condition_name": "_Test Cold " + suffix}).insert()
		item = make_item("_Test Cold Item " + suffix, {"storage_condition": condition.name})
		cold = frappe.get_doc({
			"doctype": "Storage Location", "warehouse": self.warehouse, "location_code": "COLD-" + suffix,
			"supported_storage_conditions": [{"storage_condition": condition.name}],
		}).insert()
		self.assertEqual(get_default_storage_location(self.warehouse, item.name), cold.name)
		with self.assertRaisesRegex(frappe.ValidationError, "has no Storage Location"):
			get_default_storage_location(self.other_warehouse, item.name)
		found = warehouse_query(
			"Warehouse", "", "name", 0, 20,
			{"storage_condition_item": item.name, "filters": {"company": "_Test Company"}},
		)
		self.assertEqual([row[0] for row in found], [self.warehouse])

	def test_dimension_setup_preserves_settings(self):
		dimension = frappe.get_doc("Inventory Dimension", "Storage Location")
		dimension.validate_negative_stock = 0
		dimension.save()
		setup_storage_location_dimension()
		self.assertEqual(frappe.get_doc("Inventory Dimension", "Storage Location").validate_negative_stock, 0)
		self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field("to_storage_location"))
		self.assertFalse(
			frappe.db.exists("Custom Field", {"dt": "Stock Count Item", "fieldname": "storage_location"})
		)

	def test_warehouse_default_validation(self):
		warehouse = frappe.get_doc("Warehouse", self.other_warehouse)
		warehouse.default_storage_location = self.locations[0].name
		self.assertRaises(frappe.ValidationError, warehouse.save)
		warehouse = frappe.get_doc("Warehouse", self.warehouse)
		warehouse.default_storage_location = self.locations[0].name
		warehouse.save()
		self.locations[0].disabled = 1
		self.locations[0].save()
		self.assertRaises(frappe.ValidationError, warehouse.save)

	def test_default_location_for_conversion_and_reconciliation(self):
		warehouse = frappe.get_doc("Warehouse", self.warehouse)
		warehouse.default_storage_location = self.locations[0].name
		warehouse.save()
		for doctype, table in (("Stock to Asset Conversion", "stock_items"), ("Stock Reconciliation", "items")):
			doc = frappe.new_doc(doctype)
			row = doc.append(table, {"warehouse": self.warehouse})
			set_default_storage_locations(doc)
			self.assertEqual(row.storage_location, self.locations[0].name)
			row.storage_location = self.locations[1].name
			set_default_storage_locations(doc)
			self.assertEqual(row.storage_location, self.locations[1].name)

	def test_warehouse_defaults_receipt_issue_override_and_cancel(self):
		warehouse = frappe.get_doc("Warehouse", self.warehouse)
		warehouse.default_storage_location = self.locations[0].name
		warehouse.save()
		item = make_item("_Test Default Location " + frappe.generate_hash(length=8))
		receipt = make_stock_entry(item_code=item.name, qty=10, rate=100,
			to_warehouse=self.warehouse, do_not_save=True)
		receipt.insert().submit()
		self.assertEqual(receipt.items[0].to_storage_location, self.locations[0].name)
		issue = make_stock_entry(item_code=item.name, qty=2,
			from_warehouse=self.warehouse, do_not_save=True)
		issue.insert().submit()
		self.assertEqual(issue.items[0].storage_location, self.locations[0].name)
		warehouse.default_storage_location = self.locations[1].name
		warehouse.save()
		issue.cancel()
		self.assertEqual(self.get_location_balance(item.name, self.locations[0].name), 10)
		transfer = make_stock_entry(item_code=item.name, qty=3,
			from_warehouse=self.warehouse, to_warehouse=self.warehouse, do_not_save=True)
		transfer.items[0].storage_location = self.locations[0].name
		transfer.insert().submit()
		self.assertEqual(transfer.items[0].storage_location, self.locations[0].name)
		self.assertEqual(transfer.items[0].to_storage_location, self.locations[1].name)
		self.assertEqual(self.get_location_balance(item.name, self.locations[0].name), 7)
		self.assertEqual(self.get_location_balance(item.name, self.locations[1].name), 3)

	def test_location_warehouse_and_disabled_validation(self):
		ledger = frappe._dict(storage_location=self.locations[0].name, warehouse=self.other_warehouse)
		self.assertRaises(frappe.ValidationError, validate_storage_location, ledger)
		ledger.warehouse = self.warehouse
		validate_storage_location(ledger)
		self.locations[0].disabled = 1
		self.locations[0].save()
		self.assertRaises(frappe.ValidationError, validate_storage_location, ledger)
		ledger.is_cancelled = 1
		validate_storage_location(ledger)

	def test_group_warehouse_rejected(self):
		location = frappe.get_doc({
			"doctype": "Storage Location", "location_code": "Invalid",
			"warehouse": "_Test Warehouse Group - _TC",
		})
		self.assertRaises(frappe.ValidationError, location.insert)

	def test_location_required_only_for_new_stock_ledger_entries(self):
		ledger = frappe._dict(
			item_code="_Test Item", warehouse=self.warehouse, actual_qty=-1
		)
		for qty in (-1, 0, 1):
			ledger.actual_qty = qty
			ledger.is_cancelled = 0
			with self.assertRaisesRegex(frappe.ValidationError, "Storage Location is required"):
				validate_storage_location(ledger)
			ledger.is_cancelled = 1
			validate_storage_location(ledger)

	def test_issue_without_location_cannot_post_stock(self):
		item = make_item("_Test Required Location " + frappe.generate_hash(length=8))
		receipt = make_stock_entry(
			item_code=item.name, qty=10, rate=100,
			to_warehouse=self.warehouse, do_not_save=True,
		)
		receipt.items[0].to_storage_location = self.locations[0].name
		receipt.insert().submit()
		issue = make_stock_entry(
			item_code=item.name, qty=2, from_warehouse=self.warehouse, do_not_save=True,
		)
		issue.insert()
		with self.assertRaisesRegex(frappe.ValidationError, "Storage Location is required"):
			issue.submit()
		self.assertFalse(frappe.db.exists("Stock Ledger Entry", {"voucher_no": issue.name}))
		self.assertEqual(self.get_location_balance(item.name, self.locations[0].name), 10)

	def test_material_request_needs_only_warehouse(self):
		from erpnext.stock.doctype.material_request.test_material_request import make_material_request

		item = make_item("_Test Planning Location " + frappe.generate_hash(length=8))
		request = make_material_request(
			item_code=item.name, uom=item.stock_uom, warehouse=self.warehouse,
			material_request_type="Purchase",
		)
		self.assertEqual(request.docstatus, 1)
		self.assertFalse(request.items[0].get("storage_location"))
		self.assertFalse(frappe.db.exists("Stock Ledger Entry", {"voucher_no": request.name}))

	def test_reconciliation_balance_reduction_requires_location(self):
		item = make_item("_Test Reconciliation Location " + frappe.generate_hash(length=8))
		reconciliation = frappe.new_doc("Stock Reconciliation")
		reconciliation.update({
			"docstatus": 1, "purpose": "Stock Reconciliation", "company": "_Test Company",
			"posting_date": today(), "posting_time": "12:00:00",
		})
		reconciliation.flags.adjustment_rows = set()
		row = reconciliation.append("items", {
			"item_code": item.name, "warehouse": self.warehouse,
			"qty": 5, "current_qty": 10, "valuation_rate": 100,
		})
		with self.assertRaisesRegex(frappe.ValidationError, "Storage Location is required"):
			reconciliation.get_sle_for_items(row)
		row.qty = 12
		reconciliation.get_sle_for_items(row)

	def test_receipt_transfer_issue_and_cancel(self):
		item = make_item("_Test Storage Location " + frappe.generate_hash(length=8))
		receipt = make_stock_entry(item_code=item.name, qty=10, rate=100,
			to_warehouse=self.warehouse, do_not_save=True)
		receipt.items[0].to_storage_location = self.locations[0].name
		receipt.insert().submit()
		transfer = make_stock_entry(item_code=item.name, qty=3,
			from_warehouse=self.warehouse, to_warehouse=self.warehouse, do_not_save=True)
		transfer.items[0].storage_location = self.locations[0].name
		transfer.items[0].to_storage_location = self.locations[1].name
		transfer.insert().submit()
		self.assertEqual(self.get_location_balance(item.name, self.locations[0].name), 7)
		self.assertEqual(self.get_location_balance(item.name, self.locations[1].name), 3)
		issue = make_stock_entry(item_code=item.name, qty=2,
			from_warehouse=self.warehouse, do_not_save=True)
		issue.items[0].storage_location = self.locations[1].name
		issue.insert().submit()
		self.assertEqual(self.get_location_balance(item.name, self.locations[1].name), 1)
		issue.cancel()
		self.assertEqual(self.get_location_balance(item.name, self.locations[1].name), 3)
		_, rows = stock_balance(frappe._dict(
			company=receipt.company, from_date=today(), to_date=today(),
			item_code=[item.name], storage_location=[self.locations[1].name],
		))
		self.assertEqual(sum(row.bal_qty for row in rows), 3)

	def test_stock_balance_location_toggle(self):
		item = make_item("_Test Location Report " + frappe.generate_hash(length=8))
		for location, qty in ((self.locations[0].name, 4), (self.locations[1].name, 6), (None, 2)):
			receipt = make_stock_entry(item_code=item.name, qty=qty, rate=100,
				to_warehouse=self.warehouse, do_not_save=True)
			receipt.items[0].to_storage_location = location
			receipt.insert().submit()
		filters = frappe._dict(company="_Test Company", from_date=today(), to_date=today(),
			item_code=[item.name], warehouse=[self.warehouse])
		columns, rows = stock_balance(filters)
		self.assertIn("storage_location", [column["fieldname"] for column in columns])
		self.assertEqual({row.get("storage_location") or None: row.bal_qty for row in rows},
			{self.locations[0].name: 4, self.locations[1].name: 6, None: 2})
		filters.show_location_wise_stock = 0
		columns, rows = stock_balance(filters)
		self.assertNotIn("storage_location", [column["fieldname"] for column in columns])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].bal_qty, 12)
		self.assertEqual(rows[0].bal_val, 1200)
		filters.storage_location = [self.locations[0].name, self.locations[1].name]
		columns, rows = stock_balance(filters)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].bal_qty, 10)
		filters.from_date = add_days(today(), 1)
		filters.to_date = add_days(today(), 1)
		filters.show_location_wise_stock = 1
		_, rows = stock_balance(filters)
		self.assertEqual(sorted(row.opening_qty for row in rows), [4, 6])
		self.locations[0].warehouse = self.other_warehouse
		self.assertRaises(frappe.ValidationError, self.locations[0].save)

	def test_location_reports_after_reconciliation(self):
		from unittest.mock import patch
		from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import create_stock_reconciliation
		from erpnext.stock.report.stock_ageing.stock_ageing import FIFOSlots

		item = make_item("_Test Location Reconcile " + frappe.generate_hash(length=8))
		for location, qty in ((self.locations[0].name, 4), (self.locations[1].name, 6)):
			receipt = make_stock_entry(item_code=item.name, qty=qty, rate=100,
				to_warehouse=self.warehouse, do_not_save=True)
			receipt.items[0].to_storage_location = location
			receipt.insert().submit()
		reconciliation = create_stock_reconciliation(item_code=item.name, warehouse=self.warehouse,
			qty=3, rate=100, do_not_save=True)
		reconciliation.items[0].storage_location = self.locations[0].name
		reconciliation.insert().submit()
		filters = frappe._dict(company="_Test Company", from_date=today(), to_date=today(),
			item_code=[item.name], warehouse=[self.warehouse], show_location_wise_stock=1)
		_, rows = stock_balance(filters)
		self.assertEqual({row.storage_location: row.bal_qty for row in rows},
			{self.locations[0].name: 3, self.locations[1].name: 6})
		fifo_filters = filters.copy()
		fifo_filters.show_warehouse_wise_stock = 1
		fifo = FIFOSlots(fifo_filters).generate()
		self.assertEqual({key[2]: value["total_qty"] for key, value in fifo.items()},
			{self.locations[0].name: 3, self.locations[1].name: 6})
		from erpnext.stock.report.stock_ledger.stock_ledger import execute as ledger_report
		from erpnext.stock.report.stock_analytics.stock_analytics import execute as analytics_report
		_, ledger = ledger_report(filters.copy())
		last_by_location = {row.storage_location: row.qty_after_transaction for row in ledger if row.get("voucher_no")}
		self.assertEqual(last_by_location, {self.locations[0].name: 3, self.locations[1].name: 6})
		analytics_filters = filters.copy()
		analytics_filters.item_code = item.name
		analytics_filters.warehouse = self.warehouse
		analytics_filters.range = "Monthly"
		analytics_filters.value_quantity = "Quantity"
		columns, analytics = analytics_report(analytics_filters)[:2]
		period_column = next(column["fieldname"] for column in columns if column.get("fieldtype") == "Float")
		self.assertEqual({row["storage_location"]: row[period_column] for row in analytics},
			{self.locations[0].name: 3, self.locations[1].name: 6})
		with patch("erpnext.stock.report.stock_balance.stock_balance.StockBalanceReport.get_sre_reserved_qty_details",
			return_value={(item.name, self.warehouse, None): 2}):
			_, rows = stock_balance(filters.copy())
			self.assertEqual(sum(row.reserved_stock for row in rows), 2)
		filters.show_location_wise_stock = 0
		_, rows = stock_balance(filters)
		self.assertEqual(sum(row.bal_qty for row in rows), 9)
		reconciliation.cancel()
		filters.show_location_wise_stock = 1
		_, rows = stock_balance(filters)
		self.assertEqual(sum(row.bal_qty for row in rows), 10)

	def test_negative_stock_per_location(self):
		item = make_item("_Test Location Shortage " + frappe.generate_hash(length=8))
		receipt = make_stock_entry(item_code=item.name, qty=10, rate=100,
			to_warehouse=self.warehouse, posting_date=add_days(today(), -1), do_not_save=True)
		receipt.items[0].to_storage_location = self.locations[0].name
		receipt.insert().submit()
		issue = make_stock_entry(item_code=item.name, qty=1,
			from_warehouse=self.warehouse, do_not_save=True)
		issue.items[0].storage_location = self.locations[1].name
		issue.insert()
		self.assertRaises(InventoryDimensionNegativeStockError, issue.submit)

	def test_stock_reports_location_toggle(self):
		from importlib import import_module

		item = make_item("_Test Reports Location " + frappe.generate_hash(length=8))
		for location, qty in ((self.locations[0].name, 4), (self.locations[1].name, 6)):
			receipt = make_stock_entry(item_code=item.name, qty=qty, rate=100,
				to_warehouse=self.warehouse, do_not_save=True)
			receipt.items[0].to_storage_location = location
			receipt.insert().submit()
		for name in ("stock_balance", "stock_ledger", "stock_ageing", "stock_analytics",
			"warehouse_wise_stock_balance", "warehouse_wise_item_balance_age_and_value",
			"total_stock_summary", "stock_projected_qty", "item_shortage_report"):
			for enabled in (1, 0):
				with self.subTest(report=name, enabled=enabled):
					filters = frappe._dict(company="_Test Company", item_code=item.name,
						warehouse=self.warehouse, from_date=today(), to_date=today(),
						show_location_wise_stock=enabled, group_by="Warehouse", value_quantity="Quantity",
						range="30, 60, 90" if name == "stock_ageing" else "Monthly")
					if name in ("stock_balance", "stock_ledger"):
						filters.item_code = [item.name]
					if name in ("stock_ledger", "item_shortage_report"):
						filters.warehouse = [self.warehouse]
					columns, rows = import_module(f"erpnext.stock.report.{name}.{name}").execute(filters)[:2]
					if name == "item_shortage_report" and not rows:
						continue
					location_columns = [column for column in columns if
						(isinstance(column, dict) and column.get("fieldname") == "storage_location") or
						(isinstance(column, str) and ":Link/Storage Location:" in column)]
					self.assertEqual(bool(location_columns), bool(enabled))
					self.assertTrue(rows)
					if enabled:
						locations = {row.get("storage_location") if isinstance(row, dict) else row[-1] for row in rows}
						self.assertTrue({location.name for location in self.locations}.issubset(locations))

	def test_batch_reports_location_toggle(self):
		from importlib import import_module

		item = make_item("_Test Batch Report Location " + frappe.generate_hash(length=8),
			{"has_batch_no": 1, "create_new_batch": 1, "batch_number_series": "BATCH-LOC-.#####"})
		for location, qty in ((self.locations[0].name, 4), (self.locations[1].name, 6)):
			receipt = make_stock_entry(item_code=item.name, qty=qty, rate=100,
				to_warehouse=self.warehouse, do_not_save=True)
			receipt.items[0].to_storage_location = location
			receipt.insert().submit()
		for name in ("available_batch_report", "batch_wise_balance_history", "batch_item_expiry_status", "serial_and_batch_summary"):
			for enabled in (1, 0):
				with self.subTest(report=name, enabled=enabled):
					filters = frappe._dict(company="_Test Company", item_code=item.name, item=item.name,
						warehouse=self.warehouse, from_date=today(), to_date=today(),
						show_location_wise_stock=enabled, include_expired_batches=1)
					columns, rows = import_module(f"erpnext.stock.report.{name}.{name}").execute(filters)[:2]
					self.assertTrue(rows)
					if enabled:
						locations = {row.get("storage_location") if isinstance(row, dict) else row[-1] for row in rows}
						self.assertEqual(locations, {location.name for location in self.locations})

	def test_projected_stock_keeps_planning_unassigned(self):
		from erpnext.stock.report.storage_location_utils import split_bin_rows

		item = make_item("_Test Location Plans " + frappe.generate_hash(length=8))
		for location, qty in ((self.locations[0].name, 4), (self.locations[1].name, 6)):
			receipt = make_stock_entry(item_code=item.name, qty=qty, rate=100,
				to_warehouse=self.warehouse, do_not_save=True)
			receipt.items[0].to_storage_location = location
			receipt.insert().submit()
		bin_row = frappe._dict(item_code=item.name, warehouse=self.warehouse, actual_qty=10,
			planned_qty=3, reserved_qty=2, projected_qty=11)
		rows = split_bin_rows([bin_row], {})
		self.assertEqual(sum(row.actual_qty for row in rows), 10)
		self.assertEqual(sum(row.projected_qty for row in rows), 11)
		self.assertEqual(sum(row.planned_qty for row in rows), 3)
		self.assertEqual(sum(row.reserved_qty for row in rows), 2)
		self.assertEqual([row.planned_qty for row in rows if row.storage_location], [0, 0])

	def test_serial_reports_location_toggle(self):
		from importlib import import_module

		item = make_item("_Test Serial Report Location " + frappe.generate_hash(length=8),
			{"has_serial_no": 1, "serial_no_series": "SER-LOC-.#####"})
		for location, qty in ((self.locations[0].name, 2), (self.locations[1].name, 3)):
			receipt = make_stock_entry(item_code=item.name, qty=qty, rate=100,
				to_warehouse=self.warehouse, do_not_save=True)
			receipt.items[0].to_storage_location = location
			receipt.insert().submit()
		for name in ("serial_no_status", "serial_no_warranty_expiry", "serial_no_service_contract_expiry",
			"stock_qty_vs_serial_no_count", "serial_no_ledger", "serial_no_and_batch_traceability", "available_serial_no"):
			for enabled in (1, 0):
				with self.subTest(report=name, enabled=enabled):
					filters = frappe._dict(company="_Test Company", item_code=item.name,
						warehouse=self.warehouse, from_date=today(), to_date=today(),
						show_location_wise_stock=enabled, traceability_direction="Both", valuation_field_type="Currency",
						posting_date=today(), posting_time="23:59:59")
					columns, rows = import_module(f"erpnext.stock.report.{name}.{name}").execute(filters)[:2]
					self.assertTrue(rows)
					if enabled:
						locations = {row.get("storage_location") for row in rows}
						self.assertTrue({location.name for location in self.locations}.issubset(locations))

	def test_other_stock_reports_location_filters(self):
		from importlib import import_module
		from unittest.mock import patch

		item = make_item("_Test Report Smoke " + frappe.generate_hash(length=8))
		receipt = make_stock_entry(item_code=item.name, qty=4, rate=100,
			to_warehouse=self.warehouse, do_not_save=True)
		receipt.items[0].to_storage_location = self.locations[0].name
		receipt.insert().submit()
		for name in ("item_balance_simple", "items_to_be_requested", "requested_items_to_be_transferred",
			"reserved_stock", "item_price_stock", "itemwise_recommended_reorder_level", "item_wise_consumption",
			"negative_batch_report", "stock_qty_vs_batch_qty", "stock_and_account_value_comparison", "cogs_by_item_group",
			"incorrect_balance_qty_after_transaction", "incorrect_serial_no_valuation", "incorrect_stock_value_report",
			"incorrect_serial_and_batch_bundle", "fifo_queue_vs_qty_after_transaction_comparison", "stock_ledger_invariant_check",
			"stock_ledger_variance", "delivery_note_trends", "purchase_receipt_trends"):
			for enabled in (1, 0):
				with self.subTest(report=name, enabled=enabled), patch("erpnext.is_perpetual_inventory_enabled", return_value=True):
					filters = frappe._dict(company="_Test Company", item_code=item.name, warehouse=self.warehouse,
						from_date=today(), to_date=today(), as_on_date=today(), show_location_wise_stock=enabled,
						based_on="Item", period="Monthly", difference_in="Qty", show_incorrect_entries=0)
					columns, rows = import_module(f"erpnext.stock.report.{name}.{name}").execute(filters)[:2]
					if columns:
						location_columns = [column for column in columns if
							(isinstance(column, dict) and column.get("fieldname") == "storage_location") or
							(isinstance(column, str) and ":Link/Storage Location:" in column)]
						self.assertEqual(bool(location_columns), bool(enabled))

	def test_receipt_trends_and_landed_cost_locations(self):
		from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt
		from erpnext.stock.doctype.landed_cost_voucher.test_landed_cost_voucher import make_landed_cost_voucher
		from erpnext.stock.report.purchase_receipt_trends.purchase_receipt_trends import execute as receipt_trends
		from erpnext.stock.report.landed_cost_report.landed_cost_report import execute as landed_cost_report

		item = make_item("_Test Receipt Location Trend " + frappe.generate_hash(length=8))
		receipts = []
		for location, qty in ((self.locations[0].name, 4), (self.locations[1].name, 6)):
			receipt = make_purchase_receipt(item_code=item.name, warehouse=self.warehouse, qty=qty,
				uom=item.stock_uom, stock_uom=item.stock_uom, rate=100, do_not_save=True)
			receipt.items[0].storage_location = location
			receipt.insert().submit()
			receipts.append(receipt)
		for enabled in (1, 0):
			filters = frappe._dict(company="_Test Company", based_on="Item", period="Monthly", show_location_wise_stock=enabled)
			columns, rows = receipt_trends(filters)[:2]
			item_rows = [row for row in rows if row[0] == item.name]
			self.assertEqual(sum(row[-4 if enabled else -3] for row in item_rows), 10)
			if enabled:
				self.assertEqual({row[-1] for row in item_rows}, {location.name for location in self.locations})
		lcv = make_landed_cost_voucher(receipt_document_type="Purchase Receipt", receipt_document=receipts[0].name,
			charges=40, do_not_save=True)
		lcv.get_items_from_purchase_receipts()
		lcv.insert().submit()
		columns, rows = landed_cost_report(frappe._dict(company="_Test Company", from_date=today(), to_date=today(),
			show_location_wise_stock=1, raw_material_voucher_no=receipts[0].name))
		allocations = [row for row in rows if row.get("allocated_cost")]
		self.assertEqual(sum(row.allocated_cost for row in allocations), 40)
		self.assertEqual({row.storage_location for row in allocations}, {self.locations[0].name})

	def test_stock_ledger_opening_and_location_running_balance(self):
		from erpnext.stock.report.stock_ledger.stock_ledger import execute as ledger_report

		item = make_item("_Test Opening Location Ledger " + frappe.generate_hash(length=8))
		for location, qty in ((self.locations[0].name, 4), (self.locations[1].name, 6)):
			receipt = make_stock_entry(item_code=item.name, qty=qty, rate=100,
				to_warehouse=self.warehouse, posting_date=add_days(today(), -1), do_not_save=True)
			receipt.items[0].to_storage_location = location
			receipt.insert().submit()
		issue = make_stock_entry(item_code=item.name, qty=1, from_warehouse=self.warehouse, do_not_save=True)
		issue.items[0].storage_location = self.locations[0].name
		issue.insert().submit()
		filters = frappe._dict(company="_Test Company", item_code=[item.name], warehouse=[self.warehouse],
			from_date=today(), to_date=today(), show_location_wise_stock=1)
		_, rows = ledger_report(filters)
		opening = [row for row in rows if not row.get("voucher_no")]
		self.assertEqual({row.storage_location: row.qty_after_transaction for row in opening},
			{self.locations[0].name: 4, self.locations[1].name: 6})
		self.assertEqual(rows[-1].qty_after_transaction, 3)
		self.assertEqual(rows[-1].stock_value, 300)
		filters.show_location_wise_stock = 0
		filters.storage_location = [self.locations[0].name, self.locations[1].name]
		_, rows = ledger_report(filters)
		self.assertEqual(rows[-1].qty_after_transaction, 9)

	def get_location_balance(self, item, location):
		return frappe.db.sql("""select coalesce(sum(actual_qty), 0) from `tabStock Ledger Entry`
			where item_code=%s and storage_location=%s and is_cancelled=0""", (item, location))[0][0]
