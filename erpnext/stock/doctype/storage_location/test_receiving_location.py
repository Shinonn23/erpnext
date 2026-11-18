"""The one Receiving rule: stock at a Receiving location is never picked, sold or issued; it leaves only by a Stock Entry
(Siwat, 17 Nov 2026). The Location Type says it, never the location code. Offline, no database: the SQL helper runs
on sqlite against the same rows the Python helper reads, so the two can never disagree."""

import sqlite3
import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.stock.doctype.storage_location import storage_location as module
from erpnext.stock.doctype.storage_location.storage_location import (
	is_receiving_location,
	receiving_location_sql,
)

LOCATIONS = (
	# name, warehouse, location_code, location_type, disabled
	("W / STAGING", "W", "STAGING", "Receiving", 0),
	("W / DOCK-2", "W", "DOCK-2", "Receiving", 0),
	("W / OLD DOCK", "W", "OLD DOCK", "Receiving", 1),
	("W / STAGING-B", "W", "STAGING-B", "Storage", 0),
	("W / A-01", "W", "A-01", "Storage", 0),
	("W / COUNTER", "W", "COUNTER", "Sales Counter", 0),
	("Q / STAGING", "Q", "STAGING", "Holding", 0),
	("OTHER / STAGING", "OTHER", "STAGING", "Receiving", 0),
)


class TestReceivingLocation(unittest.TestCase):
	def setUp(self):
		self.db = sqlite3.connect(":memory:")
		self.db.execute(
			"create table `tabStorage Location` (name text, warehouse text, location_code text, "
			"location_type text, disabled int)"
		)
		self.db.executemany("insert into `tabStorage Location` values (?, ?, ?, ?, ?)", LOCATIONS)
		self.db.execute("create table probe (location text, warehouse text)")
		self.addCleanup(self.db.close)

	def python(self, location, warehouse=None):
		def get_value(_doctype, name, fields, as_dict=False):
			row = next((row for row in LOCATIONS if row[0] == name), None)
			if not row:
				return None
			return frappe._dict(warehouse=row[1], location_type=row[3], disabled=row[4])

		with patch.object(module, "frappe", MagicMock(**{"db.get_value.side_effect": get_value})):
			return is_receiving_location(location, warehouse)

	def sql(self, location, warehouse=None):
		self.db.execute("delete from probe")
		self.db.execute("insert into probe values (?, ?)", (location, warehouse))
		condition = receiving_location_sql("probe.location", "probe.warehouse" if warehouse else None)
		return bool(self.db.execute(f"select {condition} from probe").fetchone()[0])

	def test_python_and_sql_agree_on_every_location(self):
		names = [row[0] for row in LOCATIONS] + ["W / MISSING", None]
		for location in names:
			for warehouse in (None, "W", "Q", "OTHER"):
				with self.subTest(location=location, warehouse=warehouse):
					self.assertEqual(self.python(location, warehouse), self.sql(location, warehouse))

	def test_the_location_type_decides_never_the_code(self):
		self.assertTrue(self.python("W / STAGING"))
		self.assertTrue(self.python("W / DOCK-2"))
		self.assertFalse(self.python("W / STAGING-B"))
		self.assertFalse(self.python("Q / STAGING"))
		self.assertFalse(self.python("W / COUNTER"))

	def test_a_disabled_or_foreign_location_is_not_receiving(self):
		self.assertFalse(self.python("W / OLD DOCK"))
		self.assertFalse(self.sql("W / OLD DOCK"))
		self.assertFalse(self.python("OTHER / STAGING", "W"))
		self.assertFalse(self.sql("OTHER / STAGING", "W"))
		self.assertTrue(self.sql("OTHER / STAGING", "OTHER"))
		self.assertFalse(self.python(None))
		self.assertFalse(self.python(""))


class TestPickListNeverTakesReceiving(unittest.TestCase):
	def test_a_receiving_location_gives_no_locations(self):
		from erpnext.stock.doctype.pick_list import pick_list

		with (
			patch.object(pick_list, "frappe", MagicMock(**{"get_cached_value.return_value": 0})),
			patch.object(pick_list, "is_receiving_location", return_value=True),
			patch.object(pick_list, "get_available_item_locations_for_other_item") as other,
		):
			found = pick_list.get_available_item_locations(
				"ITEM",
				["W"],
				1,
				"C",
				ignore_validation=True,
				inventory_dimensions_dict={"storage_location": "W / STAGING"},
			)
		self.assertEqual(found, [])
		other.assert_not_called()
