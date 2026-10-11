import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import add_days, cint, today

from erpnext.assets.doctype.asset.test_asset import create_fixed_asset_item
from erpnext.assets.doctype.material_request.material_request import (
	MATERIAL_REQUEST_PURPOSES,
	CustomerMaterialRequest,
)
from erpnext.buying.doctype.supplier.test_supplier import create_supplier
from erpnext.regional.thailand.tests.utils import ensure_company_tax_settings
from erpnext.selling.doctype.customer.test_customer import create_internal_customer
from erpnext.setup.doctype.employee.test_employee import make_employee
from erpnext.stock.doctype.item.test_item import make_item
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
	def test_before_cancel_cascades_to_customer_transactions(self):
		request = CustomerMaterialRequest.__new__(CustomerMaterialRequest)

		with (
			patch("erpnext.assets.doctype.material_request.material_request.MaterialRequest.before_cancel") as native,
			patch.object(CustomerMaterialRequest, "cancel_linked_custody_loans") as cancel_loans,
			patch.object(CustomerMaterialRequest, "cancel_linked_asset_movements") as cancel_movements,
		):
			request.before_cancel()

		native.assert_called_once_with()
		cancel_loans.assert_called_once_with()
		cancel_movements.assert_called_once_with()

	def test_customer_request_cancellation_cancels_linked_loan_and_movements(self):
		request = SimpleNamespace(
			doctype="Material Request",
			name="MAT-MR-001",
			material_request_type="Customer Demo",
		)
		request.cancel_linked_document = lambda doctype, name: CustomerMaterialRequest.cancel_linked_document(
			request, doctype, name
		)
		loan = Mock()
		movement = Mock()
		loan.get.return_value = ()
		movement.get.return_value = ()

		with (
			patch("frappe.get_all", side_effect=[["LOAN-001"], ["ASM-001"]]),
			patch("frappe.get_doc", side_effect=[loan, movement]),
		):
			CustomerMaterialRequest.cancel_linked_custody_loans(request)
			CustomerMaterialRequest.cancel_linked_asset_movements(request)

		loan.cancel.assert_called_once_with()
		movement.cancel.assert_called_once_with()
		self.assertEqual(loan.ignore_linked_doctypes, ("Material Request",))
		self.assertEqual(movement.ignore_linked_doctypes, ("Material Request",))

	def test_material_request_purpose_options_are_limited_to_supported_types(self):
		doctype_path = Path(__file__).resolve().parents[3] / "stock/doctype/material_request/material_request.json"
		with doctype_path.open() as source:
			fields = json.load(source)["fields"]
		purpose = next(field for field in fields if field["fieldname"] == "material_request_type")

		self.assertEqual(purpose["options"].splitlines(), list(MATERIAL_REQUEST_PURPOSES))
		self.assertEqual(
			MATERIAL_REQUEST_PURPOSES,
			("Purchase", "Material Transfer", "Asset Transfer", "Customer Demo", "Customer Borrow / Loan"),
		)

	def test_custom_asset_statuses_do_not_include_in_transit(self):
		doctype_path = Path(__file__).resolve().parents[3] / "stock/doctype/material_request/material_request.json"
		with doctype_path.open() as source:
			fields = json.load(source)["fields"]
		asset_transfer_status = next(field for field in fields if field["fieldname"] == "asset_transfer_status")

		self.assertNotIn("In Transit", asset_transfer_status["options"].splitlines())
		doctype_root = Path(__file__).resolve().parents[3]
		for doctype_path, fieldname in (
			(doctype_root / "assets/doctype/asset/asset.json", "demo_loan_status"),
			(doctype_root / "loan/doctype/custody_loan/custody_loan.json", "status"),
			(doctype_root / "loan/doctype/custody_loan_return/custody_loan_return.json", "status"),
		):
			with doctype_path.open() as source:
				doctype_fields = json.load(source)["fields"]
			status_field = next(field for field in doctype_fields if field["fieldname"] == fieldname)
			self.assertNotIn("In Transit", status_field["options"].splitlines())

	def test_asset_movement_schema_has_no_shipment_transit_fields(self):
		movement_path = Path(__file__).parents[1] / "asset_movement/asset_movement.json"
		item_path = Path(__file__).parents[1] / "asset_movement_item/asset_movement_item.json"
		with movement_path.open() as source:
			movement_fields = {field["fieldname"] for field in json.load(source)["fields"]}
		with item_path.open() as source:
			item_fields = {field["fieldname"] for field in json.load(source)["fields"]}

		removed_fields = {"requires_shipment", "shipment", "shipment_transit_leg", "shipment_source_movement"}
		self.assertTrue(removed_fields.isdisjoint(movement_fields))
		self.assertNotIn("shipment_destination_location", item_fields)

	def test_purpose_specific_fields_are_scoped_to_their_request_type(self):
		doctype_path = Path(__file__).resolve().parents[3] / "stock/doctype/material_request/material_request.json"
		with doctype_path.open() as source:
			doctype = json.load(source)
		fields = {field["fieldname"]: field for field in doctype["fields"]}
		item_doctype_path = Path(__file__).resolve().parents[3] / "stock/doctype/material_request_item/material_request_item.json"
		with item_doctype_path.open() as source:
			item_fields = {field["fieldname"]: field for field in json.load(source)["fields"]}

		self.assertEqual(fields["customer"]["depends_on"], "eval:doc.material_request_type == 'Customer Demo'")
		self.assertEqual(
			fields["recipient_address"]["depends_on"],
			"eval:in_list(['Customer Demo', 'Customer Borrow / Loan'], doc.material_request_type)",
		)
		self.assertEqual(
			fields["recipient_address"]["mandatory_depends_on"],
			"eval:doc.material_request_type == 'Customer Demo'",
		)
		self.assertNotIn("customer_address", fields)
		self.assertNotIn("loan_recipient_address", fields)
		self.assertEqual(
			fields["contact_person"]["depends_on"],
			"eval:doc.material_request_type == 'Customer Demo' || (doc.material_request_type == 'Customer Borrow / Loan' && doc.loan_recipient_type == 'Customer')",
		)
		self.assertEqual(
			fields["customer_location"]["depends_on"], "eval:doc.material_request_type == 'Customer Demo'"
		)
		self.assertIn("Customer Borrow / Loan", fields["borrow_item_type"]["depends_on"])
		self.assertIn("Customer Demo", fields["items"]["depends_on"])
		self.assertIn("borrow_item_type", fields["items"]["depends_on"])
		self.assertIn("borrow_item_type == 'Stock Item'", fields["customer_loan_warehouse"]["depends_on"])
		self.assertIn("borrow_item_type == 'Asset'", fields["loan_recipient_location"]["depends_on"])
		self.assertEqual(fields["asset_source_location"]["depends_on"], "eval:doc.material_request_type == 'Asset Transfer'")
		self.assertIn("Customer Demo", item_fields["asset"]["depends_on"])
		self.assertIn("borrow_item_type == 'Asset'", item_fields["asset"]["depends_on"])
		self.assertEqual(item_fields["from_warehouse"]["depends_on"], 'eval:parent.material_request_type == "Material Transfer"')
		self.assertEqual(
			item_fields["source_location"]["depends_on"], "eval:parent.material_request_type == 'Asset Transfer'"
		)
		self.assertEqual(item_fields["warehouse"]["label"], "Target Warehouse")
		self.assertNotIn("demo_items", fields)
		self.assertNotIn("asset_items", fields)

		field_sections = {}
		active_section = None
		for fieldname in doctype["field_order"]:
			field = fields[fieldname]
			if field["fieldtype"] == "Section Break":
				active_section = fieldname
			else:
				field_sections[fieldname] = active_section
		self.assertEqual(field_sections["customer_loan_warehouse"], "loan_recipient_section")
		self.assertEqual(field_sections["expected_return_date"], "return_details_section")
		self.assertEqual(field_sections["transaction_date"], "request_schedule_section")
		self.assertEqual(field_sections["asset_transfer_status"], "asset_items_section")
		self.assertEqual(field_sections["items"], "items_section")

	def test_legacy_material_request_purposes_are_rejected(self):
		for purpose in ("Material Issue", "Manufacture", "Subcontracting", "Customer Provided"):
			with self.subTest(purpose=purpose), patch("frappe.throw", side_effect=ValueError):
				with self.assertRaises(ValueError):
					request = CustomerMaterialRequest.__new__(CustomerMaterialRequest)
					request.material_request_type = purpose
					request.auto_created_via_reorder = 0
					request.validate_material_request_type()

	def test_only_trusted_auto_reorder_can_validate_a_legacy_purpose(self):
		request = CustomerMaterialRequest.__new__(CustomerMaterialRequest)
		request.material_request_type = "Manufacture"
		request.auto_created_via_reorder = 1
		validated_purposes = []

		def capture_validated_purpose():
			validated_purposes.append(request.material_request_type)

		with (
			patch.dict(frappe.flags, {"in_auto_reorder_material_request": True}),
			patch(
				"erpnext.assets.doctype.material_request.material_request.MaterialRequest._validate_selects",
				side_effect=capture_validated_purpose,
			),
		):
			request._validate_selects()

		self.assertEqual(validated_purposes, ["Purchase"])
		self.assertEqual(request.material_request_type, "Manufacture")

	def request(self, material_request_type, *, items=None, borrow_item_type=None, status="Pending", docstatus=1):
		return SimpleNamespace(
			reload=Mock(),
			check_permission=Mock(),
			docstatus=docstatus,
			material_request_type=material_request_type,
			borrow_item_type=borrow_item_type,
			request_progress_status=status,
			asset_transfer_status=status,
			company="_Test Company",
			name="MAT-MR-TEST",
			customer="_Test Customer",
			recipient_address="Address-001",
			recipient_address_display="Test address",
			contact_person="Contact-001",
			customer_contact_display="Test contact",
			customer_contact_email="contact@example.test",
			expected_return_date="2026-12-31",
			customer_location="Customer Location",
			loan_recipient_type="Customer",
			loan_recipient_location="Customer Location",
			customer_loan_warehouse="Customer Loan Warehouse - _TC",
			items=items or [],
			update_progress=Mock(),
		)

	def item(self, name, item_type, qty, fulfilled_qty=0, outstanding_qty=0, **values):
		return frappe._dict(
			name=name,
			item_code=values.pop("item_code", "ITEM-001"),
			item_type=item_type,
			qty=qty,
			fulfilled_qty=fulfilled_qty,
			returned_qty=values.pop("returned_qty", 0),
			outstanding_qty=outstanding_qty,
			from_warehouse=values.pop("from_warehouse", "Stores - _TC"),
			**values,
		)

	def test_demo_action_creates_an_asset_custody_loan(self):
		request = self.request(
			"Customer Demo",
			items=[self.item("MR-DEMO", "Fixed Asset", 1, asset="ASSET-001")],
		)
		with (
			patch(
			"erpnext.loan.doctype.custody_loan.custody_loan_core.get_custody_loan_actions",
			return_value=frappe._dict(stock_loan=False, asset_loan=True),
			),
		):
			actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertTrue(actions.asset_custody_loan)
		self.assertFalse(actions.stock_custody_loan)
		self.assertFalse(actions.asset_movement)

	def test_borrow_action_exposes_only_selected_stock_loan(self):
		request = self.request(
			"Customer Borrow / Loan",
			items=[self.item("MR-LOAN-STOCK", "Stock Item", 5)],
			borrow_item_type="Stock Item",
		)
		with patch(
			"erpnext.loan.doctype.custody_loan.custody_loan_core.get_custody_loan_actions",
			return_value=frappe._dict(stock_loan=True, asset_loan=False),
		):
			actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertTrue(actions.stock_custody_loan)
		self.assertFalse(actions.asset_custody_loan)
		self.assertFalse(actions.asset_movement)

	def test_asset_transfer_action_is_available_for_unfulfilled_items(self):
		request = self.request(
			"Asset Transfer",
			items=[
				self.item("MR-TRANSFER-TRANSIT", "Fixed Asset", 1),
				self.item("MR-TRANSFER-OPEN", "Fixed Asset", 1),
			],
		)
		actions = CustomerMaterialRequest.get_post_submit_actions(request)

		self.assertTrue(actions.asset_movement)
		self.assertFalse(actions.stock_custody_loan)

	def test_unsubmitted_request_has_no_post_submit_actions(self):
		request = self.request("Customer Demo", items=[self.item("MR-DRAFT", "Fixed Asset", 1)], docstatus=0)
		actions = CustomerMaterialRequest.get_post_submit_actions(request)
		self.assertFalse(any(actions.values()))

	def test_customer_request_and_asset_transfer_cannot_use_standard_stop_status_action(self):
		for request_type in ("Customer Demo", "Customer Borrow / Loan", "Asset Transfer"):
			request = self.request(request_type)
			with self.subTest(request_type=request_type), patch("frappe.throw", side_effect=ValueError):
				with self.assertRaises(ValueError):
					CustomerMaterialRequest.update_status(request, "Stopped")

	def test_demo_asset_movement_must_be_created_from_the_custody_loan(self):
		request = self.request(
			"Customer Demo",
			items=[self.item("MR-DEMO", "Fixed Asset", 1, item_code="DEMO-ITEM", asset="ASSET-001")],
		)
		with patch("frappe.throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				CustomerMaterialRequest.make_asset_movement(request)

	def test_asset_transfer_movement_uses_material_request_asset_links(self):
		request = self.request(
			"Asset Transfer",
			items=[
				self.item("MR-TRANSIT", "Fixed Asset", 1, asset="ASSET-TRANSIT"),
				self.item(
					"MR-OPEN", "Fixed Asset", 1, asset="ASSET-OPEN", item_code="ITEM-OPEN",
					source_location="Office", target_location="Warehouse",
				),
			],
		)
		movement = FakeDocument("Asset Movement")
		with (
			patch("frappe.new_doc", return_value=movement),
			patch(
				"frappe.get_all",
				return_value=[
					frappe._dict(
						name="ASSET-TRANSIT",
						asset_name="Transit Asset",
						asset_serial_number="SN-TRANSIT",
						is_demo_asset=0,
						demo_loan_status="Available",
					),
					frappe._dict(
						name="ASSET-OPEN",
						asset_name="Open Asset",
						asset_serial_number="SN-OPEN",
						is_demo_asset=1,
						demo_loan_status="Available",
					)
				],
			),
		):
			CustomerMaterialRequest.make_asset_transfer_movement(request)

		self.assertEqual([row.asset for row in movement.assets], ["ASSET-TRANSIT", "ASSET-OPEN"])
		self.assertEqual(movement.assets[1].asset_name, "Open Asset")
		self.assertEqual(movement.assets[1].asset_serial_number, "SN-OPEN")
		self.assertEqual(movement.assets[1].is_demo_asset, 1)
		self.assertEqual(movement.assets[1].demo_loan_status, "Available")
		self.assertEqual(movement.assets[1].request_item, "MR-OPEN")
		self.assertEqual(movement.assets[1].source_location, "Office")
		self.assertEqual(movement.assets[1].target_location, "Warehouse")

	def test_customer_demo_delivery_movement_must_be_created_from_custody_loan(self):
		request = self.request(
			"Customer Demo",
			items=[self.item("MR-DEMO", "Fixed Asset", 1, asset="ASSET-001")],
		)
		with patch("frappe.throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				CustomerMaterialRequest.make_asset_movement(request)

class TestCustomerMaterialRequestIntegration(ERPNextTestSuite):
	company = "_Test Company"
	source_warehouse = "_Test Warehouse - _TC"

	def setUp(self):
		super().setUp()
		ensure_company_tax_settings(self.company)
		frappe.db.set_value(
			"Company", self.company, "capital_work_in_progress_account", "CWIP Account - _TC"
		)
		frappe.db.set_value("Company", self.company, "stock_adjustment_account", "_Test Account Stock Adjustment - _TC")
		create_internal_customer(
			customer_name=f"Custody Loan Internal Customer {test_id()}",
			represents_company=self.company,
		)
		self.loan_warehouse = create_warehouse("_Test Customer Loan Warehouse")
		self.transit_warehouse = create_warehouse(
			"_Test Custody Loan Transit Warehouse", properties={"warehouse_type": "Transit"}
		)
		for name in (self.source_warehouse, self.loan_warehouse, self.transit_warehouse):
			storage_location = frappe.get_doc({
				"doctype": "Storage Location", "warehouse": name,
				"location_code": f"Loan test shelf {test_id()}",
			}).insert()
			warehouse = frappe.get_doc("Warehouse", name)
			warehouse.default_storage_location = storage_location.name
			warehouse.account = "Stock In Hand - _TC"
			warehouse.save()
		frappe.db.set_value("Company", self.company, "customer_loan_warehouse", self.loan_warehouse)
		frappe.db.set_value("Company", self.company, "default_in_transit_warehouse", self.transit_warehouse)
		self.source_location = self.ensure_location("Test Location")
		self.customer_location = self.ensure_location("Customer Location")
		self.target_location = self.ensure_location("Test Location 2")
		self.customer_address = self.ensure_address("_Test Customer", "Customer")
		self.customer_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"Customer Contact {test_id()}",
				"links": [{"link_doctype": "Customer", "link_name": "_Test Customer"}],
			}
		).insert().name
		self.company_address = self.ensure_address(self.company, "Company")
		frappe.db.set_value("Warehouse", self.source_warehouse, "asset_location", self.source_location)

	def ensure_location(self, name):
		if not frappe.db.exists("Location", name):
			frappe.get_doc(
				{
					"doctype": "Location",
					"location_name": name,
				}
			).insert()
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
			do_not_submit=True,
		)
		if frappe.db.exists("Workflow", {"document_type": "Purchase Receipt", "is_active": 1}):
			for action in ("Request Inspection", "Accept Receipt", "Submit Receipt"):
				pr = apply_workflow(pr.as_dict(), action)
				pr.reload()
		else:
			pr.submit()
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

	def make_stock_item(self, qty=5):
		item = make_item(properties={"is_stock_item": 1, "stock_uom": "Nos"})
		make_stock_entry(
			item_code=item.name,
			qty=qty,
			to_warehouse=self.source_warehouse,
			rate=100,
		)
		return item.name

	def test_material_transfer_requires_target_but_allows_blank_source(self):
		stock_item = self.make_stock_item(qty=1)

		def make_transfer(target_warehouse):
			request = frappe.new_doc("Material Request")
			request.material_request_type = "Material Transfer"
			request.company = self.company
			request.transaction_date = today()
			request.schedule_date = add_days(today(), 1)
			request.append(
				"items",
				{
					"item_code": stock_item,
					"qty": 1,
					"uom": "Nos",
					"stock_uom": "Nos",
					"conversion_factor": 1,
					"schedule_date": request.schedule_date,
					"from_warehouse": None,
					"warehouse": target_warehouse,
				},
			)
			return request

		with self.assertRaises(frappe.ValidationError):
			make_transfer(None).insert()

		request = make_transfer(self.loan_warehouse).insert()
		self.assertFalse(request.items[0].from_warehouse)

	def make_request(
		self,
		request_type,
		items=None,
		*,
		borrow_item_type=None,
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
		if request_type == "Customer Borrow / Loan":
			request.borrow_item_type = borrow_item_type or "Stock Item"
			request.loan_recipient_type = recipient_type
			request.loan_recipient = recipient or "_Test Customer"
			if recipient_type == "Customer":
				request.customer = request.loan_recipient
				request.recipient_address = self.customer_address
				request.contact_person = self.customer_contact
				request.customer_location = self.customer_location
				request.loan_recipient_contact = self.customer_contact
				request.loan_recipient_location = self.customer_location
			elif recipient_type == "Contact":
				request.loan_recipient_contact = request.loan_recipient
			if include_expected_return_date:
				request.expected_return_date = add_days(today(), 30)
		elif request_type == "Customer Demo" and customer_request:
			request.customer = "_Test Customer"
			request.recipient_address = self.customer_address
			request.contact_person = self.customer_contact
			if include_expected_return_date:
				request.expected_return_date = add_days(today(), 30)
			request.customer_location = self.customer_location
		for values in items or []:
			if values.get("asset"):
				asset = frappe.get_doc("Asset", values["asset"])
				item = frappe.get_cached_doc("Item", asset.item_code)
				request.append(
					"items",
					{
						"asset": asset.name,
						"item_code": item.name,
						"item_name": item.item_name,
						"qty": 1,
						"uom": item.stock_uom,
						"stock_uom": item.stock_uom,
						"conversion_factor": 1,
						"schedule_date": request.schedule_date,
						"target_location": values.get("target_location"),
					}
				)
				continue
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
				}
			)
		request.insert()
		if frappe.db.exists("Workflow", {"document_type": "Material Request", "is_active": 1}):
			apply_workflow(request.as_dict(), "Request Approval")
			request.reload()
			approval_transitions = [
				transition
				for transition in get_transitions(request)
				if transition.next_state == "Submitted"
			]
			if len(approval_transitions) != 1:
				frappe.throw(
					_("Expected one eligible approval transition for Material Request {0}.").format(
						request.name
					)
				)
			apply_workflow(request.as_dict(), approval_transitions[0].action)
			request.reload()
		else:
			request.submit()
		return frappe.get_doc("Material Request", request.name)

	def make_custody_loan(self, request, loan_type, requires_shipment=False):
		loan_data = request.make_custody_loan(loan_type)
		loan = frappe.get_doc("Custody Loan", loan_data["name"])
		if loan_type == "Stock":
			for row in loan.items:
				row.source_warehouse = self.source_warehouse
		loan.requires_shipment = cint(requires_shipment)
		loan.save()
		return loan

	def complete_stock_loan_issue(self, loan):
		shipment = None
		pick_list = frappe.get_doc("Pick List", loan.pick_list)
		pick_list.submit()
		delivery_note = frappe.get_doc("Delivery Note", loan.make_delivery_note()["name"])
		if loan.requires_shipment:
			shipment = self.create_shipment("Delivery Note", delivery_note.name)
		else:
			delivery_note.submit()
		loan.reload()
		return frappe.get_doc("Delivery Note", delivery_note.name), shipment if loan.requires_shipment else None

	def complete_stock_loan_return(self, return_doc):
		from erpnext.loan.doctype.custody_loan.custody_loan_core import (
			make_custody_loan_return_quality_inspections,
		)

		return_doc.reload()
		delivery_note = frappe.get_doc("Delivery Note", return_doc.delivery_note)
		self.assertEqual(delivery_note.docstatus, 0)
		self.assertEqual(
			frappe.db.count(
				"Stock Ledger Entry",
				{"voucher_type": "Delivery Note", "voucher_no": delivery_note.name},
			),
			0,
			"A draft Loan Receive must not post stock",
		)

		inspection_names = make_custody_loan_return_quality_inspections(delivery_note.name)
		self.assertEqual(len(inspection_names), len(delivery_note.items))
		for inspection_name in inspection_names:
			inspection = frappe.get_doc("Quality Inspection", inspection_name)
			inspection.status = "Accepted"
			inspection.remarks = "Returned item inspected and accepted"
			inspection.save()
			inspection.submit()

		delivery_note.reload()
		self.assertEqual(
			len({row.quality_inspection for row in delivery_note.items}),
			len(delivery_note.items),
			"Every returned Delivery Note row must keep its own inspection link",
		)
		return_doc.reload()
		self.assertEqual(return_doc.status, "Pending Receipt")
		delivery_note.submit()
		return_doc.reload()
		self.assertEqual(return_doc.status, "Received")
		self.assertGreater(
			frappe.db.count(
				"Stock Ledger Entry",
				{"voucher_type": "Delivery Note", "voucher_no": delivery_note.name},
			),
			0,
			"Submitting Loan Receive must post stock through Delivery Note",
		)
		return return_doc

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

	def test_demo_fixed_assets_move_directly_to_customer_and_back(self):
		asset_item_code = f"Demo Asset {test_id()}"
		demo_assets = [self.make_asset(asset_item_code, is_demo=True) for _ in range(2)]
		request = self.make_request(
			"Customer Demo",
			items=[{"asset": asset.name} for asset in demo_assets],
		)

		actions = request.get_post_submit_actions()
		self.assertTrue(actions.asset_custody_loan)
		loan = self.make_custody_loan(request, "Asset")
		loan.submit()
		movement = frappe.get_doc("Asset Movement", loan.asset_movement)
		self.assertEqual(movement.docstatus, 1)
		self.assertEqual(frappe.db.get_value("Asset", demo_assets[0].name, "location"), self.customer_location)
		self.assertEqual(
			frappe.db.get_value("Asset", demo_assets[0].name, "demo_loan_status"), "With Customer"
		)
		request.reload()
		self.assertEqual(loan.status, "Active")
		self.assertEqual(request.request_progress_status, "Fulfilled")
		for asset in demo_assets:
			self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.customer_location)

		asset_return = loan.make_return()
		asset_return.submit()
		asset_return.reload()
		loan.reload()
		request.reload()
		self.assertEqual(asset_return.status, "Received")
		self.assertEqual(loan.status, "Returned")
		self.assertEqual(request.request_progress_status, "Returned")
		for asset in demo_assets:
			self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.source_location)

	def test_asset_custody_loan_uses_selected_material_request_asset(self):
		asset_item_code = f"Loan Asset {test_id()}"
		asset = self.make_asset(asset_item_code)
		request = self.make_request(
			"Customer Borrow / Loan",
			borrow_item_type="Asset",
			items=[{"asset": asset.name, "target_location": self.customer_location}],
		)
		actions = request.get_post_submit_actions()
		self.assertTrue(actions.asset_custody_loan)
		self.assertFalse(actions.stock_custody_loan)
		asset_loan = self.make_custody_loan(request, "Asset")
		asset_loan.submit()
		self.assertEqual(asset_loan.status, "Active")
		asset_movement = frappe.get_doc("Asset Movement", asset_loan.asset_movement)
		self.assertEqual(asset_movement.is_system_generated, 1)
		self.assertEqual(asset_movement.is_custody_loan_transaction, 1)
		self.assertEqual(asset_movement.custody_loan, asset_loan.name)
		self.assertEqual(asset_movement.assets[0].request_item, request.items[0].name)
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.customer_location)
		asset_return = asset_loan.make_return()
		asset_return.submit()
		asset_loan.reload()
		request.reload()
		self.assertEqual(asset_loan.status, "Returned")
		self.assertEqual(request.request_progress_status, "Returned")
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.source_location)

	def test_stock_loan_return_waits_for_shipment_and_quality_inspection(self):
		stock_item = self.make_stock_item(qty=5)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": stock_item}])

		stock_loan = self.make_custody_loan(request, "Stock", requires_shipment=True)
		stock_loan.submit()
		stock_issue, stock_issue_shipment = self.complete_stock_loan_issue(stock_loan)
		self.assertEqual(stock_issue.docstatus, 1)
		stock_loan.reload()
		self.assertEqual(stock_loan.status, "Pending Shipment")
		self.assertFalse(stock_loan.stock_entry, "Loan issue stock must be posted by the Delivery Note")
		ledger_rows = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_type": "Delivery Note", "voucher_no": stock_issue.name},
			fields=["warehouse", "actual_qty"],
		)
		self.assertEqual(len(ledger_rows), 2)
		self.assertEqual(
			{(row.warehouse, row.actual_qty) for row in ledger_rows},
			{(self.source_warehouse, -1.0), (self.loan_warehouse, 1.0)},
		)
		self.confirm_shipment_delivery(stock_issue_shipment)
		stock_loan.reload()
		self.assertEqual(stock_loan.status, "Active")

		stock_return = stock_loan.make_return()
		stock_return.requires_shipment = 1
		stock_return.save()
		stock_return.submit()
		stock_return_dn = frappe.get_doc("Delivery Note", stock_return.delivery_note)
		stock_shipment = self.create_shipment("Delivery Note", stock_return_dn.name)
		stock_return.reload()
		self.assertEqual(stock_return.status, "Pending Receipt")
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")
		self.confirm_shipment_delivery(stock_shipment)
		stock_return.reload()
		stock_loan.reload()
		self.assertEqual(stock_return.status, "Pending Quality Inspection")
		self.assertIn(stock_loan.status, ("Pending Quality Inspection", "Pending Receipt"))
		self.complete_stock_loan_return(stock_return)
		stock_loan.reload()
		self.assertEqual(stock_return.status, "Received")
		self.assertEqual(stock_loan.status, "Returned")

	def test_asset_loan_cannot_use_shipment(self):
		asset_item_code = f"Direct Loan Asset {test_id()}"
		asset = self.make_asset(asset_item_code)
		asset_request = self.make_request(
			"Customer Borrow / Loan",
			borrow_item_type="Asset",
			items=[{"asset": asset.name, "target_location": self.customer_location}],
		)
		with self.assertRaises(frappe.ValidationError):
			self.make_custody_loan(asset_request, "Asset", requires_shipment=True)

	def test_borrow_partial_stock_return_updates_request_status(self):
		stock_item = self.make_stock_item(qty=5)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": stock_item, "qty": 2}])

		stock_loan = self.make_custody_loan(request, "Stock")
		stock_loan.submit()
		self.complete_stock_loan_issue(stock_loan)
		request.reload()
		self.assertEqual(request.request_progress_status, "Fulfilled")

		return_doc = stock_loan.make_return()
		return_doc.items[0].returned_qty = 1
		return_doc.submit()
		return_doc.reload()
		self.assertEqual(return_doc.status, "Pending Quality Inspection")
		self.complete_stock_loan_return(return_doc)
		stock_loan.reload()
		request.reload()

		self.assertEqual(request.request_progress_status, "Partially Returned")
		self.assertEqual(request.items[0].status, "Partially Returned")
		self.assertEqual(request.items[0].outstanding_qty, 1)

	def test_stock_return_inspections_link_to_duplicate_item_rows_individually(self):
		settings = [frappe.get_single(doctype) for doctype in ("Buying Settings", "Selling Settings")]
		previous = [doc.allow_multiple_items for doc in settings]
		try:
			for doc in settings:
				doc.allow_multiple_items = 1
				doc.save()
			stock_item = self.make_stock_item(qty=3)
			request = self.make_request(
				"Customer Borrow / Loan",
				[{"item_code": stock_item}, {"item_code": stock_item}],
			)
			loan = self.make_custody_loan(request, "Stock")
			loan.submit()
			self.complete_stock_loan_issue(loan)

			return_doc = loan.make_return()
			return_doc.submit()
			self.complete_stock_loan_return(return_doc)

			delivery_note = frappe.get_doc("Delivery Note", return_doc.delivery_note)
			self.assertEqual(len(delivery_note.items), 2)
			self.assertEqual(len({row.quality_inspection for row in delivery_note.items}), 2)
		finally:
			for doc, value in zip(settings, previous, strict=True):
				doc.allow_multiple_items = value
				doc.save()

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
				self.complete_stock_loan_issue(loan)
				self.assertEqual(loan.borrower_type, recipient_type)
				self.assertEqual(loan.borrower, recipient)
				self.assertEqual(loan.status, "Active")
				return_doc = loan.make_return()
				return_doc.submit()
				self.complete_stock_loan_return(return_doc)
				loan.reload()
				self.assertEqual(loan.status, "Returned")

	def test_stock_batch_return_requires_original_batch_and_adjustment_closes_loan(self):
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
		self.complete_stock_loan_issue(loan)
		frappe.db.set_value("Company", self.company, "stock_adjustment_account", "Stock Adjustment - _TC")

		returned = loan.make_return()
		returned.items[0].batch_no = replacement_batch.name
		with self.assertRaises(frappe.ValidationError):
			returned.submit()
		self.assertFalse(frappe.db.exists("Custody Loan Return", returned.name))
		self.assertEqual(loan.items[0].outstanding_qty, 1)
		self.assertEqual(loan.status, "Active")

		adjustment = loan.make_adjustment()
		adjustment.items[0].reason = "Write off the outstanding issued Batch"
		adjustment.submit()
		loan.reload()
		self.assertEqual(adjustment.status, "Submitted")
		self.assertEqual(loan.status, "Closed")
		self.assertEqual(loan.items[0].outstanding_qty, 0)

	def test_cancelling_return_then_loan_cancels_generated_delivery_note_and_pick_list(self):
		stock_item = self.make_stock_item(qty=2)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": stock_item}])
		loan = self.make_custody_loan(request, "Stock")
		loan.submit()
		self.complete_stock_loan_issue(loan)

		return_doc = loan.make_return()
		return_doc.submit()
		with self.assertRaises(frappe.ValidationError):
			loan.cancel()

		return_delivery_note = return_doc.delivery_note
		return_doc.cancel()
		loan.reload()
		self.assertEqual(loan.status, "Active")
		loan.cancel()
		loan.reload()
		self.assertEqual(loan.docstatus, 2)
		self.assertEqual(loan.status, "Cancelled")
		self.assertEqual(frappe.db.get_value("Delivery Note", loan.delivery_note, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Pick List", loan.pick_list, "docstatus"), 2)
		self.assertFalse(frappe.db.exists("Delivery Note", return_delivery_note))

	def test_asset_transfer_partial_and_full_movements_update_request_status(self):
		asset_item_code = f"Transfer Asset {test_id()}"
		assets = [self.make_asset(asset_item_code) for _ in range(2)]
		request = self.make_request(
			"Asset Transfer",
			items=[{"asset": asset.name, "target_location": self.target_location} for asset in assets],
			customer_request=False,
		)
		self.assertTrue(request.get_post_submit_actions().asset_movement)
		movement = request.make_asset_movement()
		movement.assets.pop()
		transferred_asset = movement.assets[0].asset
		remaining_asset = next(asset for asset in assets if asset.name != transferred_asset)
		movement.submit()

		self.assertEqual(frappe.db.get_value("Asset", transferred_asset, "location"), self.target_location)
		request.reload()
		self.assertEqual(request.asset_transfer_status, "Partially Fulfilled")
		self.assertTrue(request.get_post_submit_actions().asset_movement)

		remaining_movement = request.make_asset_movement()
		self.assertEqual([row.asset for row in remaining_movement.assets], [remaining_asset.name])
		remaining_movement.submit()
		request.reload()
		self.assertEqual(request.asset_transfer_status, "Fulfilled")
		self.assertEqual(frappe.db.get_value("Asset", remaining_asset.name, "location"), self.target_location)

	def test_asset_transfer_direct_move_updates_status_and_location(self):
		asset = self.make_asset(f"Direct Transfer Asset {test_id()}")
		request = self.make_request(
			"Asset Transfer",
			items=[{"asset": asset.name, "target_location": self.target_location}],
			customer_request=False,
		)

		movement = request.make_asset_movement()
		movement.submit()
		request.reload()
		self.assertEqual(request.asset_transfer_status, "Fulfilled")
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.target_location)
		self.assertFalse(request.get_post_submit_actions().asset_movement)

	def test_partial_asset_transfer_move_updates_status_and_cancelling_it_recalculates_progress(self):
		asset_item_code = f"Partial Transfer Asset {test_id()}"
		assets = [self.make_asset(asset_item_code) for _ in range(2)]
		request = self.make_request(
			"Asset Transfer",
			items=[{"asset": asset.name, "target_location": self.target_location} for asset in assets],
			customer_request=False,
		)
		main_status = request.status

		movement = request.make_asset_movement()
		movement.assets.pop()
		transferred_asset = movement.assets[0].asset
		movement.submit()

		request.reload()
		self.assertEqual(request.asset_transfer_status, "Partially Fulfilled")
		self.assertEqual(request.status, main_status)

		movement.cancel()
		request.reload()
		self.assertEqual(request.asset_transfer_status, "Pending")
		self.assertEqual(request.status, main_status)
		self.assertEqual(frappe.db.get_value("Asset", transferred_asset, "location"), self.source_location)

	def test_cancelling_asset_transfer_request_cancels_its_asset_movement(self):
		asset = self.make_asset(f"Cancelled Transfer Asset {test_id()}")
		request = self.make_request(
			"Asset Transfer",
			items=[{"asset": asset.name, "target_location": self.target_location}],
			customer_request=False,
		)
		movement = request.make_asset_movement()
		movement.submit()

		request.reload()
		request.cancel()
		request.reload()
		movement.reload()

		self.assertEqual(request.docstatus, 2)
		self.assertEqual(request.asset_transfer_status, "Cancelled")
		self.assertEqual(movement.docstatus, 2)
		self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.source_location)

	def test_asset_movement_cannot_create_shipment(self):
		asset = self.make_asset(f"Unshipped Asset {test_id()}")
		request = self.make_request(
			"Asset Transfer",
			items=[{"asset": asset.name, "target_location": self.target_location}],
			customer_request=False,
		)
		movement = request.make_asset_movement()
		with self.assertRaises(frappe.ValidationError):
			make_shipment_from_document("Asset Movement", movement.name)

	def test_demo_request_rejects_stock_items(self):
		stock_item = self.make_stock_item(qty=1)
		with self.assertRaises(frappe.ValidationError):
			self.make_request("Customer Demo", items=[{"item_code": stock_item}])

	def test_demo_and_loan_requests_require_an_expected_return_date(self):
		asset_item_code = f"Demo Asset {test_id()}"
		asset = self.make_asset(asset_item_code, is_demo=True)
		stock_item = self.make_stock_item(qty=2)
		with self.assertRaises(frappe.ValidationError):
			self.make_request(
				"Customer Demo",
				items=[{"asset": asset.name}],
				include_expected_return_date=False,
			)
		with self.assertRaises(frappe.ValidationError):
			self.make_request(
				"Customer Borrow / Loan",
				[{"item_code": stock_item}],
				include_expected_return_date=False,
			)

	def test_demo_request_rejects_an_asset_not_marked_for_demo(self):
		asset_item_code = f"Non Demo Asset {test_id()}"
		asset = self.make_asset(asset_item_code, is_demo=False)
		with self.assertRaises(frappe.ValidationError):
			self.make_request("Customer Demo", items=[{"asset": asset.name}])

	def test_custody_loan_rejects_changing_generated_asset_to_another_asset(self):
		asset_item_code = f"Requested Loan Asset {test_id()}"
		requested_asset = self.make_asset(asset_item_code)
		other_asset = self.make_asset(asset_item_code)
		request = self.make_request(
			"Customer Borrow / Loan",
			borrow_item_type="Asset",
			items=[{"asset": requested_asset.name, "target_location": self.customer_location}],
		)
		loan = self.make_custody_loan(request, "Asset")
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
				items=[
					{"asset": duplicate_asset.name, "target_location": self.target_location},
					{"asset": duplicate_asset.name, "target_location": self.target_location},
				],
				customer_request=False,
			)

		target_asset = self.make_asset(f"Changed Target Asset {test_id()}")
		target_request = self.make_request(
			"Asset Transfer",
			items=[{"asset": target_asset.name, "target_location": self.target_location}],
			customer_request=False,
		)
		wrong_target = target_request.make_asset_movement()
		wrong_target.assets[0].target_location = self.customer_location
		with self.assertRaises(frappe.ValidationError):
			wrong_target.insert()

		source_asset = self.make_asset(f"Changed Source Asset {test_id()}")
		source_request = self.make_request(
			"Asset Transfer",
			items=[{"asset": source_asset.name, "target_location": self.target_location}],
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
