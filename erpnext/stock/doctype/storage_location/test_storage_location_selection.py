# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import inspect
import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.item.item import Item
from erpnext.stock.doctype.putaway_rule.putaway_rule import apply_putaway_rule
from erpnext.stock.doctype.stock_settings.stock_settings import is_putaway_rule_enabled
from erpnext.stock.doctype.storage_location.storage_location import (
	storage_location_query,
	validate_item_storage_condition,
	validate_storage_location,
)

MODULE = "erpnext.stock.doctype.storage_location.storage_location"


class TestStorageLocationSelection(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch(f"{MODULE}._", side_effect=lambda text: text))
		self.enterContext(
			patch("frappe.throw", side_effect=lambda text, *args, **kwargs: self.fail_with(text))
		)
		self.db = self.enterContext(patch("frappe.db", Mock()))

	@staticmethod
	def fail_with(text):
		raise frappe.ValidationError(text)

	def test_unclassified_item_keeps_existing_location_behavior(self):
		with patch("frappe.get_cached_value", return_value=None), patch("frappe.get_cached_doc") as location:
			validate_item_storage_condition("Shelf", "Item")
			location.assert_not_called()

	def test_location_can_support_multiple_conditions(self):
		location = frappe._dict(
			supported_storage_conditions=[
				frappe._dict(storage_condition="Ambient"),
				frappe._dict(storage_condition="Cold"),
			]
		)
		with patch("frappe.get_cached_value", return_value="Cold"), patch(
			"frappe.get_cached_doc", return_value=location
		):
			validate_item_storage_condition("Shelf", "Item")
			location.supported_storage_conditions.pop()
			with self.assertRaisesRegex(frappe.ValidationError, "does not support Storage Condition Cold"):
				validate_item_storage_condition("Shelf", "Item")

	def test_empty_location_condition_does_not_mean_all_conditions(self):
		with patch("frappe.get_cached_doc", return_value=frappe._dict(supported_storage_conditions=[])):
			with self.assertRaisesRegex(frappe.ValidationError, "does not support"):
				validate_item_storage_condition("Shelf", "Item", "Cold")

	def test_unsaved_item_condition_takes_precedence_during_master_validation(self):
		with patch("frappe.get_cached_value") as get_item:
			validate_item_storage_condition("Shelf", "Item", "")
			get_item.assert_not_called()

	def test_ledger_rejects_incompatible_location_but_preserves_cancellation(self):
		entry = frappe._dict(
			warehouse="Warehouse", storage_location="Shelf", item_code="Item", is_cancelled=0
		)
		with patch(
			"frappe.get_cached_value", return_value=frappe._dict(warehouse="Warehouse", disabled=0)
		), patch(
			f"{MODULE}.validate_item_storage_condition", side_effect=frappe.ValidationError("Incompatible")
		) as validate:
			with self.assertRaisesRegex(frappe.ValidationError, "Incompatible"):
				validate_storage_location(entry)
			validate.assert_called_once_with("Shelf", "Item")
			validate.reset_mock()
			entry.is_cancelled = 1
			validate_storage_location(entry)
			validate.assert_not_called()

	def query(self, filters):
		return inspect.unwrap(storage_location_query)("Storage Location", "Shelf", "name", 0, 20, filters)

	def test_landed_cost_recreation_keeps_historical_condition_only_for_trusted_entry(self):
		entry = frappe._dict(
			warehouse="Warehouse", storage_location="Shelf", item_code="Item", is_cancelled=0
		)
		with patch(
			"frappe.get_cached_value", return_value=frappe._dict(warehouse="Warehouse", disabled=0)
		), patch(
			f"{MODULE}.validate_item_storage_condition", side_effect=frappe.ValidationError("Incompatible")
		) as validate, patch("frappe.flags", frappe._dict(warehouse_policy_lcv_entry=entry)):
			validate_storage_location(entry)
			validate.assert_not_called()
			with self.assertRaisesRegex(frappe.ValidationError, "Incompatible"):
				validate_storage_location(frappe._dict(entry))

	def test_picker_cannot_override_persisted_item_requirement(self):
		warehouse = frappe._dict(is_group=0, disabled=0)
		item = frappe._dict(storage_condition="Cold")
		self.db.sql.return_value = []
		with patch("frappe.get_cached_doc", side_effect=[warehouse, item]), patch(
			"frappe.has_permission", return_value=True
		), patch(f"{MODULE}.get_match_cond", return_value=""), patch(
			f"{MODULE}.location_stock", return_value={}
		):
			self.query({"warehouse": "Warehouse", "item_code": "Item", "storage_condition": ""})
			self.assertEqual(self.db.sql.call_args.args[1]["condition"], "Cold")
			self.assertEqual(self.db.sql.call_args.args[1]["item_code"], "Item")

	def test_no_warehouse_does_not_return_other_company_locations(self):
		self.assertEqual(self.query({"item_code": "Item"}), [])
		self.db.sql.assert_not_called()

	def test_company_and_warehouse_permissions_apply_before_search(self):
		with patch("frappe.has_permission", side_effect=[True, False]), patch(
			"frappe.get_cached_doc", return_value=frappe._dict(is_group=0, disabled=0)
		):
			self.assertEqual(self.query({"warehouse": "Other Company Warehouse"}), [])
			self.db.sql.assert_not_called()

	def test_location_permission_is_required_for_direct_endpoint_call(self):
		with patch("frappe.has_permission", return_value=False):
			with self.assertRaisesRegex(frappe.ValidationError, "Not permitted"):
				self.query({"warehouse": "Warehouse"})
			self.db.sql.assert_not_called()

	def test_duplicate_item_preferences_are_rejected(self):
		item = frappe._dict(
			name="Item",
			storage_condition="",
			preferred_storage_locations=[
				frappe._dict(idx=1, warehouse="Warehouse", storage_location="Shelf"),
				frappe._dict(idx=2, warehouse="Warehouse", storage_location="Shelf"),
			],
		)
		with patch(f"{MODULE}.validate_storage_location_warehouse"), patch(
			"erpnext.stock.doctype.item.item._", side_effect=lambda text: text
		):
			with self.assertRaisesRegex(frappe.ValidationError, "cannot be repeated"):
				Item.validate_storage_preferences(item)

	def test_putaway_setting_preserves_existing_sites(self):
		for value, expected in [(None, True), (1, True), ("1", True), (0, False), ("0", False)]:
			self.db.get_singles_dict.return_value = {} if value is None else {"enable_putaway_rule": value}
			self.assertEqual(is_putaway_rule_enabled(), expected)

	def test_disabled_putaway_rejects_backend_call(self):
		with patch(
			"erpnext.stock.doctype.putaway_rule.putaway_rule.is_putaway_rule_enabled", return_value=False
		), patch("erpnext.stock.doctype.putaway_rule.putaway_rule._", side_effect=lambda text: text):
			with self.assertRaisesRegex(frappe.ValidationError, "Putaway Rules are disabled"):
				apply_putaway_rule("Stock Entry", [], "Company", purpose="Material Transfer")


