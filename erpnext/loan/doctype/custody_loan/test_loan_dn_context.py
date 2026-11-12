from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.controllers import sales_and_purchase_return
from erpnext.loan.doctype.custody_loan import custody_loan_core as core
from erpnext.stock.doctype.delivery_note import delivery_note as dn


class TestLoanDeliveryNoteContext(TestCase):
	def test_direct_sales_mappers_refuse_loan_delivery_note(self):
		for mapper in (dn.make_sales_invoice, dn.make_sales_return):
			with (
				self.subTest(mapper=mapper.__name__),
				patch.object(frappe, "get_doc", return_value=frappe._dict(is_custody_loan_transaction=1)),
				patch.object(frappe, "throw", side_effect=frappe.ValidationError),
				self.assertRaises(frappe.ValidationError),
			):
				mapper("DN-LOAN")

	def test_ordinary_sales_return_still_uses_native_mapper(self):
		with (
			patch.object(frappe, "get_doc", return_value=frappe._dict(is_custody_loan_transaction=0)),
			patch.object(sales_and_purchase_return, "make_return_doc", return_value="RETURN") as mapper,
		):
			self.assertEqual(dn.make_sales_return("DN-SALE"), "RETURN")
		mapper.assert_called_once_with("Delivery Note", "DN-SALE", None)

	def test_invoice_rejects_linked_loan_note_even_without_stock_update(self):
		invoice = SimpleNamespace(items=[frappe._dict(delivery_note="DN-LOAN")], update_stock=0)
		with (
			patch.object(frappe, "db", Mock(exists=Mock(return_value=True))) as db,
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			SalesInvoice.validate_delivery_note(invoice)
		db.exists.assert_called_once_with(
			"Delivery Note", {"name": ["in", ["DN-LOAN"]], "is_custody_loan_transaction": 1}
		)

	def test_ordinary_invoice_without_stock_update_remains_valid(self):
		invoice = SimpleNamespace(items=[frappe._dict(delivery_note="DN-SALE")], update_stock=0)
		with patch.object(frappe, "db", Mock(exists=Mock(return_value=False))):
			SalesInvoice.validate_delivery_note(invoice)

	def test_internal_loan_return_uses_native_unexposed_mapper_and_keeps_source_markers(self):
		loan = SimpleNamespace(
			name="LOAN-1", delivery_note="DN-LOAN", items=[], company="CD", customer_loan_warehouse="Customer"
		)
		receipt = SimpleNamespace(items=[], flags=frappe._dict(), set=Mock(), insert=Mock())
		request = SimpleNamespace(
			custody_loan=loan.name, items=[], doctype="Custody Loan Return", name="RET-1"
		)
		with (
			patch.object(frappe, "get_doc", side_effect=[loan, SimpleNamespace(items=[])]),
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=1))),
			patch.object(sales_and_purchase_return, "make_return_doc", return_value=receipt) as mapper,
		):
			self.assertIs(core.CustodyLoanReturn.make_stock_return_delivery_note(request), receipt)
		mapper.assert_called_once_with("Delivery Note", "DN-LOAN")
		self.assertEqual(receipt.custody_loan_source_name, "RET-1")
		self.assertEqual(receipt.is_custody_loan_transaction, 1)
		receipt.insert.assert_not_called()
