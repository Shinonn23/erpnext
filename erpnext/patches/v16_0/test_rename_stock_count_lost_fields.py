import unittest
from unittest.mock import Mock, patch

from erpnext.patches.v16_0 import rename_stock_count_lost_fields as patch_module


class TestRenameStockCountLostFields(unittest.TestCase):
	def run_patch(self, columns, filled=()):
		db = Mock()
		db.exists.return_value = True
		db.has_column.side_effect = lambda doctype, fieldname: fieldname in columns
		db.sql.side_effect = (
			lambda query, *args, **kwargs: [(1,)] if any(f"`{f}`" in query for f in filled) else []
		)
		with (
			patch.object(patch_module.frappe, "db", db),
			patch.object(patch_module.frappe, "reload_doc"),
			patch.object(patch_module.frappe, "clear_cache"),
			patch.object(patch_module.frappe, "log_error") as log,
			patch.object(patch_module, "rename_field") as rename,
		):
			patch_module.execute()
		dropped = [call.args[0] for call in db.sql_ddl.call_args_list]
		return rename, dropped, log

	def test_the_lost_fields_are_renamed_and_the_old_columns_dropped(self):
		rename, dropped, _log = self.run_patch({"quarantine_warehouse", "quarantine_storage_location"})
		rename.assert_any_call("Stock Count", "quarantine_warehouse", "lost_warehouse")
		rename.assert_any_call("Stock Count", "quarantine_storage_location", "lost_storage_location")
		self.assertTrue(any("drop column `quarantine_warehouse`" in sql for sql in dropped))

	def test_a_second_run_changes_nothing(self):
		rename, dropped, _log = self.run_patch(set())
		rename.assert_not_called()
		self.assertEqual(dropped, [])

	def test_the_found_columns_are_dropped_only_while_empty(self):
		_rename, dropped, log = self.run_patch(
			{"found_warehouse", "found_stock_entry", "found_transfer_entry"}, filled={"found_stock_entry"}
		)
		self.assertTrue(any("drop column `found_warehouse`" in sql for sql in dropped))
		self.assertTrue(any("drop column `found_transfer_entry`" in sql for sql in dropped))
		self.assertFalse(any("found_stock_entry" in sql for sql in dropped))
		log.assert_called_once()
