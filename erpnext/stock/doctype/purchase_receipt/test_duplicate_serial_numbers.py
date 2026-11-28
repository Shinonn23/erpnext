from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.purchase_receipt.purchase_receipt import PurchaseReceipt


class TestPurchaseReceiptDuplicateSerialNumbers(TestCase):
	def test_receipt_validation_rejects_a_serial_in_accepted_and_rejected_fields(self):
		doc = frappe._dict(
			items=[
				frappe._dict(idx=1, serial_no="A, B\r\nC"),
				frappe._dict(idx=2, rejected_serial_no=" B "),
			]
		)
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError) as throw:
			with self.assertRaises(frappe.ValidationError):
				PurchaseReceipt.validate(doc)
		throw.assert_called_once_with("Row 2: Serial No B is already entered in row 1.", title="Duplicate Serial No")
