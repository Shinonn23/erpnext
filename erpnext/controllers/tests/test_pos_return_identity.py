"""POS returns use their actual customer and original POS item row."""
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
from frappe.model.docstatus import DocStatus

from erpnext.controllers import sales_and_purchase_return as returns


class TestPOSReturnIdentity(TestCase):
	def check_customer(self, customer):
		doc = frappe._dict(doctype="POS Invoice", name="RETURN", return_against="SALE", company="CD", customer=customer, posting_date="2026-10-16", posting_time="12:00:00", conversion_rate=1)
		doc.meta = Mock()
		doc.meta.get_label.side_effect = lambda field: field
		source = frappe._dict(doctype="POS Invoice", name="SALE", company="CD", customer="CUSTOMER", docstatus=DocStatus(1), posting_date="2026-10-16", posting_time="12:00:00", conversion_rate=1)
		def reject(message, **kwargs):
			raise frappe.ValidationError(message)
		with patch.object(frappe, "db", Mock()), patch.object(frappe, "get_doc", return_value=source), patch.object(returns, "_", side_effect=lambda text: text), patch.object(frappe, "throw", side_effect=reject):
			returns.validate_return_against(doc)

	def test_matching_customer_is_valid(self):
		self.check_customer("CUSTOMER")

	def test_wrong_customer_cannot_return_another_customers_pos_sale(self):
		with self.assertRaisesRegex(frappe.ValidationError, "does not match"):
			self.check_customer("OTHER-CUSTOMER")

	def test_pos_reference_uses_pos_item_without_changing_other_return_links(self):
		self.assertEqual(returns.get_return_against_item_fields("POS Invoice"), "pos_invoice_item")
		self.assertEqual(returns.get_return_against_item_fields("Sales Invoice"), "sales_invoice_item")
		self.assertEqual(returns.get_return_against_item_fields("Delivery Note"), "dn_detail")
