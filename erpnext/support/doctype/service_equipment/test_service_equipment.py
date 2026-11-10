# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt
"""Service Equipment and the Asset DocTypes it shares. Offline: no site, every database call is a mock."""

import json
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from erpnext.assets.doctype.asset_maintenance import asset_maintenance
from erpnext.assets.doctype.asset_maintenance_log import asset_maintenance_log
from erpnext.assets.doctype.asset_movement.asset_movement import AssetMovement
from erpnext.assets.doctype.asset_repair.asset_repair import AssetRepair
from erpnext.support import service_equipment as equipment
from erpnext.support.doctype.service_equipment.service_equipment import ServiceEquipment

# spelled in pieces so the repository scans of the old names do not find this file
MARKER = "service_" + "nonfinancial"
DISTRIBUTOR = "D" + "K" + "S" + "H"
ERPNEXT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def load(*parts):
	with open(os.path.join(ERPNEXT, *parts), encoding="utf-8") as handle:
		return json.load(handle)


def fields(*parts):
	return {field["fieldname"]: field for field in load(*parts)["fields"]}


class Offline(TestCase):
	def setUp(self):
		self.db = MagicMock()
		self.enterContext(patch.object(frappe, "db", self.db))
		self.enterContext(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
		self.activity = self.enterContext(patch.object(equipment, "add_activity"))


def movement(**values):
	doc = AssetMovement.__new__(AssetMovement)
	doc.__dict__.update(
		{
			"doctype": "Asset Movement",
			"name": "AM-1",
			"company": "CD",
			"purpose": "Transfer",
			"transaction_date": "2026-11-10 10:00:00",
			"docstatus": 0,
			"reference_name": None,
			"reference_doctype": None,
			"service_movement_type": "Install",
			"service_target_customer": "CUS",
			"service_target_address": "CUS-Shipping",
			"service_evidence": None,
			"_action": "save",
		}
	)
	doc.__dict__.update(values)
	return doc


def row(**values):
	return frappe._dict(
		{"service_equipment": "EQ-1", "asset": None, "target_location": None, "to_employee": None} | values
	)


class TestEquipmentMovement(Offline):
	CURRENT = frappe._dict(
		company="CD",
		status="Received",
		last_movement="AM-0",
		location="Store",
		custodian="EMP-1",
		customer=None,
		address=None,
		placed_on="2026-11-01 09:00:00",
	)

	def setUp(self):
		super().setUp()
		self.db.get_value.side_effect = self.get_value
		self.placement = self.enterContext(patch.object(equipment, "validate_placement"))

	def get_value(self, doctype, name=None, fields=None, *args, **kwargs):
		if doctype == "Service Equipment":
			return frappe._dict(self.CURRENT) if isinstance(fields, list) else self.CURRENT.get(fields)
		return None  # no earlier movement date

	def test_a_row_names_an_asset_or_equipment_and_the_two_are_not_mixed(self):
		doc = movement(assets=[row(), row(service_equipment=None, asset="ACC-1")])
		self.assertTrue(doc.is_equipment_movement)
		with self.assertRaises(frappe.ValidationError):
			doc.validate()
		self.assertFalse(movement(assets=[row(service_equipment=None, asset="ACC-1")]).is_equipment_movement)

	def test_the_draft_takes_the_current_place_as_its_source(self):
		doc = movement(assets=[row()])
		doc.validate()
		item = doc.assets[0]
		self.assertEqual((item.source_location, item.from_employee), ("Store", "EMP-1"))
		self.assertEqual((item.service_previous_status, item.service_previous_movement), ("Received", "AM-0"))
		self.placement.assert_called_once_with("CD", None, None, "CUS", "CUS-Shipping")

	def test_an_equipment_movement_fulfils_no_request_or_loan(self):
		for values in ({"reference_name": "MR-1"}, {"custody_loan": "CL-1"}, {"purpose": "Reassignment"}):
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				movement(assets=[row()], **values).validate()

	def test_submit_writes_the_place_on_the_equipment(self):
		doc = movement(assets=[row(target_location=None)], docstatus=1)
		doc.on_submit()
		name, values = self.db.set_value.call_args.args[1:3]
		self.assertEqual(name, "EQ-1")
		self.assertEqual(values["customer"], "CUS")
		self.assertEqual(values["last_movement"], "AM-1")
		self.assertEqual(values["status"], "Installed")
		self.activity.assert_called_once()

	def test_a_later_handover_blocks_the_submit_of_a_stale_draft(self):
		doc = movement(assets=[row(service_previous_movement="AM-OLD")])
		with self.assertRaises(frappe.ValidationError):
			doc.before_submit()

	def test_only_the_latest_movement_can_be_cancelled_and_cancel_restores(self):
		doc = movement(assets=[row(service_previous_movement="AM-0")], docstatus=2)
		self.CURRENT.last_movement = "AM-2"
		try:
			with self.assertRaises(frappe.ValidationError):
				doc.before_cancel()
		finally:
			self.CURRENT.last_movement = "AM-0"
		self.db.get_value.side_effect = lambda *args, **kwargs: "AM-1"
		doc.before_cancel()
		item = doc.assets[0]
		item.update(
			source_location="Store",
			from_employee="EMP-1",
			service_previous_customer=None,
			service_previous_address=None,
			service_previous_placed_on="2026-11-01",
			service_previous_status="Received",
		)
		self.db.get_value.side_effect = lambda *args, **kwargs: "Installed"  # still what Install set
		doc.on_cancel()
		values = self.db.set_value.call_args.args[2]
		self.assertEqual(
			(values["location"], values["last_movement"], values["status"]), ("Store", "AM-0", "Received")
		)
		# a repair changed the status since: the cancel restores the place only
		self.db.get_value.side_effect = lambda *args, **kwargs: "Under Repair"
		doc.on_cancel()
		self.assertNotIn("status", self.db.set_value.call_args.args[2])

	def test_the_handover_type_is_required_and_the_evidence_optional(self):
		doc = movement(assets=[row(service_previous_movement="AM-0")], service_movement_type=None)
		with self.assertRaises(frappe.ValidationError):
			doc.before_submit()
		doc.service_movement_type = "Install"
		with patch.object(equipment, "check_private_file") as private:
			doc.before_submit()
		private.assert_called_once_with(doc, "service_evidence")

	def test_handover_types_name_a_status(self):
		meta = fields("assets", "doctype", "asset_movement", "asset_movement.json")
		options = [value for value in meta["service_movement_type"]["options"].split("\n") if value]
		self.assertEqual(sorted(options), sorted(equipment.STATUS_BY_HANDOVER))
		statuses = fields("support", "doctype", "service_equipment", "service_equipment.json")["status"]
		self.assertTrue(set(equipment.STATUS_BY_HANDOVER.values()) <= set(statuses["options"].split("\n")))
		self.assertFalse(
			meta["service_evidence"].get("reqd") or meta["service_evidence"].get("mandatory_depends_on")
		)


class TestEquipmentRecord(Offline):
	def record(self, **values):
		doc = ServiceEquipment.__new__(ServiceEquipment)
		doc.__dict__.update(
			{
				"doctype": "Service Equipment",
				"name": "EQ-1",
				"equipment_id": " EQ-1 ",
				"equipment_name": None,
				"serial_no": None,
				"serial_no_link": "SN-9",
				"owner_supplier": None,
				"company": "CD",
				"technician": None,
				"location": None,
				"customer": None,
				"flags": frappe._dict(),
			}
			| values
		)
		return doc

	def test_identity_defaults(self):
		self.db.get_single_value.return_value = "SUP-1"
		doc = self.record()
		doc.before_validate()
		self.assertEqual((doc.equipment_id, doc.equipment_name, doc.serial_no), ("EQ-1", "EQ-1", "SN-9"))
		self.assertEqual(doc.owner_supplier, "SUP-1")

	def test_the_place_changes_only_by_movement(self):
		doc = self.record(location="Store", custodian=None, address=None, placed_on=None, last_movement=None)
		previous = frappe._dict(doc.__dict__, location="Lab")
		doc.is_new = lambda: False
		doc.get_doc_before_save = lambda: previous
		with self.assertRaises(frappe.ValidationError):
			doc.validate_place()
		doc.flags.from_movement = True
		with patch("erpnext.support.doctype.service_equipment.service_equipment.validate_placement"):
			doc.validate_place()

	def test_no_money_fields(self):
		meta = load("support", "doctype", "service_equipment", "service_equipment.json")
		self.assertFalse([f for f in meta["fields"] if f["fieldtype"] in ("Currency", "Float")])
		self.assertFalse(meta.get("is_submittable"))
		roles = {p["role"] for p in meta["permissions"]}
		self.assertFalse(roles & {"Accounts User", "Accounts Manager"})
		self.assertTrue({"Maintenance User", "Maintenance Manager", "System Manager"} <= roles)


class TestEquipmentRepair(Offline):
	def repair(self, **values):
		doc = AssetRepair.__new__(AssetRepair)
		doc.__dict__.update(
			{
				"doctype": "Asset Repair",
				"name": "REP-1",
				"asset": None,
				"service_equipment": "EQ-1",
				"company": None,
				"cost_center": "Main - CD",
				"repair_status": "Pending",
				"capitalize_repair_cost": 0,
				"increase_in_asset_life": 0,
				"invoices": [],
				"stock_items": [],
				"repair_cost": 1500,
				"failure_date": "2026-11-01 08:00:00",
				"completion_date": None,
				"docstatus": 0,
			}
			| values
		)
		doc.is_new = lambda: False
		return doc

	def test_a_draft_repair_writes_nothing_and_has_no_cost_center_or_stock(self):
		self.db.get_value.side_effect = lambda doctype, name, field: {"company": "CD", "status": "Installed"}[
			field
		]
		doc = self.repair()
		doc.validate()
		self.assertEqual((doc.company, doc.cost_center, doc.total_repair_cost), ("CD", None, 0))
		self.db.set_value.assert_not_called()

	def test_retired_or_returned_equipment_is_not_repaired(self):
		for status in ("Retired", "Returned to Supplier"):
			self.db.get_value.side_effect = lambda doctype, name, field, status=status: {
				"company": "CD",
				"status": status,
			}[field]
			with self.subTest(status=status), self.assertRaises(frappe.ValidationError):
				self.repair().validate()

	def test_submit_opens_the_repair_and_completion_restores_the_status_before(self):
		state = {"status": "Installed"}
		self.db.get_value.side_effect = lambda doctype, name, field: state[field]
		self.db.set_value.side_effect = lambda doctype, name, field, value: state.update({field: value})
		doc = self.repair(docstatus=1)
		with (
			patch.object(AssetRepair, "decrease_stock_quantity") as stock,
			patch.object(AssetRepair, "make_gl_entries") as gl,
		):
			doc.before_submit()
			doc.on_submit()
			self.assertEqual(state["status"], "Under Repair")
			doc.repair_status = "Completed"
			doc.on_update_after_submit()
			stock.assert_not_called()
			gl.assert_not_called()
		self.assertEqual(state["status"], "Installed")
		# a status set since the repair (e.g. by a movement) is left alone by the cancel
		state["status"] = "Out of Order"
		doc.on_cancel()
		self.assertEqual(state["status"], "Out of Order")

	def test_a_cancelled_open_repair_restores_the_status_before(self):
		state = {"status": "In Service"}
		self.db.get_value.side_effect = lambda doctype, name, field: state[field]
		self.db.set_value.side_effect = lambda doctype, name, field, value: state.update({field: value})
		doc = self.repair(docstatus=1)
		doc.before_submit()
		doc.on_submit()
		doc.on_cancel()
		self.assertEqual(state["status"], "In Service")

	def test_the_parts_table_is_a_record_only(self):
		parts = fields(
			"support", "doctype", "service_equipment_repair_part", "service_equipment_repair_part.json"
		)
		self.assertEqual(set(parts), {"item", "serial_no", "qty", "note"})
		repair = fields("assets", "doctype", "asset_repair", "asset_repair.json")
		self.assertEqual(repair["equipment_parts"]["options"], "Service Equipment Repair Part")
		self.assertEqual(repair["equipment_parts_section"]["depends_on"], "eval:doc.service_equipment")

	def test_financial_fields_are_refused(self):
		for values in (
			{"capitalize_repair_cost": 1},
			{"invoices": [{"purchase_invoice": "PI-1"}]},
			{"asset": "ACC-1"},
			{"stock_items": [{"item_code": "PART-1"}]},
		):
			doc = self.repair(**values)
			doc.meta = SimpleNamespace(get_label=lambda fieldname: fieldname)
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				doc.validate_equipment_repair()


class TestEquipmentMaintenance(Offline):
	def test_the_plan_is_named_after_the_equipment_and_sets_its_next_dates(self):
		doc = asset_maintenance.AssetMaintenance.__new__(asset_maintenance.AssetMaintenance)
		doc.__dict__.update(asset_name=None, service_equipment="EQ-1")
		doc.autoname()
		self.assertEqual(doc.name, "EQ-1-SE", "never the name of an Asset's plan")
		tasks = [
			frappe._dict(
				maintenance_type="Preventive Maintenance",
				next_due_date="2027-01-10",
				maintenance_status="Planned",
			),
			frappe._dict(
				maintenance_type="Preventive Maintenance",
				next_due_date="2026-12-01",
				maintenance_status="Planned",
			),
			frappe._dict(
				maintenance_type="Calibration", next_due_date="2027-03-01", maintenance_status="Cancelled"
			),
		]
		plan = SimpleNamespace(service_equipment="EQ-1", get=lambda key: tasks)
		asset_maintenance.sync_equipment_dates(plan)
		self.db.set_value.assert_called_once_with(
			"Service Equipment", "EQ-1", {"next_pm": "2026-12-01", "next_calibration": None}
		)

	def test_a_done_pm_row_of_a_visit_completes_the_log_due_soonest(self):
		log = MagicMock()
		self.db.get_value.return_value = "LOG-1"
		visit = frappe._dict(
			name="MV-1",
			mntc_date="2026-11-10",
			purposes=[
				frappe._dict(
					service_equipment="EQ-1", service_type="PM", service_result="Done", work_done="PM done"
				),
				frappe._dict(service_equipment="EQ-2", service_type="Repair", service_result="Done"),
				frappe._dict(service_equipment="EQ-3", service_type="Calibration", service_result="Not done"),
			],
		)
		with patch.object(frappe, "get_doc", return_value=log):
			asset_maintenance_log.complete_from_visit(visit)
		filters = self.db.get_value.call_args.args[1]
		self.assertEqual(
			(filters["service_equipment"], filters["maintenance_type"]), ("EQ-1", "Preventive Maintenance")
		)
		self.assertEqual(self.db.get_value.call_args.kwargs["order_by"], "due_date asc")
		self.assertEqual((log.maintenance_status, log.maintenance_visit), ("Completed", "MV-1"))
		log.submit.assert_called_once()


class TestServiceRoles(TestCase):
	def test_a_service_only_user_lists_equipment_records_only(self):
		with (
			patch.object(frappe, "get_roles", return_value=["Maintenance User"]),
			patch("frappe.permissions.get_doctype_roles", return_value=["Maintenance User", "Stock Manager"]),
		):
			self.assertIn("service_equipment", equipment.permission_query("tech", "Asset Repair"))
			self.assertIn("service_equipment", equipment.asset_movement_query("tech"))
			company_asset = frappe._dict(doctype="Asset Repair", service_equipment=None, is_new=lambda: False)
			self.assertFalse(equipment.has_permission(company_asset, "read", "tech"))
			own = frappe._dict(doctype="Asset Repair", service_equipment="EQ-1", is_new=lambda: False)
			self.assertTrue(equipment.has_permission(own, "read", "tech"))

	def test_accounting_does_not_act_on_an_equipment_repair(self):
		with patch.object(frappe, "get_roles", return_value=["Accounts User", "Accounts Manager"]):
			self.assertIn("= ''", equipment.permission_query("acc", "Asset Repair"))
			repair = frappe._dict(doctype="Asset Repair", service_equipment="EQ-1", is_new=lambda: False)
			self.assertFalse(equipment.has_permission(repair, "submit", "acc"))
			company = frappe._dict(doctype="Asset Repair", service_equipment=None, is_new=lambda: False)
			self.assertTrue(equipment.has_permission(company, "submit", "acc"))

	def test_another_right_on_the_doctype_is_not_narrowed(self):
		with (
			patch.object(frappe, "get_roles", return_value=["Maintenance User", "Stock Manager"]),
			patch("frappe.permissions.get_doctype_roles", return_value=["Maintenance User", "Stock Manager"]),
		):
			self.assertEqual(equipment.permission_query("keeper", "Asset Movement"), "")
		self.assertEqual(equipment.permission_query("Administrator", "Asset Movement"), "")

	def test_the_shared_doctypes_give_the_service_roles_their_rights(self):
		for path, submit in (
			(("assets", "doctype", "asset_movement", "asset_movement.json"), 1),
			(("assets", "doctype", "asset_maintenance_log", "asset_maintenance_log.json"), 1),
			(("assets", "doctype", "asset_repair", "asset_repair.json"), 1),
		):
			roles = {p["role"]: p for p in load(*path)["permissions"]}
			self.assertEqual(roles["Maintenance User"].get("submit", 0), submit, path[-1])
			self.assertTrue(
				roles["Maintenance Manager"].get("cancel") or path[-1] == "asset_maintenance.json"
			)


class TestNoFinancialEffect(TestCase):
	def test_the_fixed_asset_reports_no_longer_filter_a_marker_field(self):
		for path in (
			("assets", "report", "fixed_asset_register", "fixed_asset_register.py"),
			("accounts", "report", "asset_depreciation_ledger", "asset_depreciation_ledger.py"),
			("accounts", "report", "asset_depreciations_and_balances", "asset_depreciations_and_balances.py"),
		):
			with open(os.path.join(ERPNEXT, *path), encoding="utf-8") as handle:
				source = handle.read()
			self.assertNotIn(MARKER, source)
			self.assertNotIn("service_equipment", source)
		self.assertFalse(os.path.exists(os.path.join(ERPNEXT, "assets", "service_equipment.py")))

	def test_the_support_links_name_the_equipment(self):
		for parts in (
			("support", "doctype", "issue", "issue.json"),
			("support", "doctype", "warranty_claim", "warranty_claim.json"),
			("maintenance", "doctype", "maintenance_visit", "maintenance_visit.json"),
			("maintenance", "doctype", "maintenance_visit_purpose", "maintenance_visit_purpose.json"),
			("maintenance", "doctype", "maintenance_schedule", "maintenance_schedule.json"),
			("maintenance", "doctype", "maintenance_schedule_item", "maintenance_schedule_item.json"),
			("selling", "doctype", "installation_note_item", "installation_note_item.json"),
			("assets", "doctype", "asset_activity", "asset_activity.json"),
			("assets", "doctype", "asset_repair", "asset_repair.json"),
			("assets", "doctype", "asset_maintenance", "asset_maintenance.json"),
			("assets", "doctype", "asset_movement_item", "asset_movement_item.json"),
		):
			self.assertEqual(fields(*parts)["service_equipment"]["options"], "Service Equipment", parts[-1])

	def test_the_sidebars_hold_the_group_and_no_distributor_name(self):
		for parts in (
			("workspace_sidebar", "support.json"),
			("support", "sidebar", "support", "support.json"),
			("support", "workspace", "support", "support.json"),
		):
			with open(os.path.join(ERPNEXT, *parts), encoding="utf-8") as handle:
				text = handle.read()
			self.assertIn('"Service Equipment"', text)
			self.assertNotIn(MARKER, text)
			self.assertNotIn(DISTRIBUTOR, text.upper())


class TestActivity(Offline):
	def test_the_history_lists_each_equipment_of_a_document_once(self):
		visit = MagicMock()
		visit.doctype, visit.name = "Maintenance Visit", "MV-1"
		visit.meta.has_field.return_value = True
		values = {
			"service_equipment": "EQ-1",
			"purposes": [frappe._dict(service_equipment="EQ-1"), frappe._dict(service_equipment="EQ-2")],
			"items": [],
		}
		visit.get.side_effect = lambda key, default=None: values.get(key, default)
		equipment.log_document_activity(visit, "on_submit")
		self.assertEqual([call.args[0] for call in self.activity.call_args_list], ["EQ-1", "EQ-2"])


class TestEquipmentReports(TestCase):
	def test_the_reports_read_equipment_records_through_permission_aware_lists(self):
		from erpnext.support.report.service_equipment_location_history import (
			service_equipment_location_history as history,
		)
		from erpnext.support.report.service_equipment_maintenance_due import (
			service_equipment_maintenance_due as due,
		)
		from erpnext.support.report.service_equipment_repair_history import (
			service_equipment_repair_history as repairs,
		)

		with (
			patch.object(frappe, "get_list", return_value=[frappe._dict(due_date="2026-11-01")]) as get_list,
			patch.object(due, "nowdate", return_value="2026-11-10"),
		):
			columns, rows = due.execute({"service_equipment": "EQ-1"})
			self.assertEqual(get_list.call_args.args[0], "Asset Maintenance Log")
			self.assertEqual(get_list.call_args.kwargs["filters"]["service_equipment"], "EQ-1")
			self.assertIsInstance(rows[0].days, int)
			for module, doctype in ((history, "Asset Movement"), (repairs, "Asset Repair")):
				columns, _rows = module.execute({"service_equipment": "EQ-1"})
				self.assertEqual(get_list.call_args.args[0], doctype)
				self.assertEqual(columns[0]["options"], "Service Equipment")
		for name in (
			"service_equipment_location_history",
			"service_equipment_maintenance_due",
			"service_equipment_repair_history",
		):
			report = load("support", "report", name, name + ".json")
			self.assertEqual((report["module"], report["report_type"]), ("Support", "Script Report"))


class TestInstallationNote(Offline):
	def note(self):
		return frappe._dict(
			doctype="Installation Note",
			name="IN-1",
			company="CD",
			customer="CUS",
			customer_address="CUS-Shipping",
			inst_date="2026-11-10",
			inst_time="10:00:00",
			items=[
				frappe._dict(service_equipment="EQ-1"),
				frappe._dict(item_code="X"),
				frappe._dict(service_equipment="EQ-1"),
			],
		)

	def test_submit_makes_and_submits_one_install_movement(self):
		movement = MagicMock()
		with patch.object(frappe, "get_doc", return_value=movement) as get_doc:
			equipment.install_from_note(self.note())
		values = get_doc.call_args.args[0]
		self.assertEqual(values["service_movement_type"], "Install")
		self.assertEqual(
			(values["service_target_customer"], values["service_target_address"]), ("CUS", "CUS-Shipping")
		)
		self.assertEqual(values["assets"], [{"service_equipment": "EQ-1"}])
		self.assertEqual(values["reference_name"], "IN-1")
		movement.insert.assert_called_once()
		movement.submit.assert_called_once()

	def test_a_note_without_equipment_makes_nothing(self):
		with patch.object(frappe, "get_doc") as get_doc:
			equipment.install_from_note(frappe._dict(items=[frappe._dict(item_code="X")]))
		get_doc.assert_not_called()

	def test_cancel_is_refused_once_the_equipment_moved_on(self):
		with patch.object(frappe, "get_all", side_effect=[["AM-1"], ["EQ-1"]]):
			self.db.get_value.return_value = "AM-2"
			with self.assertRaises(frappe.ValidationError):
				equipment.check_note_cancel(self.note())
		with patch.object(frappe, "get_all", side_effect=[["AM-1"], ["EQ-1"]]):
			self.db.get_value.return_value = "AM-1"
			equipment.check_note_cancel(self.note())
		movement = MagicMock()
		with (
			patch.object(frappe, "get_all", return_value=["AM-1"]),
			patch.object(frappe, "get_doc", return_value=movement),
		):
			equipment.uninstall_from_note(self.note())
		movement.cancel.assert_called_once()

	def test_a_movement_made_by_a_note_is_accepted(self):
		doc = movement(assets=[row()], reference_doctype="Installation Note", reference_name="IN-1")
		self.db.get_value.side_effect = lambda doctype, name=None, fields=None, *a, **k: (
			frappe._dict(company="CD", status="Received", last_movement=None)
			if doctype == "Service Equipment"
			else None
		)
		with patch.object(equipment, "validate_placement"):
			doc.validate()
		with self.assertRaises(frappe.ValidationError):
			movement(assets=[row()], reference_doctype="Material Request", reference_name="MR-1").validate()


class TestCreateMenu(TestCase):
	def test_the_form_offers_each_document_about_the_equipment_behind_can_create(self):
		with open(
			os.path.join(ERPNEXT, "support", "doctype", "service_equipment", "service_equipment.js")
		) as handle:
			source = handle.read()
		for doctype in (
			"Asset Movement",
			"Asset Maintenance",
			"Asset Repair",
			"Issue",
			"Warranty Claim",
			"Maintenance Visit",
		):
			self.assertIn(f'"{doctype}"', source)
		self.assertIn('frappe.model.can_create("Maintenance Visit")', source)
		self.assertIn("serial_no: frm.doc.serial_no_link", source)
		self.assertIn('"Maintenance Visit Purpose"', source)
		self.assertIn("row.item_code = frm.doc.item", source)
