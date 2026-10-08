import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.asset_movement.asset_movement import AssetMovement
from erpnext.patches.v16_0.migrate_asset_movement_customer_use_reference import execute


class TestAssetMovementRequestReference(unittest.TestCase):
	def setUp(self):
		for target, replacement in (
			("frappe.db", Mock()),
			("erpnext.assets.doctype.asset_movement.asset_movement._", lambda message: message),
			("erpnext.patches.v16_0.migrate_asset_movement_customer_use_reference._", lambda message: message),
		):
			patcher = patch(target, replacement)
			patcher.start()
			self.addCleanup(patcher.stop)

	def movement(self, reference_doctype="Material Request", reference_name="CUR-001"):
		movement = AssetMovement.__new__(AssetMovement)
		movement.reference_doctype = reference_doctype
		movement.reference_name = reference_name
		movement.assets = []
		return movement

	def test_purchase_reference_does_not_update_request(self):
		movement = self.movement("Purchase Receipt")
		with patch("frappe.get_doc") as get_doc:
			movement.validate_material_request()
			movement.update_request_progress()
			movement.update_demo_loan_status()
		get_doc.assert_not_called()

	def test_missing_request_reference_is_rejected(self):
		with patch("frappe.throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				self.movement(reference_name=None).validate_material_request()

	def test_request_items_cannot_use_purchase_reference(self):
		movement = self.movement("Purchase Invoice")
		movement.assets = [frappe._dict(request_item="CUR-ITEM-001")]
		with patch("frappe.throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				movement.validate_material_request()

	def test_progress_uses_reference_name(self):
		request = Mock(material_request_type="Customer Borrow / Loan", request_progress_status="Fulfilled")
		with patch("frappe.get_doc", return_value=request) as get_doc:
			self.movement().update_request_progress()
		get_doc.assert_called_once_with("Material Request", "CUR-001")
		request.update_progress.assert_called_once_with()
		request.db_set.assert_called_once_with("request_progress_status", "Fulfilled")

	def test_customer_transfer_validates_locations_without_employee(self):
		movement = self.movement()
		movement.customer = "Customer-001"
		movement.purpose = "Transfer"
		row = frappe._dict(asset="ASSET-001", source_location=None, target_location="Customer Location")
		with (
			patch("frappe.get_cached_doc", return_value=frappe._dict(customer_location="Customer Location")),
			patch("frappe.db.get_value", return_value="Office Location"),
			patch.object(movement, "validate_employee") as validate_employee,
		):
			movement.validate_movement(row)
		self.assertEqual(row.source_location, "Office Location")
		validate_employee.assert_not_called()

	def test_customer_transfer_rejects_wrong_target(self):
		movement = self.movement()
		movement.customer = "Customer-001"
		movement.purpose = "Transfer"
		row = frappe._dict(target_location="Other Location")
		with (
			patch("frappe.get_cached_doc", return_value=frappe._dict(customer_location="Customer Location")),
			patch("frappe.throw", side_effect=ValueError),
		):
			with self.assertRaises(ValueError):
				movement.validate_movement(row)

	def test_asset_transfer_matches_the_requested_asset_and_locations(self):
		movement = self.movement()
		movement.purpose = "Transfer"
		movement.customer = None
		movement.expected_return_date = None
		movement.shipment_source_movement = None
		movement.get = lambda fieldname: False
		row = frappe._dict(
			asset="ASSET-001",
			request_item="MR-ITEM-001",
			source_location=None,
			target_location="Target Location",
			shipment_destination_location=None,
		)
		movement.assets = [row]
		request_item = frappe._dict(
			name="MR-ITEM-001",
			parent="MAT-MR-001",
			asset_transfer_asset="ASSET-001",
			asset_transfer_source_location="Source Location",
			asset_transfer_target_location="Target Location",
			qty=1,
			fulfilled_qty=0,
		)
		request = Mock(items=[request_item], update_progress=Mock())
		request.name = "MAT-MR-001"
		with patch("frappe.db.get_value", return_value="Source Location"):
			AssetMovement.validate_asset_transfer_request(movement, request)

		self.assertEqual(row.source_location, "Source Location")
		request.update_progress.assert_called_once_with()

	def test_asset_transfer_rejects_a_stale_source_location(self):
		movement = self.movement()
		movement.purpose = "Transfer"
		movement.customer = None
		movement.expected_return_date = None
		movement.shipment_source_movement = None
		movement.get = lambda fieldname: False
		movement.assets = [
			frappe._dict(
				asset="ASSET-001",
				request_item="MR-ITEM-001",
				source_location="Source Location",
				target_location="Target Location",
				shipment_destination_location=None,
			)
		]
		request_item = frappe._dict(
			name="MR-ITEM-001",
			parent="MAT-MR-001",
			asset_transfer_asset="ASSET-001",
			asset_transfer_source_location="Source Location",
			asset_transfer_target_location="Target Location",
			qty=1,
			fulfilled_qty=0,
		)
		request = Mock(items=[request_item], update_progress=Mock())
		request.name = "MAT-MR-001"
		with (
			patch("frappe.db.get_value", return_value="A Different Location"),
			patch("frappe.throw", side_effect=ValueError),
		):
			with self.assertRaises(ValueError):
				AssetMovement.validate_asset_transfer_request(movement, request)

	def test_custody_loan_asset_movement_requires_a_loan_marker(self):
		movement = self.movement()
		movement.customer = "Customer-001"
		movement.company = "Company-001"
		movement.purpose = "Receipt"
		movement.expected_return_date = "2026-10-10"
		movement.assets = [frappe._dict(asset="ASSET-001", request_item="CUR-ITEM-001")]
		request_item = frappe._dict(
			name="CUR-ITEM-001", parent="CUR-001", item_code="ITEM-001",
			item_type="Fixed Asset", qty=1, fulfilled_qty=1, outstanding_qty=1,
		)
		request = Mock(
			name="CUR-001", docstatus=1, customer=movement.customer, company=movement.company,
			contact_person=None, material_request_type="Customer Borrow / Loan", items=[request_item],
		)
		# Mock's name argument names the mock rather than setting the document's name.
		request.name = "CUR-001"
		with (
			patch("frappe.get_doc", side_effect=lambda doctype, name: request if doctype == "Material Request" else request_item),
			patch("frappe.db.get_value", return_value="ITEM-001"),
			patch("frappe.db.sql") as sql,
			patch("erpnext.assets.doctype.material_request.material_request.get_in_transit_asset_movement_qty", return_value=0),
			patch(
				"erpnext.assets.doctype.material_request.material_request.get_customer_delivery_snapshot",
				return_value=frappe._dict(),
			),
			patch("frappe.throw", side_effect=ValueError),
		):
			for reference_doctype, reference_name in (
				("Purchase Receipt", "CUR-001"), ("Material Request", "CUR-002"),
			):
				sql.return_value = [frappe._dict(purpose="Transfer", reference_doctype=reference_doctype, reference_name=reference_name)]
				with self.assertRaises(ValueError):
					movement.validate_material_request()
			sql.return_value = [frappe._dict(purpose="Transfer", reference_doctype="Material Request", reference_name="CUR-001")]
			with self.assertRaises(ValueError):
				movement.validate_material_request()

	def test_migration_without_legacy_column(self):
		with patch("frappe.db.has_column", return_value=False), patch("frappe.db.sql") as sql:
			execute()
		sql.assert_not_called()

	def test_migration_preserves_conflicting_reference(self):
		with (
			patch("frappe.db.has_column", return_value=True),
			patch("frappe.db.sql", return_value=["ASM-001"]) as sql,
			patch("frappe.throw", side_effect=ValueError),
		):
			with self.assertRaises(ValueError):
				execute()
		self.assertEqual(sql.call_count, 1)

	def test_migration_copies_legacy_reference(self):
		with patch("frappe.db.has_column", return_value=True), patch("frappe.db.sql", return_value=[]) as sql:
			execute()
		self.assertEqual(sql.call_count, 2)
		self.assertIn("reference_name = customer_demo_loan_request", sql.call_args.args[0])
