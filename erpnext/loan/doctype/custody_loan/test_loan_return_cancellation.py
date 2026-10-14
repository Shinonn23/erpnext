from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_core as core


class Record(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class TestLoanReturnCancellation(TestCase):
	def setUp(self):
		self.previous = Record(
			name="RETURN-1",
			docstatus=1,
			company="CD",
			custody_loan="LOAN-1",
			loan_type="Stock",
			delivery_note="RETURN-DN",
			asset_movement=None,
			items=[Record(custody_loan_item="LOAN-ROW")],
		)
		self.return_doc = Record(**vars(self.previous))
		self.return_doc.docstatus = 2
		self.return_doc.get_doc_before_save = lambda: self.previous
		self.delivery = Record(
			name="RETURN-DN",
			docstatus=1,
			company="CD",
			custody_loan="LOAN-1",
			is_return=1,
			is_custody_loan_transaction=1,
			custody_loan_source_doctype="Custody Loan Return",
			custody_loan_source_name="RETURN-1",
			items=[Record(name="DN-ROW", item_code="ITEM-1", custody_loan_item="LOAN-ROW")],
		)
		self.inspection = Record(
			name="QI-1",
			docstatus=1,
			company="CD",
			item_code="ITEM-1",
			child_row_reference="DN-ROW",
			flags=frappe._dict(),
			meta=Record(get_workflow=lambda: "QI-WORKFLOW"),
		)
		self.events = []
		self.deleted_delivery = False
		self.inspection.cancel = self.cancel_inspection
		patchers = [
			patch.object(
				frappe,
				"get_doc",
				side_effect=lambda dt, name: self.delivery if dt == "Delivery Note" else self.inspection,
			),
			patch.object(frappe, "get_all", return_value=[self.inspection.name]),
			patch.object(frappe, "delete_doc", side_effect=self.delete_inspection),
			patch.object(frappe, "throw", side_effect=ValueError),
			patch.object(core, "_", lambda value: value),
			patch.object(core, "cancel_source_transaction", side_effect=self.cancel_source),
			patch.object(core, "set_workflow_state_on_action", side_effect=self.cancel_workflow),
		]
		for patcher in patchers:
			patcher.start()
			self.addCleanup(patcher.stop)

	def cancel_source(self, doc, field):
		if field != "delivery_note":
			return
		self.events.append("delivery")
		if self.delivery.docstatus == 0:
			self.deleted_delivery = True
		else:
			self.delivery.docstatus = 2

	def cancel_workflow(self, inspection, workflow, action):
		self.assertEqual((inspection.docstatus, workflow, action), (2, "QI-WORKFLOW", "cancel"))
		inspection.workflow_state = "Quality Inspection Cancelled"

	def cancel_inspection(self):
		self.assertFalse(self.deleted_delivery)
		self.assertNotEqual(self.delivery.docstatus, 1)
		self.assertTrue(self.inspection.flags.ignore_permissions)
		self.assertEqual(self.inspection.workflow_state, "Quality Inspection Cancelled")
		self.events.append("inspection")

	def delete_inspection(self, doctype, name, **kwargs):
		self.assertFalse(self.deleted_delivery)
		self.assertEqual((doctype, name), ("Quality Inspection", "QI-1"))
		self.assertEqual(kwargs, {"force": True, "ignore_permissions": True})
		self.events.append("delete inspection")

	def test_submitted_receipt_is_reversed_before_its_inspection(self):
		core.CustodyLoanReturn.before_cancel(self.return_doc)
		self.assertEqual(self.events, ["delivery", "inspection"])
		frappe.get_all.assert_called_once_with(
			"Quality Inspection",
			filters={"reference_type": "Delivery Note", "reference_name": "RETURN-DN"},
			pluck="name",
		)

	def test_draft_receipt_survives_until_submitted_or_draft_inspection_cleanup(self):
		self.delivery.docstatus = 0
		core.CustodyLoanReturn.before_cancel(self.return_doc)
		self.assertEqual(self.events, ["inspection", "delivery"])
		self.events.clear()
		self.deleted_delivery = False
		self.inspection.docstatus = 0
		core.CustodyLoanReturn.before_cancel(self.return_doc)
		self.assertEqual(self.events, ["delete inspection", "delivery"])

	def test_changed_cancel_payload_cannot_reverse_another_return(self):
		for field, value in (
			("delivery_note", "OTHER-DN"),
			("company", "UB"),
			("custody_loan", "OTHER-LOAN"),
			("asset_movement", "OTHER-MOVEMENT"),
			("loan_type", "Asset"),
		):
			with self.subTest(field=field):
				setattr(self.return_doc, field, value)
				with self.assertRaises(ValueError):
					core.CustodyLoanReturn.before_cancel(self.return_doc)
				setattr(self.return_doc, field, getattr(self.previous, field))
		self.assertEqual(self.events, [])

	def test_foreign_delivery_or_inspection_is_rejected_before_any_reversal(self):
		for record, field, value in (
			(self.delivery, "company", "UB"),
			(self.delivery, "custody_loan_source_name", "OTHER-RETURN"),
			(self.inspection, "company", "UB"),
			(self.inspection, "child_row_reference", "OTHER-ROW"),
			(self.inspection, "item_code", "OTHER-ITEM"),
		):
			with self.subTest(field=field, record=record.name):
				previous = getattr(record, field)
				setattr(record, field, value)
				with self.assertRaises(ValueError):
					core.CustodyLoanReturn.before_cancel(self.return_doc)
				setattr(record, field, previous)
		self.assertEqual(self.events, [])

	def test_already_cancelled_inspection_is_not_cancelled_again(self):
		self.inspection.docstatus = 2
		self.inspection.child_row_reference = "REMOVED-DRAFT-ROW"
		self.inspection.cancel = Mock()
		core.CustodyLoanReturn.before_cancel(self.return_doc)
		self.inspection.cancel.assert_not_called()
		self.assertFalse(self.inspection.flags.ignore_permissions)
