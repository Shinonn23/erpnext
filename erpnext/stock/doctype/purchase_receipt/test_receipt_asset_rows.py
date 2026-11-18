"""Fixed-asset rows of a Purchase Receipt / Invoice and the bin a quarantined row was meant for (UX WP6, 19 Nov 2026).
Offline: mocks, no site."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe

from erpnext.controllers.buying_controller import BuyingController
from erpnext.stock.doctype.quality_inspection import purchase_inspection_units as units
from erpnext.utilities.transaction_base import TransactionBase

ROOT = Path(__file__).resolve().parents[3]
ROW_JSONS = {
	"Purchase Receipt Item": ROOT / "stock/doctype/purchase_receipt_item/purchase_receipt_item.json",
	"Purchase Invoice Item": ROOT / "accounts/doctype/purchase_invoice_item/purchase_invoice_item.json",
}
PARENT_JSONS = {
	"Purchase Receipt": ROOT / "stock/doctype/purchase_receipt/purchase_receipt.json",
	"Purchase Invoice": ROOT / "accounts/doctype/purchase_invoice/purchase_invoice.json",
}


def refuse(message, *args, **kwargs):
	raise frappe.ValidationError(message)


def fields(path):
	data = json.loads(path.read_text())
	return {field["fieldname"]: field for field in data["fields"]}, data["field_order"]


class TestRowJson(unittest.TestCase):
	def test_an_asset_row_hides_the_stock_fields_and_requires_its_asset_location(self):
		for doctype, path in ROW_JSONS.items():
			by_name, order = fields(path)
			for fieldname in (
				"storage_location",
				"rejected_qty",
				"rejected_warehouse",
				"rejected_storage_location",
			):
				self.assertIn("!doc.is_fixed_asset", by_name[fieldname]["depends_on"], (doctype, fieldname))
			self.assertIn("doc.rejected_warehouse", by_name["rejected_storage_location"]["depends_on"])
			self.assertIn("doc.is_fixed_asset", by_name["asset_location"]["mandatory_depends_on"], doctype)
			self.assertEqual(
				by_name["storage_location"]["read_only_depends_on"], "eval:doc.putaway_warehouse"
			)
			putaway = by_name["putaway_storage_location"]
			self.assertEqual(
				(putaway["fieldtype"], putaway["options"], putaway["no_copy"], putaway["read_only"]),
				("Link", "Storage Location", 1, 1),
			)
			self.assertEqual(order.index("putaway_storage_location"), order.index("putaway_warehouse") + 1)

	def test_the_header_warehouse_is_the_warehouse_the_goods_are_received_for(self):
		for path in PARENT_JSONS.values():
			by_name, _order = fields(path)
			self.assertEqual(by_name["set_warehouse"]["label"], "Receive for Warehouse")


class TestAssetLocationOnSave(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "throw", side_effect=refuse)
		patcher.start()
		self.addCleanup(patcher.stop)

	def doc(self, doctype="Purchase Receipt", **values):
		doc = frappe._dict(
			doctype=doctype,
			is_return=0,
			update_stock=1,
			items=[
				frappe._dict(idx=1, item_code="STOCK", is_fixed_asset=0),
				frappe._dict(idx=2, item_code="SCANNER", is_fixed_asset=1, asset_location=None),
			],
		)
		doc.update(values)
		return doc

	def test_a_fixed_asset_row_without_its_asset_location_is_refused_on_save(self):
		for doctype in ("Purchase Receipt", "Purchase Invoice"):
			with self.assertRaisesRegex(frappe.ValidationError, "Row #2"):
				BuyingController.validate_asset_locations(self.doc(doctype))

	def test_a_named_location_a_return_or_an_invoice_without_stock_pass(self):
		doc = self.doc()
		doc["items"][1].asset_location = "HQ"
		BuyingController.validate_asset_locations(doc)
		BuyingController.validate_asset_locations(self.doc(is_return=1))
		invoice = self.doc("Purchase Invoice")
		invoice.update_stock = 0
		BuyingController.validate_asset_locations(invoice)
		BuyingController.validate_asset_locations(self.doc("Purchase Order"))


class TestReceiveForWarehouse(unittest.TestCase):
	def test_rows_held_in_quarantine_keep_the_header_warehouse(self):
		doc = MagicMock()
		doc.get.return_value = [
			frappe._dict(warehouse="QUARANTINE - CD STOCK - CD", putaway_warehouse="CD STOCK - CD"),
			frappe._dict(warehouse="CD STOCK - CD"),
		]
		TransactionBase.reset_default_field_value(doc, "set_warehouse", "items", "warehouse")
		doc.set.assert_not_called()
		doc.get.return_value.append(frappe._dict(warehouse="LAB GS - CD"))
		TransactionBase.reset_default_field_value(doc, "set_warehouse", "items", "warehouse")
		doc.set.assert_called_once_with("set_warehouse", None)


class TestReleaseToTheChosenBin(unittest.TestCase):
	def setUp(self):
		self.locations = {"CD STOCK - CD / A1": "CD STOCK - CD", "LAB / B1": "LAB GS - CD"}
		entry = MagicMock()
		entry.items = []
		entry.append.side_effect = lambda table, row: entry.items.append(row)
		self.entry = entry
		db = MagicMock()
		db.get_value.side_effect = lambda doctype, name, *args, **kwargs: (
			self.locations.get(name) if doctype == "Storage Location" else None
		)
		for patcher in (
			patch.object(units.frappe, "db", db),
			patch.object(units.frappe, "new_doc", return_value=entry),
			patch.object(units, "_preferred_location", return_value="CD STOCK - CD / PREFERRED"),
			patch.object(units, "write_off_warehouse", return_value="CD STOCK DAMAGE - CD"),
			patch(
				"erpnext.stock.doctype.storage_location.storage_location.is_receiving_location",
				side_effect=lambda location, warehouse=None: location.endswith("STAGING"),
			),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def release(self, status="Accepted", putaway_location=None):
		doc = frappe._dict(doctype="Purchase Receipt", name="PR-1", company="CD")
		source = frappe._dict(
			name="r1", stock_uom="Nos", storage_location="QUARANTINE - CD STOCK - CD / STAGING"
		)
		row = {
			"row": "r1",
			"item_code": "A",
			"status": status,
			"home": "CD STOCK - CD",
			"quarantine": "QUARANTINE - CD STOCK - CD",
			"putaway_location": putaway_location,
			"parts": [{"qty": 2, "batch_no": None, "serial_nos": []}],
		}
		units.build_release_entry(doc, [source], [row])
		return self.entry.items[-1]["to_storage_location"]

	def test_accepted_goods_go_to_the_bin_the_receipt_row_was_meant_for(self):
		self.assertEqual(self.release(putaway_location="CD STOCK - CD / A1"), "CD STOCK - CD / A1")

	def test_else_the_items_preferred_one(self):
		self.assertEqual(self.release(), "CD STOCK - CD / PREFERRED")
		# a bin of another warehouse, or a Receiving one, is not a target
		self.assertEqual(self.release(putaway_location="LAB / B1"), "CD STOCK - CD / PREFERRED")
		self.locations["CD STOCK - CD / STAGING"] = "CD STOCK - CD"
		self.assertEqual(
			self.release(putaway_location="CD STOCK - CD / STAGING"), "CD STOCK - CD / PREFERRED"
		)

	def test_rejected_goods_do_not_go_to_the_chosen_bin(self):
		self.assertEqual(
			self.release(status="Rejected", putaway_location="CD STOCK - CD / A1"),
			"CD STOCK - CD / PREFERRED",
		)


if __name__ == "__main__":
	unittest.main()
