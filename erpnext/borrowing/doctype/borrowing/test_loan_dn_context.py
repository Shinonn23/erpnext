from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.borrowing.doctype.borrowing import borrowing_core as core
from erpnext.controllers import sales_and_purchase_return
from erpnext.stock.doctype.delivery_note import delivery_note as dn


class TestLoanDeliveryNoteContext(TestCase):
	def test_the_invoice_mapper_refuses_a_loan_delivery_note(self):
		with (
			patch.object(frappe, "get_doc", return_value=frappe._dict(is_borrowing_transaction=1)),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			dn.make_sales_invoice("DN-LOAN")

	def test_the_return_of_a_loan_delivery_note_is_the_borrowing_return(self):
		"""Create > Return on a borrowing's issue note opens its borrowing return (UX WP4), never a sales return."""
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=1))),
			patch.object(core, "make_borrowing_return_delivery_note", return_value="RETURN") as builder,
			patch.object(sales_and_purchase_return, "make_return_doc") as native,
		):
			self.assertEqual(dn.make_sales_return("DN-LOAN"), "RETURN")
		builder.assert_called_once_with("DN-LOAN")
		native.assert_not_called()

	def test_ordinary_sales_return_still_uses_native_mapper(self):
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=0))),
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
			"Delivery Note", {"name": ["in", ["DN-LOAN"]], "is_borrowing_transaction": 1}
		)

	def test_ordinary_invoice_without_stock_update_remains_valid(self):
		invoice = SimpleNamespace(items=[frappe._dict(delivery_note="DN-SALE")], update_stock=0)
		with patch.object(frappe, "db", Mock(exists=Mock(return_value=False))):
			SalesInvoice.validate_delivery_note(invoice)
