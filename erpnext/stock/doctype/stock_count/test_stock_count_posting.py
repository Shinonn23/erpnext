"""A Stock Count posts its result when it is approved (Connect Diagnostics, 5 Nov 2026; offline)."""

import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_count import stock_count
from erpnext.stock.doctype.stock_count.stock_count import (
	StockCount,
	failure_reason,
	is_count_posting,
	system_posting,
)


def entry(name="SE-1", stock_count="SC-1", doctype="Stock Entry"):
	return frappe._dict(doctype=doctype, name=name, stock_count=stock_count)


class TestSystemPostingIdentity(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))

	def test_only_the_entry_being_posted_qualifies(self):
		posting = entry()
		self.assertFalse(is_count_posting(posting))
		with system_posting(posting):
			self.assertTrue(is_count_posting(posting))
			# the ledger policy reloads the voucher: the name identifies it
			self.assertTrue(is_count_posting(entry()))
			# a user's own entry, even one that names the count, never qualifies
			self.assertFalse(is_count_posting(entry(name="SE-2")))
			self.assertFalse(is_count_posting(entry(name=None)))
			self.assertFalse(is_count_posting(entry(doctype="Delivery Note")))
		self.assertFalse(is_count_posting(posting))

	def test_the_count_link_is_required_and_the_flag_is_restored(self):
		posting = entry(stock_count=None)
		with system_posting(posting):
			self.assertFalse(is_count_posting(posting))
		outer = entry(name="SE-3")
		with system_posting(outer):
			with system_posting(entry(name="SE-4")):
				self.assertTrue(is_count_posting(entry(name="SE-4")))
			self.assertTrue(is_count_posting(outer))
		self.assertIsNone(frappe.flags.get("stock_count_posting"))

	def test_failure_reason_returns_the_messages_and_drops_them(self):
		log = ['{"message": "<b>Item X</b> needs 5 more"}', {"message": "in LOST"}]
		with patch.object(frappe.local, "message_log", log, create=True):
			self.assertEqual(failure_reason(Exception("raw"), 0), "Item X needs 5 more in LOST")
			self.assertEqual(log, [])
			self.assertEqual(failure_reason(Exception("raw text"), 0), "raw text")


class TestSurplusIsNotPosted(unittest.TestCase):
	def test_reconciliation_records_shortages_without_receiving_surplus(self):
		rows = [
			frappe._dict(item_code="A", batch_no=None, serial_no=None, system_qty=5, counted_qty=8),
			frappe._dict(item_code="B", batch_no=None, serial_no=None, system_qty=5, counted_qty=3),
			frappe._dict(item_code="S", batch_no=None, serial_no="SN-NEW", system_qty=0, counted_qty=1),
		]
		doc = SimpleNamespace(
			items=rows, company="C", warehouse="WH", name="COUNT", snapshot_datetime="2026-01-01 12:00:00"
		)
		doc.get_row_dimension_values = lambda row: {}
		doc.posts_variance = lambda: False
		doc.set_reconciliation_department = lambda reconciliation: None
		reconciliation = Mock()
		reconciliation.meta.has_field.return_value = False
		with (
			patch.object(frappe, "new_doc", return_value=reconciliation),
			patch.object(frappe, "get_meta", return_value=Mock()),
			patch.object(frappe, "get_cached_doc", return_value=frappe._dict(has_serial_no=0)),
		):
			StockCount.make_stock_reconciliation(doc)
		items = [call.args[1] for call in reconciliation.append.call_args_list]
		self.assertEqual([(row["item_code"], row["qty"]) for row in items], [("B", 3)])

	def test_serial_surplus_does_not_replace_a_missing_serial(self):
		rows = [
			frappe._dict(item_code="S", batch_no=None, serial_no="SN-OLD", system_qty=1, counted_qty=0),
			frappe._dict(item_code="S", batch_no=None, serial_no="SN-NEW", system_qty=0, counted_qty=1),
		]
		doc = SimpleNamespace(
			items=rows, company="C", warehouse="WH", name="COUNT", snapshot_datetime="2026-01-01 12:00:00"
		)
		doc.get_row_dimension_values = lambda row: {}
		doc.posts_variance = lambda: False
		doc.set_reconciliation_department = lambda reconciliation: None
		reconciliation = Mock()
		reconciliation.meta.has_field.return_value = False
		with (
			patch.object(frappe, "new_doc", return_value=reconciliation),
			patch.object(frappe, "get_meta", return_value=Mock()),
			patch.object(frappe, "get_cached_doc", return_value=frappe._dict(has_serial_no=1)),
		):
			StockCount.make_stock_reconciliation(doc)
		row = reconciliation.append.call_args.args[1]
		self.assertEqual((row["qty"], row["serial_no"]), (0, ""))


