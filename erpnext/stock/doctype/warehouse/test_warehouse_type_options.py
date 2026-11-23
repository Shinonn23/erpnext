"""Warehouse Type Select choices and migrated child field schema (offline)."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.warehouse import warehouse
from erpnext.stock.warehouse_policy import WAREHOUSE_TYPE_OPERATIONS


class TestWarehouseTypeOptions(unittest.TestCase):
	def test_migrated_company_gets_only_modern_types_and_its_current_legacy_type(self):
		with (
			patch.object(warehouse, "uses_stock_locations", return_value=True),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(
				frappe,
				"get_doc",
				return_value=frappe._dict(company="CD", warehouse_type="Lost"),
			),
		):
			choices = warehouse.warehouse_type_options("CD", "WH-CD")

		self.assertEqual(choices, ["Stock", "Transit", "Borrowed", "Lost"])

	def test_non_migrated_company_keeps_all_known_policy_types(self):
		with (
			patch.object(warehouse, "uses_stock_locations", return_value=False),
			patch.object(frappe, "has_permission", return_value=True),
		):
			choices = warehouse.warehouse_type_options("UB")

		self.assertEqual(choices, list(WAREHOUSE_TYPE_OPERATIONS))

	def test_select_schemas_keep_blank_and_fetch_the_target_type_as_data(self):
		warehouse_schema = json.loads(
			(Path(__file__).parent / "warehouse.json").read_text()
		)
		warehouse_type = next(field for field in warehouse_schema["fields"] if field["fieldname"] == "warehouse_type")
		self.assertEqual(warehouse_type["fieldtype"], "Select")
		self.assertTrue(warehouse_type["options"].startswith("\n"))
		self.assertEqual(warehouse_type["options"].splitlines()[1:], list(WAREHOUSE_TYPE_OPERATIONS))

		entry_schema = json.loads(
			(Path(__file__).parents[1] / "stock_entry_detail" / "stock_entry_detail.json").read_text()
		)
		target_type = next(field for field in entry_schema["fields"] if field["fieldname"] == "t_warehouse_type")
		self.assertEqual(target_type["fieldtype"], "Data")
		self.assertEqual(target_type["fetch_from"], "t_warehouse.warehouse_type")


if __name__ == "__main__":
	unittest.main()
