# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

import unittest
from unittest.mock import patch

import frappe
from frappe.utils.nestedset import NestedSet

from erpnext.assets.doctype.location.location import Location
from erpnext.stock import stock_control
from erpnext.stock.doctype.warehouse.warehouse import Warehouse


class TestStockControlLocks(unittest.TestCase):
	def test_mapping_save_shares_type_before_native_mapping_lock(self):
		for controller in (Warehouse, Location):
			with self.subTest(controller=controller.__name__):
				events = []
				doc = object.__new__(controller)
				with (
					patch.object(
						stock_control,
						"lock_stock_control_assignment_types",
						side_effect=lambda doc: events.append("Type share"),
					),
					patch.object(
						NestedSet, "_save", side_effect=lambda **kwargs: events.append("Native mapping lock")
					),
				):
					doc._save()
				self.assertEqual(events, ["Type share", "Native mapping lock"])

	def test_assignment_validation_does_not_upgrade_type_lock(self):
		doc = frappe._dict(
			doctype="Warehouse",
			name="Warehouse",
			company="Company",
			is_group=0,
			disabled=0,
			warehouse_type="New Type",
			stock_control_change_reason="Approved change",
		)
		doc.get_doc_before_save = lambda: frappe._dict(warehouse_type="Old Type")
		with (
			patch.object(stock_control, "require_stock_control_role"),
			patch.object(
				stock_control, "_current_doc", return_value=frappe._dict(handling_category="Normal")
			) as current,
		):
			stock_control.validate_stock_control_assignment(doc)
		self.assertTrue(any(call.args[0] == "Warehouse Type" for call in current.call_args_list))
		for call in current.call_args_list:
			if call.args[0] == "Warehouse Type":
				self.assertFalse(call.kwargs.get("exclusive", False))
