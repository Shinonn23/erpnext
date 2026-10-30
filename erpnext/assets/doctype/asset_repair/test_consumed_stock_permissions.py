"""Offline tests: the Stock Entry a repair's submit makes and the bundle its cancel reverses skip the permission
check, which the repair's own submit or cancel already passed."""

import unittest
from unittest.mock import Mock, patch

import frappe


def entries():
	return getattr(frappe.local, "asset_repair_consumption_entries", None) or {}


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
		entry.name = "MAT-STE-1"
		entry.flags = frappe._dict()
		seen = {}

		def submit():
			seen.update(submit=dict(entry.flags), consuming=dict(entries()))

		entry.insert.side_effect = lambda: seen.update(insert=dict(entry.flags))
		entry.submit.side_effect = submit
		with (
			patch.object(repair.frappe, "get_doc", return_value=entry),
			patch.object(repair, "get_accounting_dimensions", return_value=[]),
		):
			repair.AssetRepair.decrease_stock_quantity(doc)
		entry.insert.assert_called_once_with()
		entry.submit.assert_called_once_with()
		seen["after"] = dict(entries())
		return seen

	def test_insert_and_submit_skip_the_permission_check(self):
		seen = self.run_decrease()
		for step in ("insert", "submit"):
			self.assertTrue(seen[step]["ignore_permissions"], step)

	def test_the_entry_names_its_repair(self):
		seen = self.run_decrease()
		self.assertEqual(seen["insert"]["asset_repair_consumption"], "ACC-ASR-1")
		entry = frappe._dict(flags=frappe._dict(seen["submit"]))
		self.assertEqual(module().get_native_repair_consumption_source(entry), "ACC-ASR-1")

	def test_the_entry_is_marked_as_consuming_only_while_it_submits(self):
		seen = self.run_decrease()
		self.assertEqual(seen["consuming"], {"MAT-STE-1": "ACC-ASR-1"})
		self.assertEqual(seen["after"], {})

	def test_a_supplied_flag_is_not_the_mark(self):
		entry = frappe._dict(
			flags=frappe._dict(asset_repair_consumption="ACC-ASR-1", asset_repair_consumption_token=object())
		)
		self.assertIsNone(module().get_native_repair_consumption_source(entry))
		self.assertIsNone(module().get_native_repair_consumption_source(frappe._dict(flags=frappe._dict())))

	def test_a_repair_without_stock_items_makes_no_entry(self):
		repair = module()
		doc = Mock()
		doc.get.return_value = []
		with patch.object(repair.frappe, "get_doc") as get_doc:
			repair.AssetRepair.decrease_stock_quantity(doc)
		get_doc.assert_not_called()


class TestRepairBundleCancelIsBySystem(unittest.TestCase):
	def run_cancel(self, voucher_type, voucher_no, entry_repair=None):
		repair = module()
		rows = [Mock(serial_and_batch_bundle="SABB-1"), Mock(serial_and_batch_bundle=None)]
		doc = Mock(stock_items=rows)
		doc.name = "ACC-ASR-1"
		doc.owns_consumed_bundle = lambda bundle: repair.AssetRepair.owns_consumed_bundle(doc, bundle)
		bundle = Mock(voucher_type=voucher_type, voucher_no=voucher_no)
		bundle.flags = frappe._dict()
		seen = {}
		bundle.cancel.side_effect = lambda: seen.update(ignore_permissions=bundle.flags.ignore_permissions)
		with (
			patch.object(repair.frappe, "get_doc", return_value=bundle) as get_doc,
			patch.object(repair.frappe, "db", Mock()) as db,
		):
			db.get_value.return_value = entry_repair
			repair.AssetRepair.cancel_sabb(doc)
		get_doc.assert_called_once_with("Serial and Batch Bundle", "SABB-1")
		bundle.cancel.assert_called_once_with()
		rows[0].db_set.assert_called_once_with("serial_and_batch_bundle", None)
		return seen["ignore_permissions"], db.get_value

	def test_a_bundle_handed_back_to_this_repair_is_cancelled_without_the_check(self):
		ignored, _get_value = self.run_cancel("Asset Repair", "ACC-ASR-1")
		self.assertTrue(ignored)

	def test_a_bundle_of_this_repairs_stock_entry_is_cancelled_without_the_check(self):
		ignored, get_value = self.run_cancel("Stock Entry", "MAT-STE-1", entry_repair="ACC-ASR-1")
		self.assertTrue(ignored)
		get_value.assert_called_once_with("Stock Entry", "MAT-STE-1", "asset_repair")

	def test_any_other_bundle_keeps_the_cancellers_check(self):
		for voucher_type, voucher_no, entry_repair in (
			("Asset Repair", "ACC-ASR-2", None),
			("Stock Entry", "MAT-STE-9", "ACC-ASR-2"),
			("Stock Entry", "MAT-STE-9", None),
			("Purchase Receipt", "PR-1", None),
			("Stock Entry", None, None),
		):
			with self.subTest(voucher_type=voucher_type, voucher_no=voucher_no):
				ignored, _get_value = self.run_cancel(voucher_type, voucher_no, entry_repair)
				self.assertFalse(ignored)


