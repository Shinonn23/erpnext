"""The one STAGING rule: a receiving location is never picked (Siwat, 17 Nov 2026). Offline, no database."""
import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.stock.doctype.storage_location import storage_location as module
from erpnext.stock.doctype.storage_location.storage_location import (
	is_staging_code,
	is_staging_location,
	staging_location_sql,
)


class TestStagingLocation(unittest.TestCase):
	def test_code_is_staging_or_staging_dash(self):
		for code in ("STAGING", "STAGING-2"):
			self.assertTrue(is_staging_code(code), code)
		for code in ("", None, "A-01", "STAGINGX", "LAB MACHINE", "staging"):
			self.assertFalse(is_staging_code(code), code)

	def test_location_must_be_enabled_and_of_the_warehouse(self):
		row = frappe._dict(warehouse="W", location_code="STAGING", disabled=0)
		with patch.object(module, "frappe", MagicMock(**{"db.get_value.return_value": row})):
			self.assertTrue(is_staging_location("W / STAGING"))
			self.assertTrue(is_staging_location("W / STAGING", "W"))
			self.assertFalse(is_staging_location("W / STAGING", "OTHER"))
		row.disabled = 1
		with patch.object(module, "frappe", MagicMock(**{"db.get_value.return_value": row})):
			self.assertFalse(is_staging_location("W / STAGING"))
		other = frappe._dict(warehouse="W", location_code="A-01", disabled=0)
		with patch.object(module, "frappe", MagicMock(**{"db.get_value.return_value": other})):
			self.assertFalse(is_staging_location("W / A-01"))
		self.assertFalse(is_staging_location(None))

	def test_sql_tests_the_given_expression(self):
		sql = staging_location_sql("sle.storage_location")
		self.assertIn("staging_sl.name = (sle.storage_location)", sql)
		self.assertIn("location_code = 'STAGING'", sql)
		self.assertIn("like 'STAGING-", sql)
		self.assertTrue(sql.startswith("not exists"))


class TestPickListNeverTakesStaging(unittest.TestCase):
	def test_a_staging_location_gives_no_locations(self):
		from erpnext.stock.doctype.pick_list import pick_list

		with (
			patch.object(pick_list, "frappe", MagicMock(**{"get_cached_value.return_value": 0})),
			patch.object(pick_list, "is_staging_location", return_value=True),
			patch.object(pick_list, "get_available_item_locations_for_other_item") as other,
		):
			found = pick_list.get_available_item_locations(
				"ITEM", ["W"], 1, "C", ignore_validation=True,
				inventory_dimensions_dict={"storage_location": "W / STAGING"},
			)
		self.assertEqual(found, [])
		other.assert_not_called()
