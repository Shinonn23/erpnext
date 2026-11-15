"""Bundle parts stated in another UOM are delivered in stock UOM (packed_item.part_conversion_factor). Offline."""

import unittest
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.packed_item import packed_item


class TestPartUom(unittest.TestCase):
	def test_part_in_its_own_uom_is_converted_to_stock_uom(self):
		part = frappe._dict(item_code="REAGENT", qty=1, uom="Test")
		with patch(
			"erpnext.stock.get_item_details.get_conversion_factor",
			return_value={"conversion_factor": 0.00025},
		):
			self.assertEqual(packed_item.part_conversion_factor(part), 0.00025)

	def test_no_uom_or_no_factor_is_one(self):
		self.assertEqual(packed_item.part_conversion_factor(frappe._dict(item_code="X", uom=None)), 1.0)
		with patch("erpnext.stock.get_item_details.get_conversion_factor", return_value={}):
			self.assertEqual(packed_item.part_conversion_factor(frappe._dict(item_code="X", uom="Box")), 1.0)

	def test_packed_row_quantity_uses_the_factor(self):
		row = frappe._dict()
		main = frappe._dict(item_code="TEST-K", name="R1", stock_qty=4, conversion_factor=1)
		part = frappe._dict(item_code="REAGENT", qty=2, uom="Test", description="")
		item = frappe._dict(item_name="Reagent", stock_uom="Bottle")
		with patch.object(packed_item, "part_conversion_factor", return_value=0.5):
			packed_item.update_packed_item_basic_data(main, row, part, item)
		self.assertEqual((row.qty, row.uom), (4.0, "Bottle"))
