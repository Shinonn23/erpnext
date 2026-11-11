import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_count import stock_count
from erpnext.stock.doctype.stock_count.stock_count import StockCount


def count(rows, before_rows):
	doc = frappe._dict(items=[frappe._dict(idx=i + 1, **row) for i, row in enumerate(rows)])
	doc.get_countable_dimensions = lambda: [frappe._dict(source_fieldname="storage_location")]
	before = frappe._dict(
		snapshot_datetime="2026-10-19 09:00:00", items=[frappe._dict(**row) for row in before_rows]
	)
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


class TestFoundIdentifiers(unittest.TestCase):
	def test_new_identifiers_are_recorded_but_existing_ones_must_match_item_and_batch(self):
		row = frappe._dict(idx=1, item_code="I", batch_no="B", serial_no="S", storage_location=None,
			is_counted=1, counted_qty=1, system_qty=0)
		doc = SimpleNamespace(items=[row], warehouse="WH", get_countable_dimensions=lambda: [])
		item = frappe._dict(is_stock_item=1, has_batch_no=1, has_serial_no=1)
		db = Mock()
		with (
			patch.object(frappe, "get_cached_doc", return_value=item),
			patch.object(frappe, "db", db),
			patch.object(stock_count, "_", lambda text: text),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
		):
			db.get_value.side_effect = [None, None]
			StockCount.validate_count_lines(doc)
			self.assertEqual(row.difference_qty, 1)
			db.get_value.side_effect = ["OTHER"]
			with self.assertRaises(frappe.ValidationError):
				StockCount.validate_count_lines(doc)
			db.get_value.side_effect = ["I", frappe._dict(item_code="I", batch_no="OTHER")]
			with self.assertRaises(frappe.ValidationError):
				StockCount.validate_count_lines(doc)


class TestRemainingSnapshotRows(unittest.TestCase):
	def test_an_added_row_is_found_stock(self):
		# A row the snapshot lacks is stock the counter found: System Qty 0 (Connect Diagnostics, 5 Nov 2026)
		added = {"item_code": "ITEM-C", "batch_no": None, "serial_no": None, "storage_location": "L1"}
		doc = count([*SNAPSHOT, dict(added, system_qty=5, counted_qty=2)], SNAPSHOT)
		StockCount.keep_snapshot_rows(doc)
		self.assertEqual([row.system_qty for row in doc["items"]], [10, 3, 0])

	def test_rows_cannot_be_dropped(self):
		self.enterContext(patch.object(stock_count, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError))
		with self.assertRaises(frappe.ValidationError):
			StockCount.keep_snapshot_rows(count(SNAPSHOT[:1], SNAPSHOT))

	def test_preparing_the_count_is_not_checked(self):
		doc = count(SNAPSHOT, [])
		doc.get_doc_before_save.return_value = frappe._dict(snapshot_datetime=None, items=[])
		StockCount.keep_snapshot_rows(doc)


class TestCancelDeletesGeneratedDrafts(unittest.TestCase):
	def test_the_link_is_cleared_before_the_generated_draft_is_deleted(self):
		# Frappe refuses to delete a document any other names, cancelled or not (a recount's FOUND receipt, 5 Nov 2026)
		calls = []
		count = Mock(name="count", recount_stock_count=None, stock_entry=None, found_transfer_entry=None)
		count.name = "SC-2"
		count.found_stock_entry = "SE-1"
		count.stock_reconciliation = None
		count.db_set.side_effect = lambda field, value: calls.append(("db_set", field, value))
		with (
			patch.object(
				stock_count.frappe, "get_doc", return_value=frappe._dict(docstatus=0, stock_count="SC-2")
			),
			patch.object(
				stock_count.frappe,
				"delete_doc",
				side_effect=lambda doctype, name, **kw: calls.append(("delete", name)),
			),
		):
			StockCount.on_cancel(count)
		self.assertEqual(calls, [("db_set", "found_stock_entry", None), ("delete", "SE-1")])

	def test_a_draft_recount_is_unlinked_then_deleted(self):
		calls = []
		count = Mock(
			stock_entry=None, found_stock_entry=None, found_transfer_entry=None, stock_reconciliation=None
		)
		count.name = "SC-1"
		count.recount_stock_count = "SC-2"
		count.db_set.side_effect = lambda field, value: calls.append(("db_set", field, value))
		recount = frappe._dict(name="SC-2", docstatus=0, recount_of="SC-1")
		with (
			patch.object(stock_count.frappe, "db", Mock(get_value=Mock(return_value=recount))),
			patch.object(
				stock_count.frappe,
				"delete_doc",
				side_effect=lambda doctype, name, **kw: calls.append(("delete", name)),
			),
		):
			StockCount.on_cancel(count)
		self.assertEqual(calls, [("db_set", "recount_stock_count", None), ("delete", "SC-2")])
