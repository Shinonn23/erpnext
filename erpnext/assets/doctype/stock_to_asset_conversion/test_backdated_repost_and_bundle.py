"""Offline tests: repost of later stock vouchers on submit / cancel, the approver's bundle submit and the
Stock Ledger preview's table (`stock_items`)."""

import unittest
from unittest.mock import Mock, patch

import frappe


def module():
	from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion

	return conversion


class TestRepostFutureStockVouchers(unittest.TestCase):
	def test_submit_reposts_after_its_own_ledger_and_outside_the_submitting_flag(self):
		conversion = module()
		doc = Mock()
		doc.flags = frappe._dict()
		doc.pick_list = None
		seen = {}
		doc.repost_future_sle_and_gle.side_effect = lambda: seen.update(submitting=doc.flags.submitting)
		conversion.StocktoAssetConversion.on_submit(doc)
		doc.repost_future_sle_and_gle.assert_called_once_with()
		self.assertFalse(seen["submitting"])
		names = [call[0] for call in doc.mock_calls]
		self.assertLess(names.index("update_stock_ledger"), names.index("repost_future_sle_and_gle"))
		self.assertLess(names.index("make_gl_entries"), names.index("repost_future_sle_and_gle"))
		self.assertLess(names.index("repost_future_sle_and_gle"), names.index("submit_generated_assets"))

	def test_cancel_reposts_after_reversing_its_ledger(self):
		conversion = module()
		doc = Mock()
		doc.flags = frappe._dict()
		doc.assets = []
		doc.pick_list = None
		with patch.object(conversion.frappe, "db", Mock()):
			conversion.StocktoAssetConversion.on_cancel(doc)
		doc.repost_future_sle_and_gle.assert_called_once_with()
		names = [call[0] for call in doc.mock_calls]
		self.assertLess(names.index("update_stock_ledger"), names.index("repost_future_sle_and_gle"))
		self.assertLess(names.index("make_gl_entries"), names.index("repost_future_sle_and_gle"))
		# the Repost Item Valuation made by the submit or this cancel must not block the cancellation
		self.assertIn("Repost Item Valuation", doc.ignore_linked_doctypes)


class TestApproverSubmitsTheConversionBundle(unittest.TestCase):
	def run_submit(self, sle_voucher_type, bundle_voucher_type, bundle_voucher_no="CONV-1"):
		from erpnext.stock import serial_batch_bundle

		bundle = Mock(voucher_type=bundle_voucher_type, voucher_no=bundle_voucher_no, voucher_detail_no="R1")
		bundle.flags = frappe._dict()
		seen = {}
		bundle.submit.side_effect = lambda: seen.update(ignore_permissions=bundle.flags.ignore_permissions)
		processor = Mock()
		processor.sle = frappe._dict(
			voucher_type=sle_voucher_type,
			voucher_no="CONV-1",
			voucher_detail_no="R1",
			serial_and_batch_bundle="SABB-1",
		)
		with patch.object(serial_batch_bundle.frappe, "get_doc", return_value=bundle):
			serial_batch_bundle.SerialBatchBundle.submit_serial_and_batch_bundle(processor)
		bundle.submit.assert_called_once_with()
		return seen["ignore_permissions"]

	def test_conversion_bundle_is_submitted_by_the_system(self):
		self.assertTrue(self.run_submit("Stock to Asset Conversion", "Stock to Asset Conversion"))

	def test_other_vouchers_keep_the_bundle_permission_check(self):
		for voucher_type in ("Stock Entry", "Delivery Note", "Asset Capitalization"):
			with self.subTest(voucher_type=voucher_type):
				self.assertIsNone(self.run_submit(voucher_type, voucher_type))

	def test_bundle_of_another_voucher_keeps_the_permission_check(self):
		self.assertIsNone(self.run_submit("Stock to Asset Conversion", "Stock Entry"))
		self.assertIsNone(self.run_submit("Stock to Asset Conversion", "Stock to Asset Conversion", "CONV-2"))


class ConversionRow:
	"""A Stock to Asset Conversion Item: a bundle, no old serial_no / batch_no fields (no such attributes)."""

	def __init__(self):
		self.name = "R1"
		self.item_code = "SERIAL-ITEM"
		self.serial_and_batch_bundle = "SABB-1"
		self.qty = 1

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestOldSerialBatchFieldsTable(unittest.TestCase):
	def run_bundle(self, doctype, rows):
		from erpnext.controllers import stock_controller

		doc = Mock()
		doc.doctype = doctype
		tables = {"stock_items": rows}
		doc.get.side_effect = lambda key, default=None: tables.get(key, default)
		doc.is_serial_batch_item.return_value = True
		with patch.object(stock_controller.frappe, "in_test", False):
			stock_controller.StockController.make_bundle_using_old_serial_batch_fields(doc)
		return doc

	def test_conversion_reads_stock_items(self):
		doc = self.run_bundle("Stock to Asset Conversion", [ConversionRow()])
		doc.get.assert_any_call("stock_items")
		self.assertNotIn("items", [call.args[0] for call in doc.get.call_args_list])
		doc.is_serial_batch_item.assert_called_once_with("SERIAL-ITEM")
		# the row already carries its bundle: nothing to validate against old fields, nothing to create
		doc.validate_serial_nos_and_batches_with_bundle.assert_not_called()
		doc.create_serial_batch_bundle.assert_not_called()

	def test_asset_capitalization_still_reads_stock_items(self):
		doc = self.run_bundle("Asset Capitalization", [])
		doc.get.assert_any_call("stock_items")


if __name__ == "__main__":
	unittest.main()
