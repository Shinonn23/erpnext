from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.loan.doctype.custody_loan.custody_loan_core import (
	CustodyLoan,
	update_return_status_on_transaction,
	validate_custody_loan_return_receipt,
)


class FakeDocument(SimpleNamespace):
	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)


class TestCustodyLoanReturnFlow(TestCase):
	def make_delivery_note(self, **values):
		fields = {
			"doctype": "Delivery Note",
			"name": "DN-RETURN-1",
			"docstatus": 0,
			"is_return": 1,
			"is_custody_loan_transaction": 1,
			"custody_loan_source_doctype": "Custody Loan Return",
			"custody_loan_source_name": "RETURN-1",
			"items": [
				FakeDocument(
					idx=1,
					name="DN-ROW-1",
					custody_loan_item="LOAN-ROW-1",
					batch_no=None,
					quality_inspection="QI-1",
				)
			],
		}
		fields.update(values)
		return FakeDocument(**fields)

	def make_return(self):
		return FakeDocument(
			docstatus=1,
			loan_type="Stock",
			items=[FakeDocument(custody_loan_item="LOAN-ROW-1", batch_no=None)],
		)

	def test_stock_return_is_received_without_any_quality_inspection(self):
		# received first, inspected after (Siwat, 9 Nov 2026): the receipt asks for no inspection
		delivery_note = self.make_delivery_note()
		delivery_note.items[0].quality_inspection = None
		with patch.object(frappe, "get_doc", return_value=self.make_return()):
			validate_custody_loan_return_receipt(delivery_note)

	def test_return_status_is_pending_receipt_or_received_and_never_waits_for_an_inspection(self):
		delivery_note = self.make_delivery_note(custody_loan_shipment="SHIP-1")
		return_doc = FakeDocument(docstatus=1, loan_type="Stock", delivery_note=delivery_note.name)
		with (
			patch.object(frappe, "db", Mock(exists=Mock(return_value=True))),
			patch.object(frappe, "get_doc", return_value=delivery_note),
		):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Pending Receipt")
		delivery_note.docstatus = 1
		with (
			patch.object(frappe, "db", Mock(exists=Mock(return_value=True))),
			patch.object(frappe, "get_doc", return_value=delivery_note),
		):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Received")

	def test_asset_return_uses_pending_receipt_until_asset_movement_is_submitted(self):
		return_doc = FakeDocument(docstatus=1, loan_type="Asset", asset_movement=None)
		self.assertEqual(update_return_status_on_transaction(return_doc), "Pending Receipt")

		return_doc.asset_movement = "AM-1"
		with patch.object(frappe.db, "get_value", return_value=1):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Received")

	def test_asset_loan_status_is_active_without_a_transit_state(self):
		loan = FakeDocument(
			name="LOAN-1",
			loan_type="Asset",
			asset_movement="MOVE-1",
			items=[FakeDocument(issued_qty=1, returned_qty=0, adjusted_qty=0, sold_qty=0, outstanding_qty=1)],
			refresh_balances=lambda: None,
		)
		with patch.object(frappe, "get_all", return_value=[]):
			self.assertEqual(CustodyLoan.get_submitted_status(loan), "Active")

	def test_a_stock_loan_whose_delivery_note_is_not_submitted_waits_for_it_only(self):
		loan = FakeDocument(
			name="LOAN-2",
			loan_type="Stock",
			pick_list="PICK-1",
			delivery_note="DN-1",
			asset_movement=None,
			items=[FakeDocument(issued_qty=1, returned_qty=0, adjusted_qty=0, sold_qty=0, outstanding_qty=1)],
			refresh_balances=lambda: None,
		)
		with (
			patch.object(frappe.db, "get_value", side_effect=[1, 0]),
			patch.object(frappe, "get_all", return_value=[]),
		):
			self.assertEqual(CustodyLoan.get_submitted_status(loan), "Pending Delivery")
