import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs import install


class TestEnsureConfiguration(unittest.TestCase):
	def test_runs_module_then_roles_then_custom_fields_then_settings(self):
		calls = []
		with (
			patch.object(install, "ensure_module_def", lambda: calls.append("module")),
			patch.object(install, "ensure_roles", lambda: calls.append("roles")),
			patch.object(install, "ensure_custom_fields", lambda: calls.append("fields")),
			patch.object(install, "ensure_settings", lambda: calls.append("settings")),
		):
			install.ensure_configuration()
		self.assertEqual(calls, ["module", "roles", "fields", "settings"])


class TestEnsureModuleDef(unittest.TestCase):
	def test_creates_the_module_def_when_sync_has_not(self):
		db = MagicMock()
		db.exists.return_value = None
		with patch.object(frappe, "db", db), patch.object(frappe, "get_doc") as get_doc:
			install.ensure_module_def()
		get_doc.assert_called_once_with(
			{"doctype": "Module Def", "module_name": "Regulatory Affairs", "app_name": "erpnext"}
		)
		get_doc.return_value.insert.assert_called_once_with(ignore_permissions=True)

	def test_leaves_an_existing_module_def_alone(self):
		db = MagicMock()
		db.exists.return_value = "Regulatory Affairs"
		with patch.object(frappe, "db", db), patch.object(frappe, "get_doc") as get_doc:
			install.ensure_module_def()
		get_doc.assert_not_called()


class TestBeforeMigrate(unittest.TestCase):
	def test_rebuilds_the_module_map_before_model_sync(self):
		with patch.object(install, "clear_global_cache") as clear_global_cache:
			install.before_migrate()
		clear_global_cache.assert_called_once_with()

	def test_is_registered_ahead_of_the_legacy_data_check(self):
		from erpnext import hooks

		self.assertEqual(hooks.before_migrate[0], "erpnext.regulatory_affairs.install.before_migrate")


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
