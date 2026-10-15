"""Stock balance regressions without a Frappe site or application database.

Run with FRAPPE_STREAM_LOGGING=1 python -m unittest erpnext.tests.test_stock_balance_dimensions.
"""

import re
import sqlite3
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.utils import get_stock_balance


class TestStockBalanceDimensions(unittest.TestCase):
	def setUp(self):
		self.connection = sqlite3.connect(":memory:")
		self.addCleanup(self.connection.close)
		self.connection.execute(
			"""create table `tabStock Ledger Entry` (
			item_code text, warehouse text, posting_datetime text, is_cancelled integer,
			storage_location text, actual_qty real, id_plant text)"""
		)
		self.connection.executemany(
			"""insert into `tabStock Ledger Entry` (item_code, warehouse, posting_datetime,
			is_cancelled, storage_location, actual_qty) values (?, ?, ?, ?, ?, ?)""",
			[
				("ITEM", "WH", "2026-10-14 09:00:00", 0, "SOURCE", 8),
				# A dimensioned reconciliation posts target 6 minus current 8, not target 6.
				("ITEM", "WH", "2026-10-14 10:00:00", 0, "SOURCE", -2),
				("ITEM", "WH", "2026-10-14 11:00:00", 0, "STAGING", 4),
				("ITEM", "WH", "2026-10-14 11:00:00", 1, "SOURCE", 100),
				("ITEM", "WH", "2026-10-14 12:00:01", 0, "SOURCE", 100),
				("OTHER", "WH", "2026-10-14 09:00:00", 0, "SOURCE", 100),
				("ITEM", "OTHER", "2026-10-14 09:00:00", 0, "SOURCE", 100),
				("ITEM", "WH", "2026-10-14 09:00:00", 0, None, 3),
				("ITEM", "WH", "2026-10-14 09:00:00", 0, "", 7),
				("ITEM", "WH", "2026-10-14 09:00:00", 0, "0", 5),
			],
		)
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.permission = self.stack.enter_context(patch("frappe.has_permission"))
		self.sql = Mock(side_effect=self.run_sql)
		self.stack.enter_context(patch("frappe.db", SimpleNamespace(sql=self.sql)))
		self.stack.enter_context(
			patch(
				"erpnext.stock.utils.get_inventory_dimensions",
				return_value=[{"fieldname": "storage_location"}, {"fieldname": "id_plant"}],
			)
		)
		self.previous = self.stack.enter_context(
			patch(
				"erpnext.stock.stock_ledger.get_previous_sle",
				side_effect=lambda *args, **kwargs: frappe._dict(qty_after_transaction=10, valuation_rate=25),
			)
		)
		self.serials = self.stack.enter_context(
			patch(
				"erpnext.stock.utils.get_available_serial_nos",
				return_value=[frappe._dict(serial_no="SERIAL-1")],
			)
		)

	def run_sql(self, query, args):
		query = re.sub(r"%\((\w+)\)s", r":\1", query)
		args = {key: str(value) if key == "posting_datetime" else value for key, value in args.items()}
		return self.connection.execute(query, args).fetchall()

	def balance(self, dimensions=None, **kwargs):
		return get_stock_balance(
			"ITEM", "WH", "2026-10-14", "12:00:00", inventory_dimensions_dict=dimensions, **kwargs
		)

	def test_dimension_quantity_and_existing_return_shapes(self):
		for with_rate, with_serial, expected in (
			(False, False, 6),
			(False, True, 6),
			(True, False, (6, 25)),
			(True, True, (6, 25, "SERIAL-1")),
		):
			with self.subTest(with_rate=with_rate, with_serial=with_serial):
				self.assertEqual(
					self.balance({"storage_location": "SOURCE"}, with_valuation_rate=with_rate, with_serial_no=with_serial),
					expected,
				)
		self.permission.assert_called_with("Item", "read", throw=True)
		self.assertEqual(self.serials.call_args.args[0].inventory_dimensions_dict, {"storage_location": "SOURCE"})
		self.assertEqual(self.balance({"storage_location": "STAGING"}), 4)

	def test_unfiltered_balance_uses_existing_warehouse_total(self):
		for dimensions in (None, {}):
			with self.subTest(dimensions=dimensions):
				self.assertEqual(self.balance(dimensions), 10)
		self.sql.assert_not_called()

	def test_posting_time_cutoff_is_inclusive(self):
		for posting_time, expected in (("09:00:00", 8), ("10:00:00", 6)):
			with self.subTest(posting_time=posting_time):
				self.assertEqual(
					get_stock_balance(
						"ITEM", "WH", "2026-10-14", posting_time,
						inventory_dimensions_dict={"storage_location": "SOURCE"},
					),
					expected,
				)

	def test_permission_denied_prevents_ledger_queries(self):
		self.permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			self.balance({"storage_location": "SOURCE"})
		self.previous.assert_not_called()
		self.sql.assert_not_called()

	def test_null_only_dimensions_preserve_native_reconciliation_balance(self):
		for value in (None, ""):
			with self.subTest(value=value):
				self.assertEqual(self.balance({"storage_location": value}), 10)
				self.assertIn("storage_location is null", self.previous.call_args.kwargs["extra_cond"])
		self.sql.assert_not_called()

	def test_zero_and_mixed_null_dimension_filters(self):
		for value, expected in ((0, 5), ("MISSING", 0)):
			with self.subTest(value=value):
				self.assertEqual(self.balance({"storage_location": value}), expected)
		self.assertEqual(self.balance({"storage_location": "SOURCE", "id_plant": None}), 6)

	def test_json_dimensions_from_http_request(self):
		self.assertEqual(self.balance('{"storage_location": "SOURCE"}'), 6)

	def test_non_object_dimensions_are_rejected_before_querying(self):
		for value in ([], '["SOURCE"]', 42, '"SOURCE"'):
			with (
				self.subTest(value=value),
				patch("erpnext.stock.utils._", side_effect=lambda message: message),
				patch("frappe.throw", side_effect=ValueError("invalid dimensions")),
				self.assertRaisesRegex(ValueError, "invalid dimensions"),
			):
				self.balance(value)
		self.previous.assert_not_called()
		self.sql.assert_not_called()

	def test_no_previous_entry_keeps_empty_return_shapes(self):
		self.previous.side_effect = None
		self.previous.return_value = {}
		for with_rate, with_serial, expected in (
			(False, False, 0),
			(False, True, 0),
			(True, False, (0, 0)),
			(True, True, (0, 0, None)),
		):
			with self.subTest(with_rate=with_rate, with_serial=with_serial):
				self.assertEqual(
					self.balance({"storage_location": "SOURCE"}, with_valuation_rate=with_rate, with_serial_no=with_serial),
					expected,
				)
		self.sql.assert_not_called()

	def test_unknown_dimension_is_rejected_before_querying(self):
		with (
			patch("erpnext.stock.utils._", side_effect=lambda message: message),
			patch("frappe.bold", side_effect=lambda value: value),
			patch("frappe.throw", side_effect=ValueError("invalid dimension")),
			self.assertRaisesRegex(ValueError, "invalid dimension"),
		):
			self.balance({"storage_location) or 1=1 --": "SOURCE"})
		self.previous.assert_not_called()
		self.sql.assert_not_called()
