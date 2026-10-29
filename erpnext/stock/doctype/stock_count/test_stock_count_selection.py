# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.stock_count import stock_count
from erpnext.stock.doctype.stock_count.stock_count import StockCount


class Doc(frappe._dict):
	"""A document stub whose `items` is the table, not dict.items."""

	@property
	def items(self):
		return self["items"]

	@items.setter
	def items(self, value):
		self["items"] = value


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
	doc.copy_template_items = lambda: StockCount.copy_template_items(doc)
	doc.append = lambda field, row: doc[field].append(frappe._dict(row))
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

	def test_only_a_monthly_count_prepared_with_a_variance_account_posts_a_reconciliation(self):
		doc = count(posts_variance_snapshot=1)
		self.assertTrue(StockCount.posts_variance(doc))
		doc.count_type = "Annual"
		self.assertFalse(StockCount.posts_variance(doc))
		doc.count_type = "Monthly"
		doc.posts_variance_snapshot = 0
		self.assertFalse(StockCount.posts_variance(doc))

	def test_variance_mode_is_fixed_at_prepare(self):
		doc = count(snapshot_datetime="2026-10-29 10:00:00", posts_variance_snapshot=0)
		doc.get_doc_before_save.return_value = frappe._dict(
			snapshot_datetime="2026-10-29 10:00:00",
			posts_variance_snapshot=1,
			variance_account_snapshot="5130-02 - CD",
		)
		StockCount.keep_variance_mode(doc)
		self.assertEqual(doc.posts_variance_snapshot, 1)
		self.assertEqual(doc.variance_account_snapshot, "5130-02 - CD")
		fresh = count(posts_variance_snapshot=1)
		fresh.variance_account_snapshot = "FORGED"
		StockCount.keep_variance_mode(fresh)
		self.assertEqual(fresh.posts_variance_snapshot, 0)
		self.assertIsNone(fresh.variance_account_snapshot)
		preparing = count(snapshot_datetime="2026-10-29 10:00:00", posts_variance_snapshot=1)
		StockCount.keep_variance_mode(preparing)
		self.assertEqual(preparing.posts_variance_snapshot, 1)

	def test_quarantine_warehouse_is_optional_only_when_the_count_posts_variance(self):
		doc = count(quarantine_warehouse=None)
		doc.validate_quarantine_warehouse = Mock(side_effect=frappe.ValidationError)
		doc.uses_variance = lambda: True
		StockCount.validate_quarantine_if_needed(doc)
		doc.uses_variance = lambda: False
		with self.assertRaises(frappe.ValidationError):
			StockCount.validate_quarantine_if_needed(doc)
		doc.uses_variance = lambda: True
		doc.quarantine_warehouse = "Q"
		with self.assertRaises(frappe.ValidationError):
			StockCount.validate_quarantine_if_needed(doc)

	def test_variance_account_must_be_an_enabled_leaf_expense_account_of_the_company(self):
		good = {"company": "C", "root_type": "Expense", "is_group": 0, "account_type": "", "disabled": 0}
		with patch.object(stock_count.frappe, "db", fake_db(get_value=frappe._dict(good))):
			stock_count.validate_variance_account("A", "C")
		for change in (
			{"company": "X"},
			{"root_type": "Asset"},
			{"is_group": 1},
			{"account_type": "Stock"},
			{"disabled": 1},
		):
			with patch.object(stock_count.frappe, "db", fake_db(get_value=frappe._dict(good, **change))):
				with self.assertRaises(frappe.ValidationError):
					stock_count.validate_variance_account("A", "C")
		with patch.object(stock_count.frappe, "db", fake_db(get_value=None)):
			with self.assertRaises(frappe.ValidationError):
				stock_count.validate_variance_account("A", "C")

	def test_reconciliation_uses_the_account_recorded_at_prepare_not_the_warehouse_now(self):
		doc = count(variance_account_snapshot="5130-02 - CD")
		reconciliation = frappe._dict(meta=Mock(has_field=lambda field: True))
		db = fake_db(has_column=True, get_value=lambda dt, name, field, *a, **kw: "Lab Dept")
		with (
			patch.object(stock_count, "get_count_variance_account", return_value="OTHER - CD") as live,
			patch.object(stock_count, "validate_variance_account") as validate,
			patch.object(stock_count.frappe, "db", db),
		):
			StockCount.set_variance_account(doc, reconciliation)
		live.assert_not_called()
		validate.assert_called_once_with("5130-02 - CD", "C")
		self.assertEqual(reconciliation.expense_account, "5130-02 - CD")
		self.assertEqual(reconciliation.department, "Lab Dept")

	def test_a_count_without_a_recorded_account_cannot_post_variance(self):
		doc = count(variance_account_snapshot=None)
		with self.assertRaises(frappe.ValidationError):
			StockCount.set_variance_account(doc, frappe._dict())

	def test_template_only_save_is_filled_on_the_server(self):
		doc = count(count_template="T", selected_items=[])
		ok = frappe._dict(company="C", warehouse=None, disabled=0)
		with (
			patch.object(stock_count.frappe, "db", fake_db(get_value=ok)),
			patch.object(stock_count.frappe, "get_all", return_value=["A", "B"]),
			patch.object(stock_count, "validate_countable_item_rows"),
		):
			StockCount.validate_item_selection(doc)
		self.assertEqual([r.item_code for r in doc.selected_items], ["A", "B"])

	def test_hand_chosen_rows_are_kept_when_a_template_is_set(self):
		doc = count(count_template="T", selected_items=rows("X"))
		ok = frappe._dict(company="C", warehouse=None, disabled=0)
		with (
			patch.object(stock_count.frappe, "db", fake_db(get_value=ok)),
			patch.object(stock_count.frappe, "get_all", return_value=["A", "B"]) as get_all,
			patch.object(stock_count, "validate_countable_item_rows"),
		):
			StockCount.validate_item_selection(doc)
		self.assertEqual([r.item_code for r in doc.selected_items], ["X"])
		get_all.assert_not_called()

	def test_a_bad_template_is_refused_by_validate(self):
		for template in (
			{"company": "OTHER", "warehouse": None, "disabled": 0},
			{"company": "C", "warehouse": None, "disabled": 1},
		):
			doc = count(count_template="T", selected_items=[])
			with patch.object(stock_count.frappe, "db", fake_db(get_value=frappe._dict(template))):
				with self.assertRaises(frappe.ValidationError):
					StockCount.validate_item_selection(doc)

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


