# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_count.stock_count import StockCount
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.storage_location.storage_location import setup_storage_location_dimension
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.tests.utils import ERPNextTestSuite


class TestStockCount(ERPNextTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_storage_location_dimension()

	def setUp(self):
		super().setUp()
		self.warehouse = create_warehouse("_Test Count Warehouse")
		self.quarantine_warehouse = create_warehouse("_Test Count Quarantine")
		self.locations = [
			frappe.get_doc(
				{
					"doctype": "Storage Location",
					"warehouse": self.warehouse,
					"location_code": f"COUNT-{frappe.generate_hash(length=6)}-{index}",
				}
			).insert()
			for index in (1, 2)
		]
		self.items = [make_item(f"_Test Count Item {frappe.generate_hash(length=8)}") for _ in range(2)]
		for item, location in zip(self.items, self.locations, strict=True):
			receipt = make_stock_entry(
				item_code=item.name,
				qty=5,
				rate=10,
				to_warehouse=self.warehouse,
				do_not_save=True,
			)
			receipt.items[0].to_storage_location = location.name
			receipt.insert().submit()

	def test_monthly_sample_is_limited_to_selected_storage_location(self):
		count = frappe.get_doc(
			{
				"doctype": "Stock Count",
				"company": frappe.db.get_value("Warehouse", self.warehouse, "company"),
				"count_type": "Monthly",
				"warehouse": self.warehouse,
				"dimensions": [
					{
						"inventory_dimension": "Storage Location",
						"reference_document": "Storage Location",
						"dimension_value": self.locations[0].name,
					}
				],
				"item_count": 10,
				"quarantine_warehouse": self.quarantine_warehouse,
			}
		)
		self.assertEqual(count.get_item_codes_to_count(now_datetime()), [self.items[0].name])
		count.append_snapshot_rows(self.items[0].name, now_datetime())
		self.assertEqual(len(count.items), 1)
		self.assertEqual(count.items[0].storage_location, self.locations[0].name)
		self.assertEqual(count.items[0].system_qty, 5)

	def test_count_lines_cannot_be_entered_manually_before_sampling(self):
		count = frappe.get_doc(
			{
				"doctype": "Stock Count",
				"company": frappe.db.get_value("Warehouse", self.warehouse, "company"),
				"count_type": "Annual",
				"warehouse": self.warehouse,
			}
		)
		count.append("items", {"item_code": self.items[0].name, "system_qty": 5, "counted_qty": 0})
		self.assertRaises(frappe.ValidationError, count.validate)


class TestStockCountLineValidation(unittest.TestCase):
	def make_count(self, rows, dimensions=("storage_location",)):
		return SimpleNamespace(
			warehouse="Count Warehouse",
			items=[
				frappe._dict(
					{
						"idx": index,
						"item_code": "Count Item",
						"batch_no": None,
						"serial_no": None,
						"system_qty": 5,
						"counted_qty": 5,
						"is_counted": 1,
						**row,
					}
				)
				for index, row in enumerate(rows, 1)
			],
			get_countable_dimensions=lambda: [
				frappe._dict(source_fieldname=fieldname) for fieldname in dimensions
			],
		)

	def validate_lines(self, count):
		with (
			patch.object(
				frappe,
				"get_cached_doc",
				return_value=frappe._dict(is_stock_item=1, has_batch_no=0, has_serial_no=0),
			),
			patch.object(
				frappe, "db", SimpleNamespace(get_value=lambda *args, **kwargs: count.warehouse)
			),
		):
			StockCount.validate_count_lines(count)

	def test_same_item_in_two_locations_is_valid(self):
		count = self.make_count(
			[
				{"storage_location": "STAGING", "system_qty": 4, "counted_qty": 4},
				{"storage_location": "SHELF", "system_qty": 6, "counted_qty": 6},
			]
		)
		self.validate_lines(count)
		self.assertEqual([row.difference_qty for row in count.items], [0, 0])

	def test_duplicate_dimensions_are_rejected(self):
		count = self.make_count([{"storage_location": "STAGING"}, {"storage_location": "STAGING"}])
		with self.assertRaisesRegex(frappe.ValidationError, "duplicate Item"):
			self.validate_lines(count)

	def test_blank_dimensions_are_normalized(self):
		count = self.make_count([{"storage_location": None}, {"storage_location": ""}])
		with self.assertRaisesRegex(frappe.ValidationError, "duplicate Item"):
			self.validate_lines(count)

	def test_other_configured_dimensions_distinguish_rows(self):
		count = self.make_count(
			[
				{"storage_location": "STAGING", "project": "Project A"},
				{"storage_location": "STAGING", "project": "Project B"},
			],
			dimensions=("project", "storage_location"),
		)
		self.validate_lines(count)

	def test_serial_is_unique_across_locations_and_items(self):
		count = self.make_count(
			[
				{"storage_location": "STAGING", "serial_no": "SERIAL-1", "counted_qty": 1},
				{
					"storage_location": "SHELF",
					"item_code": "Other Item",
					"serial_no": "SERIAL-1",
					"counted_qty": 1,
				},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "listed more than once"):
			self.validate_lines(count)

	def test_scan_location_must_be_active_in_counted_warehouse(self):
		count = SimpleNamespace(warehouse="Count Warehouse", get=lambda fieldname: "SCAN-LOCATION")
		for location in (
			None,
			frappe._dict(warehouse="Other Warehouse", disabled=0),
			frappe._dict(warehouse="Count Warehouse", disabled=1),
		):
			with self.subTest(location=location), patch.object(
				frappe, "get_cached_value", return_value=location
			):
				with self.assertRaises(frappe.ValidationError):
					StockCount.validate(count)
