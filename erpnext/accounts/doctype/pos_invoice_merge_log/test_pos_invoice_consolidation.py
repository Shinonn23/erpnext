"""Source proof for deferred POS inventory posting, independent of StockControl."""

from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.pos_invoice_merge_log import pos_invoice_merge_log as merge


class TestPOSInvoiceConsolidation(TestCase):
	def setUp(self):
		for name, value in (("db", Mock()), ("flags", frappe._dict())):
			patcher = patch.object(frappe, name, value)
			patcher.start()
			self.addCleanup(patcher.stop)
		patcher = patch.object(frappe, "get_system_settings", return_value="Banker's Rounding")
		patcher.start()
		self.addCleanup(patcher.stop)
		patcher = patch.object(merge, "_", side_effect=lambda text: text)
		patcher.start()
		self.addCleanup(patcher.stop)
		def reject(message):
			raise frappe.ValidationError(message)

		patcher = patch.object(frappe, "throw", side_effect=reject)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.original_item = frappe._dict(
			name="POS-ITEM",
			item_code="ITEM",
			warehouse="MAIN",
			qty=2,
			conversion_factor=1,
			serial_no="SERIAL-B\nSERIAL-A",
			storage_location="BIN",
			batch_no="BATCH",
		)
		self.original_packed = frappe._dict(item_code="COMPONENT", warehouse="MAIN", qty=4)
		self.source = frappe._dict(
			name="POS",
			docstatus=1,
			company="COMPANY",
			is_return=0,
			items=[self.original_item],
			packed_items=[self.original_packed],
		)
		mapped = deepcopy(self.original_item)
		mapped.update(
			name="SI-ITEM", pos_invoice="POS", pos_invoice_item="POS-ITEM", serial_no="SERIAL-A SERIAL-B"
		)
		self.invoice = frappe._dict(
			doctype="Sales Invoice",
			company="COMPANY",
			is_return=0,
			items=[mapped],
			packed_items=[deepcopy(self.original_packed)],
		)
		patcher = patch.object(frappe, "get_doc", return_value=self.source)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_source_proof_requires_context_and_preserves_exact_inventory(self):
		self.assertFalse(merge.is_verified_pos_consolidation(self.invoice))
		with merge.pos_invoice_consolidation(["POS"]):
			self.assertTrue(merge.is_verified_pos_consolidation(self.invoice))
			frappe.db.get_value.assert_called_with("POS Invoice", "POS", "name", for_update=True)
			self.invoice["items"][0].storage_location = "OTHER-BIN"
			with self.assertRaisesRegex(frappe.ValidationError, "inventory allocation"):
				merge.is_verified_pos_consolidation(self.invoice)

	def test_unsubmitted_source_duplicate_items_and_packed_changes_are_rejected(self):
		with merge.pos_invoice_consolidation(["POS"]):
			self.source.docstatus = 0
			with self.assertRaisesRegex(frappe.ValidationError, "Invalid POS"):
				merge.is_verified_pos_consolidation(self.invoice)
			self.source.docstatus = 1
			self.invoice["items"].append(deepcopy(self.invoice["items"][0]))
			with self.assertRaisesRegex(frappe.ValidationError, "items and quantities"):
				merge.is_verified_pos_consolidation(self.invoice)
			self.invoice["items"].pop()
			self.invoice["packed_items"][0].qty = 5
			with self.assertRaisesRegex(frappe.ValidationError, "packed items"):
				merge.is_verified_pos_consolidation(self.invoice)
			self.assertTrue(merge.is_verified_pos_consolidation(self.invoice, check_packed=False))

	def test_context_restores_outer_source_after_failure(self):
		frappe.flags.stock_control_pos_consolidation = frozenset({"OUTER"})
		with self.assertRaises(ValueError), merge.pos_invoice_consolidation(["POS"]):
			self.assertEqual(frappe.flags.stock_control_pos_consolidation, frozenset({"POS"}))
			raise ValueError("failed submission")
		self.assertEqual(frappe.flags.stock_control_pos_consolidation, frozenset({"OUTER"}))
