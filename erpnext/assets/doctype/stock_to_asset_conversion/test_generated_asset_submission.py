import unittest
from unittest.mock import Mock, patch

import frappe


class TestGeneratedAssetSubmission(unittest.TestCase):
	def test_generated_assets_are_submitted_by_the_system_after_approval(self):
		from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion

		asset = Mock(name="Asset", is_demo_asset=0)
		asset.name = "ASSET-1"
		asset.flags = frappe._dict()
		seen = {}
		asset.submit.side_effect = lambda: seen.update(ignore_permissions=asset.flags.ignore_permissions)
		doc = frappe._dict(
			name="CONV-1",
			posting_date="2026-10-19",
			assets=[frappe._dict(asset="ASSET-1", source_serial_no=None, accounting_treatment="Capitalized")],
		)
		with patch.object(conversion.frappe, "get_doc", return_value=asset), patch.object(conversion.frappe, "db", Mock()):
			conversion.StocktoAssetConversion.submit_generated_assets(doc)
		# The approver may only read Assets; the approval itself authorises the system submission.
		self.assertEqual(seen, {"ignore_permissions": True})


class TestSystemAssetReceiptMovement(unittest.TestCase):
	def run_receipt(self, ignore_permissions):
		from erpnext.assets.doctype.asset import asset as asset_module

		movement = Mock()
		movement.flags = frappe._dict()
		doc = frappe._dict(
			name="ACC-ASS-1", asset_name="Generated", location="Office", custodian=None, company="CD",
			purchase_receipt=None, purchase_invoice=None, purchase_date="2026-10-19",
			flags=frappe._dict(ignore_permissions=ignore_permissions),
		)
		with patch.object(asset_module.frappe, "get_doc", return_value=movement):
			asset_module.Asset.make_asset_movement(doc)
		movement.insert.assert_called_once()
		movement.submit.assert_called_once()
		return movement.flags.ignore_permissions

	def test_receipt_of_a_system_submitted_asset_is_recorded_as_system(self):
		self.assertTrue(self.run_receipt(True))

	def test_a_user_submitting_an_asset_still_needs_movement_rights(self):
		self.assertFalse(self.run_receipt(None))
