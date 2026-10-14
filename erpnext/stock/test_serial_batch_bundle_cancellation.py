from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.stock import serial_batch_bundle as bundle_module


class TestSerialBatchBundleCancellation(TestCase):
	def setUp(self):
		self.sle = frappe._dict(
			name="SLE-1",
			is_cancelled=1,
			serial_and_batch_bundle="BUNDLE-1",
			voucher_type="Stock Entry",
			voucher_no="STE-1",
			voucher_detail_no="STE-ROW-1",
			item_code="ITEM-1",
			company="COMPANY-1",
			warehouse="WAREHOUSE-1",
		)
		self.bundle = frappe._dict(self.sle)
		self.bundle.update(name="BUNDLE-1", docstatus=1, flags=frappe._dict(), cancel=Mock())
		self.processor = object.__new__(bundle_module.SerialBatchBundle)
		self.processor.sle = self.sle
		for patcher in (
			patch.object(frappe, "get_cached_doc", return_value=self.bundle),
			patch.object(frappe, "get_cached_value", side_effect=lambda *args: self.bundle.voucher_type),
			patch.object(frappe, "throw", side_effect=ValueError),
			patch.object(bundle_module, "_", lambda value: value),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_cancelled_stock_entry_reverses_only_its_linked_bundle(self):
		self.bundle.cancel.side_effect = lambda: self.assertTrue(self.bundle.flags.ignore_permissions)
		self.processor.cancel_serial_and_batch_bundle()
		frappe.get_cached_doc.assert_called_once_with("Serial and Batch Bundle", "BUNDLE-1")
		self.bundle.cancel.assert_called_once_with()

	def test_foreign_bundle_is_rejected_before_permission_bypass(self):
		for field in ("voucher_type", "voucher_no", "voucher_detail_no", "item_code", "company", "warehouse"):
			with self.subTest(field=field):
				original = self.bundle[field]
				self.bundle[field] = "OTHER"
				with self.assertRaises(ValueError):
					self.processor.cancel_serial_and_batch_bundle()
				self.bundle[field] = original
				self.assertFalse(self.bundle.flags.ignore_permissions)
				self.bundle.cancel.assert_not_called()

	def test_active_stock_entry_cannot_bypass_bundle_permissions(self):
		self.sle.is_cancelled = 0
		with self.assertRaises(ValueError):
			self.processor.cancel_serial_and_batch_bundle()
		self.assertFalse(self.bundle.flags.ignore_permissions)
		self.bundle.cancel.assert_not_called()

	def test_draft_and_cancelled_bundles_are_unchanged(self):
		for status in (0, 2):
			with self.subTest(status=status):
				self.bundle.docstatus = status
				self.processor.cancel_serial_and_batch_bundle()
				self.assertFalse(self.bundle.flags.ignore_permissions)
				self.bundle.cancel.assert_not_called()

	def test_pos_and_asset_repair_bundles_keep_the_existing_skip(self):
		for voucher_type, bundle_voucher_type in (
			("Sales Invoice", "POS Invoice"),
			("Stock Entry", "Asset Repair"),
		):
			with self.subTest(voucher_type=voucher_type):
				self.sle.voucher_type = voucher_type
				self.bundle.voucher_type = bundle_voucher_type
				self.processor.cancel_serial_and_batch_bundle()
				frappe.get_cached_doc.assert_not_called()
				self.bundle.cancel.assert_not_called()

	def test_native_bundle_cancellation_errors_propagate(self):
		self.bundle.cancel.side_effect = ValueError("Invalid inventory")
		with self.assertRaisesRegex(ValueError, "Invalid inventory"):
			self.processor.cancel_serial_and_batch_bundle()
