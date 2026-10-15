import json
import unittest
from pathlib import Path


class TestConversionFormLayout(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		path = Path(__file__).resolve().parents[1] / "assets/doctype/stock_to_asset_conversion"
		cls.layout = json.loads((path / "stock_to_asset_conversion.json").read_text())
		cls.fields = {field["fieldname"]: field for field in cls.layout["fields"]}

	def test_generated_assets_are_visible_and_read_only(self):
		assets = self.fields["assets"]
		self.assertFalse(assets.get("hidden"))
		self.assertEqual(assets["read_only"], 1)
		self.assertEqual(assets["fieldtype"], "Table")
		self.assertEqual(assets["options"], "Stock to Asset Conversion Asset")

	def test_generated_assets_section_and_table_require_rows(self):
		for fieldname in ("assets_section", "assets"):
			with self.subTest(fieldname=fieldname):
				self.assertEqual(
					self.fields[fieldname]["depends_on"], "eval:doc.assets && doc.assets.length > 0"
				)

	def test_details_tab_is_explicit_before_more_info(self):
		order = self.layout["field_order"]
		self.assertEqual(order[0], "details_tab")
		self.assertEqual(self.fields["details_tab"]["fieldtype"], "Tab Break")
		self.assertEqual(self.fields["details_tab"]["label"], "Details")
		self.assertLess(order.index("assets"), order.index("more_info_tab"))
