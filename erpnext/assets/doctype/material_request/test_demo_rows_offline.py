"""Offline: a Demo / Borrow request stores only what applies to each row (UX WP7, 19 Nov 2026)."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext.assets.doctype.material_request import material_request as requests


class TestDemoRows(TestCase):
	def setUp(self):
		self.items = {
			"REAGENT": frappe._dict(is_stock_item=1, stock_uom="Box", disabled=0),
		}
		for context in (
			patch.object(
				frappe,
				"get_cached_value",
				side_effect=lambda doctype, name, fields, as_dict=False: self.items.get(name),
			),
			patch.object(frappe, "throw", side_effect=ValueError),
			patch.object(requests, "_", lambda text: text),
		):
			context.start()
			self.addCleanup(context.stop)

	def request(self, rows):
		doc = requests.CustomerMaterialRequest.__new__(requests.CustomerMaterialRequest)
		doc.__dict__.update(
			{
				"material_request_type": "Customer Demo",
				"customer_location": "Customer Site",
				"schedule_date": "2026-11-30",
				"items": [frappe._dict(idx=index, **row) for index, row in enumerate(rows, 1)],
			}
		)
		return doc

	def test_a_stock_row_gets_no_asset_location_from_the_header(self):
		doc = self.request([{"item_code": "REAGENT", "item_type": "Stock Item", "qty": 2}])
		doc.validate_demo_items()
		row = doc.items[0]
		self.assertIsNone(row.get("target_location"))
		self.assertEqual(row.uom, "Box")
		self.assertEqual(row.schedule_date, "2026-11-30")
		# the customer's Location stays on the header
		self.assertEqual(doc.customer_location, "Customer Site")

	def test_a_stock_row_with_no_quantity_is_refused(self):
		doc = self.request([{"item_code": "REAGENT", "item_type": "Stock Item", "qty": 0}])
		with self.assertRaises(ValueError):
			doc.validate_demo_items()

	def test_an_asset_row_keeps_no_warehouse_or_storage_location(self):
		doc = self.request(
			[
				{
					"item_code": "MODEL",
					"asset": "ASSET-A",
					"warehouse": "CD STOCK - CD",
					"from_warehouse": "CD STOCK - CD",
					"storage_location": "CD STOCK - CD / A1",
					"from_storage_location": "CD STOCK - CD / A1",
				}
			]
		)
		doc.material_request_type = "Asset Transfer"
		for name in (
			"reset_amended_workflow_state",
			"clear_loan_recipient_details",
			"clear_customer_details",
		):
			setattr(doc, name, lambda: None)
		doc.before_validate()
		row = doc.items[0]
		self.assertEqual(row.item_type, "Fixed Asset")
		for field in ("warehouse", "from_warehouse", "storage_location", "from_storage_location"):
			self.assertIsNone(row.get(field), field)
