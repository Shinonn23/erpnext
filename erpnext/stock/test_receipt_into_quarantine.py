"""Received first, inspected after (Connect Diagnostics, Siwat, 9 Nov 2026): a goods receipt row received into a
Quarantine warehouse is not stopped for the Quality Inspection that comes after the receipt. Offline."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from erpnext.controllers.stock_controller import StockController


class Row(SimpleNamespace):
	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)


class Document(SimpleNamespace):
	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def is_receipt_into_quarantine(self, row):
		return StockController.is_receipt_into_quarantine(self, row)


class TestReceiptIntoQuarantine(TestCase):
	def ask(self, doctype, warehouse, is_return=0, quarantine=("QUARANTINE - CD",)):
		doc = Document(doctype=doctype, company="CD", is_return=is_return)
		with patch(
			"erpnext.loan.doctype.custody_loan.custody_loan_core.is_quarantine_warehouse",
			side_effect=lambda name, company: name in quarantine,
		):
			return doc.is_receipt_into_quarantine(Row(warehouse=warehouse))

	def test_a_purchase_receipt_row_in_quarantine_is_not_stopped(self):
		self.assertTrue(self.ask("Purchase Receipt", "QUARANTINE - CD"))
		self.assertTrue(self.ask("Purchase Invoice", "QUARANTINE - CD"))

	def test_a_flagged_row_received_anywhere_else_is_still_stopped(self):
		self.assertFalse(self.ask("Purchase Receipt", "CD STOCK - CD"))
		self.assertFalse(self.ask("Purchase Receipt", None))

	def test_a_purchase_return_is_not_a_receipt(self):
		self.assertFalse(self.ask("Purchase Receipt", "QUARANTINE - CD", is_return=1))

	def test_goods_coming_back_are_a_receipt_but_goods_going_out_are_not(self):
		self.assertTrue(self.ask("Delivery Note", "QUARANTINE - CD", is_return=1))
		self.assertTrue(self.ask("Sales Invoice", "QUARANTINE - CD", is_return=1))
		self.assertFalse(self.ask("Delivery Note", "QUARANTINE - CD", is_return=0))

	def test_other_documents_are_never_receipts(self):
		self.assertFalse(self.ask("Stock Entry", "QUARANTINE - CD"))


class Gate(Document):
	def is_inspected_after_receipt_without_stock(self, row):
		return StockController.is_inspected_after_receipt_without_stock(self, row)

	def validate_qi_presence(self, row):
		return StockController.validate_qi_presence(self, row)

	def validate_inspection(self):
		return StockController.validate_inspection(self)


class TestInspectionGate(TestCase):
	def run_gate(self, warehouse, is_stock_item=1):
		doc = Gate(
			doctype="Purchase Receipt",
			company="CD",
			is_return=0,
			docstatus=1,
			items=[Row(item_code="X", warehouse=warehouse, quality_inspection=None, idx=1)],
		)

		def item_value(doctype, name, field):
			return is_stock_item if field == "is_stock_item" else 1

		with (
			patch("erpnext.controllers.stock_controller.frappe.get_cached_value", side_effect=item_value),
			patch("erpnext.controllers.stock_controller.frappe.get_single_value", return_value=0),
			patch(
				"frappe.throw",
				side_effect=lambda message, title=None, exc=Exception: (_error for _error in ()).throw(
					exc(message)
				),
			),
			patch(
				"erpnext.controllers.stock_controller.is_inspection_exempt_secondary_row", return_value=False
			),
			patch(
				"erpnext.loan.doctype.custody_loan.custody_loan_core.is_quarantine_warehouse",
				side_effect=lambda name, company: name == "QUARANTINE - CD",
			),
		):
			doc.validate_inspection()

	def test_a_row_in_quarantine_passes_the_submit_gate(self):
		self.run_gate("QUARANTINE - CD")

	def test_a_flagged_row_elsewhere_is_still_stopped(self):
		from erpnext.controllers.stock_controller import QualityInspectionRequiredError

		with self.assertRaises(QualityInspectionRequiredError):
			self.run_gate("CD STOCK - CD")

	def test_a_non_stock_row_is_not_stopped(self):
		self.run_gate("", is_stock_item=0)
