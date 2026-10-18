"""Unit tests for the patch that retires the Item Thai FDA Assessment Status (mocks only, no database)."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import frappe

from erpnext.patches.v16_0 import remove_thai_fda_assessment_status as removal
from erpnext.regulatory_affairs.custom_fields import CUSTOM_FIELDS

STATUS_FIELD = frappe._dict(
	name="Item-custom_thai_fda_applicability", fieldname="custom_thai_fda_applicability"
)
NOTE_FIELD = frappe._dict(
	name="Item-custom_thai_fda_assessment_note", fieldname="custom_thai_fda_assessment_note"
)


class TestKeepRequiredItemsRegulated(unittest.TestCase):
	def keep(self, items):
		db = MagicMock()
		db.sql.side_effect = [[{"name": item} for item in items], None]
		with (
			patch.object(removal.frappe, "db", db),
			patch("erpnext.regulatory_affairs.install.ensure_custom_fields") as ensure_custom_fields,
			patch("builtins.print") as printed,
		):
			result = removal.keep_required_items_regulated()
		return result, db, ensure_custom_fields, printed

	def test_required_items_without_a_category_become_other_not_specified(self):
		result, db, ensure_custom_fields, printed = self.keep(["ITEM-1", "ITEM-2"])
		self.assertEqual(result, ["ITEM-1", "ITEM-2"])
		ensure_custom_fields.assert_called_once_with()
		select, params = db.sql.call_args_list[0].args
		self.assertIn("`custom_thai_fda_applicability` in %(required)s", select)
		self.assertIn("ifnull(`regulatory_product_category`, '') = ''", select)
		self.assertEqual(params["required"], ("Registration Required", "ต้องมีทะเบียน"))
		update, values = db.sql.call_args_list[1].args
		self.assertTrue(
			update.startswith("update `tabItem` set `regulatory_product_category` = %(category)s")
		)
		self.assertEqual(
			values, {"category": "Other", "other": "Not specified (legacy)", "items": ("ITEM-1", "ITEM-2")}
		)
		self.assertIn("2 Item(s)", printed.call_args.args[0])

	def test_nothing_to_convert_writes_nothing(self):
		result, db, _, printed = self.keep([])
		self.assertEqual(result, [])
		self.assertEqual(db.sql.call_count, 1)
		printed.assert_not_called()


class TestReportExemptItemsWithCategory(unittest.TestCase):
	def report(self, count):
		db = MagicMock()
		db.sql.return_value = ((count,),)
		with patch.object(removal.frappe, "db", db), patch("builtins.print") as printed:
			result = removal.report_exempt_items_with_category()
		return result, db, printed

	def test_exempt_items_with_a_category_are_counted_and_printed(self):
		result, db, printed = self.report(3)
		self.assertEqual(result, 3)
		query, params = db.sql.call_args.args
		self.assertIn("`custom_thai_fda_applicability` in %(exempt)s", query)
		self.assertIn("ifnull(`regulatory_product_category`, '') != ''", query)
		self.assertEqual(params["exempt"], ("Not Required / Exempt", "ไม่ต้องมี/ได้รับยกเว้น"))
		self.assertIn("3 Item(s) assessed as Not Required / Exempt", printed.call_args.args[0])
		db.sql.assert_called_once()  # it only reads

	def test_no_such_item_prints_nothing(self):
		result, _, printed = self.report(0)
		self.assertEqual(result, 0)
		printed.assert_not_called()


class TestExecute(unittest.TestCase):
	def execute(self, has_column=True, custom_fields=(STATUS_FIELD, NOTE_FIELD)):
		db = MagicMock()
		db.has_column.return_value = has_column
		with (
			patch.object(removal.frappe, "db", db),
			patch.object(removal.frappe, "get_all", return_value=list(custom_fields)) as get_all,
			patch.object(removal.frappe, "delete_doc") as delete_doc,
			patch.object(removal.frappe, "clear_cache") as clear_cache,
			patch.object(removal, "keep_required_items_regulated") as keep,
			patch.object(removal, "report_exempt_items_with_category") as report,
		):
			removal.execute()
		return db, get_all, delete_doc, clear_cache, keep, report

	def test_converts_then_deletes_both_custom_fields(self):
		db, get_all, delete_doc, clear_cache, keep, report = self.execute()
		db.has_column.assert_called_once_with("Item", "custom_thai_fda_applicability")
		keep.assert_called_once_with()
		report.assert_called_once_with()
		self.assertEqual(
			get_all.call_args.kwargs["filters"],
			{
				"dt": "Item",
				"fieldname": ("in", ("custom_thai_fda_applicability", "custom_thai_fda_assessment_note")),
			},
		)
		self.assertEqual(
			delete_doc.call_args_list,
			[
				call("Custom Field", field.name, ignore_missing=True, force=True)
				for field in (STATUS_FIELD, NOTE_FIELD)
			],
		)
		clear_cache.assert_called_once_with(doctype="Item")
		# The columns and their values stay: nothing is dropped.
		db.sql.assert_not_called()
		db.sql_ddl.assert_not_called()

	def test_rerun_after_the_status_field_is_gone_converts_nothing(self):
		# A cleared category must not come back: once the status Custom Field is deleted it no longer decides.
		_, _, delete_doc, _, keep, report = self.execute(custom_fields=(NOTE_FIELD,))
		keep.assert_not_called()
		report.assert_not_called()
		delete_doc.assert_called_once_with("Custom Field", NOTE_FIELD.name, ignore_missing=True, force=True)
		_, _, delete_doc, _, keep, _ = self.execute(custom_fields=())
		keep.assert_not_called()
		delete_doc.assert_not_called()

	def test_status_field_without_its_column_converts_nothing(self):
		_, _, delete_doc, _, keep, report = self.execute(has_column=False)
		keep.assert_not_called()
		report.assert_not_called()
		self.assertEqual(delete_doc.call_count, 2)


class TestRegistration(unittest.TestCase):
	def test_patch_runs_right_after_the_merge_patch(self):
		lines = (Path(removal.__file__).resolve().parents[2] / "patches.txt").read_text().splitlines()
		merge = lines.index("erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs")
		self.assertEqual(lines.index("erpnext.patches.v16_0.remove_thai_fda_assessment_status"), merge + 1)

	def test_regulatory_affairs_does_not_create_the_deleted_fields_again(self):
		fieldnames = {spec["fieldname"] for specs in CUSTOM_FIELDS.values() for spec in specs}
		self.assertFalse(fieldnames & set(removal.LEGACY_FIELDS))
