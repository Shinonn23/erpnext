"""Offline tests: the Stock Entry a repair's submit makes and the bundle its cancel reverses skip the permission
check, which the repair's own submit or cancel already passed."""

import unittest
from unittest.mock import Mock, patch

import frappe


def module():
	from erpnext.assets.doctype.asset_repair import asset_repair

	return asset_repair


class TestRepairStockEntryIsPostedBySystem(unittest.TestCase):
	def run_decrease(self):
		repair = module()
		doc = Mock()
		doc.name = "ACC-ASR-1"
		doc.company = "Company"
		doc.cost_center = doc.project = None
		row = frappe._dict(
			warehouse="Stores",
			item_code="PART",
			consumed_quantity=1,
			valuation_rate=5,
			serial_and_batch_bundle=None,
		)
		doc.get.side_effect = lambda field: [row] if field == "stock_items" else None
		entry = Mock()
		entry.flags = frappe._dict()
		seen = {}
		entry.insert.side_effect = lambda: seen.update(insert=dict(entry.flags))
		entry.submit.side_effect = lambda: seen.update(submit=dict(entry.flags))
		with (
			patch.object(repair.frappe, "get_doc", return_value=entry),
			patch.object(repair, "get_accounting_dimensions", return_value=[]),
		):
			repair.AssetRepair.decrease_stock_quantity(doc)
		entry.insert.assert_called_once_with()
		entry.submit.assert_called_once_with()
		return seen

	def test_insert_and_submit_skip_the_permission_check(self):
		seen = self.run_decrease()
		for step in ("insert", "submit"):
			self.assertTrue(seen[step]["ignore_permissions"], step)

	def test_the_entry_names_its_repair(self):
		seen = self.run_decrease()
		self.assertEqual(seen["insert"]["asset_repair_consumption"], "ACC-ASR-1")

	def test_a_repair_without_stock_items_makes_no_entry(self):
		repair = module()
		doc = Mock()
		doc.get.return_value = []
		with patch.object(repair.frappe, "get_doc") as get_doc:
			repair.AssetRepair.decrease_stock_quantity(doc)
		get_doc.assert_not_called()


class TestRepairBundleCancelIsBySystem(unittest.TestCase):
	def test_only_the_repairs_own_bundles_are_cancelled_without_the_check(self):
		repair = module()
		rows = [Mock(serial_and_batch_bundle="SABB-1"), Mock(serial_and_batch_bundle=None)]
		doc = Mock(stock_items=rows)
		bundle = Mock()
		bundle.flags = frappe._dict()
		seen = {}
		bundle.cancel.side_effect = lambda: seen.update(ignore_permissions=bundle.flags.ignore_permissions)
		with patch.object(repair.frappe, "get_doc", return_value=bundle) as get_doc:
			repair.AssetRepair.cancel_sabb(doc)
		get_doc.assert_called_once_with("Serial and Batch Bundle", "SABB-1")
		bundle.cancel.assert_called_once_with()
		self.assertTrue(seen["ignore_permissions"])
		rows[0].db_set.assert_called_once_with("serial_and_batch_bundle", None)
