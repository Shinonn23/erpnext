"""A batch the borrower brings back that was not lent is swapped for the lent batch (Siwat, 5 and 9 Nov 2026).

UX WP4 (Siwat, 18 Nov 2026): the swap is typed on the return Delivery Note row itself; submitting the return posts the
value-preserving Repack inside the Borrowed warehouse and cancelling the return cancels it. Nobody makes it by hand.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.borrowing.doctype.borrowing import borrowing_core as core


def entry(loan_item, batch, issued, open_qty):
	return frappe._dict(
		borrowing_item=loan_item, batch_no=batch, serial_no=None, issued_qty=issued, open_qty=open_qty
	)


def note_row(batch, qty, loan_item="li"):
	return frappe._dict(
		idx=1,
		borrowing_item=loan_item,
		batch_no=batch,
		serial_no=None,
		serial_and_batch_bundle=None,
		qty=-qty,
		stock_qty=-qty,
		conversion_factor=1,
	)


class Note(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def is_new(self):
		return False


def return_note(rows):
	return Note(
		doctype="Delivery Note",
		name="DN-RET-1",
		is_return=1,
		is_borrowing_transaction=1,
		borrowing="L-1",
		company="CD",
		posting_date="2026-11-19",
		posting_time="10:00:00",
		items=rows,
	)


class TestReturnBatchSwaps(unittest.TestCase):
	def swaps(self, rows, balances):
		loan = SimpleNamespace(name="L-1", items=[frappe._dict(name="li", item_code="ITEM")])
		with (
			patch.object(core, "get_balances", return_value=balances) as balances_of,
			patch.object(core, "conversion_factors", return_value={}),
		):
			result = core.return_batch_swaps(return_note(rows), loan)
		balances_of.assert_called_once_with(loan, exclude=("Delivery Note", "DN-RET-1"))
		return result

	def test_an_unknown_batch_takes_the_lent_batches_earliest_first(self):
		balances = {
			("li", "B-2", None): entry("li", "B-2", 5, 5),
			("li", "B-1", None): entry("li", "B-1", 3, 3),
		}
		got = [(lent, back, qty) for _, _, lent, back, qty in self.swaps([note_row("NEWLOT", 4)], balances)]
		self.assertEqual(got, [("B-1", "NEWLOT", 3), ("B-2", "NEWLOT", 1)])

	def test_a_lent_batch_coming_back_needs_no_swap(self):
		balances = {("li", "B-1", None): entry("li", "B-1", 3, 3)}
		self.assertEqual(self.swaps([note_row("B-1", 2)], balances), [])


class TestSwapPostedWithTheReturn(unittest.TestCase):
	def test_submitting_the_return_posts_one_repack_per_swap(self):
		loan = frappe._dict(name="L-1", customer_loan_warehouse="BORROWED")
		entry_doc = MagicMock()
		with (
			patch.object(core.frappe, "get_doc", return_value=loan),
			patch.object(core, "lock_borrowing"),
			patch.object(core, "return_batch_swaps", return_value=[("li", "ITEM", "B-1", "NEW", 2.0)]),
			patch.object(core, "new_stock_entry", return_value=entry_doc) as create,
			patch.object(core, "set_loan_transaction_source") as source,
			patch.object(core, "get_default_storage_location", return_value="LOC"),
			patch.object(core.frappe, "get_cached_value", return_value="Nos"),
		):
			core.post_return_batch_swaps(return_note([note_row("NEW", 2)]))
		create.assert_called_once_with("CD", "Repack")
		source.assert_called_once_with(entry_doc, loan, loan)
		self.assertEqual(entry_doc.delivery_note_no, "DN-RET-1")
		out, into = (call.args[1] for call in entry_doc.append.call_args_list)
		self.assertEqual((out["s_warehouse"], out["batch_no"], out["qty"]), ("BORROWED", "B-1", 2.0))
		self.assertEqual(
			(into["t_warehouse"], into["batch_no"], into["is_finished_item"]), ("BORROWED", "NEW", 1)
		)
		entry_doc.insert.assert_called_once()
		entry_doc.submit.assert_called_once()

	def test_a_sales_return_posts_no_swap(self):
		note = return_note([])
		note.is_borrowing_transaction = 0
		with patch.object(core, "new_stock_entry") as create:
			core.post_return_batch_swaps(note)
		create.assert_not_called()

	def test_cancelling_the_return_cancels_its_swaps(self):
		entry_doc = MagicMock(flags=frappe._dict())
		entry_doc.get.return_value = ()
		with (
			patch.object(core.frappe, "get_all", return_value=["STE-1"]) as get_all,
			patch.object(core.frappe, "get_doc", return_value=entry_doc),
		):
			core.cancel_return_batch_swaps(return_note([]))
		self.assertEqual(get_all.call_args.kwargs["filters"]["delivery_note_no"], "DN-RET-1")
		self.assertTrue(entry_doc.flags.borrowing_cancellation)
		entry_doc.cancel.assert_called_once()


if __name__ == "__main__":
	unittest.main()
