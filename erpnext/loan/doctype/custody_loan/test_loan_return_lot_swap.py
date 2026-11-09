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


if __name__ == "__main__":
	unittest.main()
