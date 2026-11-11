from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.serial_and_batch_bundle import serial_and_batch_bundle as bundle


class TestAutoDataDimensions(TestCase):
	def test_json_and_dict_dimensions_reach_serial_and_batch_lookups(self):
		for has_serial, value in ((1, '{"storage_location":"A1"}'), (0, {"storage_location": "A1"})):
			with self.subTest(has_serial=has_serial), patch.object(
				bundle, "get_available_serial_nos" if has_serial else "get_auto_batch_nos"
			) as lookup:
				bundle.get_auto_data(has_serial_no=has_serial, has_batch_no=1, inventory_dimensions_dict=value)
				self.assertEqual(lookup.call_args.args[0].inventory_dimensions_dict, {"storage_location": "A1"})

	def test_non_object_dimensions_are_rejected(self):
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError):
				bundle.get_auto_data(has_serial_no=1, inventory_dimensions_dict='["A1"]')
