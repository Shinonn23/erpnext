# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""A Borrow loan unit converted to a Demo Asset (Connect Diagnostics, Siwat 16 Nov 2026), offline.

The conversion names the loan and its row: a draft that is not Rejected reserves the unit, a submitted one settles it,
a cancelled or deleted one counts for nothing; only that conversion may take the unit out of the Loan warehouse; the
Asset loan that records the Demo Assets afterwards counts neither in the request's fulfilment nor asks for an outgoing
Asset Movement, and the conversion is not cancelled while that loan is active.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion_module
from erpnext.borrowing.doctype.borrowing import borrowing_core as core
from erpnext.borrowing.doctype.borrowing import borrowing_tracking as tracking
from erpnext.stock import warehouse_policy

REJECTED = core.CONVERSION_REJECTED_STATE


class Doc(frappe._dict):
	"""A document stub whose `items` is the table, not dict.items."""

	@property
	def items(self):
		return self["items"]


class Thrown(Exception):
	pass


def _throw(message, *args, **kwargs):
	raise Thrown(message)


def fake_db(parents, items, entries=None, factor=1, has_column=True):
	"""frappe.get_all / frappe.db for loan_conversion_rows: ``parents`` are the conversions, ``items`` their rows by
	parent, ``entries`` the bundle entries by bundle."""
	entries = entries or {}

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		filters = filters or {}
		if doctype == "Stock to Asset Conversion":
			wanted = filters["docstatus"][1]
			rows = [row for row in parents if row.docstatus in wanted]
			if "borrowing_item" in filters:
				rows = [row for row in rows if row.borrowing_item == filters["borrowing_item"]]
			if "name" in filters:
				rows = [row for row in rows if row.name != filters["name"][1]]
			return rows
		if doctype == "Stock to Asset Conversion Item":
			return items.get(filters["parent"], [])
		if doctype == "Serial and Batch Entry":
			return entries.get(filters["parent"], [])
		return []

	db = MagicMock()
	db.has_column.return_value = has_column
	db.get_value.return_value = factor
	return get_all, db


def parent(name, docstatus, state=None, loan_item="CLI-1"):
	return frappe._dict(name=name, docstatus=docstatus, workflow_state=state, borrowing_item=loan_item)


def item(qty, bundle=None):
	return frappe._dict(qty=qty, serial_and_batch_bundle=bundle)


class ConversionRowsTest(unittest.TestCase):
	def rows(self, parents, items, entries=None, **kwargs):
		get_all, db = fake_db(parents, items, entries)
		with patch.object(frappe, "get_all", side_effect=get_all), patch.object(frappe, "db", db):
			return core.loan_conversion_rows("CL-1", "CLI-1", **kwargs)

	def test_a_draft_reserves_and_a_rejected_draft_does_not(self):
		rows = self.rows(
			[
				parent("STAC-1", 0, "Stock to Asset Conversion Pending Accounts Review"),
				parent("STAC-2", 0, REJECTED),
			],
			{"STAC-1": [item(1)], "STAC-2": [item(1)]},
			submitted=False,
		)
		self.assertEqual([row.name for row in rows], ["STAC-1"])

	def test_submitted_only_and_the_serials_of_the_bundle(self):
		rows = self.rows(
			[parent("STAC-1", 0), parent("STAC-3", 1)],
			{"STAC-1": [item(1)], "STAC-3": [item(2, "SBB-3")]},
			{
				"SBB-3": [
					frappe._dict(serial_no="SN-1", batch_no="B-1"),
					frappe._dict(serial_no="SN-2", batch_no="B-1"),
				]
			},
			submitted=True,
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].serial_no, "SN-1\nSN-2")
		self.assertEqual(rows[0].batch_no, "B-1")
		self.assertEqual(rows[0].qty, 2)

	def test_the_conversion_being_validated_is_left_out(self):
		rows = self.rows(
			[parent("STAC-1", 0), parent("STAC-2", 0)],
			{"STAC-1": [item(1)], "STAC-2": [item(1)]},
			exclude="STAC-1",
		)
		self.assertEqual([row.name for row in rows], ["STAC-2"])

	def test_cancelled_or_deleted_conversions_count_for_nothing(self):
		# a cancelled conversion is docstatus 2, a deleted one is gone: neither is asked for
		self.assertEqual(self.rows([parent("STAC-9", 2)], {"STAC-9": [item(1)]}), [])
		self.assertEqual(self.rows([], {}), [])

	def test_before_the_fork_field_exists_nothing_is_read(self):
		get_all, db = fake_db([parent("STAC-1", 0)], {"STAC-1": [item(1)]}, has_column=False)
		with patch.object(frappe, "get_all", side_effect=get_all), patch.object(frappe, "db", db):
			self.assertEqual(core.loan_conversion_rows("CL-1"), [])

	def test_a_quantity_is_in_loan_units(self):
		get_all, db = fake_db([parent("STAC-1", 0)], {"STAC-1": [item(6)]}, factor=3)
		with patch.object(frappe, "get_all", side_effect=get_all), patch.object(frappe, "db", db):
			self.assertEqual(core.loan_conversion_rows("CL-1", "CLI-1")[0].qty, 2)


