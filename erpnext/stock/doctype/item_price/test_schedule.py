from datetime import date
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.item_price import schedule


class TestScheduledItemPrice(TestCase):
	def test_legacy_blank_uom_only_applies_to_stock_uom(self):
		db = Mock()
		db.sql.return_value = []
		with patch.object(frappe, "db", db), patch.object(frappe, "get_cached_value", return_value="Box"):
			schedule.get_current_general_price("List", "Item", "Box", "2026-11-07", for_update=True)
			schedule.get_current_general_price("List", "Item", "Each", "2026-11-07")
		stock_query, other_query = db.sql.call_args_list
		self.assertEqual(stock_query.args[1]["uoms"], ("Box", ""))
		self.assertTrue(stock_query.args[0].endswith("for update"))
		self.assertEqual(other_query.args[1]["uoms"], ("Each",))
		self.assertFalse(other_query.args[0].endswith("for update"))

	def test_general_price_ends_previous_price_and_preserves_future_price(self):
		db = Mock()
		db.sql.side_effect = [[frappe._dict(name="undated", valid_from=None)], [date(2026, 11, 19)]]
		previous = Mock()
		new = Mock(insert=Mock(return_value=frappe._dict(name="new")))
		with (
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_cached_value", return_value="Box"),
			patch.object(frappe, "get_doc", side_effect=[previous, new]) as get_doc,
		):
			name = schedule.upsert_scheduled_item_price(
				"List", "Item", "Box", None, 120, "2026-11-07", "note", include_general_history=True
			)
		self.assertEqual(name, "new")
		db.get_value.assert_called_once_with("Price List", "List", "name", for_update=True)
		db.release_savepoint.assert_called_once_with("upsert_item_price")
		self.assertEqual(previous.valid_upto, date(2026, 11, 6))
		previous.save.assert_called_once_with(ignore_permissions=False)
		self.assertEqual(get_doc.call_args.args[0]["valid_upto"], date(2026, 11, 18))
		new.insert.assert_called_once_with(ignore_permissions=False)
