"""A Quality Inspection of a borrowing's return Asset Movement (UX WP4) reads the movement's ``assets`` rows, whose Item
is ``asset_item_code``: the native ``<reference_type> Item.item_code`` query would fail on it."""

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.quality_inspection import quality_inspection as module
from erpnext.stock.doctype.quality_inspection.quality_inspection import QualityInspection


class TestAssetMovementReference(unittest.TestCase):
	def _qi(self, **values):
		return frappe._dict(
			{
				"reference_type": "Asset Movement",
				"reference_name": "MOV-RET",
				"child_row_reference": "",
				"item_code": "ITEM-A",
				**values,
			}
		)

	def test_row_reference_is_the_first_uninspected_row_of_the_item(self):
		def get_all(doctype, filters=None, **kwargs):
			if doctype == "Quality Inspection":
				return ["ROW-1"]
			self.assertEqual(doctype, "Asset Movement Item")
			self.assertEqual(filters["asset_item_code"], "ITEM-A")
			self.assertEqual(filters["parent"], "MOV-RET")
			return ["ROW-1", "ROW-2"]

		qi = self._qi()
		with (
			patch.object(module.frappe, "get_all", side_effect=get_all),
			patch.object(module.frappe, "qb", Mock()) as query,
		):
			QualityInspection.set_child_row_reference(qi)
		self.assertEqual(qi.child_row_reference, "ROW-2")
		query.assert_not_called()

	def test_a_typed_row_reference_is_kept(self):
		qi = self._qi(child_row_reference="ROW-9")
		with patch.object(module.frappe, "get_all") as get_all:
			QualityInspection.set_child_row_reference(qi)
		self.assertEqual(qi.child_row_reference, "ROW-9")
		get_all.assert_not_called()

	def test_no_row_of_the_item_leaves_it_empty(self):
		qi = self._qi()
		with patch.object(module.frappe, "get_all", return_value=[]):
			QualityInspection.set_child_row_reference(qi)
		self.assertIsNone(qi.child_row_reference)

	def test_item_search_lists_the_uninspected_asset_items_of_the_movement(self):
		movement = Mock(docstatus=1)
		movement.get.return_value = [
			frappe._dict(asset_item_code="ITEM-A", quality_inspection=None),
			frappe._dict(asset_item_code="ITEM-B", quality_inspection="QI-1"),
			frappe._dict(asset_item_code="ITEM-A", quality_inspection=None),
		]
		listed = [frappe._dict(name="ITEM-A", item_name="Item A")]
		with (
			patch.object(module.frappe, "get_doc", return_value=movement),
			patch.object(module.frappe, "get_list", return_value=listed) as get_list,
		):
			result = module.asset_movement_items("MOV-RET", "", 0, 20)
		movement.check_permission.assert_called_once_with("read")
		self.assertEqual(get_list.call_args.kwargs["filters"], {"name": ["in", ["ITEM-A"]]})
		self.assertEqual(result, [("ITEM-A", "Item A")])

	def test_item_search_without_a_movement_is_empty(self):
		self.assertEqual(module.asset_movement_items(None, "", 0, 20), [])

	def test_item_search_of_a_cancelled_movement_is_empty(self):
		movement = Mock(docstatus=2)
		with (
			patch.object(module.frappe, "get_doc", return_value=movement),
			patch.object(module.frappe, "get_list") as get_list,
		):
			self.assertEqual(module.asset_movement_items("MOV-RET", "", 0, 20), [])
		get_list.assert_not_called()

	def test_update_reference_writes_only_the_inspected_row(self):
		qi = self._qi(child_row_reference="ROW-2", docstatus=1, name="QI-9")
		with (
			patch.object(module.frappe, "db", Mock()) as db,
			patch.object(module.frappe, "qb", Mock()) as query,
		):
			QualityInspection.update_qc_reference(qi)
			QualityInspection.update_qc_reference(frappe._dict(qi, docstatus=2))
		self.assertEqual(
			[call.args for call in db.set_value.call_args_list],
			[
				("Asset Movement Item", "ROW-2", "quality_inspection", "QI-9"),
				("Asset Movement Item", "ROW-2", "quality_inspection", ""),
			],
		)
		query.update.assert_not_called()

	def test_update_reference_without_a_row_touches_nothing(self):
		with (
			patch.object(module.frappe, "db", Mock()) as db,
			patch.object(module.frappe, "qb", Mock()) as query,
		):
			QualityInspection.update_qc_reference(self._qi(docstatus=1, name="QI-9"))
		db.set_value.assert_not_called()
		query.update.assert_not_called()


if __name__ == "__main__":
	unittest.main()
