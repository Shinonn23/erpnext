from inspect import unwrap
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.asset_repair import reinspection
from erpnext.stock.doctype.quality_inspection.quality_inspection import item_query


class TestRepairReinspectionSource(TestCase):
	def setUp(self):
		self.repair = frappe._dict(
			docstatus=1,
			repair_status="Completed",
			asset="ASSET",
			company="C",
			return_movement="RETURN",
			return_movement_item="ROW",
			check_permission=Mock(),
		)
		self.row = frappe._dict(name="ROW", idx=1, asset="ASSET", asset_item_code="ITEM", quality_inspection="QI")
		self.movement = frappe._dict(
			doctype="Asset Movement",
			name="RETURN",
			company="C",
			docstatus=1,
			purpose="Receipt",
			borrowing="BORROWING",
			borrowing_source_doctype="Borrowing",
			assets=[self.row],
		)
		self.inspection = frappe._dict(
			docstatus=1,
			status="Rejected",
			reference_type="Asset Movement",
			reference_name="RETURN",
			child_row_reference="ROW",
			item_code="ITEM",
			inspection_type="Incoming",
			company="C",
		)

	def test_item_picker_reads_repair_and_matching_item_permissions_without_a_write_lock(self):
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=self.inspection))),
			patch.object(frappe, "get_doc", side_effect=[self.repair, self.movement]) as get_doc,
			patch.object(frappe, "get_list", return_value=[frappe._dict(name="ITEM", item_name="Device")]) as items,
		):
			result = unwrap(item_query)(
				"Item", "dev", "name", 0, 20, {"reference_doctype": "Asset Repair", "reference_name": "REPAIR"}
			)
		self.assertEqual(result, [("ITEM", "Device")])
		self.repair.check_permission.assert_called_once_with("read")
		self.assertEqual(get_doc.call_args_list[0].kwargs, {"for_update": False})
		self.assertEqual(items.call_args.kwargs["filters"], {"name": ["in", ["ITEM"]]})

	def test_unreadable_repair_stops_before_reading_the_return(self):
		self.repair.check_permission.side_effect = frappe.PermissionError
		with (
			patch.object(frappe, "get_doc", return_value=self.repair) as get_doc,
			self.assertRaises(frappe.PermissionError),
		):
			reinspection.get_reinspection_source("REPAIR")
		get_doc.assert_called_once_with("Asset Repair", "REPAIR", for_update=False)

	def test_write_locks_the_repair_and_rechecks_original_rejection(self):
		self.inspection.child_row_reference = "OTHER"
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=self.inspection))),
			patch.object(frappe, "get_doc", side_effect=[self.repair, self.movement]) as get_doc,
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
		):
			with self.assertRaises(frappe.ValidationError):
				reinspection.get_reinspection_source("REPAIR", for_update=True)
			self.assertIn("rejected inspection must belong", frappe.throw.call_args.args[0])
		self.assertEqual(get_doc.call_args_list[0].kwargs, {"for_update": True})
