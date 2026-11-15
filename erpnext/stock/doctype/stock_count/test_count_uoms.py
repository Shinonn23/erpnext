import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_count.stock_count import get_count_uoms


class TestCountUoms(unittest.TestCase):
	def test_returns_only_positive_item_conversions_and_checks_permissions(self):
		item = Mock(stock_uom="Test", has_serial_no=0)
		item.uoms = [
			frappe._dict(uom="Test", conversion_factor=1),
			frappe._dict(uom="Bottle", conversion_factor=720),
			frappe._dict(uom="Box", conversion_factor=0),
			frappe._dict(uom="Invalid", conversion_factor=float("inf")),
		]
		with (
			patch.object(frappe, "has_permission") as permission,
			patch.object(frappe, "get_doc", return_value=item),
			patch.object(frappe, "get_cached_value", return_value=1),
		):
			result = get_count_uoms("LYSE")
		permission.assert_called_once_with("Stock Count", "write", throw=True)
		item.check_permission.assert_called_once_with("read")
		self.assertEqual(result["uoms"], [{"uom": "Test", "factor": 1}, {"uom": "Bottle", "factor": 720}])
