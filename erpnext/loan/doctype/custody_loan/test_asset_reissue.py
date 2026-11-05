"""Offline per-Asset reissue guards; SQLite fixtures exercise the actual native SQL predicates."""

import sqlite3
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.material_request import material_request as requests
from erpnext.loan.doctype.custody_loan import custody_loan_core as core


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
			create table `tabCustody Loan` (name text, material_request text, docstatus integer, creation text, status text, loan_type text);
			create table `tabCustody Loan Item` (name text, parent text, asset text, material_request_item text, qty real, issued_qty real);
			create table `tabCustody Loan Return` (name text, custody_loan text, docstatus integer, status text, loan_type text, asset_movement text);
			create table `tabCustody Loan Return Item` (name text, parent text, custody_loan_item text, returned_qty real);
			create table `tabAsset Movement` (name text, docstatus integer, purpose text, custody_loan text,
				custody_loan_source_doctype text, custody_loan_source_name text);
			create table `tabMaterial Request` (name text, docstatus integer, material_request_type text, borrow_item_type text,
				request_progress_status text, asset_transfer_status text);
			create table `tabMaterial Request Item` (name text, parent text, asset text);
			insert into `tabAsset` values ('ASSET-A','CD','MODEL','Submitted','Stock Room',null,1,1,'Available','Asset A','SERIAL-A');
			insert into `tabAsset` values ('ASSET-B','CD','MODEL','Submitted','Customer Site',null,1,1,'With Customer','Asset B','SERIAL-B');
			insert into `tabItem` values ('MODEL',1,'Unit');
			insert into `tabCustody Loan` values ('OLD-LOAN','OLD-MR',1,'2026-01-01','Partially Returned','Asset');
			insert into `tabCustody Loan Item` values ('OLD-A','OLD-LOAN','ASSET-A','OLD-MR-A',1,1);
			insert into `tabCustody Loan Item` values ('OLD-B','OLD-LOAN','ASSET-B','OLD-MR-B',1,1);
			insert into `tabCustody Loan Return` values ('RETURN-A','OLD-LOAN',1,'Received','Asset','RECEIPT-A');
			insert into `tabCustody Loan Return Item` values ('RETURN-A-ROW','RETURN-A','OLD-A',1);
			insert into `tabAsset Movement` values ('RECEIPT-A',1,'Receipt','OLD-LOAN','Custody Loan Return','RETURN-A');
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
		self.assertFalse(core.asset_has_unreturned_custody_loan("ASSET-A"))
		self.assertTrue(core.asset_has_unreturned_custody_loan("ASSET-B"))
		self.assertFalse(self.active_request("ASSET-A"))
		self.assertTrue(self.active_request("ASSET-B"))
		core.CustodyLoan.validate_items(self.loan)
		self.assertEqual(self.row.source_location, "Stock Room")

	def test_draft_physical_receipt_does_not_release_a_even_if_header_is_received(self):
		self.sql.execute("update `tabAsset Movement` set docstatus=0 where name='RECEIPT-A'")
		self.assertTrue(core.asset_has_unreturned_custody_loan("ASSET-A"))
		self.assertTrue(self.active_request("ASSET-A"))
		with self.assertRaises(ValueError):
			core.CustodyLoan.validate_items(self.loan)

	def test_pending_or_cancelled_return_does_not_release_a(self):
		for sql in (
			"update `tabCustody Loan Return` set status='Pending Receipt' where name='RETURN-A'",
			"update `tabCustody Loan Return` set status='Received', docstatus=2 where name='RETURN-A'",
		):
			self.sql.execute(sql)
			self.assertTrue(core.asset_has_unreturned_custody_loan("ASSET-A"))
			self.assertTrue(self.active_request("ASSET-A"))

	def test_zero_or_partial_received_quantities_never_release_a(self):
		for qty in (0, 0.5):
			self.sql.execute("update `tabCustody Loan Return Item` set returned_qty=?", (qty,))
			self.assertTrue(core.asset_has_unreturned_custody_loan("ASSET-A"))
			self.assertTrue(self.active_request("ASSET-A"))

	def test_closed_loss_or_sale_is_not_a_received_return(self):
		self.sql.execute("delete from `tabCustody Loan Return Item`")
		self.sql.execute("update `tabCustody Loan` set status='Closed'")
		self.assertTrue(core.asset_has_unreturned_custody_loan("ASSET-A"))
		with self.assertRaises(ValueError):
			core.CustodyLoan.validate_items(self.loan)

	def test_submitted_but_unissued_allocation_is_not_available(self):
		self.sql.execute("update `tabCustody Loan Item` set issued_qty=0 where name='OLD-A'")
		self.assertTrue(core.asset_has_unreturned_custody_loan("ASSET-A"))
		self.assertTrue(self.active_request("ASSET-A"))

	def test_unissued_approved_request_and_pending_asset_transfer_keep_reservation(self):
		self.sql.execute("delete from `tabCustody Loan`")
		self.assertTrue(self.active_request("ASSET-A"))
		self.sql.execute(
			"update `tabMaterial Request` set material_request_type='Asset Transfer', asset_transfer_status='Pending'"
		)
		self.assertTrue(self.active_request("ASSET-A"))

	def test_guard_uses_current_reads_under_asset_lock_and_excludes_current_loan(self):
		self.assertFalse(core.asset_has_unreturned_custody_loan("ASSET-B", exclude_loan="OLD-LOAN"))
		calls = self.db.sql.call_args_list
		self.assertIn("`tabAsset`", calls[0].args[0])
		self.assertIn("for update", calls[0].args[0])
		self.assertIn("for update", calls[1].args[0])
		self.assertEqual(calls[1].args[1], ("ASSET-B", "OLD-LOAN"))

	def test_cancelled_loan_does_not_block_reissue(self):
		self.sql.execute("update `tabCustody Loan` set docstatus=2")
		self.assertFalse(core.asset_has_unreturned_custody_loan("ASSET-A"))

	def test_repair_status_stays_unavailable_in_native_loan_guard(self):
		for status in ("In Maintenance", "Out of Order"):
			self.sql.execute("update `tabAsset` set status=? where name='ASSET-A'", (status,))
			with self.assertRaises(ValueError):
				core.CustodyLoan.validate_items(self.loan)

	def test_native_asset_picker_releases_only_received_a(self):
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
			patch.object(frappe, "get_all", side_effect=[["STOCK-RETURN"], [1], ["ADJUSTMENT"], [1]]),
			patch.object(self.db, "sql", return_value=[(2,)]),
		):
			self.assertEqual(core.get_loan_item_balances("STOCK-LOAN", "STOCK-ROW"), (1, 1, 2))