class ReservationTest(unittest.TestCase):
	def test_reserved_quantity_adds_the_draft_conversions(self):
		with (
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(core, "loan_conversion_rows", return_value=[frappe._dict(qty=1)]) as rows,
		):
			self.assertEqual(core.get_loan_item_reserved_qty("CL-1", "CLI-1", exclude_conversion="STAC-1"), 1)
		rows.assert_called_once_with("CL-1", "CLI-1", submitted=False, exclude="STAC-1")

	def test_a_return_or_write_off_cannot_take_a_reserved_unit(self):
		loan_item = frappe._dict(name="CLI-1", qty=2)
		with (
			patch.object(core, "get_loan_item_balances", return_value=(0, 0, 0)),
			patch.object(core, "get_loan_item_reserved_qty", return_value=2),
		):
			self.assertEqual(core.loan_item_available_to_receive("CL-1", loan_item), 0)
			self.assertEqual(core.loan_item_available_to_adjust("CL-1", loan_item), 0)

	def test_a_submitted_conversion_settles_like_an_adjustment(self):
		db = MagicMock()
		db.sql.return_value = [[0]]
		with (
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(core, "get_loan_item_returned_qty", return_value=0),
			patch.object(core, "loan_conversion_rows", return_value=[frappe._dict(qty=1)]) as rows,
		):
			self.assertEqual(core.get_loan_item_balances("CL-1", "CLI-1"), (0, 1, 0))
		rows.assert_called_once_with("CL-1", "CLI-1", submitted=True)

	def test_reserved_and_converted_serials(self):
		with (
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(core, "loan_conversion_serials", return_value=["SN-1"]),
		):
			self.assertEqual(core.get_reserved_serials("CL-1", "CLI-1"), ["SN-1"])
			self.assertEqual(core.get_adjusted_serials("CL-1", "CLI-1"), ["SN-1"])


class TrackingTest(unittest.TestCase):
	def test_a_converted_serial_is_converted_to_asset_and_a_draft_is_pending(self):
		loan = Doc(
			name="CL-1",
			loan_type="Stock",
			delivery_note="DN-1",
			items=[frappe._dict(name="CLI-1", idx=1, item_code="SERIAL")],
		)
		issued = {"CLI-1": [("B-1", "SN-1", 1), ("B-1", "SN-2", 1)]}
		converted = frappe._dict(
			borrowing_item="CLI-1",
			qty=1,
			batch_no="B-1",
			serial_no="SN-1",
			serial_and_batch_bundle="SBB-1",
		)
		draft = frappe._dict(
			borrowing_item="CLI-1",
			qty=1,
			batch_no="B-1",
			serial_no="SN-2",
			serial_and_batch_bundle="SBB-2",
		)
		with (
			patch.object(tracking, "item_flags", return_value=frappe._dict(has_batch_no=1, has_serial_no=1)),
			patch.object(tracking, "conversion_factors", return_value={}),
			patch.object(tracking, "issued_entries", return_value=issued),
			patch.object(
				tracking, "_movement_rows", return_value=[("adjusted", converted), ("pending", draft)]
			),
			patch.object(tracking, "_", side_effect=lambda text: text),
		):
			balances = tracking.get_balances(loan)
		self.assertEqual(balances[("CLI-1", None, "SN-1")].status, "Converted to Asset")
		self.assertEqual(balances[("CLI-1", None, "SN-1")].outstanding_qty, 0)
		self.assertEqual(balances[("CLI-1", None, "SN-2")].status, "Pending Conversion")
		self.assertEqual(balances[("CLI-1", None, "SN-2")].open_qty, 0)

	def test_a_pending_return_stays_pending_return(self):
		entry = frappe._dict(
			issued_qty=1, returned_qty=0, pending_return_qty=1, adjusted_qty=0, outstanding_qty=1
		)
		self.assertEqual(tracking.tracking_status(entry), "Pending Return")
		entry.pending_conversion_qty = 1
		self.assertEqual(tracking.tracking_status(entry), "Pending Conversion")

	def test_a_write_off_stays_written_off(self):
		entry = frappe._dict(
			issued_qty=1, returned_qty=0, pending_return_qty=0, adjusted_qty=1, outstanding_qty=0
		)
		self.assertEqual(tracking.tracking_status(entry), "Written Off")


