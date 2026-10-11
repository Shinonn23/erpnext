# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.controllers.stock_controller import StockController
from erpnext.stock.doctype.storage_location.storage_location import (
	get_storage_location_warehouses,
	validate_storage_location,
)

CONTROLLER = "erpnext.controllers.stock_controller"
STORAGE_LOCATION = "erpnext.stock.doctype.storage_location.storage_location"


def raise_validation_error(message, **kwargs):
	raise frappe.ValidationError(message)


class StockDocument(frappe._dict):
	"""Exercise controller methods without initializing a site or a Document."""

	def update_storage_location(self, row, ledger):
		StockController.update_storage_location(self, row, ledger)

	def update_inventory_dimensions(self, row, ledger):
		StockController.update_inventory_dimensions(self, row, ledger)

	def is_service_item_row(self, row):
		return StockController.is_service_item_row(self, row)

	def validate_mandatory_dimension_row(self, row, dimension, mandatory_fields, child_meta):
		StockController.validate_mandatory_dimension_row(self, row, dimension, mandatory_fields, child_meta)


class TestStockControllerStorageLocation(unittest.TestCase):
	def setUp(self):
		self.doc = StockDocument(
			doctype="Stock Entry",
			name="TEST-ENTRY",
			company="Company",
			docstatus=1,
			posting_date="2026-10-12",
			posting_time="10:00:00",
			is_return=0,
		)
		self.row = frappe._dict(
			name="TEST-ROW",
			item_code="Stock Item",
			warehouse="Source",
			stock_qty=2,
			s_warehouse="Source",
			t_warehouse="Target",
			storage_location="Source Shelf",
			to_storage_location="Target Shelf",
			from_warehouse="Source",
			from_storage_location="From Shelf",
			target_warehouse="Target",
			rejected_warehouse="Rejected",
			rejected_storage_location="Rejected Shelf",
		)
		self.enterContext(patch(f"{CONTROLLER}.get_fiscal_year", return_value=("2026",)))
		self.enterContext(patch("frappe.get_cached_value", return_value="Nos"))
		self.dimensions = self.enterContext(
			patch(f"{CONTROLLER}.get_evaluated_inventory_dimension", return_value=[])
		)
		self.enterContext(patch("erpnext.deprecation_dumpster.deprecation_warning"))

	def ledger(self, warehouse, qty, **kwargs):
		return StockController.get_sl_entries(
			self.doc, self.row, {"warehouse": warehouse, "actual_qty": qty, **kwargs}
		)

	def test_stock_entry_source_and_target_are_native_without_inventory_dimension(self):
		self.assertEqual(self.ledger("Source", -2).storage_location, "Source Shelf")
		self.assertEqual(self.ledger("Target", 2).storage_location, "Target Shelf")

	def test_reconciliation_direct_dimension_update_sets_native_location(self):
		self.doc.doctype = "Stock Reconciliation"
		ledger = frappe._dict(warehouse="Source", actual_qty=2)
		StockController.update_inventory_dimensions(self.doc, self.row, ledger)
		self.assertEqual(ledger.storage_location, "Source Shelf")

	def test_same_warehouse_transfer_distinguishes_locations_by_sign(self):
		self.row.t_warehouse = "Source"
		self.assertEqual(self.ledger("Source", -2).storage_location, "Source Shelf")
		self.assertEqual(self.ledger("Source", 2).storage_location, "Target Shelf")

	def test_cancel_preserves_caller_source_and_target_mapping(self):
		self.doc.docstatus = 2
		self.assertEqual(self.ledger("Source", -2).storage_location, "Source Shelf")
		self.assertEqual(self.ledger("Target", 2).storage_location, "Target Shelf")

	def test_explicit_location_and_explicit_none_override_row_location(self):
		for location in ("Explicit Shelf", None):
			with self.subTest(location=location):
				ledger = self.ledger("Target", 2, storage_location=location)
				self.assertIn("storage_location", ledger)
				self.assertEqual(ledger.storage_location, location)

	def test_purchase_source_and_rejected_mapping_for_receipts_and_returns(self):
		for doctype in ("Purchase Receipt", "Purchase Invoice"):
			self.doc.doctype = doctype
			for is_return, qty in ((0, 2), (1, -2)):
				self.doc.is_return = is_return
				with self.subTest(doctype=doctype, is_return=is_return):
					self.assertEqual(self.ledger("Source", -qty).storage_location, "From Shelf")
					self.assertEqual(self.ledger("Rejected", qty).storage_location, "Rejected Shelf")
					self.assertEqual(self.ledger("Accepted", qty).storage_location, "Source Shelf")

	def test_purchase_source_falls_back_to_primary_location(self):
		self.doc.doctype = "Purchase Receipt"
		self.row.from_storage_location = None
		self.assertEqual(self.ledger("Source", -2).storage_location, "Source Shelf")

	def test_sales_target_mapping_for_deliveries_and_returns(self):
		for doctype in ("Delivery Note", "Sales Invoice", "POS Invoice"):
			self.doc.doctype = doctype
			for is_return, qty in ((0, -2), (1, 2)):
				self.doc.is_return = is_return
				with self.subTest(doctype=doctype, is_return=is_return):
					self.assertEqual(self.ledger("Source", qty).storage_location, "Source Shelf")
					self.assertEqual(self.ledger("Target", -qty).storage_location, "Target Shelf")
		self.row.to_storage_location = None
		self.assertEqual(self.ledger("Target", -qty).storage_location, "Source Shelf")

	def test_explicit_source_and_rejected_locations_when_warehouses_overlap(self):
		self.doc.doctype = "Purchase Receipt"
		self.row.warehouse = "Accepted"
		self.row.rejected_warehouse = self.row.from_warehouse
		for docstatus in (1, 2):
			self.doc.docstatus = docstatus
			with self.subTest(docstatus=docstatus):
				self.assertEqual(
					self.ledger("Source", -2, storage_location="From Shelf").storage_location, "From Shelf"
				)
				self.assertEqual(
					self.ledger("Source", 2, storage_location="Rejected Shelf").storage_location,
					"Rejected Shelf",
				)

	def test_ordinary_and_subcontracting_rows_use_primary_location(self):
		self.row.from_warehouse = None
		for doctype in ("Stock Reconciliation", "Subcontracting Receipt", "Asset Capitalization"):
			self.doc.doctype = doctype
			with self.subTest(doctype=doctype):
				self.assertEqual(self.ledger("Source", 2).storage_location, "Source Shelf")
		self.doc.doctype = "Subcontracting Receipt"
		self.assertEqual(self.ledger("Rejected", 2).storage_location, "Rejected Shelf")

	def test_wrapped_item_row_supplies_location(self):
		self.doc.doctype = "Delivery Note"
		self.row = frappe._dict(
			name="PACKED-ROW",
			item_code="Stock Item",
			warehouse="Source",
			stock_qty=2,
			item_row=self.row,
		)
		self.assertEqual(self.ledger("Target", 2).storage_location, "Target Shelf")

	def test_generic_storage_dimension_cannot_overwrite_native_or_explicit_location(self):
		self.dimensions.return_value = [
			frappe._dict(source_fieldname="legacy_location", target_fieldname="storage_location"),
			frappe._dict(source_fieldname="batch_zone", target_fieldname="batch_zone"),
		]
		self.row.legacy_location = "Wrong Shelf"
		self.row.batch_zone = "Zone A"
		ledger = self.ledger("Source", -2)
		self.assertEqual(ledger.storage_location, "Source Shelf")
		self.assertEqual(ledger.batch_zone, "Zone A")
		self.assertIsNone(self.ledger("Source", -2, storage_location=None).storage_location)

	def validation_document(self, **kwargs):
		self.doc.meta = Mock()
		self.doc.meta.get_table_fields.return_value = [
			frappe._dict(fieldname="items"),
			frappe._dict(fieldname="supplied_items"),
		]
		self.row.meta = Mock()
		self.row.meta.has_field.return_value = True
		self.doc["items"] = [self.row]
		self.doc.supplied_items = []
		self.doc.update(kwargs)
		return self.doc

	def test_validation_skips_cancellation_and_invoices_without_stock_updates(self):
		with patch(f"{CONTROLLER}.validate_storage_location_warehouse") as validate:
			for doctype, docstatus, update_stock in (
				("Stock Entry", 2, 1),
				("Sales Invoice", 1, 0),
				("Purchase Invoice", 1, 0),
				("POS Invoice", 1, 0),
			):
				with self.subTest(doctype=doctype, docstatus=docstatus):
					StockController.validate_storage_locations(
						self.validation_document(
							doctype=doctype,
							docstatus=docstatus,
							update_stock=update_stock,
						)
					)
			validate.assert_not_called()

	def test_validation_checks_stock_rows_in_all_child_tables_and_skips_services(self):
		self.validation_document()
		service = frappe._dict(item_code="Service", storage_location="Service Shelf")
		supplied = frappe._dict(
			rm_item_code="Raw Material", storage_location="Supplier Shelf", meta=self.row.meta
		)
		self.doc["items"].append(service)
		self.doc.supplied_items.append(supplied)
		with (
			patch("frappe.get_cached_value", side_effect=lambda doctype, code, field: code != "Service"),
			patch(
				f"{CONTROLLER}.get_storage_location_warehouses",
				return_value={
					"storage_location": "Warehouse",
					"to_storage_location": "Target",
					"from_storage_location": "Source",
					"rejected_storage_location": "Rejected",
				},
			) as warehouses,
			patch(f"{CONTROLLER}.validate_storage_location_warehouse") as validate,
		):
			StockController.validate_storage_locations(self.doc)
			self.assertEqual(warehouses.call_count, 2)
			warehouses.assert_any_call(self.doc, self.row)
			warehouses.assert_any_call(self.doc, supplied)
			validate.assert_any_call("Source Shelf", "Warehouse")
			validate.assert_any_call("Target Shelf", "Target")
			validate.assert_any_call("From Shelf", "Source")
			validate.assert_any_call("Rejected Shelf", "Rejected")
			validate.assert_any_call("Supplier Shelf", "Warehouse")
			self.assertEqual(validate.call_count, 5)

	def test_validation_propagates_invalid_location(self):
		with (
			patch("frappe.get_cached_value", return_value=1),
			patch(
				f"{CONTROLLER}.validate_storage_location_warehouse", side_effect=ValueError("Invalid shelf")
			),
		):
			with self.assertRaisesRegex(ValueError, "Invalid shelf"):
				StockController.validate_storage_locations(self.validation_document())

	def test_validation_uses_native_warehouse_mapping_for_transfers(self):
		with patch(f"{CONTROLLER}.validate_storage_location_warehouse") as validate:
			StockController.validate_storage_locations(self.validation_document())
			validate.assert_any_call("Source Shelf", "Source")
			validate.assert_any_call("Target Shelf", "Target")
			validate.assert_any_call("From Shelf", "Source")
			validate.assert_any_call("Rejected Shelf", "Rejected")
			self.assertEqual(validate.call_count, 4)

	def test_empty_locations_are_allowed_before_posting(self):
		for fieldname in (
			"storage_location",
			"to_storage_location",
			"from_storage_location",
			"rejected_storage_location",
		):
			self.row[fieldname] = None
		with patch(f"{CONTROLLER}.validate_storage_location_warehouse") as validate:
			for doctype in (
				"Stock Entry",
				"Quotation",
				"Sales Order",
				"Sales Invoice",
				"Supplier Quotation",
				"Purchase Order",
				"Purchase Invoice",
				"Material Request",
			):
				for docstatus in (0, 1):
					with self.subTest(doctype=doctype, docstatus=docstatus):
						StockController.validate_storage_locations(
							self.validation_document(doctype=doctype, docstatus=docstatus, update_stock=0)
						)
			validate.assert_not_called()

	def test_legacy_mandatory_storage_dimension_does_not_require_draft_location(self):
		self.row.doctype = "Stock Entry Detail"
		self.row.storage_location = None
		with (
			patch(
				f"{CONTROLLER}.get_mandatory_inventory_dimensions",
				return_value=[frappe._dict(source_fieldname="storage_location", reqd=1)],
			),
			patch("frappe.get_meta"),
			patch(f"{CONTROLLER}.get_mandatory_dimension_fields") as fields,
		):
			StockController.validate_mandatory_dimensions_in_table(self.doc, [self.row])
			fields.assert_not_called()

	def test_other_mandatory_inventory_dimensions_are_still_enforced(self):
		self.row.doctype = "Stock Entry Detail"
		self.row.idx = 1
		self.row.batch_zone = None
		meta = Mock()
		meta.has_field.return_value = True
		meta.get_label.return_value = "Batch Zone"
		with (
			patch(
				f"{CONTROLLER}.get_mandatory_inventory_dimensions",
				return_value=[
					frappe._dict(source_fieldname="storage_location", reqd=1),
					frappe._dict(name="Batch Zone", source_fieldname="batch_zone", reqd=1),
				],
			),
			patch("frappe.get_meta", return_value=meta),
			patch(f"{CONTROLLER}.get_mandatory_dimension_fields", return_value=[("batch_zone", None)]),
			patch(f"{CONTROLLER}._", side_effect=lambda text: text),
			patch(f"{CONTROLLER}.bold", side_effect=lambda text: text),
			patch("frappe.throw", side_effect=raise_validation_error),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Batch Zone is mandatory"):
				StockController.validate_mandatory_dimensions_in_table(self.doc, [self.row])

	def assert_missing_posting_location_rejected(self, actual_qty):
		ledger = frappe._dict(item_code="Stock Item", warehouse="Source", actual_qty=actual_qty)
		with (
			patch(f"{STORAGE_LOCATION}._", side_effect=lambda text: text),
			patch("frappe.throw", side_effect=raise_validation_error),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Storage Location is required"):
				validate_storage_location(ledger)

	def test_incoming_ledger_requires_location(self):
		self.assert_missing_posting_location_rejected(2)

	def test_outgoing_ledger_requires_location(self):
		self.assert_missing_posting_location_rejected(-2)

	def test_zero_quantity_valuation_ledger_requires_location(self):
		self.assert_missing_posting_location_rejected(0)

	def test_cancellation_preserves_historical_missing_location(self):
		with patch("frappe.get_cached_value") as get_location:
			validate_storage_location(
				frappe._dict(warehouse="Source", actual_qty=2, storage_location=None, is_cancelled=1)
			)
			get_location.assert_not_called()

	def test_posted_location_must_exist_belong_to_warehouse_and_be_active(self):
		ledger = frappe._dict(warehouse="Source", actual_qty=2, storage_location="Source Shelf")
		for location, error in (
			(None, "does not belong"),
			(frappe._dict(warehouse="Target", disabled=0), "does not belong"),
			(frappe._dict(warehouse="Source", disabled=1), "is disabled"),
			(frappe._dict(warehouse="Source", disabled=0), None),
		):
			with (
				self.subTest(location=location),
				patch("frappe.get_cached_value", return_value=location) as get_location,
				patch(f"{STORAGE_LOCATION}._", side_effect=lambda text: text),
				patch("frappe.throw", side_effect=raise_validation_error),
			):
				if error:
					with self.assertRaisesRegex(frappe.ValidationError, error):
						validate_storage_location(ledger)
				else:
					validate_storage_location(ledger)
				get_location.assert_called_once_with(
					"Storage Location", "Source Shelf", ["warehouse", "disabled"], as_dict=True
				)

	def test_raw_material_supplied_rows_belong_to_supplier_warehouse(self):
		self.doc.doctype = "Subcontracting Receipt"
		self.doc.supplier_warehouse = "Supplier"
		row = frappe._dict(rm_item_code="Raw Material", parentfield="supplied_items", warehouse="Finished")
		self.assertEqual(get_storage_location_warehouses(self.doc, row)["storage_location"], "Supplier")
		row.parentfield = "items"
		self.assertEqual(get_storage_location_warehouses(self.doc, row)["storage_location"], "Finished")

	def test_warehouse_mapping_uses_sales_target_and_purchase_source(self):
		self.doc.doctype = "Delivery Note"
		self.row.t_warehouse = None
		self.assertEqual(get_storage_location_warehouses(self.doc, self.row)["to_storage_location"], "Target")
		self.doc.doctype = "Purchase Receipt"
		self.assertEqual(
			get_storage_location_warehouses(self.doc, self.row)["from_storage_location"], "Source"
		)
