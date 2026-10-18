from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as module


class TestDemoCategoryValidation(TestCase):
	def setUp(self):
		self.database = Mock()
		for patcher in (
			patch.object(frappe, "db", self.database),
			patch.object(frappe, "throw", side_effect=ValueError("rejected")),
			patch.object(module, "_", lambda text: text),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def validate(self, request_type, category_is_demo, material_request="MR-1"):
		doc = Mock(spec=module.StocktoAssetConversion)
		doc.material_request = material_request
		self.database.get_value.return_value = request_type
		target = frappe._dict(name="Demo Analyzer", asset_category="Demo Equipment")
		with patch.object(frappe, "get_cached_value", return_value=category_is_demo) as category:
			module.StocktoAssetConversion.validate_demo_category(doc, frappe._dict(idx=1), target)
		return category

	def test_customer_demo_requires_a_target_item_in_a_demo_category(self):
		with self.assertRaises(ValueError):
			self.validate("Customer Demo", 0)

	def test_customer_demo_accepts_a_demo_category(self):
		category = self.validate("Customer Demo", 1)
		self.database.get_value.assert_called_once_with("Material Request", "MR-1", "material_request_type")
		category.assert_called_once_with("Asset Category", "Demo Equipment", "is_demo_category")

	def test_other_requests_are_not_restricted(self):
		for request_type in ("Asset Transfer", "Customer Borrow / Loan", "Purchase"):
			with self.subTest(request_type=request_type):
				self.assertFalse(self.validate(request_type, 0).called)

	def test_conversion_without_a_request_is_not_restricted(self):
		self.validate(None, 0, material_request=None)
		self.database.get_value.assert_not_called()

	def test_generated_assets_no_longer_set_the_flag_themselves(self):
		with open(module.__file__, encoding="utf-8") as handle:
			self.assertNotIn("asset.is_demo_asset =", handle.read())