class TestStorageLocationStock(unittest.TestCase):
	"""The picker shows the Item's quantity per location and follows the row's serial nos (5 Nov 2026)."""

	def setUp(self):
		self.db = patch("frappe.db", Mock()).start()
		self.addCleanup(patch.stopall)
		self.candidates = [
			frappe._dict(name="A-1", location_code="A1", preferred=0, idx=None),
			frappe._dict(name="A-2", location_code="A2", preferred=0, idx=None),
			frappe._dict(name="B-1", location_code="B1", preferred=1, idx=1),
		]
		self.db.sql.return_value = self.candidates
		patch(
			"frappe.get_cached_doc",
			side_effect=lambda doctype, name: frappe._dict(is_group=0, disabled=0, storage_condition=None),
		).start()
		patch("frappe.has_permission", return_value=True).start()
		patch(f"{MODULE}.get_match_cond", return_value="").start()
		patch("frappe.format", side_effect=lambda value, df=None: f"{value:g}").start()
		patch(f"{MODULE}._", side_effect=lambda text, *args, **kwargs: text).start()

	def query(self, **filters):
		return inspect.unwrap(storage_location_query)(
			"Storage Location", "", "name", 0, 20, {"warehouse": "W", "item_code": "Item", **filters}
		)

	def test_each_location_shows_the_items_quantity_preferred_first_then_most_stock(self):
		with patch(f"{MODULE}.location_stock", return_value={"A-2": 5.0, "B-1": 1.0}):
			rows = self.query()
		self.assertEqual([row[0] for row in rows], ["B-1", "A-2", "A-1"])
		self.assertEqual([row[2] for row in rows], ["Qty 1", "Qty 5", "Qty 0"])

	def test_preferred_locations_are_sorted_by_quantity_before_their_saved_order(self):
		self.candidates.append(frappe._dict(name="B-2", location_code="B2", preferred=1, idx=2))
		with patch(f"{MODULE}.location_stock", return_value={"A-2": 50.0, "B-1": 1.0, "B-2": 5.0}):
			rows = self.query()
		self.assertEqual([row[0] for row in rows], ["B-2", "B-1", "A-2", "A-1"])

	def test_a_source_field_offers_only_locations_holding_the_item(self):
		with patch(f"{MODULE}.location_stock", return_value={"A-2": 5.0}):
			self.assertEqual([row[0] for row in self.query(only_stock=1)], ["A-2"])
		# nothing recorded per location: every location stays offered
		with patch(f"{MODULE}.location_stock", return_value={}):
			self.assertEqual(len(self.query(only_stock=1)), 3)

	def test_a_row_with_serial_nos_offers_only_their_locations(self):
		with patch(f"{MODULE}.location_stock", return_value={"A-2": 5.0, "A-1": 2.0}), patch(
			f"{MODULE}.serial_locations", return_value={"A-1": 1}
		) as where:
			self.assertEqual([row[0] for row in self.query(serial_no="SN-1\nSN-2\n")], ["A-1"])
		self.assertEqual(where.call_args.args, (["SN-1", "SN-2"], "W"))


class TestLocationCodeFromPosition(unittest.TestCase):
	def location(self, **values):
		from erpnext.stock.doctype.storage_location.storage_location import StorageLocation

		doc = StorageLocation.__new__(StorageLocation)
		doc.__dict__.update({"location_code": "", "zone": "", "rack": "", "shelf": "", **values})
		doc.get = lambda field: doc.__dict__.get(field)
		return doc

	def test_a_blank_code_is_built_from_zone_rack_and_shelf(self):
		doc = self.location(zone="A", rack="01", shelf="02")
		doc.set_location_code()
		self.assertEqual(doc.location_code, "A-01-02")
		doc = self.location(zone="A", shelf="02")
		doc.set_location_code()
		self.assertEqual(doc.location_code, "A-02")

	def test_a_typed_code_is_kept(self):
		doc = self.location(location_code="X-9", zone="A", rack="01", shelf="02")
		doc.set_location_code()
		self.assertEqual(doc.location_code, "X-9")

	def test_nothing_to_build_from_is_refused(self):
		with patch("frappe.throw", side_effect=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError):
				self.location().set_location_code()
