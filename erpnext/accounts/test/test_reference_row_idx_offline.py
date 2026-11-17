"""A split reference row never reuses the idx of another row of the Payment Entry. Offline, no site."""

import unittest

import frappe

from erpnext.accounts.utils import _append_reference_row


class Entry(frappe._dict):
	def append(self, field, row):
		table = self.setdefault(field, [])
		row = frappe._dict(row)
		table.append(row)
		if not row.get("idx"):
			row.idx = len(table)
		return row


class TestReferenceRowIdx(unittest.TestCase):
	def test_second_split_after_cleared_row_gets_a_new_idx(self):
		# PE6911045: the order row (idx 1) is split to the invoice (idx 2) and cleared at 0,
		# then the unallocated part is appended; Frappe alone would number it 2 again and the
		# gain/loss of 5.76 THB was skipped as already booked
		entry = Entry(references=[frappe._dict(idx=1, allocated_amount=4000)])
		first = _append_reference_row(entry, {"allocated_amount": 4000})
		self.assertEqual(first.idx, 2)
		entry.references = [first]  # clear_unallocated_reference_document_rows
		second = _append_reference_row(entry, {"allocated_amount": 320})
		self.assertEqual(second.idx, 3)
		self.assertEqual(len({row.idx for row in entry.references}), 2)

	def test_empty_entry_starts_at_one(self):
		self.assertEqual(_append_reference_row(Entry()).idx, 1)


if __name__ == "__main__":
	unittest.main()
