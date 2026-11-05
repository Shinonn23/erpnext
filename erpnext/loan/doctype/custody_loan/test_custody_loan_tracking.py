# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from unittest.mock import patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_tracking as tracking
from erpnext.loan.report import requester


class Doc(frappe._dict):
	"""A document stub whose `items` is the table, not dict.items."""

	@property
	def items(self):
		return self["items"]

	@items.setter
	def items(self, value):
		self["items"] = value


FLAGS = {
	"BATCHED": frappe._dict(has_batch_no=1, has_serial_no=0),
	"SERIAL": frappe._dict(has_batch_no=1, has_serial_no=1),
	"PLAIN": frappe._dict(has_batch_no=0, has_serial_no=0),
}


def loan(*items):
	return Doc(
		name="CL-1",
		doctype="Custody Loan",
		loan_type="Stock",
		delivery_note="DN-1",
		items=[frappe._dict(name=name, idx=i + 1, item_code=code) for i, (name, code) in enumerate(items)],
	)


def movement(kind, loan_item, qty, batch_no=None, serial_no=None):
	return (kind, frappe._dict(custody_loan_item=loan_item, qty=qty, batch_no=batch_no, serial_no=serial_no))


class TrackingTest(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(tracking, "item_flags", side_effect=lambda code: FLAGS[code]),
			patch.object(tracking, "conversion_factors", return_value={}),
			patch.object(tracking, "_", side_effect=lambda text: text),
		]
		for item in patches:
			item.start()
			self.addCleanup(item.stop)

	def balances(self, doc, issued, movements=()):
		with (
			patch.object(tracking, "issued_entries", return_value=issued),
			patch.object(tracking, "_movement_rows", return_value=list(movements)),
		):
			return tracking.get_balances(doc)

	def test_row_picked_from_two_batches_is_tracked_per_batch(self):
		doc = loan(("L1", "BATCHED"))
		balances = self.balances(
			doc,
			{"L1": [("B-1", None, 3), ("B-2", None, 2)]},
			[movement("returned", "L1", 1, "B-1"), movement("pending", "L1", 2, "B-2")],
		)
		first, second = balances[("L1", "B-1", None)], balances[("L1", "B-2", None)]
		self.assertEqual(
			(first.issued_qty, first.returned_qty, first.outstanding_qty, first.open_qty), (3, 1, 2, 2)
		)
		self.assertEqual(first.status, "With Borrower")
		self.assertEqual((second.outstanding_qty, second.open_qty, second.status), (2, 0, "Pending Return"))

	def test_serials_keep_the_batch_they_were_issued_from(self):
		doc = loan(("L1", "SERIAL"))
		balances = self.balances(
			doc,
			{"L1": [("B-1", "SN-1", 1), ("B-2", "SN-2", 1)]},
			[movement("adjusted", "L1", 1, None, "SN-2")],
		)
		self.assertEqual(balances[("L1", None, "SN-1")].batch_no, "B-1")
		written_off = balances[("L1", None, "SN-2")]
		self.assertEqual(
			(written_off.batch_no, written_off.outstanding_qty, written_off.status), ("B-2", 0, "Written Off")
		)

	def test_items_without_batch_or_serial_are_not_tracked(self):
		self.assertEqual(self.balances(loan(("L1", "PLAIN")), {"L1": [(None, None, 4)]}), {})

	def test_grouped_open_rows_give_one_row_per_batch(self):
		doc = loan(("L1", "SERIAL"))
		issued = {"L1": [("B-1", "SN-1", 1), ("B-1", "SN-2", 1), ("B-2", "SN-3", 1)]}
		with (
			patch.object(tracking, "issued_entries", return_value=issued),
			patch.object(
				tracking, "_movement_rows", return_value=[movement("pending", "L1", 1, None, "SN-2")]
			),
		):
			groups = tracking.grouped_open_rows(doc, doc["items"][0])
		self.assertEqual(
			groups,
			[
				{"batch_no": "B-1", "serials": ["SN-1"], "qty": 1},
				{"batch_no": "B-2", "serials": ["SN-3"], "qty": 1},
			],
		)

	def validate(self, doc, rows, issued, movements=()):
		target = Doc(doctype="Custody Loan Return", name=None, items=rows)
		with (
			patch.object(tracking, "issued_entries", return_value=issued),
			patch.object(tracking, "_movement_rows", return_value=list(movements)),
			patch.object(tracking.frappe, "throw", side_effect=frappe.ValidationError),
		):
			tracking.validate_tracked_rows(target, doc, "returned_qty")
		return target

	def test_return_sets_the_batch_of_its_serials(self):
		doc = loan(("L1", "SERIAL"))
		row = frappe._dict(idx=1, custody_loan_item="L1", returned_qty=1, serial_no="SN-2", batch_no=None)
		self.validate(doc, [row], {"L1": [("B-1", "SN-1", 1), ("B-2", "SN-2", 1)]})
		self.assertEqual(row.batch_no, "B-2")

	def test_return_refuses_serials_from_two_batches_on_one_row(self):
		doc = loan(("L1", "SERIAL"))
		row = frappe._dict(idx=1, custody_loan_item="L1", returned_qty=2, serial_no="SN-1\nSN-2")
		with self.assertRaises(frappe.ValidationError):
			self.validate(doc, [row], {"L1": [("B-1", "SN-1", 1), ("B-2", "SN-2", 1)]})

	def test_return_refuses_a_serial_already_on_a_pending_return(self):
		doc = loan(("L1", "SERIAL"))
		row = frappe._dict(idx=1, custody_loan_item="L1", returned_qty=1, serial_no="SN-1")
		with self.assertRaises(frappe.ValidationError):
			self.validate(
				doc, [row], {"L1": [("B-1", "SN-1", 1)]}, [movement("pending", "L1", 1, None, "SN-1")]
			)

	def test_return_refuses_more_than_the_batch_still_out(self):
		doc = loan(("L1", "BATCHED"))
		issued = {"L1": [("B-1", None, 3), ("B-2", None, 2)]}
		ok = frappe._dict(idx=1, custody_loan_item="L1", returned_qty=3, batch_no="B-1")
		self.validate(
			doc, [ok, frappe._dict(idx=2, custody_loan_item="L1", returned_qty=2, batch_no="B-2")], issued
		)
		too_much = frappe._dict(idx=1, custody_loan_item="L1", returned_qty=3, batch_no="B-2")
		with self.assertRaises(frappe.ValidationError):
			self.validate(doc, [too_much], issued)

	def test_return_refuses_a_batch_not_issued_and_duplicate_batch_rows(self):
		doc = loan(("L1", "BATCHED"))
		issued = {"L1": [("B-1", None, 3)]}
		with self.assertRaises(frappe.ValidationError):
			self.validate(
				doc, [frappe._dict(idx=1, custody_loan_item="L1", returned_qty=1, batch_no="B-9")], issued
			)
		rows = [frappe._dict(idx=i, custody_loan_item="L1", returned_qty=1, batch_no="B-1") for i in (1, 2)]
		with self.assertRaises(frappe.ValidationError):
			self.validate(doc, rows, issued)


