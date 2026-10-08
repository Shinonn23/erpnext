import unittest

from erpnext.assets.doctype.stock_to_asset_conversion.stock_to_asset_conversion import (
	get_accounting_treatment,
)


class TestStockToAssetConversion(unittest.TestCase):
	def test_below_threshold_is_expensed_without_depreciation(self):
		self.assertEqual(get_accounting_treatment(99.99, 100), ("Expense", 0))

	def test_at_or_above_threshold_is_capitalized_and_depreciated(self):
		self.assertEqual(get_accounting_treatment(100, 100), ("Capitalized", 1))
		self.assertEqual(get_accounting_treatment(100.01, 100), ("Capitalized", 1))
