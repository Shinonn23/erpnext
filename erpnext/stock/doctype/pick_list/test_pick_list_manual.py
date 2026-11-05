# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.pick_list import pick_list
from erpnext.stock.doctype.pick_list.pick_list import PickList


def draft(pick_manually, rows, was_manual=0):
	doc = Mock(spec=["docstatus", "pick_manually", "get", "get_doc_before_save"])
	doc.docstatus = 0
	doc.pick_manually = pick_manually
	doc.get = lambda field: rows if field == "locations" else None
	doc.get_doc_before_save = lambda: frappe._dict(pick_manually=was_manual)
	return doc


def row(**values):
	return frappe._dict(
		dict(batch_no="B-1", serial_no="SN-1", serial_and_batch_bundle=None, picked_qty=2, warehouse="W"),
		**values,
	)


class ManualPickingTest(unittest.TestCase):
	def test_a_draft_never_picks_on_save(self):
		rows = [row()]
		doc = draft(0, rows)
		PickList.keep_manual_picking(doc)
		self.assertEqual(doc.pick_manually, 1)
		# what was on the rows stays: only the Auto Pick button picks
		self.assertEqual((rows[0].batch_no, rows[0].picked_qty), ("B-1", 2))


FLAGS = {
	"BATCHED": frappe._dict(has_batch_no=1, has_serial_no=0),
	"SERIAL": frappe._dict(has_batch_no=0, has_serial_no=1),
	"PLAIN": frappe._dict(has_batch_no=0, has_serial_no=0),
}


class PickedSerialBatchTest(unittest.TestCase):
	def check(self, *rows):
		doc = Mock(spec=["docstatus", "get"])
		doc.docstatus = 0
		doc.get = lambda field: list(rows) if field == "locations" else None
		with (
			patch.object(
				pick_list.frappe,
				"get_cached_value",
				side_effect=lambda dt, code, f, as_dict: FLAGS[code],
			),
			patch.object(pick_list.frappe, "throw", side_effect=frappe.ValidationError) as throw,
			patch.object(pick_list.frappe, "bold", side_effect=str),
			patch.object(pick_list, "_", side_effect=lambda text: text),
		):
			try:
				PickList.validate_picked_serial_batch(doc)
			except frappe.ValidationError:
				return throw.call_args.args[0]
		return []

	def line(self, idx, item_code, picked_qty, **values):
		return frappe._dict(
			dict(idx=idx, item_code=item_code, picked_qty=picked_qty, batch_no=None, serial_no=None), **values
		)

	def test_picked_rows_must_name_batch_and_one_serial_per_unit(self):
		problems = self.check(
			self.line(1, "BATCHED", 2),
			self.line(2, "SERIAL", 2, serial_no="SN-1"),
			self.line(3, "SERIAL", 2, serial_no="SN-1\nSN-1"),
		)
		self.assertEqual(len(problems), 3)
		self.assertIn("Row 1", problems[0])
		self.assertIn("(1 entered)", problems[1])

	def test_complete_rows_unpicked_rows_and_bundles_pass(self):
		self.assertEqual(
			self.check(
				self.line(1, "BATCHED", 2, batch_no="B-1"),
				self.line(2, "SERIAL", 2, serial_no="SN-1\nSN-2"),
				self.line(3, "SERIAL", 0),
				self.line(4, "BATCHED", 1, serial_and_batch_bundle="SBB-1"),
				self.line(5, "PLAIN", 3),
			),
			[],
		)