class TestRepairCancelReversesItsStockEntry(unittest.TestCase):
	def test_each_submitted_entry_of_the_repair_is_cancelled_with_the_mark(self):
		repair = module()
		doc = Mock()
		doc.name = "ACC-ASR-1"
		entry = Mock()
		entry.name = "MAT-STE-1"
		entry.flags = frappe._dict()
		seen = {}
		entry.cancel.side_effect = lambda: seen.update(
			source=repair.get_native_repair_consumption_source(entry),
			ignore_permissions=entry.flags.ignore_permissions,
			consuming=dict(entries()),
		)
		with (
			patch.object(repair.frappe, "get_all", return_value=["MAT-STE-1"]) as get_all,
			patch.object(repair.frappe, "get_doc", return_value=entry),
		):
			repair.AssetRepair.cancel_stock_entry(doc)
		get_all.assert_called_once_with(
			"Stock Entry",
			filters={"asset_repair": "ACC-ASR-1", "docstatus": 1},
			pluck="name",
			order_by="creation",
		)
		entry.cancel.assert_called_once_with()
		self.assertEqual(seen["source"], "ACC-ASR-1")
		self.assertTrue(seen["ignore_permissions"])
		self.assertEqual(seen["consuming"], {"MAT-STE-1": "ACC-ASR-1"})
		self.assertEqual(entries(), {})

	def test_a_failed_cancel_clears_the_mark(self):
		repair = module()
		doc = Mock()
		doc.name = "ACC-ASR-1"
		entry = Mock()
		entry.flags = frappe._dict()
		entry.cancel.side_effect = RuntimeError("refused")
		with (
			patch.object(repair.frappe, "get_all", return_value=["MAT-STE-1"]),
			patch.object(repair.frappe, "get_doc", return_value=entry),
			self.assertRaises(RuntimeError),
		):
			repair.AssetRepair.cancel_stock_entry(doc)
		self.assertEqual(entries(), {})

	def test_on_cancel_reverses_the_stock_entry_before_the_bundles(self):
		repair = module()
		doc = Mock()
		doc.get.return_value = 0
		order = []
		doc.cancel_stock_entry.side_effect = lambda: order.append("stock entry")
		doc.cancel_sabb.side_effect = lambda: order.append("bundles")
		with patch.object(repair.frappe, "get_doc"):
			repair.AssetRepair.on_cancel(doc)
		self.assertEqual(order, ["stock entry", "bundles"])


class TestRepairBundleSubmitIsBySystem(unittest.TestCase):
	def submit(
		self, voucher_no="MAT-STE-1", bundle_voucher=("Stock Entry", "MAT-STE-1"), consuming=None, row=True
	):
		from erpnext.stock import serial_batch_bundle

		repair = module()
		handler = object.__new__(serial_batch_bundle.SerialBatchBundle)
		handler.sle = frappe._dict(
			voucher_type="Stock Entry",
			voucher_no=voucher_no,
			voucher_detail_no=None,
			serial_and_batch_bundle="SABB-1",
		)
		handler.validate_actual_qty = Mock()
		bundle = Mock(voucher_type=bundle_voucher[0], voucher_no=bundle_voucher[1])
		bundle.name = "SABB-1"
		bundle.flags = frappe._dict()
		seen = {}
		bundle.submit.side_effect = lambda: seen.update(ignore_permissions=bundle.flags.ignore_permissions)
		with (
			patch.object(serial_batch_bundle.frappe, "get_doc", return_value=bundle),
			patch.object(repair.frappe, "db", Mock()) as db,
		):
			db.exists.return_value = row
			if consuming:
				with repair._repair_consumption(*consuming):
					handler.submit_serial_and_batch_bundle()
			else:
				handler.submit_serial_and_batch_bundle()
		bundle.submit.assert_called_once_with()
		return seen["ignore_permissions"], db.exists

	def test_the_repairs_consumed_bundle_is_submitted_without_the_check(self):
		ignored, exists = self.submit(consuming=("MAT-STE-1", "ACC-ASR-1"))
		self.assertTrue(ignored)
		exists.assert_called_once_with(
			"Asset Repair Consumed Item",
			{"parenttype": "Asset Repair", "parent": "ACC-ASR-1", "serial_and_batch_bundle": "SABB-1"},
		)

	def test_an_ordinary_stock_entry_bundle_keeps_the_check(self):
		for label, kwargs in (
			("not consuming", {}),
			("other entry consuming", {"consuming": ("MAT-STE-2", "ACC-ASR-1")}),
			("bundle not a consumed row", {"consuming": ("MAT-STE-1", "ACC-ASR-1"), "row": False}),
			(
				"bundle of another voucher",
				{"consuming": ("MAT-STE-1", "ACC-ASR-1"), "bundle_voucher": ("Stock Entry", "MAT-STE-2")},
			),
		):
			with self.subTest(label):
				ignored, _exists = self.submit(**kwargs)
				self.assertFalse(ignored)
