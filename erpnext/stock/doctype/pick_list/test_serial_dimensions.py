"""Serial selection and legacy bundle dimensions without a site or database."""
import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.controllers.stock_controller import StockController
from erpnext.stock.doctype.pick_list.pick_list import PickList, map_pl_locations
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import get_available_serial_nos

PICK = "erpnext.stock.doctype.pick_list.pick_list"
BUNDLE = "erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle"


class StockDocument(frappe._dict):
	def is_internal_transfer(self):
		return self.get("internal_transfer", False)

	def update_storage_location(self, row, details):
		StockController.update_storage_location(self, row, details)


class TestSerialDimensions(unittest.TestCase):
	def test_serial_selection_accepts_single_and_multiple_warehouses(self):
		with (
			patch(f"{BUNDLE}.get_reserved_serial_nos_for_sre", return_value=[]),
			patch(f"{BUNDLE}.get_reserved_serial_nos_for_pos", return_value=[]),
			patch(f"{BUNDLE}.get_serial_nos_based_on_filters", return_value=[]) as select,
		):
			for warehouse, expected in (
				("Source", "Source"),
				(["Source"], ("in", ["Source"])),
				(["Source", "Other"], ("in", ["Source", "Other"])),
			):
				with self.subTest(warehouse=warehouse):
					get_available_serial_nos(frappe._dict(item_code="Item", warehouse=warehouse))
					self.assertEqual(select.call_args.args[0]["warehouse"], expected)

	def test_pick_legacy_bundle_keeps_storage_location(self):
		row = frappe._dict(
			item_code="Item", warehouse="Source", storage_location="Source Shelf", stock_qty=2,
			serial_no="SN-1\nSN-2", batch_no=None, use_serial_batch_fields=1,
			serial_and_batch_bundle=None, name="ROW", db_set=Mock(),
		)
		doc = frappe._dict(doctype="Pick List", name="PICK", company="Company", locations=[row])
		with patch(f"{PICK}.SerialBatchCreation") as creation:
			creation.return_value.make_serial_and_batch_bundle.return_value.name = "BUNDLE"
			PickList.make_bundle_using_old_serial_batch_fields(doc)
			self.assertEqual(creation.call_args.args[0]["storage_location"], "Source Shelf")
			self.assertEqual(creation.call_args.args[0]["serial_nos"], ["SN-1", "SN-2"])

	def test_pick_delivery_mapper_keeps_picked_location(self):
		location = frappe._dict(
			sales_order=None, sales_order_item=None, product_bundle_item=None,
			picked_qty=2, delivered_qty=0, warehouse="Source", storage_location="Picked Shelf",
			batch_no=None, serial_no=None, use_serial_batch_fields=0, name="ROW",
		)
		pick = frappe._dict(locations=[location], name="PICK", company="Company")
		child = frappe._dict(conversion_factor=1)
		target = frappe._dict(items=[child])
		with (
			patch(f"{PICK}.map_child_doc", return_value=child),
			patch(f"{PICK}.add_product_bundles_to_target"),
			patch(f"{PICK}.set_target_missing_values"),
		):
			map_pl_locations(pick, {}, target)
		self.assertEqual((child.warehouse, child.storage_location, child.qty), ("Source", "Picked Shelf", 2))

	def test_stock_bundle_uses_native_location_for_each_movement_leg(self):
		for doctype, row, is_return, internal, rejected, expected in (
			("Delivery Note", {}, 0, False, False, "Source Shelf"),
			("POS Invoice", {}, 0, False, False, "Source Shelf"),
			("Stock Entry", {"s_warehouse": "Source", "t_warehouse": "Source", "transfer_qty": 2}, 0, False, False, "Source Shelf"),
			("Stock Entry", {"s_warehouse": None, "t_warehouse": "Source", "transfer_qty": 2}, 0, False, False, "Target Shelf"),
			("Purchase Receipt", {"rejected_warehouse": "Source", "rejected_qty": 2}, 0, False, True, "Rejected Shelf"),
			("Purchase Receipt", {"from_warehouse": "Source", "warehouse": "Target"}, 0, True, False, "From Shelf"),
			("Delivery Note", {"target_warehouse": "Target"}, 1, True, False, "Target Shelf"),
		):
			with self.subTest(doctype=doctype, row=row):
				document = StockDocument(doctype=doctype, is_return=is_return, internal_transfer=internal)
				item = frappe._dict(
					warehouse="Source", qty=2, stock_qty=2, serial_no=None, batch_no="BATCH",
					storage_location="Source Shelf", to_storage_location="Target Shelf",
					from_storage_location="From Shelf", rejected_storage_location="Rejected Shelf",
					conversion_factor=1,
				)
				item.update(row)
				details = {}
				inward = doctype in ("Purchase Receipt", "Stock Entry") and not item.get("s_warehouse")
				with (
					patch(
						"erpnext.controllers.stock_controller.get_type_of_transaction",
						return_value="Inward" if inward else "Outward",
					),
					patch("frappe.get_precision", return_value=2),
				):
					StockController.update_bundle_details(document, details, "items", item, is_rejected=rejected)
				self.assertEqual(details["storage_location"], expected)
