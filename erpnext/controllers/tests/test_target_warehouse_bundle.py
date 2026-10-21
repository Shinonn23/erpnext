"""A Delivery Note row with a Target Warehouse (a custody loan's delivery) moves serials into that warehouse with a
bundle of its own; cancelling the note reverses that bundle, not the source warehouse's (D-LOAN-02: "Serial and
Batch Bundle ... does not belong to the cancelled stock ledger entry").
Offline: ERPNext's own get_sle_for_target_warehouse; the ledger lookups are stand-ins."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.controllers import selling_controller
from erpnext.controllers.selling_controller import SellingController

ROW = frappe._dict(
	name="DNI-1",
	item_code="01-NM-01003",
	qty=1,
	stock_qty=1,
	conversion_factor=1,
	warehouse="CD STOCK - CD",
	target_warehouse="Loan & Borrow - CD",
	serial_and_batch_bundle="SABB-OUT",
	incoming_rate=53730,
)


def _note(docstatus, is_return=0):
	note = SimpleNamespace(doctype="Delivery Note", name="DN-1", docstatus=docstatus, is_return=is_return)
	# the row's fields, as get_sl_entries gives them: the row's own (source warehouse) bundle
	note.get_sl_entries = lambda row, args: frappe._dict(
		{"warehouse": row.warehouse, "serial_and_batch_bundle": row.serial_and_batch_bundle, **args}
	)
	note.make_package_for_transfer = Mock(return_value="SABB-IN")
	return note


class TestTargetWarehouseBundle(unittest.TestCase):
	def target_sle(self, note):
		get_value = Mock(return_value="SABB-IN")
		with (
			patch.object(selling_controller.frappe, "db", SimpleNamespace(get_value=get_value)),
			# the ledger quantity is rounded to Stock Ledger Entry's precision
			patch.object(selling_controller, "get_ledger_qty", side_effect=lambda qty: qty),
		):
			sle = SellingController.get_sle_for_target_warehouse(note, ROW)
		return sle, get_value

	def test_the_delivery_gives_the_target_warehouse_its_own_bundle(self):
		note = _note(docstatus=1)
		sle, _ = self.target_sle(note)
		self.assertEqual((sle.warehouse, sle.serial_and_batch_bundle), ("Loan & Borrow - CD", "SABB-IN"))
		note.make_package_for_transfer.assert_called_once()

	def test_the_cancellation_reverses_the_target_warehouse_bundle(self):
		sle, get_value = self.target_sle(_note(docstatus=2))
		self.assertEqual((sle.warehouse, sle.serial_and_batch_bundle), ("Loan & Borrow - CD", "SABB-IN"))
		get_value.assert_called_once_with(
			"Stock Ledger Entry",
			{
				"voucher_type": "Delivery Note",
				"voucher_no": "DN-1",
				"voucher_detail_no": "DNI-1",
				"warehouse": "Loan & Borrow - CD",
				"is_cancelled": 0,
			},
			"serial_and_batch_bundle",
		)

	def test_a_return_keeps_the_row_bundle(self):
		sle, get_value = self.target_sle(_note(docstatus=2, is_return=1))
		self.assertEqual(sle.serial_and_batch_bundle, "SABB-OUT")
		get_value.assert_not_called()


if __name__ == "__main__":
	unittest.main()