class WarehousePolicyTest(unittest.TestCase):
	def check(self, doc, loan=None, parent_of_row="CL-1"):
		db = MagicMock()
		loan = loan or frappe._dict(
			docstatus=1, loan_type="Stock", customer_loan_warehouse="LOAN - CD", company="CD"
		)

		def get_value(doctype, name, fields=None, as_dict=False):
			return loan if doctype == "Borrowing" else parent_of_row

		db.get_value.side_effect = get_value
		with patch.object(frappe, "db", db), patch.object(frappe, "throw", side_effect=_throw):
			return warehouse_policy.is_loan_movement(doc)

	def conversion(self, **values):
		doc = frappe._dict(
			doctype="Stock to Asset Conversion",
			borrowing="CL-1",
			borrowing_item="CLI-1",
			company="CD",
			material_request=None,
			stock_items=[frappe._dict(warehouse="LOAN - CD")],
		)
		doc.update(values)
		return doc

	def test_the_conversion_of_its_own_loan_row_is_a_loan_movement(self):
		self.assertTrue(self.check(self.conversion()))

	def test_another_warehouse_row_or_a_request_is_refused(self):
		for doc in (
			self.conversion(stock_items=[frappe._dict(warehouse="CD STOCK - CD")]),
			self.conversion(material_request="MR-1"),
			self.conversion(borrowing_item=None),
		):
			with self.assertRaises(Thrown):
				self.check(doc)
		with self.assertRaises(Thrown):
			self.check(self.conversion(), parent_of_row="CL-2")
		with self.assertRaises(Thrown):
			self.check(
				self.conversion(),
				loan=frappe._dict(
					docstatus=2, loan_type="Stock", customer_loan_warehouse="LOAN - CD", company="CD"
				),
			)

	def test_a_conversion_without_a_loan_is_no_loan_movement(self):
		self.assertFalse(warehouse_policy.is_loan_movement(frappe._dict(doctype="Stock to Asset Conversion")))


class ConvertedAssetLoanTest(unittest.TestCase):
	def loan(self, **values):
		doc = core.Borrowing.__new__(core.Borrowing)
		doc.__dict__.update(
			dict(
				doctype="Borrowing",
				name="CL-2",
				loan_type="Asset",
				docstatus=1,
				material_request="MR-1",
				source_conversion="STAC-1",
				asset_movement=None,
				delivery_note=None,
				status="Active",
				items=[],
			)
		)
		doc.__dict__.update(values)
		return doc

	def test_the_request_fulfilment_is_untouched(self):
		with patch.object(frappe, "get_doc") as get_doc, patch.object(frappe, "db", MagicMock()):
			self.loan().update_material_request_status()
		get_doc.assert_not_called()

	def test_the_demo_customer_address_and_contact_are_kept(self):
		request = Doc(
			items=[],
			docstatus=1,
			material_request_type="Borrow",
			company="CD",
			loan_recipient_type="Employee",
			loan_recipient="EMP-1",
			customer=None,
			transaction_date="2026-11-01",
			expected_return_date="2026-12-01",
			recipient_address="REQ-ADDR",
			recipient_address_display="request address",
			loan_recipient_contact="REQ-CON",
			contact_person=None,
			loan_recipient_contact_display=None,
			customer_contact_display=None,
			loan_recipient_contact_email=None,
			customer_contact_email=None,
			loan_recipient_location=None,
			customer_location=None,
		)
		db = MagicMock()
		db.exists.return_value = True
		for recipient_customer, address in (("CUST-1", "CUST-ADDR"), (None, "REQ-ADDR")):
			doc = self.loan(
				recipient_customer=recipient_customer,
				recipient_address="CUST-ADDR",
				company="CD",
				borrower_type="Employee",
				borrower="EMP-1",
				expected_return_date="2026-12-01",
				items=[],
			)
			with (
				patch.object(frappe, "get_doc", return_value=request),
				patch.object(frappe, "db", db),
				patch.object(core.Borrowing, "set_requester"),
			):
				doc.validate_material_request()
			self.assertEqual(doc.recipient_address, address)

	def test_no_outgoing_asset_movement_is_asked(self):
		doc = self.loan()
		with (
			patch.object(core.Borrowing, "reload"),
			patch.object(core.Borrowing, "check_permission"),
			patch.object(frappe, "throw", side_effect=_throw),
		):
			with self.assertRaises(Thrown):
				doc.prepare_asset_movement()
		self.assertFalse(doc.waits_for_stock_delivery())

	def test_the_assets_count_as_issued_at_once(self):
		doc = self.loan(items=[frappe._dict(name="CLI-9", qty=1)])
		with (
			patch.object(core, "transaction_is_received", return_value=False),
			patch.object(frappe, "db", MagicMock()),
		):
			doc.update_issued_quantities()
		self.assertEqual(doc.items[0].issued_qty, 1)


