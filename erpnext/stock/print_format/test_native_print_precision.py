"""Render native custom print formats offline without changing stored quantities or tax amounts."""

import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.utils import fmt_money
from frappe.utils.number_format import NumberFormat
from jinja2 import DictLoader
from jinja2.sandbox import SandboxedEnvironment

ROOT = Path(__file__).resolve().parents[2]


class TestNativePrintPrecision(unittest.TestCase):
	def test_stock_count_quantities_keep_two_decimals_and_locale(self):
		environment = SandboxedEnvironment(
			loader=DictLoader(
				{
					"templates/print_formats/standard_macros.html": (
						"{% macro add_header() %}{% set _ = varargs %}{% endmacro %}"
					),
				}
			)
		)
		template = environment.from_string(
			(ROOT / "stock/print_format/stock_count_audit/stock_count_audit.html").read_text()
		)
		row = frappe._dict(
			idx=1,
			item_code="ITEM-001",
			system_qty=1234.567,
			counted_qty=0,
			difference_qty=-1234.567,
			is_counted=True,
		)
		doc = frappe._dict(
			items=[row],
			count_type="Annual",
			docstatus=0,
			get_countable_dimensions=lambda: [],
			get_formatted=lambda field: "",
		)
		before = deepcopy(row)
		for number_format, expected in (
			("#,###.##", ("1,234.57", "0.00", "-1,234.57")),
			("#.###,##", ("1.234,57", "0,00", "-1.234,57")),
		):
			with (
				self.subTest(number_format=number_format),
				patch(
					"frappe.utils.data.get_number_format",
					return_value=NumberFormat.from_string(number_format),
				),
			):
				html = template.render(
					doc=doc,
					frappe=SimpleNamespace(utils=SimpleNamespace(fmt_money=fmt_money, get_url=lambda: "")),
					_=lambda value: value,
					print_settings=frappe._dict(),
				)
				for value in expected:
					self.assertIn(f'<td class="number">{value}</td>', html)
				self.assertNotIn("1234.567", html)
				row.is_counted = False
				uncounted = template.render(
					doc=doc,
					frappe=SimpleNamespace(utils=SimpleNamespace(fmt_money=fmt_money, get_url=lambda: "")),
					_=lambda value: value,
					print_settings=frappe._dict(),
				)
				self.assertEqual(uncounted.count('<td class="number"></td>'), 2)
				row.is_counted = True
		self.assertEqual(row, before)

	def test_certificate_words_use_the_same_two_decimals_as_the_amount(self):
		for name, converter in (
			("withholding_tax_cert", "amount_to_text"),
			("withholding_tax_cert_50_tawi", "amount_in_bahttext"),
		):
			data = json.loads((ROOT / f"regional/print_format/{name}/{name}.json").read_text())
			environment = SandboxedEnvironment()
			environment.filters["str"] = str
			template = environment.from_string(data["html"])
			for amount in (0, 1, 1.005, 1.999, -1.999):
				with self.subTest(name=name, amount=amount):
					words = Mock(return_value="Amount in words", unsafe_callable=False, alters_data=False)
					row = frappe._dict(type_of_income="6", tax_base=amount, tax_rate=3, tax_amount=amount)
					doc = frappe._dict(
						withholding_tax_items=[row],
						employee="EMP-001",
						supplier=None,
						employee_address="Address",
						date=None,
					)
					html = template.render(
						doc=doc,
						frappe=SimpleNamespace(
							get_fullname=lambda owner: "Owner",
							get_doc=lambda *args: frappe._dict(),
						),
						**{converter: words},
					)
					self.assertIn("Amount in words", html)
					self.assertTrue(words.called)
					for call in words.call_args_list:
						self.assertEqual(call.args[0], f"{amount:.2f}")
					self.assertEqual(row.tax_amount, amount)
