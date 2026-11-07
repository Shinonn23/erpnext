"""No site needed: the capitalization threshold rule of a Stock to Asset Conversion output.

Siwat, 2 Nov 2026 (SOP "ไม่เกิน 10,000 บาท") and 7 Nov 2026: a unit worth the Company's
asset_capitalization_threshold or less is expensed at once (Low Value Asset Expense Account);
exactly the threshold is expensed, only a value above it is capitalized and depreciated.
"""

import unittest

from erpnext.assets.doctype.stock_to_asset_conversion.stock_to_asset_conversion import (
	get_accounting_treatment,
	get_default_accounting_treatment,
)


class TestCapitalizationThreshold(unittest.TestCase):
	def test_value_exactly_at_threshold_is_expensed(self):
		self.assertEqual(get_default_accounting_treatment(10000, 10000), "Expense")
		self.assertEqual(get_accounting_treatment(10000, 10000), ("Expense", 0))

	def test_value_below_threshold_is_expensed(self):
		self.assertEqual(get_default_accounting_treatment(9999.99, 10000), "Expense")
		self.assertEqual(get_accounting_treatment(9999.99, 10000), ("Expense", 0))

	def test_value_above_threshold_is_depreciated(self):
		self.assertEqual(get_default_accounting_treatment(10000.01, 10000), "Scheduled Depreciation")
		self.assertEqual(get_accounting_treatment(10000.01, 10000), ("Capitalized", 1))

	def test_no_threshold_never_expenses(self):
		self.assertEqual(get_default_accounting_treatment(1, 0), "Scheduled Depreciation")
		self.assertEqual(get_accounting_treatment(1, 0), ("Capitalized", 1))


if __name__ == "__main__":
	unittest.main()
