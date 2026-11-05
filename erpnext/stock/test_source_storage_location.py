# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""An outgoing row with no source Storage Location takes the one that holds the stock, or is refused (never STAGING)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.storage_location import storage_location as module


def doc(doctype="Delivery Note", **values):
	return frappe._dict({"doctype": doctype, "docstatus": 0, "is_return": 0, **values})


class TestSourceStorageLocation(unittest.TestCase):
	def setUp(self):
		self.addCleanup(patch.stopall)
		self.item = frappe._dict(is_stock_item=1, has_serial_no=0, has_batch_no=0)
		self.negative = 0

		def throw(message, exc=frappe.ValidationError, title=None):
			raise exc(message)

		fake = SimpleNamespace(
			get_cached_value=lambda *args, **kwargs: self.item,
			db=SimpleNamespace(get_single_value=lambda *args: self.negative),
			throw=throw,
			ValidationError=frappe.ValidationError,
		)
		patch.object(module, "frappe", fake).start()
		self.places = {}
		self.unlocated = {}
		patch.object(
			module,
			"location_stock",
			side_effect=lambda *args, unlocated=False: self.unlocated if unlocated else self.places,
		).start()
		patch.object(module, "_", side_effect=lambda text: text).start()

	def row(self, **values):
		return frappe._dict(idx=2, qty=5, conversion_factor=1, stock_qty=5, **values)

	def test_choose_the_only_location_that_holds_enough(self):
		self.assertEqual(module.choose_source_location({"A": 10, "B": 2}, 5), ("A", ["A"]))
		self.assertEqual(module.choose_source_location({"A": 10, "B": 9}, 5), (None, ["A", "B"]))
		self.assertEqual(module.choose_source_location({"A": 3, "B": 2}, 5), (None, []))

	def test_the_location_holding_the_stock_is_used(self):
		self.places = {"WH / A-01": 8, "WH / STAGING": 1}
		self.assertEqual(module.source_storage_location(doc(), self.row(), "WH", "ITEM"), "WH / A-01")

	def test_two_locations_each_holding_enough_are_refused_by_name(self):
		self.places = {"WH / A-01": 8, "WH / A-02": 9}
		with self.assertRaisesRegex(frappe.ValidationError, r"Row 2.*more than one.*A-01 \(8\).*A-02 \(9\)"):
			module.source_storage_location(doc(), self.row(), "WH", "ITEM")

	def test_stock_split_below_the_need_is_refused_by_name(self):
		self.places = {"WH / A-01": 3, "WH / A-02": 3}
		with self.assertRaisesRegex(frappe.ValidationError, r"Row 2.*no single Storage Location.*A-01 \(3\)"):
			module.source_storage_location(doc(), self.row(), "WH", "ITEM")

	def test_no_stock_at_any_location_keeps_the_default(self):
		self.assertIsNone(module.source_storage_location(doc(), self.row(), "WH", "ITEM"))

	def test_stock_without_any_location_keeps_the_default(self):
		self.unlocated = {"": 8}
		self.assertIsNone(module.source_storage_location(doc(), self.row(), "WH", "ITEM"))
		self.places = {"WH / A-01": 1}
		self.assertIsNone(module.source_storage_location(doc(), self.row(), "WH", "ITEM"))

	def test_unlocated_stock_below_the_need_is_still_refused(self):
		self.unlocated = {"": 3}
		self.places = {"WH / A-01": 1}
		with self.assertRaisesRegex(frappe.ValidationError, "no single Storage Location"):
			module.source_storage_location(doc(), self.row(), "WH", "ITEM")

	def test_unlocated_stock_does_not_hide_ambiguous_locations(self):
		self.unlocated = {"": 8}
		self.places = {"WH / A-01": 8, "WH / A-02": 9}
		with self.assertRaisesRegex(frappe.ValidationError, "more than one"):
			module.source_storage_location(doc(), self.row(), "WH", "ITEM")

	def test_negative_stock_allowed_and_non_stock_items_keep_the_default(self):
		self.item.is_stock_item = 0
		self.assertIsNone(module.source_storage_location(doc(), self.row(), "WH", "ITEM"))
		self.item.is_stock_item = 1
		self.negative = 1
		self.assertIsNone(module.source_storage_location(doc(), self.row(), "WH", "ITEM"))
		self.negative = 0
		self.assertIsNone(
			module.source_storage_location(doc(), self.row(serial_and_batch_bundle="SBB-1"), "WH", "ITEM")
		)

	def test_serial_items_follow_their_serial_nos(self):
		self.item.has_serial_no = 1
		with patch.object(module, "serial_locations", return_value={"WH / A-02": 2}):
			self.assertEqual(
				module.source_storage_location(doc(), self.row(serial_no="S1\nS2"), "WH", "ITEM"),
				"WH / A-02",
			)
		with patch.object(module, "serial_locations", return_value={"WH / A-01": 1, "WH / A-02": 1}):
			with self.assertRaises(frappe.ValidationError):
				module.source_storage_location(doc(), self.row(serial_no="S1\nS2"), "WH", "ITEM")

	def test_which_rows_are_outgoing(self):
		self.assertTrue(module.is_outgoing_row(doc("Stock Entry"), frappe._dict(s_warehouse="A")))
		self.assertFalse(module.is_outgoing_row(doc("Stock Entry"), frappe._dict(t_warehouse="B")))
		self.assertTrue(module.is_outgoing_row(doc(), self.row()))
		self.assertTrue(module.is_outgoing_row(doc("Sales Invoice"), self.row()))
		self.assertFalse(module.is_outgoing_row(doc(is_return=1), self.row()))
		self.assertFalse(module.is_outgoing_row(doc("Purchase Receipt"), self.row()))

	def test_stock_entry_quantity_is_the_transfer_qty(self):
		row = frappe._dict(transfer_qty=12, qty=1, conversion_factor=12)
		self.assertEqual(module.row_stock_qty(doc("Stock Entry"), row), 12)
		self.assertEqual(module.row_stock_qty(doc(), frappe._dict(qty=3, conversion_factor=4)), 12)
