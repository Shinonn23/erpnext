"""A Stock Count posts its result when it is approved (Connect Diagnostics, 5 Nov 2026; offline)."""

import unittest
from collections import defaultdict
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


class TestTakeBackFromLost(unittest.TestCase):
	def count(self, lost, serial_items=()):
		doc = Mock()
		doc.lost_balance = lambda item, batch: lost.get((item, batch or ""), 0)
		self.enterContext(
			patch.object(
				stock_count.frappe,
				"get_cached_value",
				side_effect=lambda doctype, item, field: item in serial_items,
			)
		)
		return doc

	def rows(self, **items):
		rows = defaultdict(lambda: {"qty": 0.0, "serial_nos": []})
		for (item, batch), qty in items.items():
			rows[(item, batch, ())]["qty"] = qty
		return rows

	def test_lost_stock_comes_back_first_and_only_the_rest_is_new(self):
		doc = self.count({("A", ""): 3, ("B", "L1"): 10})
		rows = self.rows()
		rows[("A", "", ())]["qty"] = 5
		rows[("B", "L1", ())]["qty"] = 4
		rows[("C", "", ())]["qty"] = 2
		back = StockCount.take_back_from_lost(doc, rows)
		self.assertEqual(back, {("A", "", ()): 3, ("B", "L1", ()): 4})
		self.assertEqual(rows[("A", "", ())]["qty"], 2)
		self.assertEqual(rows[("B", "L1", ())]["qty"], 0)
		self.assertEqual(rows[("C", "", ())]["qty"], 2)

	def test_two_rows_of_one_item_share_what_lost_holds(self):
		doc = self.count({("A", ""): 4})
		rows = defaultdict(lambda: {"qty": 0.0, "serial_nos": []})
		rows[("A", "", (("storage_location", "S1"),))]["qty"] = 3
		rows[("A", "", (("storage_location", "S2"),))]["qty"] = 3
		back = StockCount.take_back_from_lost(doc, rows)
		self.assertEqual(sum(back.values()), 4)
		self.assertEqual(sum(values["qty"] for values in rows.values()), 2)

	def test_serial_numbers_are_never_netted(self):
		doc = self.count({("S", ""): 5}, serial_items=("S",))
		rows = defaultdict(lambda: {"qty": 0.0, "serial_nos": []})
		rows[("S", "", ())]["qty"] = 1
		rows[("S", "", ())]["serial_nos"] = ["SN-1"]
		self.assertEqual(StockCount.take_back_from_lost(doc, rows), {})
		self.assertEqual(rows[("S", "", ())]["qty"], 1)


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


class TestHandleSurplus(unittest.TestCase):
	def doc(self, recount_of, pending, first_surplus=None, transfers=None, back=None):
		doc = Mock()
		doc.recount_of = recount_of
		doc.found_warehouse = "FOUND - CD"
		doc.split_surplus = lambda: (pending, transfers or {}, [])
		doc.take_back_from_lost = lambda rows: back or {}
		doc.make_recount = Mock(return_value=frappe._dict(name="SC-2"))
		doc.make_found_receipt = Mock(return_value=frappe._dict(name="SE-F"))
		self.enterContext(
			patch.object(
				stock_count.frappe,
				"get_doc",
				return_value=Mock(surplus=lambda: first_surplus or {}),
			)
		)
		return doc

	def test_a_first_count_posts_the_transfer_back_and_makes_the_recount(self):
		key = ("A", "", "", ())
		doc = self.doc(None, {key: 2}, transfers={("S", "", "LOST - CD", ()): ["SN-1"]})
		StockCount.handle_surplus(doc)
		doc.make_found_transfer.assert_called_once()
		doc.db_set.assert_called_with("recount_stock_count", "SC-2")
		doc.make_found_receipt.assert_not_called()

	def test_a_recount_takes_back_from_lost_and_receives_only_the_rest(self):
		key = ("A", "", "", ())
		doc = self.doc("SC-1", {key: 5}, first_surplus={key: 5}, back={("A", "", ()): 3})

		def net(rows):
			rows[("A", "", ())]["qty"] -= 3
			return {("A", "", ()): 3}

		doc.take_back_from_lost = net
		StockCount.handle_surplus(doc)
		doc.make_found_transfer.assert_called_once_with({}, {("A", "", ()): 3})
		received = doc.make_found_receipt.call_args.args[0]
		self.assertEqual(received[("A", "", ())]["qty"], 2)
		doc.db_set.assert_called_with("found_stock_entry", "SE-F")

	def test_nothing_is_received_when_lost_covers_the_surplus(self):
		key = ("A", "", "", ())
		doc = self.doc("SC-1", {key: 3}, first_surplus={key: 3})

		def net(rows):
			rows[("A", "", ())]["qty"] = 0
			return {("A", "", ()): 3}

		doc.take_back_from_lost = net
		StockCount.handle_surplus(doc)
		doc.make_found_transfer.assert_called_once()
		doc.make_found_receipt.assert_not_called()


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