class ReportRequesterTest(unittest.TestCase):
	def test_requester_columns_follow_the_borrower(self):
		columns = [{"fieldname": "loan"}, {"fieldname": "borrower"}, {"fieldname": "status"}]
		names = [
			column["fieldname"]
			for column in requester.insert_columns_after(columns, "borrower", requester.requester_columns())
		]
		self.assertEqual(
			names, ["loan", "borrower", "requested_by", "requested_by_name", "requested_by_user", "status"]
		)

	def test_requester_filter_uses_the_material_request_fallback(self):
		conditions, values = [], {}
		requester.add_requester_filter(frappe._dict(requested_by="EMP-1"), conditions, values)
		self.assertEqual(values, {"requested_by": "EMP-1"})
		self.assertIn("request.owner", conditions[0])

	def test_open_batches_split_report_rows(self):
		row = frappe._dict(
			loan="CL-1", loan_item="L1", qty=5, returned_qty=1, adjusted_qty=0, outstanding_qty=4
		)
		entries = [
			frappe._dict(
				custody_loan_item="L1",
				batch_no="B-1",
				serial_no=None,
				issued_qty=3,
				returned_qty=1,
				adjusted_qty=0,
				outstanding_qty=2,
			),
			frappe._dict(
				custody_loan_item="L1",
				batch_no="B-2",
				serial_no=None,
				issued_qty=2,
				returned_qty=0,
				adjusted_qty=0,
				outstanding_qty=2,
			),
			frappe._dict(
				custody_loan_item="L1",
				batch_no="B-3",
				serial_no=None,
				issued_qty=1,
				returned_qty=1,
				adjusted_qty=0,
				outstanding_qty=0,
			),
		]
		with patch.object(requester.frappe, "get_all", return_value=entries):
			result = requester.expand_serial_batches([row])
		self.assertEqual(
			[(r.batch_no, r.qty, r.outstanding_qty) for r in result], [("B-1", 3, 2), ("B-2", 2, 2)]
		)


class ResolveLoanItemRowsTest(unittest.TestCase):
	def resolve(self, rows):
		from erpnext.loan.doctype.custody_loan import custody_loan_core as core

		loan_doc = Doc(
			name="CL-1",
			items=[
				frappe._dict(name="L1", item_code="A", outstanding_qty=0, asset=None),
				frappe._dict(name="L2", item_code="A", outstanding_qty=2, asset=None),
				frappe._dict(name="L3", item_code="B", outstanding_qty=1, asset=None),
			],
		)
		with (
			patch.object(core.frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(core, "_", side_effect=lambda text: text),
		):
			core.resolve_loan_item_rows(Doc(items=rows), loan_doc)
		return rows

	def test_item_picks_the_loan_row_with_quantity_still_out(self):
		rows = self.resolve([frappe._dict(idx=1, item_code="A"), frappe._dict(idx=2, item_code="B")])
		self.assertEqual([row.custody_loan_item for row in rows], ["L2", "L3"])

	def test_a_changed_item_moves_the_link_and_a_kept_link_stays(self):
		rows = self.resolve(
			[
				frappe._dict(idx=1, item_code="B", custody_loan_item="L1"),
				frappe._dict(idx=2, item_code=None, custody_loan_item="L1"),
			]
		)
		self.assertEqual([(row.custody_loan_item, row.item_code) for row in rows], [("L3", "B"), ("L1", "A")])

	def test_an_item_not_on_the_loan_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.resolve([frappe._dict(idx=1, item_code="Z")])
