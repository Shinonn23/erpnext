import copy
import re
import unittest
from unittest.mock import Mock, patch

from erpnext.patches.v16_0 import translate_thai_fda_to_english as migration


class TestThaiFDAEnglishMigration(unittest.TestCase):
	def make_db(self, rows, tables=None, has_column=True):
		def sql(query, values):
			doctype, fieldname = re.fullmatch(
				r"update `tab(.+)` set `(.+)` = %s where `.+` = %s", query
			).groups()
			new_value, old_value = values
			for row in rows[doctype]:
				if row.get(fieldname) == old_value:
					row[fieldname] = new_value

		db = Mock()
		db.table_exists.side_effect = lambda doctype: doctype in (tables if tables is not None else rows)
		db.has_column.return_value = has_column
		db.sql.side_effect = sql
		return db

	def test_translates_select_values_and_preserves_business_data_on_rerun(self):
		rows = {
			"Item": [
				{
					"custom_thai_fda_applicability": "ต้องมีทะเบียน",
					"custom_thai_fda_assessment_note": "เอกสารจากลูกค้า",
					"item_name": "อาหาร",
					"modified": "2026-10-01",
				},
				{"custom_thai_fda_applicability": "Not Assessed"},
				{"custom_thai_fda_applicability": ""},
			],
			"Thai FDA Registration": [
				{
					"regulatory_category": "อาหาร",
					"status": "มีผล",
					"activity": "นำเข้า",
					"expiry_basis": "เอกสารไม่ระบุวันหมดอายุ",
					"renewal_status": "ยื่นแล้ว",
					"attachment": "/private/files/อย.pdf",
					"registration_number": "อย-123",
					"remarks": "รออนุมัติ",
				}
			],
			"Thai FDA Renewal Log": [{"stage": "ไม่อนุมัติ/ยุติ", "notes": "ยื่นแล้ว"}],
			"Thai FDA Reminder Log": [{"status": "รอส่งซ้ำ", "error_message": "ผิดพลาด"}],
		}
		expected = copy.deepcopy(rows)
		expected["Item"][0]["custom_thai_fda_applicability"] = "Registration Required"
		expected["Thai FDA Registration"][0].update(
			regulatory_category="Food",
			status="Active",
			activity="Import",
			expiry_basis="No Expiry Date Specified",
			renewal_status="Submitted",
		)
		expected["Thai FDA Renewal Log"][0]["stage"] = "Rejected / Discontinued"
		expected["Thai FDA Reminder Log"][0]["status"] = "Pending Retry"

		with patch.object(migration.frappe, "db", self.make_db(rows)):
			migration.execute()
			self.assertEqual(rows, expected)
			migration.execute()
			self.assertEqual(rows, expected)

	def test_works_when_the_legacy_doctypes_are_gone(self):
		"""Only Item remains: no DocType reload and no dependency on the retired definitions."""
		rows = {"Item": [{"custom_thai_fda_applicability": "ยังไม่ประเมิน"}]}
		db = self.make_db(rows)
		with (
			patch.object(migration.frappe, "db", db),
			patch.object(migration.frappe, "reload_doc") as reload_doc,
		):
			migration.execute()
		self.assertEqual(rows["Item"][0]["custom_thai_fda_applicability"], "Not Assessed")
		reload_doc.assert_not_called()
		self.assertFalse(hasattr(migration, "ERP_CUSTOM_FIELDS"))

	def test_skips_missing_tables_and_columns(self):
		for tables_exist in (False, True):
			with self.subTest(tables_exist=tables_exist):
				db = Mock()
				db.table_exists.return_value = tables_exist
				db.has_column.return_value = False
				with patch.object(migration.frappe, "db", db):
					migration.execute()
				db.sql.assert_not_called()