class TestPostEntry(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(stock_count, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(frappe.local, "message_log", [], create=True))

	def test_the_entry_is_inserted_and_submitted_while_it_is_the_posting_document(self):
		seen = []
		stock_entry = Mock()
		stock_entry.insert.side_effect = lambda **kw: seen.append(
			("insert", frappe.flags.stock_count_posting)
		)
		stock_entry.submit.side_effect = lambda: seen.append(("submit", frappe.flags.stock_count_posting))
		self.assertIs(StockCount.post_entry(Mock(), stock_entry, "the transfer"), stock_entry)
		self.assertEqual(seen, [("insert", stock_entry), ("submit", stock_entry)])
		self.assertTrue(stock_entry.flags.ignore_permissions)
		self.assertIsNone(frappe.flags.get("stock_count_posting"))

	def test_a_failure_says_what_could_not_be_posted(self):
		stock_entry = Mock()
		stock_entry.submit.side_effect = frappe.ValidationError("negative stock")
		with (
			patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError) as throw,
			self.assertRaises(frappe.ValidationError),
		):
			StockCount.post_entry(Mock(), stock_entry, "the transfer to the Lost Warehouse")
		message = throw.call_args.args[0]
		self.assertIn("cannot be approved yet", message)
		self.assertIn("the transfer to the Lost Warehouse", message)
		self.assertIn("negative stock", message)


class TestRecountRows(unittest.TestCase):
	def test_a_recount_carries_the_surplus_the_system_does_not_hold(self):
		doc = Mock()
		doc.recount_of = "SC-1"
		doc.items = [frappe._dict(item_code="A", batch_no=None, serial_no=None)]
		doc.row_key = lambda row: (row.item_code, row.batch_no or "", row.serial_no or "", ())
		doc.split_surplus_of = lambda name: {
			("A", "", "", ()): 2,
			("B", "L1", "", (("storage_location", "S9"),)): 4,
		}
		added = []
		doc.append = lambda table, values: added.append(values)
		with patch.object(stock_count.frappe, "get_cached_value", return_value=None):
			self.assertEqual(StockCount.add_first_count_surplus_rows(doc), {"B"})
		self.assertEqual(len(added), 1)
		self.assertEqual(added[0]["item_code"], "B")
		self.assertEqual(added[0]["system_qty"], 0)
		self.assertEqual(added[0]["counted_qty"], 0)
		self.assertEqual(added[0]["storage_location"], "S9")

	def test_a_first_count_carries_nothing(self):
		doc = Mock()
		doc.recount_of = None
		self.assertEqual(StockCount.add_first_count_surplus_rows(doc), set())


class TestDeadSurplusCodeIsGone(unittest.TestCase):
	def test_no_found_receipt_recount_or_surplus_posting_is_left(self):
		"""The count posts only its shortage (11 Nov 2026); the FOUND receipt and the recount maker were deleted (WP10)."""
		for name in (
			"handle_surplus",
			"make_recount",
			"make_found_receipt",
			"make_found_transfer",
			"take_back_from_lost",
			"lost_balance",
			"validate_found_warehouse",
		):
			self.assertFalse(hasattr(StockCount, name), name)
		for name in (
			"get_found_warehouse",
			"found_valuation_rate",
			"confirmed_surplus",
			"FOUND_WAREHOUSE_TYPE",
		):
			self.assertFalse(hasattr(stock_count, name), name)


class TestResolveStorageLocation(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(stock_count.frappe, "has_permission"))
		self.enterContext(patch.object(stock_count, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError))

	def test_name_then_code(self):
		with patch.object(stock_count.frappe, "db", Mock(get_value=Mock(return_value="WH / C1"))):
			self.assertEqual(stock_count.resolve_storage_location("WH", " WH / C1 "), "WH / C1")
		with (
			patch.object(stock_count.frappe, "db", Mock(get_value=Mock(return_value=None))),
			patch.object(stock_count.frappe, "get_all", return_value=["WH / C1"]),
		):
			self.assertEqual(stock_count.resolve_storage_location("WH", "C1"), "WH / C1")

	def test_unknown_or_ambiguous_label_is_refused(self):
		for matches in ([], ["WH / C1", "WH / C1 "]):
			with (
				patch.object(stock_count.frappe, "db", Mock(get_value=Mock(return_value=None))),
				patch.object(stock_count.frappe, "get_all", return_value=matches),
				self.assertRaises(frappe.ValidationError),
			):
				stock_count.resolve_storage_location("WH", "C1")
