import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_entry import stock_entry


class TestEndTransitStorageLocation(unittest.TestCase):
	def test_end_transit_row_starts_from_the_transit_location(self):
		captured = {}

		def mapped(doctype, name, mapping, target, missing):
			captured.update(mapping)
			return None

		with (
			patch.object(stock_entry, "get_mapped_doc", side_effect=mapped),
			patch.object(stock_entry.frappe, "get_precision", return_value=3),
			patch.object(frappe, "get_system_settings", return_value=None),
		):
			stock_entry.make_stock_in_entry("STE-TRANSIT")
		update_item = captured["Stock Entry Detail"]["postprocess"]
		source = frappe._dict(
			t_warehouse="IN TRANSIT - CD",
			storage_location="CD STOCK - CD / STAGING",
			to_storage_location="IN TRANSIT - CD / STAGING",
			transfer_qty=2,
			transferred_qty=0,
			conversion_factor=1,
			material_request_item=None,
			material_request=None,
		)
		target = frappe._dict(storage_location="CD STOCK - CD / STAGING", to_storage_location="IN TRANSIT - CD / STAGING")
		with patch.object(frappe, "get_system_settings", return_value=None):
			update_item(source, target, Mock())
		self.assertEqual(target.s_warehouse, "IN TRANSIT - CD")
		self.assertEqual(target.storage_location, "IN TRANSIT - CD / STAGING")
		self.assertIsNone(target.to_storage_location)
		self.assertEqual(target.qty, 2)
