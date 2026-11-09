"""Offline proof of MR estimate mapping; no Frappe site or transaction is created."""

import ast
import copy
import json
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch


class Record(dict):
	__getattr__ = dict.get
	__setattr__ = dict.__setitem__


class TestEstimatedRates(TestCase):
	def setUp(self):
		self.rows = {
			"NEW": Record(parent="MR-NEW", rate=3500, estimate_rate=999, estimate_currency="USD"),
			"OLD": Record(
				parent="MR-OLD",
				rate=3500,
				estimate_rate=100,
				estimate_currency="USD",
				estimate_exchange_rate=35,
			),
			"OLDEST": Record(parent="MR-OLD", rate=3550),
		}
		self.reads = []

		def get_value(doctype, name, fields, **kwargs):
			self.reads.append((doctype, name))
			self.assertEqual(doctype, "Material Request Item")
			return self.rows.get(name)

		def cached(doctype, name, field):
			self.reads.append((doctype, name))
			if doctype == "Company":
				return "THB"
			if doctype == "Material Request":
				return "Estimated Buying - THB" if name == "MR-NEW" else "Standard Buying"
			if doctype == "Price List":
				return name == "Estimated Buying - THB"
			raise AssertionError(f"Unexpected catalog read: {doctype}")

		def throw(message):
			raise ValueError(message)

		self.frappe = SimpleNamespace(
			db=SimpleNamespace(get_value=get_value), get_cached_value=cached, throw=throw
		)
		source = Path(__file__).with_name("material_request.py").read_text()
		functions = [
			node
			for node in ast.parse(source).body
			if isinstance(node, ast.FunctionDef)
			and node.name
			in {"apply_estimated_rates", "set_missing_values", "material_request_rate_in_order_currency"}
		]
		self.assertEqual(len(functions), 3)
		namespace = {
			"frappe": self.frappe,
			"math": math,
			"flt": lambda value: float(value or 0),
			"_": lambda message: message,
			"getdate": lambda value: value or "",
			"nowdate": lambda: "2026-11-09",
		}
		exec(compile(ast.Module(functions, []), "material_request.py", "exec"), namespace)
		self.map_rates = namespace["apply_estimated_rates"]
		self.fill = namespace["set_missing_values"]

	def order(self, currency="THB", conversion_rate=1, refs=("NEW",), **kwargs):
		return Record(
			doctype="Purchase Order",
			company="CD",
			currency=currency,
			conversion_rate=conversion_rate,
			transaction_date="2026-11-09",
			schedule_date="2026-12-01",
			items=[Record(material_request_item=ref, qty=2, conversion_factor=10, rate=9000) for ref in refs],
			**kwargs,
		)

	def test_new_request_uses_approved_company_rate_not_legacy_fields_or_live_item_price(self):
		order = self.order()
		before = copy.deepcopy(self.rows)
		self.map_rates(order)
		self.assertEqual(order["items"][0].rate, 3500)
		self.assertEqual(self.rows, before)
		self.assertFalse(any(doctype == "Item Price" for doctype, name in self.reads))

	def test_new_foreign_orders_preserve_the_approved_company_value_and_uom(self):
		for currency, conversion, expected in (("USD", 35, 100), ("EUR", 40, 87.5)):
			with self.subTest(currency=currency):
				order = self.order(currency, conversion)
				self.map_rates(order)
				item = order["items"][0]
				self.assertEqual(item.rate, expected)
				self.assertEqual(item.rate * order.conversion_rate, 3500)
				self.assertEqual((item.qty, item.conversion_factor), (2, 10))
				self.assertEqual(
					(item.price_list_rate, item.discount_percentage, item.discount_amount), (expected, 0, 0)
				)

	def test_non_divisible_company_rate_is_converted_without_inventing_a_currency_tolerance(self):
		self.rows["NEW"].rate = 5000
		order = self.order("USD", 35)
		self.map_rates(order)
		self.assertEqual(order["items"][0].rate, 5000 / 35)

	def test_mixed_historical_and_current_requests_keep_their_distinct_frozen_bases(self):
		order = self.order("USD", 35.5, refs=("NEW", "OLD"))
		self.map_rates(order)
		self.assertAlmostEqual(order["items"][0].rate, 3500 / 35.5)
		self.assertEqual(order["items"][1].rate, 100)

	def test_legacy_quote_conversion_remains_unchanged(self):
		for currency, conversion, expected in (("THB", 1, 3500), ("USD", 35.5, 100), ("EUR", 40, 87.5)):
			with self.subTest(currency=currency):
				order = self.order(currency, conversion, refs=("OLD",))
				self.map_rates(order)
				self.assertEqual(order["items"][0].rate, expected)

	def test_historical_request_without_estimate_fields_uses_its_company_rate(self):
		order = self.order("USD", 35.5, refs=("OLDEST",))
		self.map_rates(order)
		self.assertEqual(order["items"][0].rate, 100)

	def test_missing_or_invalid_exchange_rate_cannot_copy_baht_as_foreign_currency(self):
		module = ModuleType("erpnext.setup.utils")
		module.get_exchange_rate = Mock(return_value=0)
		with patch.dict(sys.modules, {"erpnext.setup.utils": module}):
			with self.assertRaisesRegex(ValueError, "valid buying exchange rate"):
				self.map_rates(self.order("USD", 0))
		with self.assertRaisesRegex(ValueError, "valid buying exchange rate"):
			self.map_rates(self.order("USD", float("nan")))

	def test_fetched_exchange_rate_is_kept_for_native_total_calculation(self):
		module = ModuleType("erpnext.setup.utils")
		module.get_exchange_rate = Mock(return_value=35)
		order = self.order("USD", 0)
		with patch.dict(sys.modules, {"erpnext.setup.utils": module}):
			self.map_rates(order)
		self.assertEqual((order.conversion_rate, order["items"][0].rate), (35, 100))

	def test_invalid_current_request_rate_is_refused(self):
		for rate in (0, -1, float("nan"), float("inf")):
			with self.subTest(rate=rate):
				self.rows["NEW"].rate = rate
				with self.assertRaisesRegex(ValueError, "no valid estimated Rate"):
					self.map_rates(self.order())

	def test_invalid_legacy_foreign_estimate_cannot_leave_an_unconverted_mapped_rate(self):
		self.rows["OLD"].estimate_rate = float("inf")
		with self.assertRaisesRegex(ValueError, "no valid estimated Rate"):
			self.map_rates(self.order("USD", 35, refs=("OLD",)))

	def test_estimate_header_is_removed_before_native_actual_list_selection(self):
		order = self.order(buying_price_list="Estimated Buying - THB", price_list_currency="THB")
		calls = []

		def native(method):
			calls.append((method, order.buying_price_list))
			if method == "set_missing_values":
				self.assertIsNone(order.buying_price_list)
				self.assertIsNone(order.price_list_currency)
				order.buying_price_list = "Supplier Actual Buying"

		order.run_method = native
		self.fill(None, order)
		self.assertEqual(order.buying_price_list, "Supplier Actual Buying")
		self.assertEqual(
			calls, [("set_missing_values", None), ("calculate_taxes_and_totals", "Supplier Actual Buying")]
		)

	def test_an_actual_order_list_is_not_replaced(self):
		order = self.order(buying_price_list="Supplier Actual Buying")
		order.run_method = Mock()
		self.fill(None, order)
		self.assertEqual(order.buying_price_list, "Supplier Actual Buying")

	def test_legacy_fields_are_retained_but_hidden_readonly_and_not_copied(self):
		path = Path(__file__).parents[1] / "material_request_item/material_request_item.json"
		fields = {row["fieldname"]: row for row in json.loads(path.read_text())["fields"]}
		for name in (
			"estimate_supplier",
			"estimate_currency",
			"estimate_rate",
			"estimate_exchange_rate",
			"estimate_source",
			"estimate_price_date",
		):
			with self.subTest(field=name):
				self.assertEqual(
					(fields[name]["hidden"], fields[name]["read_only"], fields[name]["no_copy"]), (1, 1, 1)
				)
		self.assertEqual(fields["estimate_section"]["hidden"], 1)
		self.assertEqual(fields["rate"]["options"], "Company:company:default_currency")
		self.assertEqual(fields["rate"]["in_list_view"], 1)
