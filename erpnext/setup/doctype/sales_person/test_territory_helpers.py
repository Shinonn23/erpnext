# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Who looks after a Territory, and which Territory a user looks after."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.setup.doctype.sales_person import sales_person

MODULE = "erpnext.setup.doctype.sales_person.sales_person."


class TestTerritoryManager(unittest.TestCase):
	def manager(self, manager, enabled=1):
		values = {("Territory", "East"): manager, ("Sales Person", manager): enabled}
		cached = patch.object(
			frappe, "get_cached_value", side_effect=lambda dt, name, field: values.get((dt, name))
		)
		with cached:
			return sales_person.get_territory_manager("East")

	def test_enabled_manager(self):
		self.assertEqual(self.manager("Alice"), "Alice")

	def test_disabled_manager_counts_as_unassigned(self):
		self.assertIsNone(self.manager("Alice", enabled=0))

	def test_territory_without_manager_or_without_territory(self):
		self.assertIsNone(self.manager(None))
		self.assertIsNone(sales_person.get_territory_manager(None))


class TestSalesPersonStamp(unittest.TestCase):
	def stamp(self, territory, previous=None, source=None):
		with patch(MODULE + "get_territory_manager", return_value="Manager") as manager:
			return sales_person.get_sales_person_stamp(territory, previous, source), manager

	def test_saved_stamp_wins_over_source_and_manager(self):
		saved = frappe._dict(territory="East", sales_person="Saved")
		source = frappe._dict(territory="East", sales_person="Source")
		self.assertEqual(self.stamp("East", saved, source)[0], "Saved")

	def test_source_wins_over_manager(self):
		source = frappe._dict(territory="East", sales_person="Source")
		self.assertEqual(self.stamp("East", None, source)[0], "Source")

	def test_stamp_of_another_territory_is_ignored(self):
		other = frappe._dict(territory="West", sales_person="Bob")
		self.assertEqual(self.stamp("East", other, other)[0], "Manager")

	def test_unstamped_documents_get_the_manager(self):
		empty = frappe._dict(territory="East", sales_person=None)
		self.assertEqual(self.stamp("East", empty, empty)[0], "Manager")

	def test_no_territory_means_no_sales_person_and_no_lookup(self):
		stamp, manager = self.stamp(None, frappe._dict(territory="East", sales_person="Bob"))
		self.assertIsNone(stamp)
		manager.assert_not_called()


class TestTerritoryOfUser(unittest.TestCase):
	def territory_of(self, employee="EMP-1", person="Alice", territories=("East",)):
		db = MagicMock()
		db.get_value.side_effect = [employee, person]
		with (
			patch.object(frappe, "db", new=db),
			patch.object(frappe, "get_all", return_value=list(territories)),
		):
			return sales_person.get_territory_of_user("seller@example.com")

	def test_sales_person_with_one_territory(self):
		self.assertEqual(self.territory_of(), "East")

	def test_several_territories_are_ambiguous(self):
		self.assertIsNone(self.territory_of(territories=("East", "West")))

	def test_no_territory_assigned(self):
		self.assertIsNone(self.territory_of(territories=()))

	def test_user_without_employee_or_sales_person(self):
		self.assertIsNone(self.territory_of(employee=None))
		self.assertIsNone(self.territory_of(person=None))

