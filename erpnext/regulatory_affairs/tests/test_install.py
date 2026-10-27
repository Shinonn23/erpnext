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
			patch.object(install, "ensure_item_category_permissions", lambda: calls.append("item perms")),
			patch.object(install, "ensure_settings", lambda: calls.append("settings")),
		):
			install.ensure_configuration()
		self.assertEqual(calls, ["module", "roles", "fields", "item perms", "settings"])


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


class TestEnsureItemCategoryPermissions(unittest.TestCase):
	def run_with(self, custom_rows, existing):
		"""custom_rows: roles reading Item at level 0 (None: Item has no Custom DocPerm); existing: set of (role, level)."""
		db = MagicMock()
		db.exists.side_effect = lambda doctype, filters=None: (
			(custom_rows is not None)
			if filters == {"parent": "Item"}
			else ((filters["role"], filters["permlevel"]) in existing)
		)
		db.get_value.side_effect = lambda doctype, filters, fieldname=None: "row" if fieldname is None else 1
		added = []
		self.db = db

		def fake_add(*a, **k):
			added.append((a[1], k["permlevel"], k["ptype"]))
			return f"row-{a[1]}-{k['permlevel']}"

		with (
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_all", return_value=list(custom_rows or [])),
			patch.object(frappe, "clear_cache"),
			patch("frappe.permissions.add_permission", fake_add),
		):
			install.ensure_item_category_permissions()
		return added

	def test_a_site_on_standard_permissions_is_left_alone(self):
		self.assertEqual(self.run_with(None, set()), [])

	def test_custom_permissions_get_level_one_read_for_readers_and_write_for_operator_and_approver(self):
		added = self.run_with(["Item Manager", "Stock User"], set())
		self.assertIn(("Item Manager", 1, "read"), added)
		self.assertIn(("Stock User", 1, "read"), added)
		self.assertIn(("Regulatory Operator", 1, "write"), added)
		self.assertIn(("Regulatory Approver", 1, "write"), added)
		self.assertNotIn(("Item Manager", 1, "write"), added)
		for role in ("Regulatory Viewer", "Regulatory Operator", "Regulatory Approver"):
			self.assertIn((role, 0, "read"), added)

	def test_existing_rows_are_not_added_again(self):
		existing = {("Item Manager", 1), ("Item Manager", 0)}
		added = self.run_with(["Item Manager"], existing)
		self.assertFalse([a for a in added if a[0] == "Item Manager"])

	def test_a_new_row_grants_only_its_own_right(self):
		self.run_with(["Item Manager"], set())
		calls = {c.args[1]: c.args[2] for c in self.db.set_value.call_args_list if isinstance(c.args[2], dict)}
		cleared = calls["row-Regulatory Operator-0"]
		self.assertEqual(cleared["export"], 0)
		for extra in ("print", "email", "share", "report", "write", "create", "delete"):
			self.assertEqual(cleared[extra], 0, extra)
		self.assertNotIn("read", cleared)
		self.assertNotIn("write", calls["row-Regulatory Operator-1"])
		self.assertEqual(calls["row-Regulatory Operator-1"]["export"], 0)

	def test_existing_rows_keep_their_rights(self):
		self.run_with(["Item Manager"], {("Item Manager", 1), ("Item Manager", 0)})
		for call in self.db.set_value.call_args_list:
			self.assertNotIn("row-Item Manager", call.args[1])


class TestHookOrder(unittest.TestCase):
	def test_the_permission_step_is_the_last_after_migrate_hook(self):
		from erpnext import hooks

		self.assertEqual(
			hooks.after_migrate[-1], "erpnext.regulatory_affairs.install.ensure_item_category_permissions"
		)

	def test_item_json_regulatory_level_zero_rows_only_read(self):
		import json
		import os

		path = os.path.join(os.path.dirname(__file__), "../../stock/doctype/item/item.json")
		with open(path) as handle:
			rows = [
				p
				for p in json.load(handle)["permissions"]
				if p["role"].startswith("Regulatory") and not p.get("permlevel")
			]
		self.assertEqual(len(rows), 3)
		for row in rows:
			self.assertEqual({k for k, v in row.items() if v == 1}, {"read"})
