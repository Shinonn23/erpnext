"""A batch the customer brings back that was not lent is exchanged for the lent batch (Siwat, 9 Nov 2026)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_core as core


def entry(loan_item, batch, issued, open_qty):
	return frappe._dict(
		custody_loan_item=loan_item, batch_no=batch, serial_no=None, issued_qty=issued, open_qty=open_qty
	)


class TestOtherLotSwaps(unittest.TestCase):
	def swaps(self, rows, balances):
		doc = MagicMock(doctype="Custody Loan Return", custody_loan="L-1", items=rows)
		doc.name = "CR-1"
		with (
			patch.object(core.frappe, "get_doc", return_value=MagicMock()),
			patch.object(core, "get_balances", return_value=balances),
			patch.object(core, "conversion_factors", return_value={}),
		):
			return core.other_lot_swaps(doc)

	def test_an_unknown_batch_takes_the_lent_batches_earliest_first(self):
		balances = {
			("li", "B-2", None): entry("li", "B-2", 5, 5),
			("li", "B-1", None): entry("li", "B-1", 3, 3),
		}
		row = frappe._dict(custody_loan_item="li", batch_no="NEWLOT-R", serial_no=None, returned_qty=4)
		got = [(b, q) for _, b, q in self.swaps([row], balances)]
		self.assertEqual(got, [("B-1", 3), ("B-2", 1)])

	def test_a_lent_batch_coming_back_needs_no_exchange(self):
		balances = {("li", "B-1", None): entry("li", "B-1", 3, 3)}
		row = frappe._dict(custody_loan_item="li", batch_no="B-1", serial_no=None, returned_qty=2)
		self.assertEqual(self.swaps([row], balances), [])


class TestSwapIsValueExactPerItem(unittest.TestCase):
	def run_swap(self):
		rows = [
			frappe._dict(item_code="ITEM-A", batch_no="A-NEW", custody_loan_item="la"),
			frappe._dict(item_code="ITEM-B", batch_no="B-NEW", custody_loan_item="lb"),
		]
		swaps = [(rows[0], "A-OLD", 2), (rows[1], "B-OLD", 3)]
		entries = []

		def new_entry(company, purpose):
			entry = MagicMock()
			entry.rows = []
			entry.append.side_effect = lambda table, row: entry.rows.append(row)
			entries.append(entry)
			return entry

		doc = core.CustodyLoanReturn.__new__(core.CustodyLoanReturn)
		doc.company = "CD"
		doc.custody_loan = "L-1"
		with (
			patch.object(core, "other_lot_swaps", return_value=swaps),
			patch.object(core.frappe, "get_doc", return_value=frappe._dict(customer_loan_warehouse="LOAN")),
			patch.object(core, "new_stock_entry", side_effect=new_entry),
			patch.object(core, "set_loan_transaction_source"),
			patch.object(core, "get_default_storage_location", return_value=None),
		):
			doc.swap_returned_lots()
		return entries

	def test_each_exchanged_pair_is_its_own_repack_of_one_item(self):
		entries = self.run_swap()
		self.assertEqual(len(entries), 2)
		for entry, item, old, new, qty in (
			(entries[0], "ITEM-A", "A-OLD", "A-NEW", 2),
			(entries[1], "ITEM-B", "B-OLD", "B-NEW", 3),
		):
			source, finished = entry.rows
			self.assertEqual({source["item_code"], finished["item_code"]}, {item})
			self.assertEqual((source["batch_no"], finished["batch_no"]), (old, new))
			self.assertEqual((source["qty"], finished["qty"]), (qty, qty))
			self.assertEqual(sum(1 for row in entry.rows if row.get("is_finished_item")), 1)
			entry.submit.assert_called_once()


class TestReceiptFailureUndoesSwap(unittest.TestCase):
	def test_a_failed_submit_rolls_back_the_swap_and_logs(self):
		note = MagicMock(docstatus=0)
		note.get.side_effect = (
			lambda key, default=None: "CR-1" if key == "custody_loan_source_name" else default
		)
		note.submit.side_effect = frappe.ValidationError("no stock")
		prepare = MagicMock()
		comment = MagicMock()
		with (
			patch.object(core.frappe, "get_doc", side_effect=[note, comment]),
			patch.object(core.frappe, "db", new=MagicMock()) as db,
			patch.object(core.frappe, "clear_last_message"),
			patch.object(core.frappe, "log_error") as log_error,
		):
			db.exists.return_value = True
			core.receive_return_delivery_note("DN-1", prepare=prepare)
		prepare.assert_called_once()
		db.savepoint.assert_called_once()
		db.rollback.assert_called_once_with(save_point=db.savepoint.call_args.args[0])
		log_error.assert_called_once()
		self.assertIn("CR-1", log_error.call_args.kwargs["title"])
		comment.add_comment.assert_called_once()

	def test_a_successful_receipt_keeps_the_swap(self):
		note = MagicMock(docstatus=0)
		prepare = MagicMock()
		with (
			patch.object(core.frappe, "get_doc", return_value=note),
			patch.object(core.frappe, "db", new=MagicMock()) as db,
		):
			core.receive_return_delivery_note("DN-1", prepare=prepare)
		prepare.assert_called_once()
		note.submit.assert_called_once()
		db.rollback.assert_not_called()


if __name__ == "__main__":
	unittest.main()
