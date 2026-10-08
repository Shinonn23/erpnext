import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.utils import add_days, cint, today

from erpnext.assets.doctype.asset.test_asset import create_fixed_asset_item
from erpnext.assets.doctype.material_request.material_request import CustomerMaterialRequest
from erpnext.buying.doctype.supplier.test_supplier import create_supplier
from erpnext.regional.thailand.tests.utils import ensure_company_tax_settings
from erpnext.setup.doctype.employee.test_employee import make_employee
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.pick_list.pick_list import PickList
from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt
from erpnext.stock.doctype.shipment.shipment import make_shipment_from_document
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.tests.utils import ERPNextTestSuite


class FakeDocument:
	def __init__(self, doctype):
		self.doctype = doctype
		self.items = []
		self.assets = []
		self.locations = []
		self.inserted = False

	def append(self, fieldname, values):
		getattr(self, fieldname).append(frappe._dict(values))

	def insert(self):
		self.inserted = True
		return self

	def as_dict(self):
		return {
			"doctype": self.doctype,
			"items": self.items,
			"assets": self.assets,
			"locations": self.locations,
		}


class TestMaterialRequestActions(unittest.TestCase):
	def request(self, material_request_type, items, status="Pending", docstatus=1):
		return SimpleNamespace(
			reload=Mock(),
			check_permission=Mock(),
			docstatus=docstatus,
			material_request_type=material_request_type,
			request_progress_status=status,
			asset_transfer_status=status,
			company="_Test Company",
			name="MAT-MR-TEST",
			customer="_Test Customer",
			customer_address="Address-001",
			customer_address_display="Test address",
			contact_person=None,
			customer_contact_display="Test contact",
			customer_contact_email="contact@example.test",
			expected_return_date="2026-12-31",
			customer_location="Customer Location",
			customer_loan_warehouse="Customer Loan Warehouse - _TC",
			items=items,
		)

	def item(self, name, item_type, qty, fulfilled_qty=0, outstanding_qty=0, **values):
		return frappe._dict(
			name=name,
			item_code=values.pop("item_code", "ITEM-001"),
			item_type=item_type,
			qty=qty,
			fulfilled_qty=fulfilled_qty,
			outstanding_qty=outstanding_qty,
			from_warehouse=values.pop("from_warehouse", "Stores - _TC"),
			**values,
		)

	def test_demo_actions_split_available_assets_and_conversion_shortage(self):
		request = self.request(
			"Customer Demo",
			[
				self.item("MR-DEMO-STOCK", "Stock Item", 3),
				self.item("MR-DEMO-ASSET", "Fixed Asset", 1, fulfilled_qty=1, outstanding_qty=1),
			],
		)
		with (
			patch("frappe.db.get_value", return_value="DEMO-ASSET-ITEM"),
			patch("frappe.db.exists", return_value=False),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_available_assets",
				return_value=[frappe._dict(name="ASSET-AVAILABLE")],
			),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
				return_value=0,
			),
		):
			actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertTrue(actions.check_asset_availability)
		self.assertTrue(actions.asset_movement)
		self.assertTrue(actions.pick_list)
		self.assertTrue(actions.return_assets)
		self.assertTrue(actions.hold_for_asset_return)
		self.assertFalse(actions.stock_entry_issue)
		self.assertFalse(actions.stock_entry_return)

	def test_borrow_actions_create_separate_stock_and_asset_loans(self):
		request = self.request(
			"Customer Borrow / Loan",
			[
				self.item("MR-LOAN-STOCK", "Stock Item", 5, fulfilled_qty=3, outstanding_qty=2),
				self.item("MR-LOAN-ASSET", "Fixed Asset", 1, fulfilled_qty=1, outstanding_qty=1),
			],
		)
		with patch(
			"erpnext.loan.doctype.custody_loan.custody_loan_core.get_custody_loan_actions",
			return_value=frappe._dict(stock_loan=True, asset_loan=True),
		):
			actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertTrue(actions.stock_custody_loan)
		self.assertTrue(actions.asset_custody_loan)
		self.assertFalse(actions.stock_entry_issue)
		self.assertFalse(actions.stock_entry_return)
		self.assertFalse(actions.return_assets)
		self.assertFalse(actions.asset_movement)
		self.assertFalse(actions.pick_list)

	def test_asset_transfer_action_ignores_items_already_in_transit(self):
		request = self.request(
			"Asset Transfer",
			[
				self.item("MR-TRANSFER-TRANSIT", "Fixed Asset", 1),
				self.item("MR-TRANSFER-OPEN", "Fixed Asset", 1),
			],
		)
		with patch(
			"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
			side_effect=lambda name, purpose: int(name == "MR-TRANSFER-TRANSIT"),
		):
			actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertTrue(actions.asset_movement)
		self.assertFalse(actions.stock_entry_issue)
		self.assertFalse(actions.pick_list)
		self.assertFalse(actions.return_assets)

	def test_hold_shows_only_release_action(self):
		request = self.request("Customer Demo", [self.item("MR-HELD", "Stock Item", 1)], status="On Hold")
		actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertTrue(actions.release_hold)
		self.assertFalse(actions.asset_movement)
		self.assertFalse(actions.stock_entry_issue)
		self.assertFalse(actions.stock_entry_return)
		self.assertFalse(actions.pick_list)
		self.assertFalse(actions.return_assets)
		self.assertFalse(actions.hold_for_asset_return)

	def test_unsubmitted_request_has_no_post_submit_actions(self):
		request = self.request(
			"Customer Demo",
			[self.item("MR-DRAFT", "Stock Item", 2)],
			docstatus=0,
		)
		actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertFalse(any(actions.values()))

	def test_customer_request_and_asset_transfer_cannot_use_standard_stop_status_action(self):
		for request_type in ("Customer Demo", "Customer Borrow / Loan", "Asset Transfer"):
			request = self.request(request_type, [])
			with self.subTest(request_type=request_type), patch("frappe.throw", side_effect=ValueError):
				with self.assertRaises(ValueError):
					CustomerMaterialRequest.update_status(request, "Stopped")

	def test_demo_asset_movement_uses_available_demo_assets(self):
		request = self.request("Customer Demo", [self.item("MR-DEMO", "Stock Item", 2)])
		movement = FakeDocument("Asset Movement")
		with (
			patch("frappe.new_doc", return_value=movement),
			patch("frappe.db.get_value", return_value="DEMO-ASSET-ITEM"),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_available_assets",
				return_value=[frappe._dict(name="ASSET-001")],
			),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
				return_value=0,
			),
		):
			result = CustomerMaterialRequest.make_asset_movement(request)

		self.assertIs(result, movement)
		self.assertEqual(len(movement.assets), 1)
		self.assertEqual(movement.assets[0].request_item, "MR-DEMO")
		self.assertEqual(movement.assets[0].target_location, "Customer Location")
		self.assertEqual(movement.assets[0].demo_asset_required, 1)

	def test_demo_asset_movement_reports_when_no_assets_are_available(self):
		request = self.request("Customer Demo", [self.item("MR-DEMO", "Stock Item", 2)])
		with (
			patch("frappe.new_doc", return_value=FakeDocument("Asset Movement")),
			patch("frappe.db.get_value", return_value="DEMO-ASSET-ITEM"),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_available_assets",
				return_value=[],
			),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
				return_value=0,
			),
			patch("frappe.throw", side_effect=ValueError),
		):
			with self.assertRaises(ValueError):
				CustomerMaterialRequest.make_asset_movement(request)

	def test_asset_transfer_movement_excludes_in_transit_request_items(self):
		request = self.request(
			"Asset Transfer",
			[
				self.item("MR-TRANSIT", "Fixed Asset", 1, asset_transfer_asset="ASSET-TRANSIT"),
				self.item(
					"MR-OPEN", "Fixed Asset", 1,
					asset_transfer_asset="ASSET-OPEN",
					asset_transfer_source_location="Office",
					asset_transfer_target_location="Warehouse",
				),
			],
		)
		movement = FakeDocument("Asset Movement")
		with (
			patch("frappe.new_doc", return_value=movement),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
				side_effect=lambda name, purpose: int(name == "MR-TRANSIT"),
			),
		):
			CustomerMaterialRequest.make_asset_transfer_movement(request)

		self.assertEqual(len(movement.assets), 1)
		self.assertEqual(movement.assets[0].asset, "ASSET-OPEN")
		self.assertEqual(movement.assets[0].target_location, "Warehouse")

	def test_borrow_stock_entry_must_be_created_from_custody_loan(self):
		request = self.request("Customer Borrow / Loan", [self.item("MR-LOAN", "Stock Item", 1)])
		with patch("frappe.throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				CustomerMaterialRequest.make_stock_entry(request)

	def test_demo_return_movement_includes_fixed_assets_and_converted_demo_assets(self):
		request = self.request(
			"Customer Demo",
			[
				self.item("MR-FIXED", "Fixed Asset", 1, fulfilled_qty=1, outstanding_qty=1),
				self.item("MR-DEMO-STOCK", "Stock Item", 1, fulfilled_qty=1, outstanding_qty=1),
			],
		)
		request.update_progress = Mock()
		movement = FakeDocument("Asset Movement")
		assets = [
			[frappe._dict(name="ASSET-FIXED", item_code="FIXED-ITEM", location="Customer Location", return_location="Office")],
			[frappe._dict(name="ASSET-DEMO", item_code="DEMO-ASSET-ITEM", location="Customer Location", return_location="Office")],
		]
		with (
			patch("frappe.new_doc", return_value=movement),
			patch("frappe.db.sql", side_effect=assets),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
				return_value=0,
			),
		):
			CustomerMaterialRequest.make_asset_receipt(request, requires_shipment=True)

		self.assertEqual(len(movement.assets), 2)
		self.assertEqual({row.asset for row in movement.assets}, {"ASSET-FIXED", "ASSET-DEMO"})
		self.assertTrue(all(row.shipment_destination_location == "Office" for row in movement.assets))

	def test_demo_pick_list_only_contains_conversion_shortage(self):
		request = self.request("Customer Demo", [self.item("MR-DEMO", "Stock Item", 3)])
		pick_list = FakeDocument("Pick List")
		with (
			patch("frappe.new_doc", return_value=pick_list),
			patch("frappe.db.get_value", side_effect=[None, "DEMO-ASSET-ITEM"]),
			patch("frappe.db.exists", return_value=False),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_available_assets",
				return_value=[frappe._dict(name="ASSET-AVAILABLE")],
			),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
				return_value=0,
			),
			patch("frappe.get_cached_doc", return_value=frappe._dict(stock_uom="Nos")),
		):
			result = CustomerMaterialRequest.make_pick_list(request)

		self.assertEqual(result["doctype"], "Pick List")
		self.assertTrue(pick_list.inserted)
		self.assertEqual(len(pick_list.locations), 1)
		self.assertEqual(pick_list.locations[0].qty, 2)
		self.assertEqual(pick_list.locations[0].material_request_item, "MR-DEMO")

	def test_asset_transfer_request_rejects_duplicate_asset_rows(self):
		request = self.request(
			"Asset Transfer",
			[
				self.item("MR-TRANSFER-1", "Fixed Asset", 1, asset_transfer_asset="ASSET-DUP"),
				self.item("MR-TRANSFER-2", "Fixed Asset", 1, asset_transfer_asset="ASSET-DUP"),
			],
		)
		with patch("frappe.throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				CustomerMaterialRequest.validate_asset_transfer_items(request)


class TestCustomerDemoPickListValidation(unittest.TestCase):
	def validate_pick_list(self, picked_qty, in_transit_qty):
		pick_list = SimpleNamespace(
			material_request="MAT-MR-DEMO",
			pick_list_type="Customer Demo - Asset Conversion",
			purpose="Material Transfer",
			company="_Test Company",
			locations=[
				frappe._dict(
					idx=1,
					material_request_item="MAT-MR-DEMO-ITEM",
					item_code="ITEM-001",
					warehouse="Stores - _TC",
					picked_qty=picked_qty,
					stock_qty=picked_qty,
				)
			],
		)
		request = frappe._dict(docstatus=1, company="_Test Company", material_request_type="Customer Demo")
		request_item = frappe._dict(
			parent="MAT-MR-DEMO",
			item_code="ITEM-001",
			item_type="Stock Item",
			from_warehouse="Stores - _TC",
			qty=3,
			fulfilled_qty=0,
		)
		with (
			patch(
				"frappe.db.get_value",
				side_effect=["Customer Demo", request, request_item],
			),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty",
				return_value=in_transit_qty,
			),
		):
			PickList.validate_customer_demo_workflow(pick_list)

	def test_demo_pick_list_can_cover_remaining_quantity_after_transit(self):
		self.validate_pick_list(picked_qty=2, in_transit_qty=1)

	def test_demo_pick_list_cannot_overpick_while_assets_are_in_transit(self):
		with patch("frappe.throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				self.validate_pick_list(picked_qty=3, in_transit_qty=1)


class TestCustomerMaterialRequestIntegration(ERPNextTestSuite):
	company = "_Test Company"
	source_warehouse = "_Test Warehouse - _TC"

	def setUp(self):
		super().setUp()
		ensure_company_tax_settings(self.company)
		frappe.db.set_value(
			"Company", self.company, "capital_work_in_progress_account", "CWIP Account - _TC"
		)
		self.loan_warehouse = create_warehouse("_Test Customer Loan Warehouse")
		self.transit_warehouse = create_warehouse(
			"_Test Custody Loan Transit Warehouse", properties={"warehouse_type": "Transit"}
		)
		frappe.db.set_value("Company", self.company, "customer_loan_warehouse", self.loan_warehouse)
		frappe.db.set_value("Company", self.company, "default_in_transit_warehouse", self.transit_warehouse)
		self.transit_location = self.ensure_location("_Test Custody Loan Transit Location", is_transit=True)
		frappe.db.set_value("Company", self.company, "default_in_transit_location", self.transit_location)
		self.source_location = self.ensure_location("Test Location")
		self.customer_location = self.ensure_location("Customer Location")
		self.target_location = self.ensure_location("Test Location 2")
		self.customer_address = self.ensure_address("_Test Customer", "Customer")
		self.company_address = self.ensure_address(self.company, "Company")
		frappe.db.set_value("Warehouse", self.source_warehouse, "asset_location", self.source_location)

	def ensure_location(self, name, is_transit=False):
		if not frappe.db.exists("Location", name):
			frappe.get_doc(
				{
					"doctype": "Location",
					"location_name": name,
					"is_in_transit_location": cint(is_transit),
				}
			).insert()
		elif is_transit:
			frappe.db.set_value("Location", name, "is_in_transit_location", 1)
		return name

	def ensure_address(self, party, party_type):
		name = f"_{party_type} {test_id()}"
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": name,
				"address_type": "Shipping",
				"address_line1": "1 Test Road",
				"city": "Bangkok",
				"country": "Thailand",
				"is_your_company_address": int(party_type == "Company"),
				"links": [{"link_doctype": party_type, "link_name": party}],
			}
		).insert()
		return address.name

	def make_asset(self, item_code, is_demo=False):
		if not frappe.db.exists("Item", item_code):
			create_fixed_asset_item(item_code=item_code, asset_category="Computers")
		pr = make_purchase_receipt(
			item_code=item_code,
			qty=1,
			rate=100000,
			warehouse=self.source_warehouse,
			location=self.source_location,
		)
		asset_name = frappe.db.get_value("Asset", {"purchase_receipt": pr.name}, "name")
		self.assertTrue(asset_name, f"Purchase Receipt {pr.name} did not create an Asset")
		asset = frappe.get_doc("Asset", asset_name)
		if asset.docstatus == 0:
			asset.available_for_use_date = today()
			asset.submit()
		frappe.db.set_value(
			"Asset",
			asset.name,
			{"is_demo_asset": int(is_demo), "demo_loan_status": "Available"},
		)
		return frappe.get_doc("Asset", asset.name)

	def make_stock_item(self, demo_asset_item_code=None, qty=5):
		item = make_item(properties={"is_stock_item": 1, "stock_uom": "Nos"})
		if demo_asset_item_code:
			frappe.db.set_value("Item", item.name, "demo_asset_item_code", demo_asset_item_code)
			frappe.clear_cache(doctype="Item")
		make_stock_entry(
			item_code=item.name,
			qty=qty,
			to_warehouse=self.source_warehouse,
			rate=100,
		)
		return item.name

	def make_request(
		self,
		request_type,
		items,
		customer_request=True,
		include_expected_return_date=True,
		recipient_type="Customer",
		recipient=None,
	):
		request = frappe.new_doc("Material Request")
		request.material_request_type = request_type
		request.company = self.company
		request.transaction_date = today()
		request.schedule_date = add_days(today(), 1)
		request.set_from_warehouse = self.source_warehouse
		if request_type == "Customer Borrow / Loan":
			request.loan_recipient_type = recipient_type
			request.loan_recipient = recipient or "_Test Customer"
			request.loan_recipient_address = self.customer_address
			request.loan_recipient_location = self.customer_location
			if recipient_type == "Customer":
				request.customer = request.loan_recipient
				request.customer_address = self.customer_address
				request.customer_location = self.customer_location
			if include_expected_return_date:
				request.expected_return_date = add_days(today(), 30)
		elif customer_request:
			request.customer = "_Test Customer"
			request.customer_address = self.customer_address
			if include_expected_return_date:
				request.expected_return_date = add_days(today(), 30)
			request.customer_location = self.customer_location
		for values in items:
			item = frappe.get_cached_doc("Item", values["item_code"])
			request.append(
				"items",
				{
					"item_code": item.name,
					"item_name": item.item_name,
					"qty": values.get("qty", 1),
					"uom": item.stock_uom,
					"stock_uom": item.stock_uom,
					"conversion_factor": 1,
					"schedule_date": request.schedule_date,
					"warehouse": self.loan_warehouse if item.is_stock_item else None,
					"from_warehouse": self.source_warehouse if item.is_stock_item else None,
					"asset_transfer_asset": values.get("asset_transfer_asset"),
					"asset_transfer_target_location": values.get("asset_transfer_target_location"),
				}
			)
		request.insert()
		request.submit()
		return frappe.get_doc("Material Request", request.name)

	def make_custody_loan(self, request, loan_type, asset=None, requires_shipment=False):
		loan_data = request.make_custody_loan(loan_type)
		loan = frappe.get_doc("Custody Loan", loan_data["name"])
		loan.requires_shipment = cint(requires_shipment)
		if asset:
			for row in loan.items:
				if row.item_code == asset.item_code:
					row.asset = asset.name
		loan.save()
		return loan

	def create_shipment(self, source_doctype, source_name):
		result = make_shipment_from_document(source_doctype, source_name)
		shipment = frappe.get_doc("Shipment", result["name"])
		shipment.pallets = "No"
		shipment.shipment_type = "Goods"
		shipment.pickup_type = "Pickup"
		shipment.pickup_date = today()
		shipment.pickup_from = "09:00:00"
		shipment.pickup_to = "17:00:00"
		shipment.append(
			"shipment_parcel",
			{"length": 1, "width": 1, "height": 1, "weight": 1, "count": 1},
		)
		shipment.save()
		shipment.submit()
		return frappe.get_doc("Shipment", shipment.name)

	def confirm_shipment_delivery(self, shipment):
		shipment.reload()
		shipment.tracking_status = "Delivered"
		shipment.received_by = "Test Receiver"
		shipment.delivery_signature = "test-signature"
		shipment.save()
		return frappe.get_doc("Shipment", shipment.name)

	def test_demo_mixed_assets_shipped_to_customer_and_returned(self):
		asset_item_code = f"Demo Asset {test_id()}"
		demo_stock_asset = self.make_asset(asset_item_code, is_demo=True)
		demo_fixed_asset = self.make_asset(asset_item_code, is_demo=True)
		stock_item = self.make_stock_item(demo_asset_item_code=asset_item_code)
		request = self.make_request(
			"Customer Demo",
			[
				{"item_code": stock_item},
				{"item_code": asset_item_code},
			],
		)

		actions = request.get_post_submit_actions()
		self.assertTrue(actions.asset_movement)
		self.assertFalse(actions.stock_entry_issue)
		movement = request.make_asset_movement()
		for row in movement.assets:
			row.asset = demo_stock_asset.name if row.request_item == request.items[0].name else demo_fixed_asset.name
		movement.requires_shipment = 1
		movement.insert()
		shipment = self.create_shipment("Asset Movement", movement.name)

		transit_movement = frappe.get_doc("Asset Movement", movement.name)
		self.assertEqual(transit_movement.docstatus, 1)
		self.assertEqual(frappe.db.get_value("Asset", demo_stock_asset.name, "location"), self.transit_location)
		self.assertEqual(
			frappe.db.get_value("Asset", demo_stock_asset.name, "demo_loan_status"), "In Transit"
		)
		request.reload()
		self.assertEqual(request.request_progress_status, "Pending")
		self.assertFalse(request.get_post_submit_actions().asset_movement)
		with self.assertRaises(frappe.ValidationError):
			request.make_asset_receipt(requires_shipment=1)

		self.confirm_shipment_delivery(shipment)
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")
		self.assertEqual(frappe.db.get_value("Asset", demo_stock_asset.name, "location"), self.customer_location)
		self.assertEqual(frappe.db.get_value("Asset", demo_fixed_asset.name, "location"), self.customer_location)

		return_movement = request.make_asset_receipt(requires_shipment=1)
		return_movement.insert()
		return_shipment = self.create_shipment("Asset Movement", return_movement.name)
		self.assertEqual(
			frappe.db.get_value("Asset", demo_stock_asset.name, "demo_loan_status"), "In Transit"
		)
		self.confirm_shipment_delivery(return_shipment)
		request.reload()
		self.assertEqual(request.request_progress_status, "Returned")
		self.assertEqual(frappe.db.get_value("Asset", demo_stock_asset.name, "location"), self.source_location)

	def test_demo_pick_list_conversion_then_asset_movement(self):
		frappe.db.set_value(
			"Company",
			self.company,
			{
				"asset_capitalization_threshold": 1000,
				"low_value_asset_expense_account": "_Test Depreciations - _TC",
				"default_inventory_account": "Stock In Hand - _TC",
			},
		)
		asset_item_code = f"Converted Demo Asset {test_id()}"
		create_fixed_asset_item(item_code=asset_item_code, asset_category="Computers")
		stock_item = self.make_stock_item(demo_asset_item_code=asset_item_code)
		request = self.make_request("Customer Demo", [{"item_code": stock_item}])

		self.assertTrue(request.get_post_submit_actions().pick_list)
		self.assertFalse(request.get_post_submit_actions().stock_entry_issue)
		with self.assertRaises(frappe.ValidationError):
			request.make_stock_entry()
		pick_list_data = request.make_pick_list()
		pick_list = frappe.get_doc("Pick List", pick_list_data["name"])
		pick_list.submit()
		conversion_data = pick_list.make_stock_to_asset_conversion()
		conversion = frappe.get_doc("Stock to Asset Conversion", conversion_data["name"])
		conversion.submit()
		asset_name = conversion.assets[0].asset
		self.assertEqual(frappe.db.get_value("Asset", asset_name, "is_demo_asset"), 1)
		self.assertEqual(frappe.db.get_value("Asset", asset_name, "demo_loan_status"), "Available")

		request.reload()
		self.assertTrue(request.get_post_submit_actions().asset_movement)
		movement = request.make_asset_movement()
		movement.assets[0].asset = asset_name
		movement.submit()
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")

		request.make_asset_receipt().submit()
		request.reload()
		self.assertEqual(request.request_progress_status, "Returned")
		self.assertEqual(frappe.db.get_value("Asset", asset_name, "demo_loan_status"), "Available")

	def test_mixed_request_creates_separate_stock_and_asset_loans(self):
		asset_item_code = f"Loan Asset {test_id()}"
		asset = self.make_asset(asset_item_code)
		stock_item = self.make_stock_item(qty=5)
		request = self.make_request(
			"Customer Borrow / Loan",
			[{"item_code": stock_item, "qty": 2}, {"item_code": asset_item_code}],
		)
		actions = request.get_post_submit_actions()
		self.assertTrue(actions.stock_custody_loan)
		self.assertTrue(actions.asset_custody_loan)

		stock_loan = self.make_custody_loan(request, "Stock", requires_shipment=True)
		self.assertEqual(stock_loan.docstatus, 0)
		self.assertEqual(len(stock_loan.items), 1)
		stock_loan.submit()
		self.assertEqual(stock_loan.status, "Pending Shipment")
		stock_entry = frappe.get_doc("Stock Entry", stock_loan.stock_entry)
		self.assertEqual(stock_entry.docstatus, 0)
		self.assertEqual(stock_entry.is_system_generated, 1)
		self.assertEqual(stock_entry.is_custody_loan_transaction, 1)
		self.assertEqual(stock_entry.custody_loan, stock_loan.name)
		self.assertEqual(stock_entry.custody_loan_source_doctype, "Custody Loan")
		shipment = self.create_shipment("Stock Entry", stock_entry.name)
		stock_loan.reload()
		self.assertEqual(stock_loan.status, "In Transit")
		request.reload()
		self.assertEqual(request.request_progress_status, "Pending")
		self.assertFalse(request.get_post_submit_actions().stock_entry_issue)

		asset_loan = self.make_custody_loan(request, "Asset", asset=asset)
		asset_loan.submit()
		self.assertEqual(asset_loan.status, "Active")
		asset_movement = frappe.get_doc("Asset Movement", asset_loan.asset_movement)
		self.assertEqual(asset_movement.is_system_generated, 1)
		self.assertEqual(asset_movement.is_custody_loan_transaction, 1)
		self.assertEqual(asset_movement.custody_loan, asset_loan.name)
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.customer_location)

		request.reload()
		self.assertEqual(request.request_progress_status, "Partially Fulfilled")

		self.confirm_shipment_delivery(shipment)
		stock_loan.reload()
		self.assertEqual(stock_loan.status, "Active")
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")

		first_return = stock_loan.make_return()
		first_return.items[0].returned_qty = 1
		first_return.submit()
		stock_loan.reload()
		self.assertEqual(stock_loan.status, "Partially Returned")
		self.assertEqual(stock_loan.items[0].outstanding_qty, 1)
		request.reload()
		self.assertEqual(request.request_progress_status, "Partially Returned")

		second_return = stock_loan.make_return()
		second_return.items[0].returned_qty = 1
		second_return.submit()
		asset_return = asset_loan.make_return()
		asset_return.submit()
		stock_loan.reload()
		asset_loan.reload()
		request.reload()
		self.assertEqual(stock_loan.status, "Returned")
		self.assertEqual(asset_loan.status, "Returned")
		self.assertEqual(request.request_progress_status, "Returned")
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.source_location)

	def test_stock_and_asset_loan_returns_wait_for_shipment_delivery(self):
		asset_item_code = f"Shipped Loan Asset {test_id()}"
		asset = self.make_asset(asset_item_code)
		stock_item = self.make_stock_item(qty=5)
		request = self.make_request(
			"Customer Borrow / Loan",
			[{"item_code": stock_item}, {"item_code": asset_item_code}],
		)

		stock_loan = self.make_custody_loan(request, "Stock")
		stock_loan.submit()
		asset_loan = self.make_custody_loan(request, "Asset", asset=asset)
		asset_loan.submit()
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")

		stock_return = stock_loan.make_return()
		stock_return.requires_shipment = 1
		stock_return.save()
		stock_return.submit()
		stock_shipment = self.create_shipment("Stock Entry", stock_return.stock_entry)
		stock_return.reload()
		self.assertEqual(stock_return.status, "In Transit")
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")
		self.confirm_shipment_delivery(stock_shipment)
		stock_return.reload()
		stock_loan.reload()
		self.assertEqual(stock_return.status, "Received")
		self.assertEqual(stock_loan.status, "Returned")

		asset_return = asset_loan.make_return()
		asset_return.requires_shipment = 1
		asset_return.save()
		asset_return.submit()
		asset_shipment = self.create_shipment("Asset Movement", asset_return.asset_movement)
		asset_return.reload()
		self.assertEqual(asset_return.status, "In Transit")
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.transit_location)
		self.confirm_shipment_delivery(asset_shipment)
		asset_return.reload()
		asset_loan.reload()
		request.reload()
		self.assertEqual(asset_return.status, "Received")
		self.assertEqual(asset_loan.status, "Returned")
		self.assertEqual(request.request_progress_status, "Returned")
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.source_location)

	def test_borrow_partial_stock_return_updates_request_status(self):
		stock_item = self.make_stock_item(qty=5)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": stock_item, "qty": 2}])

		stock_loan = self.make_custody_loan(request, "Stock")
		stock_loan.submit()
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")

		return_doc = stock_loan.make_return()
		return_doc.items[0].returned_qty = 1
		return_doc.submit()
		stock_loan.reload()
		request.reload()

		self.assertEqual(request.request_progress_status, "Partially Returned")
		self.assertEqual(request.items[0].status, "Partially Returned")
		self.assertEqual(request.items[0].outstanding_qty, 1)

	def test_stock_loans_support_all_recipient_types(self):
		stock_item = self.make_stock_item(qty=5)
		employee = make_employee(f"custody-loan-{test_id()}@example.test", company=self.company)
		supplier = create_supplier(supplier_name=f"Custody Supplier {test_id()}").name
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"Custody Contact {test_id()}",
				"links": [{"link_doctype": "Customer", "link_name": "_Test Customer"}],
			}
		).insert()
		recipients = [
			("Employee", employee),
			("Customer", "_Test Customer"),
			("Supplier", supplier),
			("Contact", contact.name),
		]

		for recipient_type, recipient in recipients:
			with self.subTest(recipient_type=recipient_type):
				request = self.make_request(
					"Customer Borrow / Loan",
					[{"item_code": stock_item}],
					recipient_type=recipient_type,
					recipient=recipient,
				)
				loan = self.make_custody_loan(request, "Stock")
				loan.submit()
				self.assertEqual(loan.borrower_type, recipient_type)
				self.assertEqual(loan.borrower, recipient)
				self.assertEqual(loan.status, "Active")
				loan.make_return().submit()
				loan.reload()
				self.assertEqual(loan.status, "Returned")

	def test_stock_batch_replacement_keeps_original_outstanding_until_adjustment(self):
		from erpnext.stock.doctype.batch.test_batch import make_new_batch

		item = make_item(properties={"is_stock_item": 1, "stock_uom": "Nos", "has_batch_no": 1})
		issued_batch = make_new_batch(batch_id=f"ISSUED-{test_id()}", item_code=item.name)
		replacement_batch = make_new_batch(batch_id=f"RETURN-{test_id()}", item_code=item.name)
		make_stock_entry(
			item_code=item.name,
			qty=2,
			to_warehouse=self.source_warehouse,
			rate=100,
			batch_no=issued_batch.name,
		)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": item.name}])
		loan = self.make_custody_loan(request, "Stock")
		loan.items[0].batch_no = issued_batch.name
		loan.submit()
		frappe.db.set_value("Company", self.company, "stock_adjustment_account", "Stock Adjustment - _TC")

		returned = loan.make_return()
		returned.items[0].batch_no = replacement_batch.name
		returned.items[0].replacement_reason = "The issued Batch was not returned"
		returned.submit()
		returned.reload()
		loan.reload()
		self.assertEqual(returned.status, "Received")
		self.assertEqual(returned.items[0].matched_qty, 0)
		self.assertEqual(returned.items[0].replacement_qty, 1)
		self.assertEqual(loan.items[0].replacement_received_qty, 1)
		self.assertEqual(loan.items[0].outstanding_qty, 1)
		self.assertEqual(loan.status, "Active")

		adjustment = loan.make_adjustment()
		adjustment.items[0].reason = "Write off the outstanding issued Batch"
		adjustment.submit()
		loan.reload()
		self.assertEqual(adjustment.status, "Submitted")
		self.assertEqual(loan.status, "Closed")
		self.assertEqual(loan.items[0].outstanding_qty, 0)

	def test_cancelling_return_then_loan_cancels_generated_stock_entries(self):
		stock_item = self.make_stock_item(qty=2)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": stock_item}])
		loan = self.make_custody_loan(request, "Stock")
		loan.submit()
		issue_entry = loan.stock_entry

		return_doc = loan.make_return()
		return_doc.submit()
		with self.assertRaises(frappe.ValidationError):
			loan.cancel()

		return_entry = return_doc.stock_entry
		return_doc.cancel()
		loan.reload()
		self.assertEqual(loan.status, "Active")
		loan.cancel()
		loan.reload()
		self.assertEqual(loan.docstatus, 2)
		self.assertEqual(loan.status, "Cancelled")
		self.assertEqual(frappe.db.get_value("Stock Entry", issue_entry, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Stock Entry", return_entry, "docstatus"), 2)

	def test_asset_transfer_shipment_completes_request_at_target_location(self):
		asset_item_code = f"Transfer Asset {test_id()}"
		asset = self.make_asset(asset_item_code)
		request = self.make_request(
			"Asset Transfer",
			[
				{
					"item_code": asset_item_code,
					"asset_transfer_asset": asset.name,
					"asset_transfer_target_location": self.target_location,
			}
			],
			customer_request=False,
		)
		self.assertTrue(request.get_post_submit_actions().asset_movement)
		movement = request.make_asset_movement()
		movement.requires_shipment = 1
		movement.insert()
		shipment = self.create_shipment("Asset Movement", movement.name)

		self.assertEqual(
			frappe.db.get_value("Asset", asset.name, "location"), self.transit_location
		)
		request.reload()
		self.assertEqual(request.asset_transfer_status, "Pending")
		self.assertFalse(request.get_post_submit_actions().asset_movement)

		self.confirm_shipment_delivery(shipment)
		request.reload()
		self.assertEqual(request.asset_transfer_status, "Fulfilled")
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.target_location)

	def test_asset_transfer_direct_move_updates_status_and_location(self):
		asset = self.make_asset(f"Direct Transfer Asset {test_id()}")
		request = self.make_request(
			"Asset Transfer",
			[
				{
					"item_code": asset.item_code,
					"asset_transfer_asset": asset.name,
					"asset_transfer_target_location": self.target_location,
				}
			],
			customer_request=False,
		)

		movement = request.make_asset_movement()
		movement.submit()
		request.reload()
		self.assertEqual(request.asset_transfer_status, "Fulfilled")
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.target_location)
		self.assertFalse(request.get_post_submit_actions().asset_movement)

	def test_asset_transfer_shipment_rejects_multiple_destinations(self):
		asset_a = self.make_asset(f"Multi Destination Asset A {test_id()}")
		asset_b = self.make_asset(f"Multi Destination Asset B {test_id()}")
		request = self.make_request(
			"Asset Transfer",
			[
				{
					"item_code": asset_a.item_code,
					"asset_transfer_asset": asset_a.name,
					"asset_transfer_target_location": self.target_location,
				},
				{
					"item_code": asset_b.item_code,
					"asset_transfer_asset": asset_b.name,
					"asset_transfer_target_location": self.customer_location,
				},
			],
			customer_request=False,
		)
		movement = request.make_asset_movement()
		movement.requires_shipment = 1
		movement.insert()
		with self.assertRaises(frappe.ValidationError):
			make_shipment_from_document("Asset Movement", movement.name)

	def test_demo_stock_request_requires_a_conversion_item(self):
		stock_item = self.make_stock_item(qty=1)
		with self.assertRaises(frappe.ValidationError):
			self.make_request("Customer Demo", [{"item_code": stock_item}])

	def test_demo_and_loan_requests_require_an_expected_return_date(self):
		asset_item_code = f"Demo Asset {test_id()}"
		create_fixed_asset_item(item_code=asset_item_code, asset_category="Computers")
		stock_item = self.make_stock_item(demo_asset_item_code=asset_item_code, qty=2)
		for request_type in ("Customer Demo", "Customer Borrow / Loan"):
			with self.subTest(request_type=request_type), self.assertRaises(frappe.ValidationError):
				self.make_request(
					request_type,
					[{"item_code": stock_item}],
					include_expected_return_date=False,
				)

	def test_demo_movement_rejects_an_asset_not_marked_for_demo(self):
		asset_item_code = f"Non Demo Asset {test_id()}"
		asset = self.make_asset(asset_item_code, is_demo=False)
		request = self.make_request("Customer Demo", [{"item_code": asset_item_code}])
		movement = request.make_asset_movement()
		movement.assets[0].asset = asset.name

		with self.assertRaises(frappe.ValidationError):
			movement.insert()

	def test_custody_loan_rejects_changing_generated_asset_to_another_asset(self):
		asset_item_code = f"Requested Loan Asset {test_id()}"
		requested_asset = self.make_asset(asset_item_code)
		other_asset = self.make_asset(asset_item_code)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": asset_item_code}])
		loan = self.make_custody_loan(request, "Asset", asset=requested_asset)
		loan.submit()
		movement = frappe.get_doc("Asset Movement", loan.asset_movement)
		movement.assets[0].asset = other_asset.name

		with self.assertRaises(frappe.ValidationError):
			movement.save()

	def test_asset_transfer_rejects_duplicate_assets_and_changed_locations(self):
		duplicate_asset = self.make_asset(f"Duplicate Transfer Asset {test_id()}")
		with self.assertRaises(frappe.ValidationError):
			self.make_request(
				"Asset Transfer",
				[
					{
						"item_code": duplicate_asset.item_code,
						"asset_transfer_asset": duplicate_asset.name,
						"asset_transfer_target_location": self.target_location,
					},
					{
						"item_code": duplicate_asset.item_code,
						"asset_transfer_asset": duplicate_asset.name,
						"asset_transfer_target_location": self.target_location,
					},
				],
				customer_request=False,
			)

		target_asset = self.make_asset(f"Changed Target Asset {test_id()}")
		target_request = self.make_request(
			"Asset Transfer",
			[
				{
					"item_code": target_asset.item_code,
					"asset_transfer_asset": target_asset.name,
					"asset_transfer_target_location": self.target_location,
				}
			],
			customer_request=False,
		)
		wrong_target = target_request.make_asset_movement()
		wrong_target.assets[0].target_location = self.customer_location
		with self.assertRaises(frappe.ValidationError):
			wrong_target.insert()

		source_asset = self.make_asset(f"Changed Source Asset {test_id()}")
		source_request = self.make_request(
			"Asset Transfer",
			[
				{
					"item_code": source_asset.item_code,
					"asset_transfer_asset": source_asset.name,
					"asset_transfer_target_location": self.target_location,
				}
			],
			customer_request=False,
		)
		other_movement = frappe.new_doc("Asset Movement")
		other_movement.company = self.company
		other_movement.purpose = "Transfer"
		other_movement.append(
			"assets",
			{
				"asset": source_asset.name,
				"source_location": self.source_location,
				"target_location": self.customer_location,
			},
		)
		other_movement.insert()
		other_movement.submit()
		stale_source = source_request.make_asset_movement()
		with self.assertRaises(frappe.ValidationError):
			stale_source.insert()


def test_id():
	return frappe.generate_hash(length=8)