class ConversionCancelTest(unittest.TestCase):
	def doc(self, **values):
		doc = conversion_module.StocktoAssetConversion.__new__(conversion_module.StocktoAssetConversion)
		doc.__dict__.update(
			dict(
				doctype="Stock to Asset Conversion",
				name="STAC-1",
				borrowing="CL-1",
				borrowing_item="CLI-1",
				material_request=None,
				pick_list=None,
				company="CD",
			)
		)
		doc.__dict__.update(values)
		return doc

	def test_cancel_is_refused_while_the_asset_loan_is_active(self):
		db = MagicMock()
		db.get_value.return_value = "CL-2"
		with patch.object(frappe, "db", db), patch.object(frappe, "throw", side_effect=_throw):
			with self.assertRaises(Thrown):
				self.doc().validate_no_active_asset_loan()
		db.get_value.assert_called_once_with(
			"Borrowing", {"source_conversion": "STAC-1", "docstatus": 1}, "name"
		)

	def test_cancel_goes_on_without_an_asset_loan(self):
		db = MagicMock()
		db.get_value.return_value = None
		with patch.object(frappe, "db", db):
			self.doc().validate_no_active_asset_loan()

	def test_the_follow_up_check_sees_the_borrowing(self):
		db = MagicMock()
		db.sql.side_effect = lambda query, values: [[1]] if "tabBorrowing Item" in query else []
		with patch.object(frappe, "db", db), patch.object(frappe, "throw", side_effect=_throw):
			with self.assertRaises(Thrown) as raised:
				self.doc().validate_asset_has_no_follow_up_transactions("AST-1")
		self.assertIn("Borrowing", str(raised.exception))

	def test_a_loan_conversion_cannot_name_a_request(self):
		with patch.object(frappe, "throw", side_effect=_throw):
			with self.assertRaises(Thrown):
				self.doc(material_request="MR-1").validate_borrowing_source()

	def test_a_row_issued_in_another_unit_cannot_convert(self):
		loan = Doc(
			docstatus=1,
			loan_type="Stock",
			company="CD",
			customer_loan_warehouse="LOAN - CD",
			items=[frappe._dict(name="CLI-1", item_code="ITEM-1")],
		)
		row = frappe._dict(
			idx=1, item_code="ITEM-1", warehouse="LOAN - CD", qty=1, serial_and_batch_bundle=None
		)
		db = MagicMock()
		db.get_value.return_value = 12
		with (
			patch.object(frappe, "get_doc", return_value=loan),
			patch.object(frappe, "db", db),
			patch.object(frappe, "throw", side_effect=_throw),
			patch.object(core, "loan_item_available_to_adjust", return_value=5) as available,
		):
			with self.assertRaises(Thrown) as raised:
				self.doc(stock_items=[row]).validate_borrowing_source()
		self.assertIn("conversion factor 1", str(raised.exception))
		available.assert_not_called()

	def test_the_issue_factor_defaults_to_one(self):
		db = MagicMock()
		db.get_value.return_value = None
		with patch.object(frappe, "db", db):
			self.assertEqual(core.loan_issue_factor("CLI-1"), 1)


if __name__ == "__main__":
	unittest.main()
