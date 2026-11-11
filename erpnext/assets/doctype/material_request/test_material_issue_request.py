"""Offline checks for issue requests and their native Stock Entry mapping."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.material_request.material_request import CustomerMaterialRequest
from erpnext.stock.doctype.material_request import material_request as native


class TestMaterialIssueRequest(unittest.TestCase):
	def test_issue_purpose_and_required_source_fields_are_available(self):
		root = Path(__file__).resolve().parents[3]
		fields = {
			field["fieldname"]: field
			for field in json.loads(
				(root / "stock/doctype/material_request/material_request.json").read_text()
			)["fields"]
		}
		self.assertIn("Material Issue", fields["material_request_type"]["options"].splitlines())
		for name in ("items_section", "items", "set_warehouse"):
			self.assertIn("Material Issue", fields[name]["depends_on"])
		self.assertIn("Material Issue", fields["items"]["mandatory_depends_on"])
		child = {
			field["fieldname"]: field
			for field in json.loads(
				(root / "stock/doctype/material_request_item/material_request_item.json").read_text()
			)["fields"]
		}
		self.assertIn("Material Issue", child["warehouse"]["mandatory_depends_on"])
		self.assertNotIn("Material Issue", child["from_warehouse"]["depends_on"])
		self.assertIn("Material Issue", child["storage_location"]["depends_on"])
		self.assertNotIn("Material Issue", child["from_storage_location"]["depends_on"])

	def test_issue_request_requires_a_stock_item_and_source_warehouse_in_company(self):
		row = frappe._dict(idx=1, item_code="ITEM", warehouse="SOURCE")
		request = SimpleNamespace(items=[row], company="COMPANY")
		with (
			patch.object(frappe, "throw", side_effect=ValueError),
			patch.object(
				frappe, "get_cached_value", side_effect=[frappe._dict(company="COMPANY", is_group=0), 1]
			),
		):
			CustomerMaterialRequest.validate_material_issue_items(request)
		for warehouse, item_stock in (
			(None, 1),
			(frappe._dict(company="OTHER", is_group=0), 1),
			(frappe._dict(company="COMPANY", is_group=1), 1),
			(frappe._dict(company="COMPANY", is_group=0), 0),
		):
			with (
				self.subTest(warehouse=warehouse, item_stock=item_stock),
				patch.object(frappe, "throw", side_effect=ValueError),
				patch.object(frappe, "get_cached_value", side_effect=[warehouse, item_stock]),
				self.assertRaises(ValueError),
			):
				CustomerMaterialRequest.validate_material_issue_items(request)
		row.warehouse = None
		with patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
			CustomerMaterialRequest.validate_material_issue_items(request)

	def test_native_mapper_keeps_issue_source_and_remaining_quantity(self):
		source = frappe._dict(
			material_request_type="Material Issue",
			set_warehouse="SOURCE",
			set_from_warehouse=None,
			job_card=None,
			work_order=None,
		)
		item = frappe._dict(
			stock_qty=5, ordered_qty=2, conversion_factor=1, warehouse="SOURCE", storage_location="SHELF"
		)
		target = frappe._dict(conversion_factor=1)
		for method in (
			"set_transfer_qty",
			"set_actual_qty",
			"calculate_rate_and_amount",
			"set_job_card_data",
		):
			target[method] = Mock()

		def map_document(doctype, source_name, mappings, target_doc, postprocess):
			validation = mappings["Material Request"]["validation"]
			self.assertEqual(validation["docstatus"], ["=", 1])
			self.assertIn("Material Issue", validation["material_request_type"][1])
			mappings["Material Request Item"]["postprocess"](item, target, source)
			postprocess(source, target)
			return target

		with patch.object(native, "get_mapped_doc", side_effect=map_document):
			result = native.make_stock_entry("MR-ISSUE")
		self.assertEqual(result.purpose, "Material Issue")
		self.assertEqual(result.stock_entry_type, "Material Issue")
		self.assertEqual(result.s_warehouse, "SOURCE")
		self.assertEqual(result.from_warehouse, "SOURCE")
		self.assertIsNone(result.to_warehouse)
		self.assertEqual(result.storage_location, "SHELF")
		self.assertEqual(result.qty, 3)
