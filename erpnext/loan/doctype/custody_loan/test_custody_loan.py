from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext.loan.doctype.custody_loan.custody_loan_core import (
	CustodyLoan,
	custody_loan_return_inspections_are_accepted,
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
			"requires_shipment": 0,
			"items": [
				FakeDocument(
					idx=1,
					name="DN-ROW-1",
					custody_loan_item="LOAN-ROW-1",
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
			items=[FakeDocument(custody_loan_item="LOAN-ROW-1")],
		)

	def test_stock_return_requires_an_accepted_quality_inspection_for_each_row(self):
		delivery_note = self.make_delivery_note()
		return_doc = self.make_return()
		quality_inspection = FakeDocument(
			name="QI-1",
			child_row_reference="DN-ROW-1",
			docstatus=1,
			status="Accepted",
		)

		with (
			patch("erpnext.loan.doctype.custody_loan.custody_loan_core.validate_custody_loan_return_shipment_arrival"),
			patch(
				"erpnext.loan.doctype.custody_loan.custody_loan_core.validate_custody_loan_return_quality_inspection"
			) as validate_qi,
			patch.object(frappe, "get_doc", side_effect=[return_doc, quality_inspection]),
		):
			validate_custody_loan_return_receipt(delivery_note)

		validate_qi.assert_called_once_with(quality_inspection, delivery_note.name)

	def test_stock_return_blocks_receipt_without_quality_inspection(self):
		delivery_note = self.make_delivery_note()
		delivery_note.items[0].quality_inspection = None

		with (
			patch("erpnext.loan.doctype.custody_loan.custody_loan_core.validate_custody_loan_return_shipment_arrival"),
			patch.object(frappe, "get_doc", return_value=self.make_return()),
			self.assertRaises(frappe.ValidationError),
		):
			validate_custody_loan_return_receipt(delivery_note)

	def test_return_status_distinguishes_inspection_receipt_and_completion(self):
		delivery_note = self.make_delivery_note()
		return_doc = FakeDocument(
			docstatus=1,
			loan_type="Stock",
			delivery_note=delivery_note.name,
			requires_shipment=0,
		)
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=delivery_note),
			patch(
				"erpnext.loan.doctype.custody_loan.custody_loan_core.custody_loan_return_inspections_are_accepted",
				return_value=False,
			),
		):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Pending Quality Inspection")

		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=delivery_note),
			patch(
				"erpnext.loan.doctype.custody_loan.custody_loan_core.custody_loan_return_inspections_are_accepted",
				return_value=True,
			),
		):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Pending Receipt")

		delivery_note.docstatus = 1
		with patch.object(frappe.db, "exists", return_value=True), patch.object(
			frappe, "get_doc", return_value=delivery_note
		):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Received")

	def test_shipped_return_uses_pending_receipt_until_delivery_before_inspection(self):
		delivery_note = self.make_delivery_note(requires_shipment=1, custody_loan_shipment="SHIP-1")
		return_doc = FakeDocument(
			docstatus=1,
			loan_type="Stock",
			delivery_note=delivery_note.name,
			requires_shipment=1,
		)
		with (
			patch.object(frappe.db, "get_value", return_value=frappe._dict(docstatus=1, tracking_status="In Progress")),
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=delivery_note),
		):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Pending Receipt")

		with (
			patch.object(frappe.db, "get_value", return_value=frappe._dict(docstatus=1, tracking_status="Delivered")),
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=delivery_note),
			patch(
				"erpnext.loan.doctype.custody_loan.custody_loan_core.custody_loan_return_inspections_are_accepted",
				return_value=False,
			),
		):
			self.assertEqual(update_return_status_on_transaction(return_doc), "Pending Quality Inspection")

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
			requires_shipment=0,
			items=[
				FakeDocument(issued_qty=1, returned_qty=0, adjusted_qty=0, sold_qty=0, outstanding_qty=1)
			],
			refresh_balances=lambda: None,
		)
		with patch.object(frappe, "get_all", return_value=[]):
			self.assertEqual(CustodyLoan.get_submitted_status(loan), "Active")

	def test_stock_loan_waiting_for_delivery_uses_pending_shipment(self):
		loan = FakeDocument(
			name="LOAN-2",
			loan_type="Stock",
			requires_shipment=1,
			pick_list="PICK-1",
			delivery_note="DN-1",
			asset_movement=None,
			items=[
				FakeDocument(issued_qty=1, returned_qty=0, adjusted_qty=0, sold_qty=0, outstanding_qty=1)
			],
			refresh_balances=lambda: None,
		)
		with (
			patch.object(frappe.db, "get_value", return_value=1),
			patch.object(frappe, "get_all", return_value=[]),
			patch("erpnext.loan.doctype.custody_loan.custody_loan_core.transaction_is_received", return_value=False),
		):
			self.assertEqual(CustodyLoan.get_submitted_status(loan), "Pending Shipment")

	def test_only_submitted_accepted_inspections_complete_the_check(self):
		delivery_note = self.make_delivery_note()
		with patch.object(
			frappe.db,
			"get_value",
			return_value=frappe._dict(docstatus=1, status="Accepted"),
		):
			self.assertTrue(custody_loan_return_inspections_are_accepted(delivery_note))

		with patch.object(
			frappe.db,
			"get_value",
			return_value=frappe._dict(docstatus=0, status="Accepted"),
		):
			self.assertFalse(custody_loan_return_inspections_are_accepted(delivery_note))
