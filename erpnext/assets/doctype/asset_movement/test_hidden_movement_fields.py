"""The row fields a purpose hides are not stored (UX WP9, 19 Nov 2026). Offline: no site, frappe.db mocked."""

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.asset_movement.asset_movement import AssetMovement


def movement(purpose, rows, **values):
	doc = AssetMovement.__new__(AssetMovement)
	doc.doctype = "Asset Movement"
	doc.meta = frappe._dict(_table_doctypes=[])
	doc.purpose = purpose
	doc.company = "CD"
	doc.assets = [frappe._dict(row) for row in rows]
	for key, value in values.items():
		setattr(doc, key, value)
	return doc


TYPED = {
	"asset": "AST-1",
	"source_location": "Office",
	"target_location": "Typed Earlier",
	"from_employee": "EMP-1",
	"to_employee": "EMP-2",
	"change_custodian": 1,
	"change_department": 1,
	"target_department": "Sales",
}


class TestHiddenMovementFields(unittest.TestCase):
	def test_an_issue_and_a_reassignment_store_no_location(self):
		for purpose in ("Issue", "Reassignment"):
			doc = movement(purpose, [TYPED])
			doc.clear_hidden_fields()
			row = doc.assets[0]
			self.assertIsNone(row.source_location, purpose)
			self.assertIsNone(row.target_location, purpose)
			self.assertEqual(row.from_employee, "EMP-1", purpose)

	def test_the_reassignment_choices_stay_only_on_a_reassignment(self):
		doc = movement("Reassignment", [TYPED])
		doc.clear_hidden_fields()
		self.assertEqual((doc.assets[0].change_custodian, doc.assets[0].change_department), (1, 1))
		self.assertEqual(doc.assets[0].target_department, "Sales")
		for purpose in ("Transfer", "Receipt", "Issue", "Transfer and Issue"):
			doc = movement(purpose, [TYPED])
			doc.clear_hidden_fields()
			row = doc.assets[0]
			self.assertEqual((row.change_custodian, row.change_department), (0, 0), purpose)
			self.assertIsNone(row.target_department, purpose)

	def test_a_transfer_and_a_receipt_keep_their_places_and_the_custodian(self):
		for purpose in ("Transfer", "Receipt", "Transfer and Issue"):
			doc = movement(purpose, [TYPED])
			doc.clear_hidden_fields()
			row = doc.assets[0]
			self.assertEqual((row.source_location, row.target_location), ("Office", "Typed Earlier"))
			self.assertEqual(row.from_employee, "EMP-1")

	def test_borrowing_and_service_equipment_rows_keep_what_the_system_wrote(self):
		for values in ({"is_borrowing_transaction": 1}, {"borrowing": "BRW-1"}):
			doc = movement("Issue", [TYPED], **values)
			doc.clear_hidden_fields()
			self.assertEqual(doc.assets[0].target_location, "Typed Earlier")
		doc = movement("Issue", [{**TYPED, "asset": None, "service_equipment": "EQ-1"}])
		doc.clear_hidden_fields()
		self.assertEqual(doc.assets[0].target_location, "Typed Earlier")

	def test_an_issue_does_not_move_the_asset(self):
		"""The latest movement's target becomes the Asset's location; a cleared Issue has none, so Asset.location stays."""
		doc = movement("Issue", [TYPED])
		doc.clear_hidden_fields()
		row = doc.assets[0]
		asset = frappe._dict(custodian="EMP-1", location="Office")
		with (
			patch("frappe.db", Mock()) as db,
			patch("frappe.get_doc", return_value=asset),
		):
			db.sql.return_value = [(row.target_location, row.to_employee)]
			location, employee = doc.get_latest_location_and_custodian(row.asset)
			doc.update_asset_location_and_custodian(row.asset, location, employee)
		written = [call.args[2] for call in db.set_value.call_args_list]
		self.assertNotIn("location", written)
		self.assertIn("custodian", written)


if __name__ == "__main__":
	unittest.main()
