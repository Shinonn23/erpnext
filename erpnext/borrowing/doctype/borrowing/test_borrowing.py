"""A borrowing chains like selling (UX WP4, Siwat 18 Nov 2026: "SO -> DN ... documents are made one from another").

Stock: Borrowing -> Pick List -> issue Delivery Note -> return Delivery Note made from the issue note. Asset: Borrowing
-> issue Asset Movement -> return Asset Movement made from the issue movement. There is no Borrowing Return. Offline.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import frappe

from erpnext.borrowing.doctype.borrowing import borrowing_core as core
from erpnext.borrowing.doctype.borrowing.borrowing_core import Borrowing


class Doc(SimpleNamespace):
	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def is_new(self):
		return False

	def as_dict(self):
		return dict(vars(self))


def throw(message, *args, **kwargs):
	raise frappe.ValidationError(message)


class Patched(TestCase):
	def setUp(self):
		for patcher in (
			patch.object(frappe, "throw", side_effect=throw),
			patch.object(core, "_", side_effect=lambda text: text),
		):
			patcher.start()
			self.addCleanup(patcher.stop)


def loan(kind="Stock", **values):
	fields = dict(
		name="LOAN-1",
		doctype="Borrowing",
		docstatus=1,
		status="Active",
		loan_type=kind,
		company="CD",
		customer_loan_warehouse="BORROWED",
		material_request="MR-1",
		borrower_type="Customer",
		borrower="CUST",
		expected_return_date="2026-12-01",
		recipient_contact=None,
		recipient_address=None,
		recipient_address_display=None,
		recipient_contact_display=None,
		recipient_contact_email=None,
		items=[
			Doc(
				name="ROW-1",
				item_code="ITEM",
				qty=2,
				source_warehouse="STORE",
				asset="AST-1" if kind == "Asset" else None,
				material_request_item="MR-ROW-1",
				source_location="HQ",
			)
		],
		check_permission=Mock(),
	)
	fields.update(values)
	return Doc(**fields)


class TestStatus(Patched):
	def test_asset_loan_status_is_active_without_a_transit_state(self):
		doc = loan("Asset", asset_movement="MOVE-1", refresh_balances=lambda: None)
		doc.items = [Doc(issued_qty=1, returned_qty=0, adjusted_qty=0, sold_qty=0, outstanding_qty=1)]
		with patch.object(frappe, "db", Mock(get_value=Mock(return_value=1))):
			self.assertEqual(Borrowing.get_submitted_status(doc), "Active")

	def test_a_stock_loan_whose_delivery_note_is_not_submitted_waits_for_it_only(self):
		doc = loan(
			pick_list="PICK-1", delivery_note="DN-1", asset_movement=None, refresh_balances=lambda: None
		)
		doc.items = [Doc(issued_qty=1, returned_qty=0, adjusted_qty=0, sold_qty=0, outstanding_qty=1)]
		with patch.object(frappe, "db", Mock(get_value=Mock(side_effect=[1, 0]))):
			self.assertEqual(Borrowing.get_submitted_status(doc), "Pending Delivery")

	def test_returned_and_partly_returned_follow_the_return_documents(self):
		doc = loan(pick_list="PICK-1", delivery_note="DN-1", refresh_balances=lambda: None)
		for returned, outstanding, status in ((2, 0, "Returned"), (1, 1, "Partially Returned")):
			doc.items = [
				Doc(
					issued_qty=2,
					returned_qty=returned,
					adjusted_qty=0,
					sold_qty=0,
					outstanding_qty=outstanding,
				)
			]
			with patch.object(frappe, "db", Mock(get_value=Mock(return_value=1))):
				self.assertEqual(Borrowing.get_submitted_status(doc), status)


class TestReturnRows(Patched):
	def test_a_submitted_note_returns_and_a_draft_reserves_in_the_borrowing_unit(self):
		def get_all(doctype, filters=None, fields=None, **kwargs):
			if doctype == "Delivery Note":
				return [frappe._dict(name="DN-R1", docstatus=1), frappe._dict(name="DN-R2", docstatus=0)]
			if doctype == "Asset Movement":
				return []
			if doctype == "Delivery Note Item":
				return [
					frappe._dict(
						parent="DN-R1",
						borrowing_item="ROW-1",
						batch_no="B1",
						serial_no=None,
						serial_and_batch_bundle=None,
						qty=-1,
						stock_qty=-10,
						conversion_factor=10,
					),
					frappe._dict(
						parent="DN-R2",
						borrowing_item="ROW-1",
						batch_no=None,
						serial_no="S1\nS2",
						serial_and_batch_bundle=None,
						qty=-2,
						stock_qty=-2,
						conversion_factor=1,
					),
				]
			raise AssertionError(doctype)

		with patch.object(frappe, "get_all", side_effect=get_all):
			rows = core.return_rows("LOAN-1")
		self.assertEqual(
			[(row.parent, row.docstatus, row.batch_no, row.serial_no, row.qty) for row in rows],
			[("DN-R1", 1, "B1", None, 1.0), ("DN-R2", 0, None, "S1", 1), ("DN-R2", 0, None, "S2", 1)],
		)

	def test_the_return_being_validated_is_left_out(self):
		def get_all(doctype, **kwargs):
			return [frappe._dict(name="DN-R1", docstatus=0)] if doctype == "Delivery Note" else []

		with patch.object(frappe, "get_all", side_effect=get_all):
			self.assertEqual(core.return_documents("LOAN-1", exclude=("Delivery Note", "DN-R1")), [])


class TestReturnDeliveryNote(Patched):
	def note(self, **values):
		fields = dict(
			doctype="Delivery Note",
			name="DN-R1",
			docstatus=0,
			is_return=1,
			company="CD",
			return_against="DN-ISSUE",
			items=[
				Doc(
					idx=1,
					borrowing_item="ROW-1",
					dn_detail="ISSUE-ROW",
					item_code="ITEM",
					qty=-2,
					stock_qty=-2,
					conversion_factor=1,
					warehouse="STORE",
					target_warehouse="BORROWED",
					batch_no=None,
					serial_no=None,
					serial_and_batch_bundle=None,
				)
			],
		)
		fields.update(values)
		return Doc(**fields)

	def validate(self, note, available=2, issue=None):
		db = Mock()
		db.get_value.return_value = issue or frappe._dict(
			docstatus=1, is_return=0, borrowing="LOAN-1", is_borrowing_transaction=1
		)
		with (
			patch.object(frappe, "db", db),
			patch.object(
				frappe,
				"get_all",
				return_value=[frappe._dict(name="ISSUE-ROW", borrowing_item="ROW-1", warehouse="STORE")],
			),
			patch.object(core, "loan_item_available_to_receive", return_value=available) as room,
			patch.object(core, "validate_tracked_rows") as tracked,
			patch.object(core, "require_quarantine_warehouse"),
			patch.object(core, "borrowing_return_row_requires_inspection", return_value=False),
		):
			core.validate_borrowing_return_delivery_note(note, loan(), "Borrowing", "LOAN-1")
		return room, tracked

	def test_a_return_of_what_is_still_out_is_accepted(self):
		room, tracked = self.validate(self.note())
		room.assert_called_once()
		self.assertEqual(room.call_args.kwargs["exclude_return"], ("Delivery Note", "DN-R1"))
		self.assertEqual(tracked.call_args.kwargs, {"allow_other_lots": True})

	def test_more_than_is_still_with_the_borrower_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "still with the borrower"):
			self.validate(self.note(), available=1)

	def test_the_return_must_be_made_from_a_submitted_issue_note_of_the_borrowing(self):
		for issue in (
			frappe._dict(docstatus=0, is_return=0, borrowing="LOAN-1", is_borrowing_transaction=1),
			frappe._dict(docstatus=1, is_return=1, borrowing="LOAN-1", is_borrowing_transaction=1),
			frappe._dict(docstatus=1, is_return=0, borrowing="OTHER", is_borrowing_transaction=1),
		):
			with self.subTest(issue=issue), self.assertRaises(frappe.ValidationError):
				self.validate(self.note(), issue=issue)

	def test_a_row_into_another_warehouse_or_not_from_the_borrowed_one_is_refused(self):
		for field, value in (("warehouse", "OTHER"), ("target_warehouse", "OTHER"), ("dn_detail", "X")):
			note = self.note()
			setattr(note.items[0], field, value)
			with (
				self.subTest(field=field),
				patch.object(core, "is_quarantine_warehouse", return_value=False),
				self.assertRaises(frappe.ValidationError),
			):
				self.validate(note)

	def test_the_issue_note_needs_a_return_source_of_its_own_borrowing(self):
		with self.assertRaises(frappe.ValidationError):
			core.validate_borrowing_return_delivery_note(self.note(), loan(), "Borrowing", "OTHER")


class TestReturnMovement(Patched):
	def movement(self, **values):
		fields = dict(
			doctype="Asset Movement",
			name="AM-R1",
			docstatus=0,
			purpose="Receipt",
			return_against="AM-ISSUE",
			assets=[
				Doc(
					idx=1,
					borrowing_item="ROW-1",
					asset="AST-1",
					request_item="MR-ROW-1",
					target_location="HQ",
				)
			],
		)
		fields.update(values)
		return Doc(**fields)

	def validate(self, movement, available=1):
		doc = loan("Asset")
		db = Mock()
		db.get_value.return_value = frappe._dict(
			docstatus=1, borrowing="LOAN-1", purpose="Transfer", borrowing_source_doctype="Borrowing"
		)
		with (
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_all", return_value=["ROW-1"]),
			patch.object(core, "loan_item_available_to_receive", return_value=available),
		):
			core.validate_borrowing_return_movement(movement, doc, {row.name: row for row in doc.items})

	def test_an_asset_still_out_goes_back_to_its_location(self):
		self.validate(self.movement())

	def test_a_returned_asset_or_another_location_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.validate(self.movement(), available=0)
		movement = self.movement()
		movement.assets[0].target_location = "ELSEWHERE"
		with self.assertRaises(frappe.ValidationError):
			self.validate(movement)

	def test_the_builder_takes_the_assets_still_out_back_to_their_location(self):
		issue = Doc(
			name="AM-ISSUE",
			docstatus=1,
			borrowing="LOAN-1",
			borrowing_source_doctype="Borrowing",
			purpose="Transfer",
			assets=[Doc(borrowing_item="ROW-1")],
			check_permission=Mock(),
		)
		movement = MagicMock()
		with (
			patch.object(frappe, "get_doc", side_effect=[issue, loan("Asset")]),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "new_doc", return_value=movement),
			patch.object(frappe, "db", Mock(get_value=Mock(return_value="CUSTOMER SITE"))),
			patch.object(core, "loan_item_available_to_receive", return_value=1),
		):
			self.assertIs(core.make_borrowing_return_movement("AM-ISSUE"), movement)
		self.assertEqual((movement.purpose, movement.return_against), ("Receipt", "AM-ISSUE"))
		row = movement.append.call_args.args[1]
		self.assertEqual(
			(row["asset"], row["source_location"], row["target_location"]), ("AST-1", "CUSTOMER SITE", "HQ")
		)
		movement.insert.assert_not_called()
		movement.submit.assert_not_called()


class TestCreateReturn(Patched):
	def test_create_return_opens_the_return_of_the_only_issue_document(self):
		for kind, builder in (
			("Stock", "make_borrowing_return_delivery_note"),
			("Asset", "make_borrowing_return_movement"),
		):
			doc = loan(kind, issue_documents=lambda: ["ISSUE-1"])
			with self.subTest(kind=kind), patch.object(core, builder, return_value="RETURN") as made:
				self.assertEqual(Borrowing.make_return(doc), "RETURN")
				made.assert_called_once_with("ISSUE-1")

	def test_several_issue_documents_need_the_one_to_return_against(self):
		doc = loan(issue_documents=lambda: ["ISSUE-1", "ISSUE-2"])
		with self.assertRaises(frappe.ValidationError):
			Borrowing.make_return(doc)
		with self.assertRaises(frappe.ValidationError):
			Borrowing.make_return(doc, "ISSUE-9")
		with patch.object(core, "make_borrowing_return_delivery_note", return_value="RETURN") as made:
			self.assertEqual(Borrowing.make_return(doc, "ISSUE-2"), "RETURN")
		made.assert_called_once_with("ISSUE-2")

	def test_a_closed_borrowing_is_not_returned(self):
		with self.assertRaises(frappe.ValidationError):
			Borrowing.make_return(loan(status="Returned", issue_documents=lambda: ["ISSUE-1"]))
