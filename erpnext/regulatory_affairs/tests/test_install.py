import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs import install


class TestEnsureItemAssessmentDefault(unittest.TestCase):
	def run_with(self, has_column):
		db = MagicMock()
		db.has_column.return_value = has_column
		with patch.object(frappe, "db", db):
			install.ensure_item_assessment_default()
		return db

	def test_fills_blank_and_null_status_with_not_assessed_in_one_update(self):
		db = self.run_with(True)
		db.has_column.assert_called_once_with("Item", "custom_thai_fda_applicability")
		db.sql.assert_called_once()
		query, values = db.sql.call_args.args
		self.assertTrue(query.startswith("update `tabItem` set `custom_thai_fda_applicability` = %s"))
		self.assertIn("where ifnull(`custom_thai_fda_applicability`, '') = ''", query)
		self.assertEqual(values, ("Not Assessed",))

	def test_does_nothing_while_the_column_is_missing(self):
		db = self.run_with(False)
		db.sql.assert_not_called()

	def test_ensure_configuration_runs_the_default_after_the_custom_fields(self):
		calls = []
		with (
			patch.object(install, "ensure_roles", lambda: calls.append("roles")),
			patch.object(install, "ensure_custom_fields", lambda: calls.append("fields")),
			patch.object(install, "ensure_item_assessment_default", lambda: calls.append("default")),
			patch.object(install, "ensure_settings", lambda: calls.append("settings")),
		):
			install.ensure_configuration()
		self.assertEqual(calls, ["roles", "fields", "default", "settings"])


class TestEnsureSettings(unittest.TestCase):
	"""`after_migrate` runs on every migrate, so seeding must happen exactly once."""

	def run_with(self, singles_rows, existing_rules):
		settings = MagicMock()
		settings.get.return_value = existing_rules
		db = MagicMock()
		db.sql.return_value = singles_rows
		with patch.object(frappe, "db", db), patch.object(frappe, "get_single", return_value=settings):
			install.ensure_settings()
		return settings, db

	def test_seeds_the_default_rules_on_a_site_that_never_saved_the_settings(self):
		settings, db = self.run_with(singles_rows=(), existing_rules=[])
		self.assertEqual(settings.append.call_count, len(install.DEFAULT_ALERT_RULES))
		settings.save.assert_called_once_with(ignore_permissions=True)
		query = db.sql.call_args.args[0]
		self.assertIn("tabSingles", query)
		self.assertEqual(db.sql.call_args.args[1], ("Regulatory Settings",))

	def test_never_seeds_again_once_the_settings_exist(self):
		settings, _ = self.run_with(singles_rows=((1,),), existing_rules=[])
		settings.append.assert_not_called()
		settings.save.assert_not_called()

	def test_does_not_duplicate_existing_rules_even_without_a_singles_row(self):
		settings, _ = self.run_with(singles_rows=(), existing_rules=["rule"])
		settings.append.assert_not_called()
		settings.save.assert_not_called()

	def test_default_rules_have_unique_days(self):
		days = [days for days, _severity, _action in install.DEFAULT_ALERT_RULES]
		self.assertEqual(len(days), len(set(days)))


if __name__ == "__main__":
	unittest.main()
