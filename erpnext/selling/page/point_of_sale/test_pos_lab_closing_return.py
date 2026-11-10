"""Offline proofs for the lab POS defects: closing signature, history permission,
reset of payment methods and the tax rows of a return. No site needed."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.pos_invoice import pos_invoice as pos_invoice_module
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice
from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import _inventory_signature
from erpnext.accounts.doctype.sales_invoice import sales_invoice as sales_invoice_module
from erpnext.selling.page.point_of_sale import point_of_sale


class TestPOSLabClosingReturn(TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "get_system_settings", return_value="Banker's Rounding")
		patcher.start()
		self.addCleanup(patcher.stop)
		self.db = Mock()
		patcher = patch.object(frappe, "db", self.db)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_signature_treats_blank_and_none_as_equal(self):
		pos_row = frappe._dict(
			item_code="I",
			warehouse="W",
			qty=2,
			batch_no="",
			serial_no="",
			storage_location="",
			serial_and_batch_bundle="",
			target_warehouse="",
			to_storage_location="",
		)
		sales_row = frappe._dict(item_code="I", warehouse="W", qty=2, batch_no=None, serial_no=None)
		self.assertEqual(_inventory_signature([pos_row]), _inventory_signature([sales_row]))

	def test_signature_still_tells_real_differences_apart(self):
		a = frappe._dict(item_code="I", warehouse="W", qty=2, batch_no="B1")
		for other in (
			frappe._dict(item_code="I", warehouse="W", qty=2, batch_no="B2"),
			frappe._dict(item_code="I", warehouse="W", qty=2, batch_no=""),
			frappe._dict(item_code="I", warehouse="W2", qty=2, batch_no="B1"),
			frappe._dict(item_code="I", warehouse="W", qty=3, batch_no="B1"),
		):
			self.assertNotEqual(_inventory_signature([a]), _inventory_signature([other]))

	def _history(self, may_read):
		calls = []

		def get_list(doctype, **kwargs):
			calls.append(doctype)
			return [{"name": doctype[:1], "posting_date": "2026-11-10", "posting_time": "10:00:00"}]

		with (
			patch.object(point_of_sale.frappe, "has_permission", return_value=may_read) as perm,
			patch.object(self.db, "get_list", side_effect=get_list),
		):
			result = point_of_sale.get_past_order_list("", "Paid")
			perm.assert_called_with("Sales Invoice", "read")
		return calls, result

	def test_history_skips_sales_invoice_without_permission(self):
		calls, result = self._history(False)
		self.assertEqual(calls, ["POS Invoice"])
		self.assertEqual([r["doctype"] for r in result], ["POS Invoice"])

	def test_history_lists_both_with_permission(self):
		calls, _ = self._history(True)
		self.assertEqual(sorted(calls), ["POS Invoice", "Sales Invoice"])

	def test_reset_payment_methods_accepts_pos_invoice_without_the_field(self):
		# a real document raises AttributeError on a field the DocType does not have
		doc = SimpleNamespace(doctype="POS Invoice", payments=[], company="C")
		doc.get = lambda key, default=None: getattr(doc, key, default)
		doc.set = lambda key, value: setattr(doc, key, value)
		profile = SimpleNamespace(get=lambda k: [])
		with (
			patch.object(sales_invoice_module, "get_mode_of_payments_info", return_value={}),
			patch.object(frappe, "toast") as toast,
		):
			sales_invoice_module.update_multi_mode_option(doc, profile)
			toast.assert_not_called()

	def test_reset_payment_methods_pos_invoice_with_payments_is_not_a_refetch(self):
		doc = frappe._dict(doctype="POS Invoice", payments=[object()], company="C")
		doc.set = lambda key, value: doc.__setitem__(key, value)
		profile = SimpleNamespace(get=lambda k: [])
		with (
			patch.object(sales_invoice_module, "get_mode_of_payments_info", return_value={}),
			patch.object(frappe, "toast") as toast,
		):
			sales_invoice_module.update_multi_mode_option(doc, profile)
			toast.assert_not_called()

	def test_return_keeps_original_taxes(self):
		keeps = POSInvoice.keeps_original_taxes
		ret = SimpleNamespace(is_return=1, get=lambda k: [object()] if k == "taxes" else None)
		sale = SimpleNamespace(is_return=0, get=lambda k: [object()] if k == "taxes" else None)
		bare = SimpleNamespace(is_return=1, get=lambda k: [])
		self.assertTrue(keeps(ret, "taxes_and_charges"))
		self.assertTrue(keeps(ret, "tax_category"))
		self.assertFalse(keeps(ret, "currency"))
		self.assertFalse(keeps(sale, "taxes_and_charges"))
		self.assertFalse(keeps(bare, "taxes_and_charges"))
