from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_core as core
from erpnext.stock.doctype.shipment.shipment import Shipment


class Record(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def db_set(self, key, value):
		setattr(self, key, value)


class TestLoanShipmentCancellation(TestCase):
	def setUp(self):
		self.events = []
		self.source = Record(
			doctype="Custody Loan Return",
			name="RETURN-1",
			company="CD",
			docstatus=2,
			custody_loan="LOAN-1",
			delivery_note="DN-1",
		)
		self.stored_source = Record(**vars(self.source))
		self.stored_source.docstatus = 1
		self.stored_source.check_permission = Mock()
		self.loan = Record(name="LOAN-1", company="CD", items=[], update_status=Mock())
		self.delivery = Record(
			doctype="Delivery Note",
			name="DN-1",
			company="CD",
			docstatus=1,
			custody_loan="LOAN-1",
			custody_loan_source_doctype=self.source.doctype,
			custody_loan_source_name=self.source.name,
			is_custody_loan_transaction=1,
			requires_shipment=1,
			custody_loan_shipment="SHIP-1",
			flags=frappe._dict(),
		)
		self.shipment = Record(
			doctype="Shipment",
			name="SHIP-1",
			docstatus=1,
			custody_loan="LOAN-1",
			flags=frappe._dict(),
			shipment_documents=[Record(reference_doctype="Delivery Note", reference_name="DN-1")],
		)
		self.delivery.cancel = self.cancel_delivery
		self.shipment.cancel = self.cancel_shipment
		self.records = {
			(self.source.doctype, self.source.name): self.stored_source,
			("Custody Loan", self.loan.name): self.loan,
			("Delivery Note", self.delivery.name): self.delivery,
			("Shipment", self.shipment.name): self.shipment,
		}
		patchers = [
			patch.object(frappe, "get_doc", side_effect=lambda dt, name: self.records[(dt, name)]),
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(frappe, "delete_doc", side_effect=self.delete_record),
			patch.object(frappe, "throw", side_effect=ValueError),
			patch.object(frappe.db, "get_value", side_effect=lambda dt, name, field: self.shipment.docstatus),
			patch.object(frappe.db, "table_exists", return_value=False),
			patch.object(frappe.db, "has_column", return_value=False),
			patch.object(core, "check_custody_loan_source", return_value=True),
			patch.object(core, "_", lambda value: value),
			patch("erpnext.stock.doctype.shipment.shipment._", lambda value: value),
		]
		for patcher in patchers:
			patcher.start()
			self.addCleanup(patcher.stop)

	def cancel_delivery(self):
		self.assertEqual(self.delivery.custody_loan_shipment, "SHIP-1")
		self.assertTrue(self.delivery.flags.ignore_permissions)
		core.validate_generated_transaction_cancellation(self.delivery)
		self.assertIn("Shipment", self.delivery.ignore_linked_doctypes)
		self.delivery.docstatus = 2
		self.events.append("reverse delivery")

	def cancel_shipment(self):
		self.assertTrue(self.shipment.flags.ignore_permissions)
		Shipment.on_cancel(self.shipment)
		self.shipment.docstatus = 2
		self.events.append("cancel shipment")

	def delete_record(self, doctype, name, **kwargs):
		self.assertEqual(kwargs, {"ignore_permissions": True, "force": True})
		self.events.append("delete " + doctype)
		del self.records[(doctype, name)]

	def test_parent_reverses_posted_delivery_before_native_shipment_cancel(self):
		core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, ["reverse delivery", "cancel shipment"])
		self.stored_source.check_permission.assert_called_once_with("cancel")
		self.assertIsNone(self.delivery.custody_loan_shipment)

	def test_direct_shipment_cancellation_still_rejects_posted_stock(self):
		with self.assertRaises(ValueError):
			Shipment.on_cancel(self.shipment)
		self.assertEqual(self.delivery.docstatus, 1)
		self.assertEqual(self.delivery.custody_loan_shipment, "SHIP-1")

	def test_draft_delivery_survives_native_shipment_callback(self):
		self.delivery.docstatus = 0
		core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, ["cancel shipment", "delete Delivery Note"])

	def test_draft_shipment_is_removed_before_its_draft_delivery(self):
		self.delivery.docstatus = self.shipment.docstatus = 0
		core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, ["delete Shipment", "delete Delivery Note"])

	def test_forged_source_payload_is_rejected(self):
		for field, value in (
			("delivery_note", "FOREIGN-DN"),
			("company", "UB"),
			("custody_loan", "OTHER-LOAN"),
		):
			with self.subTest(field=field):
				previous = getattr(self.source, field)
				setattr(self.source, field, value)
				with self.assertRaises(ValueError):
					core.cancel_source_transaction(self.source, "delivery_note")
				setattr(self.source, field, previous)
		self.assertEqual(self.events, [])

	def test_foreign_transaction_or_shipment_is_rejected_before_reversal(self):
		for record, field, value in (
			(self.delivery, "company", "UB"),
			(self.delivery, "custody_loan", "OTHER-LOAN"),
			(self.delivery, "custody_loan_source_name", "OTHER-RETURN"),
			(self.delivery, "custody_loan_source_doctype", "Custody Loan"),
			(self.delivery, "is_custody_loan_transaction", 0),
			(self.shipment, "custody_loan", "OTHER-LOAN"),
			(
				self.shipment,
				"shipment_documents",
				[Record(reference_doctype="Delivery Note", reference_name="OTHER-DN")],
			),
			(self.shipment, "shipment_documents", self.shipment.shipment_documents * 2),
		):
			with self.subTest(field=field, record=record.name):
				previous = getattr(record, field)
				setattr(record, field, value)
				with self.assertRaises(ValueError):
					core.cancel_source_transaction(self.source, "delivery_note")
				setattr(record, field, previous)
		self.assertEqual(self.events, [])

	def test_parent_permission_is_required_before_any_reversal(self):
		self.stored_source.check_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, [])

	def test_serializable_flags_cannot_exempt_shipment_links(self):
		self.delivery.flags.custody_loan_cancellation = True
		self.delivery.flags.custody_operation_token = "forged"
		self.delivery.flags.custody_loan_shipment_cancellation = "SHIP-1"
		self.delivery.ignore_linked_doctypes = ("Shipment",)
		core.validate_generated_transaction_cancellation(self.delivery)
		self.assertNotIn("Shipment", self.delivery.ignore_linked_doctypes)

	def test_active_foreign_shipment_links_in_either_table_prevent_cancellation(self):
		for child_doctype in ("Shipment Document", "Shipment Delivery Note"):
			for status in (0, 1):
				with self.subTest(child_doctype=child_doctype, status=status):

					def linked_shipments(doctype, *, filters, **kwargs):
						if doctype != child_doctype:
							return []
						self.assertEqual(filters["docstatus"], ["!=", 2])
						return ["OTHER-SHIP"] if status != filters["docstatus"][1] else []

					frappe.get_all.side_effect = linked_shipments
					with self.assertRaises(ValueError):
						core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, [])

	def test_replacement_shipment_allows_cancelled_history_in_both_tables(self):
		queried = []

		def linked_shipments(doctype, *, filters, **kwargs):
			if doctype not in ("Shipment Document", "Shipment Delivery Note"):
				return []
			queried.append(doctype)
			self.assertEqual(filters["docstatus"], ["!=", 2])
			history = [("SHIP-1", 1), ("CANCELLED-SHIP", 2)]
			return [parent for parent, status in history if status != filters["docstatus"][1]]

		frappe.get_all.side_effect = linked_shipments
		core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(queried, ["Shipment Document", "Shipment Delivery Note"])
		self.assertEqual(self.events, ["reverse delivery", "cancel shipment"])

	def test_own_legacy_rows_cannot_include_other_delivery_notes(self):
		self.shipment.shipment_delivery_note = [Record(delivery_note="OTHER-DN")]
		with self.assertRaises(ValueError):
			core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, [])
		self.shipment.shipment_delivery_note = [Record(delivery_note="DN-1")]
		core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, ["reverse delivery", "cancel shipment"])

	def test_posted_delivery_is_reversed_before_draft_shipment_deletion(self):
		self.shipment.docstatus = 0
		core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, ["reverse delivery", "delete Shipment"])

	def test_stock_reversal_failure_prevents_shipment_cancellation(self):
		self.delivery.cancel = Mock(side_effect=frappe.ValidationError("Frozen stock"))
		with self.assertRaisesRegex(frappe.ValidationError, "Frozen stock"):
			core.cancel_source_transaction(self.source, "delivery_note")
		self.assertEqual(self.events, [])
		self.assertEqual(self.shipment.docstatus, 1)
