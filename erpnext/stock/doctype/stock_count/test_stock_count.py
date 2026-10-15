# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import now_datetime

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_count.stock_count import (
	StockCount,
	allow_generated_stock_count_cancellation,
)
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.storage_location.storage_location import setup_storage_location_dimension
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.tests.utils import ERPNextTestSuite


class TestStockCount(ERPNextTestSuite):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_storage_location_dimension()

	def setUp(self):
		super().setUp()
		self.warehouse = create_warehouse("_Test Count Warehouse")
		self.quarantine_warehouse = create_warehouse("_Test Count Quarantine")
		self.locations = [
			frappe.get_doc(
				{
					"doctype": "Storage Location",
					"warehouse": self.warehouse,
					"location_code": f"COUNT-{frappe.generate_hash(length=6)}-{index}",
				}
			).insert()
			for index in (1, 2)
		]
		self.items = [make_item(f"_Test Count Item {frappe.generate_hash(length=8)}") for _ in range(2)]
		for item, location in zip(self.items, self.locations, strict=True):
			receipt = make_stock_entry(
				item_code=item.name,
				qty=5,
				rate=10,
				to_warehouse=self.warehouse,
				do_not_save=True,
			)
			receipt.items[0].to_storage_location = location.name
			receipt.insert().submit()

	def test_monthly_sample_is_limited_to_selected_storage_location(self):
		count = frappe.get_doc(
			{
				"doctype": "Stock Count",
				"company": frappe.db.get_value("Warehouse", self.warehouse, "company"),
				"count_type": "Monthly",
				"warehouse": self.warehouse,
				"dimensions": [
					{
						"inventory_dimension": "Storage Location",
						"reference_document": "Storage Location",
						"dimension_value": self.locations[0].name,
					}
				],
				"item_count": 10,
				"quarantine_warehouse": self.quarantine_warehouse,
			}
		)
		self.assertEqual(count.get_item_codes_to_count(now_datetime()), [self.items[0].name])
		count.append_snapshot_rows(self.items[0].name, now_datetime())
		self.assertEqual(len(count.items), 1)
		self.assertEqual(count.items[0].storage_location, self.locations[0].name)
		self.assertEqual(count.items[0].system_qty, 5)

	def test_count_lines_cannot_be_entered_manually_before_sampling(self):
		count = frappe.get_doc(
			{
				"doctype": "Stock Count",
				"company": frappe.db.get_value("Warehouse", self.warehouse, "company"),
				"count_type": "Annual",
				"warehouse": self.warehouse,
			}
		)
		count.append("items", {"item_code": self.items[0].name, "system_qty": 5, "counted_qty": 0})
		self.assertRaises(frappe.ValidationError, count.validate)


class TestStockCountSnapshot(unittest.TestCase):
	def test_expiry_is_copied_once_per_group_and_batchless_rows_stay_empty(self):
		for has_serial_no in (False, True):
			for batch_no, expiry_date in (("BATCH-A", "2027-01-31"), ("BATCH-B", None), (None, None)):
				with self.subTest(has_serial_no=has_serial_no, batch_no=batch_no):
					rows = []
					count = SimpleNamespace(
						warehouse="Count Warehouse",
						get_dimension_filters=lambda: (
							[frappe._dict(fieldname="storage_location", source_fieldname="storage_location")],
							{},
						),
						get_stock_ledger_groups=MagicMock(
							return_value=[
								frappe._dict(
									item_code="Count Item", batch_no=batch_no, qty=2, storage_location="SHELF"
								)
							]
						),
						append=lambda fieldname, row: rows.append(row),
					)
					with (
						patch.object(frappe, "get_cached_doc", return_value=frappe._dict(has_serial_no=has_serial_no)),
						patch.object(frappe, "get_cached_value", return_value=expiry_date) as get_expiry,
						patch(
							"erpnext.stock.doctype.stock_count.stock_count.get_serial_nos_based_on_posting_date",
							return_value=["SERIAL-1", "SERIAL-2"],
						) as get_serials,
					):
						StockCount.append_snapshot_rows(count, "Count Item", "2026-10-15 12:00:00")
					if batch_no:
						get_expiry.assert_called_once_with("Batch", batch_no, "expiry_date")
					else:
						get_expiry.assert_not_called()
					base_row = {
						"item_code": "Count Item",
						"batch_no": batch_no,
						"expiry_date": expiry_date,
						"system_qty": 2,
						"counted_qty": 0,
						"storage_location": "SHELF",
					}
					if has_serial_no:
						self.assertEqual(
							rows,
							[{**base_row, "serial_no": serial, "system_qty": 1} for serial in ("SERIAL-1", "SERIAL-2")],
						)
						get_serials.assert_called_once()
						self.assertEqual(get_serials.call_args.args[0].batch_no, batch_no)
						self.assertEqual(
							get_serials.call_args.args[0].inventory_dimensions_dict, {"storage_location": "SHELF"}
						)
					else:
						self.assertEqual(rows, [base_row])
						get_serials.assert_not_called()


