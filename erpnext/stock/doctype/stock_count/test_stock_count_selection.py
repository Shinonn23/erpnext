# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_count import stock_count
from erpnext.stock.doctype.stock_count.stock_count import StockCount


def fake_db(**methods):
	db = Mock()
	for name, value in methods.items():
		if callable(value) and not isinstance(value, Mock):
			getattr(db, name).side_effect = value
		else:
			getattr(db, name).return_value = value
	return db


def rows(*codes):
	return [frappe._dict(idx=i + 1, item_code=code) for i, code in enumerate(codes)]


def count(**values):
	fields = {
		"count_type": "Monthly",
		"item_selection": "Selected Items",
		"count_template": None,
		"company": "C",
		"warehouse": "W",
		"snapshot_datetime": None,
		"selected_items": [],
		"flags": frappe._dict(),
		**values,
	}
	doc = frappe._dict(fields)
	doc.selects_items = lambda: StockCount.selects_items(doc)
	doc.get_selected_item_codes = lambda: StockCount.get_selected_item_codes(doc)
	doc.validate_count_template = lambda: StockCount.validate_count_template(doc)
	doc.get_doc_before_save = Mock(return_value=None)
	doc.set = lambda field, value: doc.__setitem__(field, value)
	doc.get_stock_ledger_groups = lambda snapshot: [frappe._dict(item_code=c) for c in doc.stock]
	return doc