def preparing(**values):
	doc = Doc(
		count(
			docstatus=0,
			status="Draft",
			items=[],
			item_count=0,
			quarantine_warehouse="Q",
			**values,
		)
	)
	doc.check_permission = Mock()
	doc.validate = Mock()
	doc.save = Mock()
	doc.get_item_codes_to_count = lambda snapshot: StockCount.get_item_codes_to_count(doc, snapshot)
	doc.validate_quarantine_if_needed = Mock()
	doc.append_snapshot_rows = lambda code, snapshot: doc["items"].append(frappe._dict(item_code=code))
	doc.stock = []
	return doc


class PrepareCountTest(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(stock_count, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError))
		self.enterContext(patch.object(stock_count, "get_count_variance_account", return_value=None))
		self.enterContext(patch.object(stock_count, "now_datetime", return_value="2026-10-29 10:00:00"))
		self.msgprint = self.enterContext(patch.object(stock_count.frappe, "msgprint"))

	def prepare(self, doc, with_stock):
		doc.stock = with_stock
		doc.get_stock_ledger_groups = lambda snapshot: [frappe._dict(item_code=c) for c in with_stock]
		with patch.object(stock_count.frappe, "get_all", return_value=list(with_stock)):
			return StockCount.prepare_count(doc)

	def test_skipped_items_are_reported_when_some_have_stock(self):
		doc = preparing(selected_items=rows("A", "GONE"))
		self.assertEqual(self.prepare(doc, ["A", "B"]), 1)
		message = self.msgprint.call_args.args[0]
		self.assertIn("GONE", message)
		self.assertIn("for the chosen filters", message)
		self.assertEqual(doc.status, "In Progress")
		self.assertEqual(doc.posts_variance_snapshot, 0)

	def test_nothing_to_count_throws_with_the_items(self):
		doc = preparing(selected_items=rows("GONE"))
		with self.assertRaises(frappe.ValidationError):
			self.prepare(doc, ["B"])
		self.msgprint.assert_not_called()

	def test_variance_mode_is_stored_at_prepare(self):
		doc = preparing(selected_items=rows("A"))
		with (
			patch.object(stock_count, "get_count_variance_account", return_value="5130-02 - CD"),
			patch.object(stock_count, "validate_variance_account") as validate,
		):
			self.prepare(doc, ["A"])
		validate.assert_called_once_with("5130-02 - CD", "C")
		self.assertEqual(doc.posts_variance_snapshot, 1)
		self.assertEqual(doc.variance_account_snapshot, "5130-02 - CD")


class StockCountTemplateTest(unittest.TestCase):
	def setUp(self):
		from erpnext.stock.doctype.stock_count_template import stock_count_template as module

		self.module = module
		self.enterContext(patch.object(module, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(module.frappe, "throw", side_effect=frappe.ValidationError))
		self.enterContext(patch.object(stock_count, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(stock_count.frappe, "throw", side_effect=frappe.ValidationError))

	def template(self, **values):
		return Doc({"title": "T", "company": "C", "warehouse": None, "items": rows("A"), **values})

	def validate(self, doc, warehouse=None):
		db = fake_db(get_value=warehouse)
		with (
			patch.object(self.module.frappe, "db", db),
			patch.object(stock_count, "validate_countable_item_rows") as rows_check,
		):
			self.module.StockCountTemplate.validate(doc)
		return rows_check

	def test_valid_template_checks_its_rows(self):
		self.validate(self.template()).assert_called_once()

	def test_title_and_items_are_required(self):
		for values in ({"title": "  "}, {"items": []}):
			with self.assertRaises(frappe.ValidationError):
				self.validate(self.template(**values))

	def test_warehouse_must_be_an_enabled_leaf_of_the_company(self):
		good = frappe._dict(company="C", is_group=0, disabled=0)
		self.validate(self.template(warehouse="W"), good)
		for change in ({"company": "X"}, {"is_group": 1}, {"disabled": 1}):
			with self.assertRaises(frappe.ValidationError):
				self.validate(self.template(warehouse="W"), frappe._dict(good, **change))
		with self.assertRaises(frappe.ValidationError):
			self.validate(self.template(warehouse="W"), None)


if __name__ == "__main__":
	unittest.main()
