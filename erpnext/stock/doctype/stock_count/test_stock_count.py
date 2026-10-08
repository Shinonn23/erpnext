# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import now_datetime

from erpnext.stock.doctype.item.test_item import make_item
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
				"storage_location": self.locations[0].name,
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
