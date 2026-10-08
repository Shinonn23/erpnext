# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, today

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.stock_ledger_entry.stock_ledger_entry import InventoryDimensionNegativeStockError
from erpnext.stock.report.stock_balance.stock_balance import execute as stock_balance
from erpnext.stock.doctype.storage_location.storage_location import (
	setup_storage_location_dimension,
	validate_storage_location,
)
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.tests.utils import ERPNextTestSuite


class TestStorageLocation(ERPNextTestSuite):
	def setUp(self):
		super().setUp()
		setup_storage_location_dimension()
		self.warehouse = create_warehouse("_Test Storage Location Warehouse")
		self.other_warehouse = create_warehouse("_Test Storage Location Other Warehouse")
		self.locations = []
		for code in ("A-01", "A-02"):
			self.locations.append(frappe.get_doc({
				"doctype": "Storage Location", "warehouse": self.warehouse,
				"location_code": code + frappe.generate_hash(length=6),
			}).insert())

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
		self.locations[0].warehouse = self.other_warehouse
		self.assertRaises(frappe.ValidationError, self.locations[0].save)

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

	def get_location_balance(self, item, location):
		return frappe.db.sql("""select coalesce(sum(actual_qty), 0) from `tabStock Ledger Entry`
			where item_code=%s and storage_location=%s and is_cancelled=0""", (item, location))[0][0]
