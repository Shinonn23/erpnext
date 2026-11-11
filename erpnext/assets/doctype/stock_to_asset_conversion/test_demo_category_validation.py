from unittest import TestCase
from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as module


class TestDemoCategoryValidation(TestCase):
	def test_generated_assets_no_longer_set_the_flag_themselves(self):
		with open(module.__file__, encoding="utf-8") as handle:
			self.assertNotIn("asset.is_demo_asset =", handle.read())
