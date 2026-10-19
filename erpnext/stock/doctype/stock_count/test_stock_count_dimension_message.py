import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_count import stock_count


class TestStorageLocationMessage(unittest.TestCase):
	def test_location_of_another_warehouse_gets_the_real_message(self):
		doc = frappe._dict(
			warehouse="CD STOCK - CD",
			dimensions=[frappe._dict(inventory_dimension="Storage Location", dimension_value="QUARANTINE - CD / STAGING")],
			get_dimension_filters=lambda: ([frappe._dict(dimension_name="Storage Location")], {"storage_location": "x"}),
		)
		location = frappe._dict(warehouse="QUARANTINE - CD", disabled=0)
		with (
			patch.object(stock_count.frappe, "db", Mock(get_value=Mock(return_value=location))),
			patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError) as throw,
			self.assertRaises(frappe.ValidationError),
		):
			stock_count.StockCount.validate_dimensions(doc)
		self.assertEqual(throw.call_args.args[0], "Storage Location must be active and belong to the selected Warehouse.")
