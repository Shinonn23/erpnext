"""Offline per-Asset reissue guards; SQLite fixtures exercise the actual native SQL predicates."""

import sqlite3
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.material_request import material_request as requests
from erpnext.borrowing.doctype.borrowing import borrowing_core as core


class Record(SimpleNamespace):
	def get(self, field, default=None):
		return getattr(self, field, default)


class TestAssetReissue(TestCase):
	def setUp(self):
		self.sql = sqlite3.connect(":memory:")
		self.addCleanup(self.sql.close)
		self.sql.row_factory = sqlite3.Row
		self.sql.executescript("""
			create table `tabAsset` (name text, company text, item_code text, status text, location text,
				custodian text, docstatus integer, is_demo_asset integer, demo_loan_status text,
				asset_name text, asset_serial_number text);
			create table `tabItem` (name text, is_fixed_asset integer, stock_uom text);
			create table `tabBorrowing` (name text, material_request text, docstatus integer, creation text, status text, loan_type text);
			create table `tabBorrowing Item` (name text, parent text, asset text, material_request_item text, qty real, issued_qty real);
			create table `tabDelivery Note` (name text, borrowing text, is_return integer, is_borrowing_transaction integer,
				docstatus integer);
			create table `tabDelivery Note Item` (name text, parent text, borrowing_item text, qty real);
			create table `tabAsset Movement` (name text, docstatus integer, purpose text, borrowing text,
				borrowing_source_doctype text, borrowing_source_name text);
			create table `tabAsset Movement Item` (name text, parent text, borrowing_item text, asset text);
			create table `tabMaterial Request` (name text, docstatus integer, material_request_type text, borrow_item_type text,
				request_progress_status text, asset_transfer_status text);
			create table `tabMaterial Request Item` (name text, parent text, asset text);
			insert into `tabAsset` values ('ASSET-A','CD','MODEL','Submitted','Stock Room',null,1,1,'Available','Asset A','SERIAL-A');
			insert into `tabAsset` values ('ASSET-B','CD','MODEL','Submitted','Customer Site',null,1,1,'With Customer','Asset B','SERIAL-B');
			insert into `tabItem` values ('MODEL',1,'Unit');
			insert into `tabBorrowing` values ('OLD-LOAN','OLD-MR',1,'2026-01-01','Partially Returned','Asset');
			insert into `tabBorrowing Item` values ('OLD-A','OLD-LOAN','ASSET-A','OLD-MR-A',1,1);
			insert into `tabBorrowing Item` values ('OLD-B','OLD-LOAN','ASSET-B','OLD-MR-B',1,1);
			insert into `tabAsset Movement` values ('RECEIPT-A',1,'Receipt','OLD-LOAN','Borrowing','OLD-LOAN');
			insert into `tabAsset Movement Item` values ('RECEIPT-A-ROW','RECEIPT-A','OLD-A','ASSET-A');
			insert into `tabMaterial Request` values ('OLD-MR',1,'Customer Demo',null,'Partially Returned',null);
			insert into `tabMaterial Request Item` values ('OLD-MR-A','OLD-MR','ASSET-A');
			insert into `tabMaterial Request Item` values ('OLD-MR-B','OLD-MR','ASSET-B');
		""")
		self.db = Mock()
		self.db.sql.side_effect = self.run_sql
		self.db.get_value.side_effect = self.get_value
		self.row = Record(
			name="NEW-LOAN-A",
			idx=1,
			material_request_item="NEW-MR-A",
			item_code="MODEL",
			qty=1,
			asset="ASSET-A",
			target_location="New Customer",
		)
		self.request_row = Record(
			name="NEW-MR-A",
			parent="NEW-MR",
			asset="ASSET-A",
			item_code="MODEL",
			qty=1,
			source_location="Stock Room",
		)
		self.request = Record(name="NEW-MR", items=[self.request_row])
		self.loan = Record(
			name="NEW-LOAN",
			company="CD",
			material_request="NEW-MR",
			items=[self.row],
			loan_type="Asset",
			borrower_type="Customer",
			recipient_location="New Customer",
		)
		for context in (
			patch.object(frappe, "db", self.db),
			patch.object(frappe, "get_meta", return_value=Mock(has_field=Mock(return_value=False))),
			patch.object(frappe, "throw", side_effect=ValueError),
			patch.object(frappe, "get_cached_doc", return_value=self.request),
			patch.object(core, "_", lambda text: text),
			patch.object(requests, "_", lambda text: text),
		):
			context.start()
			self.addCleanup(context.stop)

	def run_sql(self, statement, values=(), as_dict=False, **kwargs):
		statement = statement.replace("for update", "").replace("%s", "?")
		if not isinstance(values, (list, tuple)):
			values = (values,)
		rows = self.sql.execute(statement, values).fetchall()
		return [frappe._dict(dict(row)) if as_dict else tuple(row) for row in rows]

	def get_value(self, doctype, name, fields, as_dict=False, **kwargs):
		columns = fields if isinstance(fields, list) else [fields]
		row = self.sql.execute(
			f"select {', '.join(columns)} from `tab{doctype}` where name = ?", (name,)
		).fetchone()
		if not row:
			return None
		return frappe._dict(dict(row)) if as_dict else row[0] if len(columns) == 1 else tuple(row)

	def active_request(self, asset):
		return requests.CustomerMaterialRequest.has_active_asset_request(Record(name="NEW-MR"), asset)

	def test_received_a_is_available_while_b_is_still_out(self):
		self.assertFalse(core.asset_has_unreturned_borrowing("ASSET-A"))
		self.assertTrue(core.asset_has_unreturned_borrowing("ASSET-B"))
		self.assertFalse(self.active_request("ASSET-A"))
		self.assertTrue(self.active_request("ASSET-B"))
		core.Borrowing.validate_items(self.loan)
		self.assertEqual(self.row.source_location, "Stock Room")

	def test_draft_physical_receipt_does_not_release_a_even_if_header_is_received(self):
		self.sql.execute("update `tabAsset Movement` set docstatus=0 where name='RECEIPT-A'")
		self.assertTrue(core.asset_has_unreturned_borrowing("ASSET-A"))
		self.assertTrue(self.active_request("ASSET-A"))
		with self.assertRaises(ValueError):
			core.Borrowing.validate_items(self.loan)

	def test_pending_or_cancelled_return_does_not_release_a(self):
		for sql in (
			"update `tabAsset Movement` set docstatus=0 where name='RECEIPT-A'",
			"update `tabAsset Movement` set docstatus=2 where name='RECEIPT-A'",
		):
			self.sql.execute(sql)
			self.assertTrue(core.asset_has_unreturned_borrowing("ASSET-A"))
			self.assertTrue(self.active_request("ASSET-A"))

	def test_a_return_of_another_row_or_an_issue_movement_never_releases_a(self):
		for sql in (
			"update `tabAsset Movement Item` set borrowing_item='OLD-B'",
			"update `tabAsset Movement` set purpose='Transfer'",
		):
			self.sql.execute(sql)
			self.assertTrue(core.asset_has_unreturned_borrowing("ASSET-A"))
			self.assertTrue(self.active_request("ASSET-A"))

	def test_closed_loss_or_sale_is_not_a_received_return(self):
		self.sql.execute("delete from `tabAsset Movement Item`")
		self.sql.execute("update `tabBorrowing` set status='Closed'")
		self.assertTrue(core.asset_has_unreturned_borrowing("ASSET-A"))
		with self.assertRaises(ValueError):
			core.Borrowing.validate_items(self.loan)

	def test_submitted_but_unissued_allocation_is_not_available(self):
		self.sql.execute("update `tabBorrowing Item` set issued_qty=0 where name='OLD-A'")
		self.assertTrue(core.asset_has_unreturned_borrowing("ASSET-A"))
		self.assertTrue(self.active_request("ASSET-A"))

	def test_unissued_approved_request_and_pending_asset_transfer_keep_reservation(self):
		self.sql.execute("delete from `tabBorrowing`")
		self.assertTrue(self.active_request("ASSET-A"))
		self.sql.execute(
			"update `tabMaterial Request` set material_request_type='Asset Transfer', asset_transfer_status='Pending'"
		)
		self.assertTrue(self.active_request("ASSET-A"))

	def test_guard_uses_current_reads_under_asset_lock_and_excludes_current_loan(self):
		self.assertFalse(core.asset_has_unreturned_borrowing("ASSET-B", exclude_loan="OLD-LOAN"))
		calls = self.db.sql.call_args_list
		self.assertIn("`tabAsset`", calls[0].args[0])
		self.assertIn("for update", calls[0].args[0])
		self.assertIn("for update", calls[1].args[0])
		self.assertEqual(calls[1].args[1], ("ASSET-B", "OLD-LOAN"))

	def test_cancelled_loan_does_not_block_reissue(self):
		self.sql.execute("update `tabBorrowing` set docstatus=2")
		self.assertFalse(core.asset_has_unreturned_borrowing("ASSET-A"))

	def test_repair_status_stays_unavailable_in_native_loan_guard(self):
		for status in ("In Maintenance", "Out of Order"):
			self.sql.execute("update `tabAsset` set status=? where name='ASSET-A'", (status,))
			with self.assertRaises(ValueError):
				core.Borrowing.validate_items(self.loan)

	def test_native_asset_picker_releases_only_received_a(self):
		self.sql.execute("update `tabAsset` set is_demo_asset=0 where name='ASSET-A'")
		assets = requests.get_available_assets("MODEL", "CD", demo_only=True)
		self.assertEqual([row.name for row in assets], ["ASSET-A"])
		self.sql.execute("update `tabAsset Movement` set docstatus=0 where name='RECEIPT-A'")
		self.assertEqual(requests.get_available_assets("MODEL", "CD", demo_only=True), [])

	def test_native_demo_status_is_already_derived_from_each_assets_receipt(self):
		from erpnext.assets.doctype.asset_movement.asset_movement import AssetMovement

		movement = Record(
			material_request_name="OLD-MR", assets=[Record(asset="ASSET-A"), Record(asset="ASSET-B")]
		)

		def latest(_sql, asset, **kwargs):
			return [
				frappe._dict(
					purpose="Receipt" if asset == "ASSET-A" else "Transfer",
					customer="CUSTOMER",
					reference_doctype="Material Request",
					reference_name="OLD-MR",
				)
			]

		with (
			patch.object(self.db, "get_value", return_value="Customer Demo"),
			patch.object(self.db, "sql", side_effect=latest),
		):
			AssetMovement.update_demo_loan_status(movement)
		self.db.set_value.assert_any_call("Asset", "ASSET-A", "demo_loan_status", "Available")
		self.db.set_value.assert_any_call("Asset", "ASSET-B", "demo_loan_status", "With Customer")

	def test_existing_stock_balances_keep_received_adjusted_and_sold_separate(self):
		with (
			patch.object(core, "return_rows", return_value=[frappe._dict(qty=1)]),
			patch.object(core, "loan_conversion_rows", return_value=[]),
			patch.object(frappe, "get_all", side_effect=[["ADJUSTMENT"], [1]]),
			patch.object(self.db, "sql", return_value=[(2,)]),
		):
			self.assertEqual(core.get_loan_item_balances("STOCK-LOAN", "STOCK-ROW"), (1, 1, 2))