class TestStockCountLineValidation(unittest.TestCase):
	def make_count(self, rows, dimensions=("storage_location",)):
		return SimpleNamespace(
			warehouse="Count Warehouse",
			items=[
				frappe._dict(
					{
						"idx": index,
						"item_code": "Count Item",
						"batch_no": None,
						"serial_no": None,
						"system_qty": 5,
						"counted_qty": 5,
						"is_counted": 1,
						**row,
					}
				)
				for index, row in enumerate(rows, 1)
			],
			get_countable_dimensions=lambda: [
				frappe._dict(source_fieldname=fieldname) for fieldname in dimensions
			],
		)

	def validate_lines(self, count):
		with (
			patch.object(
				frappe,
				"get_cached_doc",
				return_value=frappe._dict(is_stock_item=1, has_batch_no=0, has_serial_no=0),
			),
			patch.object(
				frappe, "db", SimpleNamespace(get_value=lambda *args, **kwargs: count.warehouse)
			),
		):
			StockCount.validate_count_lines(count)

	def test_same_item_in_two_locations_is_valid(self):
		count = self.make_count(
			[
				{"storage_location": "STAGING", "system_qty": 4, "counted_qty": 4},
				{"storage_location": "SHELF", "system_qty": 6, "counted_qty": 6},
			]
		)
		self.validate_lines(count)
		self.assertEqual([row.difference_qty for row in count.items], [0, 0])

	def test_duplicate_dimensions_are_rejected(self):
		count = self.make_count([{"storage_location": "STAGING"}, {"storage_location": "STAGING"}])
		with self.assertRaisesRegex(frappe.ValidationError, "duplicate Item"):
			self.validate_lines(count)

	def test_blank_dimensions_are_normalized(self):
		count = self.make_count([{"storage_location": None}, {"storage_location": ""}])
		with self.assertRaisesRegex(frappe.ValidationError, "duplicate Item"):
			self.validate_lines(count)

	def test_other_configured_dimensions_distinguish_rows(self):
		count = self.make_count(
			[
				{"storage_location": "STAGING", "project": "Project A"},
				{"storage_location": "STAGING", "project": "Project B"},
			],
			dimensions=("project", "storage_location"),
		)
		self.validate_lines(count)

	def test_serial_is_unique_across_locations_and_items(self):
		count = self.make_count(
			[
				{"storage_location": "STAGING", "serial_no": "SERIAL-1", "counted_qty": 1},
				{
					"storage_location": "SHELF",
					"item_code": "Other Item",
					"serial_no": "SERIAL-1",
					"counted_qty": 1,
				},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "listed more than once"):
			self.validate_lines(count)

	def test_scan_location_must_be_active_in_counted_warehouse(self):
		count = SimpleNamespace(warehouse="Count Warehouse", get=lambda fieldname: "SCAN-LOCATION")
		for location in (
			None,
			frappe._dict(warehouse="Other Warehouse", disabled=0),
			frappe._dict(warehouse="Count Warehouse", disabled=1),
		):
			with self.subTest(location=location), patch.object(
				frappe, "get_cached_value", return_value=location
			):
				with self.assertRaises(frappe.ValidationError):
					StockCount.validate(count)

	def test_current_quantity_uses_bundle_aware_groups_in_matching_dimensions(self):
		count = SimpleNamespace(
			get_countable_dimensions=lambda: [
				frappe._dict(fieldname="storage_location", source_fieldname="storage_location")
			],
			get_stock_ledger_groups=MagicMock(
				return_value=[
					frappe._dict(batch_no="BATCH-A", storage_location="STAGING", qty=4),
					frappe._dict(batch_no="BATCH-A", storage_location="SHELF", qty=6),
					frappe._dict(batch_no="BATCH-B", storage_location="SHELF", qty=10),
				]
			),
		)
		row = frappe._dict(item_code="Count Item", batch_no="BATCH-A", storage_location="SHELF")
		with patch(
			"erpnext.stock.doctype.stock_count.stock_count.now_datetime", return_value="2026-10-13 12:00:00"
		):
			self.assertEqual(StockCount.get_current_stock_qty(count, row), 6)
		count.get_stock_ledger_groups.assert_called_once_with("2026-10-13 12:00:00", row.item_code)

	def test_annual_reconciliation_documents_count_reason_and_remains_draft(self):
		count = SimpleNamespace(
			company="Count Company",
			warehouse="Count Warehouse",
			name="COUNT-001",
			snapshot_datetime="2026-10-13 12:00:00",
			items=[frappe._dict(item_code="Count Item", batch_no=None, system_qty=10, counted_qty=9)],
			get_row_dimension_values=lambda row: {},
		)
		for has_reason_field in (True, False):
			with self.subTest(has_reason_field=has_reason_field):
				reconciliation = MagicMock(docstatus=0)
				reconciliation.meta.has_field.return_value = has_reason_field
				with (
					patch.object(frappe, "new_doc", return_value=reconciliation),
					patch.object(frappe, "get_meta", return_value=MagicMock()),
					patch.object(frappe, "get_cached_doc", return_value=frappe._dict(has_serial_no=0)),
				):
					self.assertIs(StockCount.make_stock_reconciliation(count), reconciliation)
				reconciliation.insert.assert_called_once_with(ignore_permissions=True)
				reconciliation.submit.assert_not_called()
				self.assertEqual(reconciliation.docstatus, 0)
				self.assertEqual(reconciliation.stock_count, count.name)
				if has_reason_field:
					self.assertEqual(
						reconciliation.stock_adjustment_reason,
						"Physical count differences from Stock Count COUNT-001.",
					)
				else:
					self.assertNotIn("stock_adjustment_reason", reconciliation.__dict__)

	def test_generated_voucher_can_be_cancelled_before_its_count(self):
		for doctype, link_field in (
			("Stock Entry", "stock_entry"),
			("Stock Reconciliation", "stock_reconciliation"),
		):
			with self.subTest(doctype=doctype):
				doc = frappe._dict(
					doctype=doctype, name="VOUCHER-001", stock_count="COUNT-001", ignore_linked_doctypes=("GL Entry",)
				)
				get_value = MagicMock(return_value=doc.name)
				with patch.object(
					frappe, "db", SimpleNamespace(get_value=get_value, exists=MagicMock(return_value=False))
				):
					allow_generated_stock_count_cancellation(doc)
				get_value.assert_called_once_with("Stock Count", doc.stock_count, link_field)
				self.assertEqual(set(doc.ignore_linked_doctypes), {"GL Entry", "Stock Count"})

	def test_unrelated_voucher_keeps_stock_count_backlink_checks(self):
		for stock_count, parent_link in ((None, None), ("COUNT-001", None), ("COUNT-001", "OTHER-VOUCHER")):
			with self.subTest(stock_count=stock_count, parent_link=parent_link):
				doc = frappe._dict(
					doctype="Stock Entry", name="VOUCHER-001", stock_count=stock_count, ignore_linked_doctypes=("GL Entry",)
				)
				with patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: parent_link)):
					allow_generated_stock_count_cancellation(doc)
				self.assertEqual(doc.ignore_linked_doctypes, ("GL Entry",))

	def test_foreign_submitted_count_prevents_cancellation_exemption(self):
		for doctype, link_field in (
			("Stock Entry", "stock_entry"),
			("Stock Reconciliation", "stock_reconciliation"),
		):
			with self.subTest(doctype=doctype):
				doc = frappe._dict(
					doctype=doctype, name="VOUCHER-001", stock_count="COUNT-001", ignore_linked_doctypes=("GL Entry",)
				)
				exists = MagicMock(return_value="FOREIGN-COUNT")
				with patch.object(
					frappe, "db", SimpleNamespace(get_value=lambda *args: doc.name, exists=exists)
				):
					allow_generated_stock_count_cancellation(doc)
				exists.assert_called_once_with(
					"Stock Count", {link_field: doc.name, "docstatus": 1, "name": ["!=", doc.stock_count]}
				)
				self.assertEqual(doc.ignore_linked_doctypes, ("GL Entry",))

	def test_count_requires_generated_voucher_to_be_cancelled_first(self):
		for doctype, link_field in (
			("Stock Entry", "stock_entry"),
			("Stock Reconciliation", "stock_reconciliation"),
		):
			with self.subTest(doctype=doctype):
				count = SimpleNamespace(name="COUNT-001", stock_entry=None, stock_reconciliation=None)
				setattr(count, link_field, "VOUCHER-001")
				with patch.object(
					frappe, "get_doc", return_value=frappe._dict(docstatus=1, stock_count=count.name)
				):
					with self.assertRaisesRegex(frappe.ValidationError, "before cancelling this Stock Count"):
						StockCount.on_cancel(count)

	def test_unrelated_draft_voucher_is_never_deleted_on_count_cancellation(self):
		for link_field in ("stock_entry", "stock_reconciliation"):
			with self.subTest(link_field=link_field):
				count = SimpleNamespace(name="COUNT-001", stock_entry=None, stock_reconciliation=None)
				setattr(count, link_field, "UNRELATED-VOUCHER")
				with (
					patch.object(frappe, "get_doc", return_value=frappe._dict(docstatus=0, stock_count="OTHER-COUNT")),
					patch.object(frappe, "delete_doc") as delete_doc,
				):
					with self.assertRaisesRegex(frappe.ValidationError, "was not generated by this Stock Count"):
						StockCount.on_cancel(count)
					delete_doc.assert_not_called()

	def test_generated_links_cannot_be_manually_assigned(self):
		for link_field in ("stock_entry", "stock_reconciliation"):
			for previous in (None, frappe._dict(stock_entry=None, stock_reconciliation=None)):
				with self.subTest(link_field=link_field, previous=previous):
					values = {link_field: "MANUAL-VOUCHER"}
					count = SimpleNamespace(get=values.get, get_doc_before_save=lambda: previous)
					with self.assertRaisesRegex(frappe.ValidationError, "can only be set by Stock Count submission"):
						StockCount.validate_generated_links(count)

	def test_submitted_generated_links_cannot_be_replaced_or_cleared(self):
		for link_field in ("stock_entry", "stock_reconciliation"):
			for value in ("OTHER-VOUCHER", None):
				with self.subTest(link_field=link_field, value=value):
					previous = frappe._dict({link_field: "GENERATED-VOUCHER"})
					values = {link_field: value}
					count = SimpleNamespace(get=values.get, get_doc_before_save=lambda: previous)
					count.validate_generated_links = lambda: StockCount.validate_generated_links(count)
					with self.assertRaisesRegex(frappe.ValidationError, "can only be set by Stock Count submission"):
						StockCount.before_update_after_submit(count)

	def test_unchanged_generated_links_remain_valid(self):
		values = frappe._dict(stock_entry="GENERATED-VOUCHER", stock_reconciliation=None)
		count = SimpleNamespace(get=values.get, get_doc_before_save=lambda: values.copy())
		StockCount.validate_generated_links(count)
