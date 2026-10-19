import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_count import stock_count
from erpnext.stock.doctype.stock_count.stock_count import StockCount


def count(rows, before_rows):
	doc = frappe._dict(items=[frappe._dict(idx=i + 1, **row) for i, row in enumerate(rows)])
	doc.get_countable_dimensions = lambda: [frappe._dict(source_fieldname="storage_location")]
	before = frappe._dict(snapshot_datetime="2026-10-19 09:00:00", items=[frappe._dict(**row) for row in before_rows])
	doc.get_doc_before_save = Mock(return_value=before)
	return doc


SNAPSHOT = [
	{"item_code": "ITEM-A", "batch_no": "B1", "serial_no": None, "storage_location": "L1", "system_qty": 10},
	{"item_code": "ITEM-B", "batch_no": None, "serial_no": None, "storage_location": "L1", "system_qty": 3},
]


class TestSnapshotRows(unittest.TestCase):
	def test_uploaded_system_qty_is_replaced_by_the_snapshot(self):
		uploaded = [dict(SNAPSHOT[0], system_qty=9, counted_qty=9), dict(SNAPSHOT[1], counted_qty=3)]
		doc = count(uploaded, SNAPSHOT)
		StockCount.keep_snapshot_rows(doc)
		self.assertEqual([row.system_qty for row in doc["items"]], [10, 3])

	def test_rows_cannot_be_added_or_dropped(self):
		self.enterContext(patch.object(stock_count, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError))
		extra = [*SNAPSHOT, {"item_code": "ITEM-C", "batch_no": None, "serial_no": None, "storage_location": "L1", "system_qty": 0}]
		with self.assertRaises(frappe.ValidationError):
			StockCount.keep_snapshot_rows(count(extra, SNAPSHOT))
		with self.assertRaises(frappe.ValidationError):
			StockCount.keep_snapshot_rows(count(SNAPSHOT[:1], SNAPSHOT))

	def test_preparing_the_count_is_not_checked(self):
		doc = count(SNAPSHOT, [])
		doc.get_doc_before_save.return_value = frappe._dict(snapshot_datetime=None, items=[])
		StockCount.keep_snapshot_rows(doc)