class StockCountSelectionTest(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(stock_count, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError))

	def codes_with_stock(self, doc, enabled):
		doc.stock = enabled
		with patch.object(stock_count.frappe, "get_all", return_value=list(enabled)):
			return StockCount.get_item_codes_to_count(doc, "2026-10-29 10:00:00")

	def test_selected_items_counts_only_chosen_items_with_stock(self):
		doc = count(selected_items=rows("A", "B", "NOSTOCK"))
		self.assertEqual(self.codes_with_stock(doc, ["A", "B", "C", "D"]), ["A", "B"])
		self.assertEqual(doc.flags.not_counted_items, ["NOSTOCK"])

	def test_selected_items_with_no_stock_at_all_are_all_reported(self):
		doc = count(selected_items=rows("A", "B"))
		self.assertEqual(self.codes_with_stock(doc, []), [])
		self.assertEqual(doc.flags.not_counted_items, ["A", "B"])

	def test_random_sample_is_unchanged(self):
		doc = count(item_selection="Random Sample", item_count=2, selected_items=rows("A"))
		result = self.codes_with_stock(doc, ["A", "B", "C", "D"])
		self.assertEqual(len(result), 2)
		self.assertFalse(doc.flags.not_counted_items)

	def test_annual_counts_everything_even_with_a_stale_selection(self):
		doc = count(count_type="Annual", selected_items=rows("A"))
		self.assertEqual(self.codes_with_stock(doc, ["A", "B", "C"]), ["A", "B", "C"])

	def test_annual_selection_is_cleared(self):
		doc = count(count_type="Annual", count_template="T", selected_items=rows("A"))
		StockCount.validate_item_selection(doc)
		self.assertEqual(doc.item_selection, "Random Sample")
		self.assertIsNone(doc.count_template)
		self.assertEqual(doc.selected_items, [])

	def test_selected_items_need_at_least_one_row(self):
		with self.assertRaises(frappe.ValidationError):
			StockCount.validate_item_selection(count())

	def test_duplicate_disabled_and_non_stock_items_are_refused(self):
		item = {"A": {"is_stock_item": 1, "disabled": 0}, "OFF": {"is_stock_item": 1, "disabled": 1}}
		item["SVC"] = {"is_stock_item": 0, "disabled": 0}
		db = fake_db(get_value=lambda dt, name, f, as_dict: frappe._dict(item[name]))
		with patch.object(stock_count.frappe, "db", db):
			stock_count.validate_countable_item_rows(rows("A"))
			for codes in (("A", "A"), ("A", "OFF"), ("SVC",)):
				with self.assertRaises(frappe.ValidationError):
					stock_count.validate_countable_item_rows(rows(*codes))

	def test_template_company_warehouse_and_disabled_are_checked(self):
		doc = count(count_template="T")
		for template in (
			{"company": "OTHER", "warehouse": None, "disabled": 0},
			{"company": "C", "warehouse": "W2", "disabled": 0},
			{"company": "C", "warehouse": None, "disabled": 1},
			None,
		):
			value = frappe._dict(template) if template else None
			with patch.object(stock_count.frappe, "db", fake_db(get_value=value)):
				with self.assertRaises(frappe.ValidationError):
					StockCount.validate_count_template(doc)
		ok = frappe._dict(company="C", warehouse=None, disabled=0)
		with patch.object(stock_count.frappe, "db", fake_db(get_value=ok)):
			StockCount.validate_count_template(doc)
		ok.warehouse = "W"
		with patch.object(stock_count.frappe, "db", fake_db(get_value=ok)):
			StockCount.validate_count_template(doc)

	def test_selection_cannot_change_after_prepare(self):
		doc = count(selected_items=rows("A", "B"), snapshot_datetime="2026-10-29 10:00:00")
		doc.get_doc_before_save.return_value = frappe._dict(
			snapshot_datetime="2026-10-29 10:00:00",
			item_selection="Selected Items",
			count_template="",
			selected_items=rows("A", "B"),
		)
		StockCount.validate_item_selection(doc)
		doc.selected_items = rows("A")
		with self.assertRaises(frappe.ValidationError):
			StockCount.validate_item_selection(doc)
		doc.selected_items = rows("A", "B")
		doc.item_selection = "Random Sample"
		with self.assertRaises(frappe.ValidationError):
			StockCount.validate_item_selection(doc)

	def test_template_fills_an_empty_list_and_never_overwrites_chosen_rows(self):
		doc = count(count_template="T")
		doc.append = lambda field, row: doc.selected_items.append(frappe._dict(row))
		with (
			patch.object(
				stock_count.frappe,
				"db",
				fake_db(get_value=frappe._dict(company="C", warehouse=None, disabled=0)),
			),
			patch.object(stock_count.frappe, "get_all", return_value=["A", "B"]),
		):
			StockCount.copy_template_items(doc)
			self.assertEqual([r.item_code for r in doc.selected_items], ["A", "B"])
			doc.selected_items = rows("X")
			StockCount.copy_template_items(doc)
			self.assertEqual([r.item_code for r in doc.selected_items], ["X"])

	def test_variance_account_is_ignored_without_the_warehouse_field(self):
		db = fake_db(has_column=False, get_value="5130-02 - CD")
		with patch.object(stock_count.frappe, "db", db):
			self.assertIsNone(stock_count.get_count_variance_account("W"))
		db.get_value.assert_not_called()
		db.has_column.assert_called_with("Warehouse", "stock_adjustment_account")

	def test_variance_account_is_read_when_the_field_exists(self):
		for stored, expected in (("5130-02 - CD", "5130-02 - CD"), (None, None)):
			db = fake_db(has_column=True, get_value=stored)
			with patch.object(stock_count.frappe, "db", db):
				self.assertEqual(stock_count.get_count_variance_account("W"), expected)
		db.get_value.assert_called_with("Warehouse", "W", "stock_adjustment_account")
		with patch.object(stock_count.frappe, "db", fake_db(has_column=True)):
			self.assertIsNone(stock_count.get_count_variance_account(None))

	def test_only_monthly_counts_of_a_variance_warehouse_post_a_reconciliation(self):
		doc = count(count_type="Monthly")
		with patch.object(stock_count, "get_count_variance_account", return_value="5130-02 - CD"):
			self.assertTrue(StockCount.posts_variance(doc))
			doc.count_type = "Annual"
			self.assertFalse(StockCount.posts_variance(doc))
		doc.count_type = "Monthly"
		with patch.object(stock_count, "get_count_variance_account", return_value=None):
			self.assertFalse(StockCount.posts_variance(doc))

	def test_on_submit_chooses_the_voucher(self):
		for variance, monthly, expected in (
			(True, True, "reconciliation"),
			(False, True, "quarantine"),
			(True, False, "reconciliation"),
		):
			doc = count(count_type="Monthly" if monthly else "Annual")
			doc.posts_variance = lambda variance=variance: variance and monthly
			calls = []
			doc.make_quarantine_stock_entry = lambda calls=calls: calls.append("quarantine")
			doc.make_stock_reconciliation = lambda calls=calls: calls.append("reconciliation")
			doc.db_set = Mock()
			StockCount.on_submit(doc)
			self.assertEqual(calls, [expected])

	def test_make_count_template_checks_permission_and_state(self):
		doc = count(docstatus=0, selected_items=rows("A"))
		doc.check_permission = Mock()
		with (
			patch.object(stock_count.frappe, "get_doc", return_value=doc),
			patch.object(stock_count.frappe, "has_permission", side_effect=frappe.PermissionError),
		):
			with self.assertRaises(frappe.PermissionError):
				stock_count.make_count_template("SC", "Title")
		doc.check_permission.assert_called_with("read")

		doc.docstatus = 1
		with (
			patch.object(stock_count.frappe, "get_doc", return_value=doc),
			patch.object(stock_count.frappe, "has_permission", return_value=True),
		):
			with self.assertRaises(frappe.ValidationError):
				stock_count.make_count_template("SC", "Title")
			doc.docstatus = 0
			doc.selected_items = []
			with self.assertRaises(frappe.ValidationError):
				stock_count.make_count_template("SC", "Title")

	def test_make_count_template_copies_rows(self):
		doc = count(docstatus=0, selected_items=rows("A", "B"))
		doc.check_permission = Mock()
		created = Mock()
		created.name = "Monthly set"
		with (
			patch.object(stock_count.frappe, "has_permission", return_value=True),
			patch.object(stock_count.frappe, "get_doc", side_effect=[doc, created]) as get_doc,
		):
			self.assertEqual(stock_count.make_count_template("SC", "Monthly set"), "Monthly set")
		payload = get_doc.call_args_list[1].args[0]
		self.assertEqual(payload["items"], [{"item_code": "A"}, {"item_code": "B"}])
		self.assertEqual((payload["company"], payload["warehouse"]), ("C", "W"))
		created.insert.assert_called_once_with()


if __name__ == "__main__":
	unittest.main()
