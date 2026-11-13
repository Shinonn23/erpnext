import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.quality_inspection import purchase_inspection_units as module
from erpnext.stock.doctype.quality_inspection.purchase_inspection_units import (
	completed_row_inspection,
	inspection_progress,
	remaining_inspection_results,
	remaining_result_parts,
	result_parts,
	split_units,
	summarize_progress,
	unit_has_moved,
	validate_result,
)


class TestPurchaseInspectionUnits(unittest.TestCase):
	def test_serial_and_batch_grouping(self):
		serials = ["S1", "S2", "S3"]
		batches = {"B1": 2, "B2": 1}
		mapping = {"S1": "B1", "S2": "B1", "S3": "B2"}
		lot = split_units(3, serials, batches, mapping, "Batch/Lot")
		self.assertEqual(
			[(u["batch_no"], u["qty"], u["serials"]) for u in lot],
			[("B1", 2, ["S1", "S2"]), ("B2", 1, ["S3"])],
		)
		serial = split_units(3, serials, batches, mapping, "Serial Number")
		self.assertEqual(
			[(u["serial_no"], u["batch_no"]) for u in serial], [("S1", "B1"), ("S2", "B1"), ("S3", "B2")]
		)

	def test_unbatched_serials_and_untracked_goods(self):
		self.assertEqual(len(split_units(2, ["S1", "S2"], {}, {}, "Batch/Lot")), 1)
		self.assertEqual(len(split_units(2, ["S1", "S2"], {}, {}, "Serial Number")), 2)
		self.assertEqual(split_units(4, [], {}, {}, "Batch/Lot")[0]["qty"], 4)

	def test_inconsistent_tracking_is_rejected(self):
		for args in [
			(float("nan"), [], {}, {}, "Batch/Lot"),
			(2, ["S1", "S1"], {}, {}, "Serial Number"),
			(2, ["S1"], {}, {}, "Serial Number"),
			(2, [], {"B": 1}, {}, "Batch/Lot"),
			(1, ["S1"], {"B": 1}, {"S1": "OTHER"}, "Batch/Lot"),
		]:
			with self.subTest(args=args), self.assertRaises(ValueError):
				split_units(*args)

	def test_partial_result_validates_quantities_and_serials(self):
		unit = {"qty": 3, "serials": ["S1", "S2", "S3"]}
		validate_result(unit, 2, 1, ["S2"])
		for accepted, rejected, serials in [
			(float("nan"), 1, ["S1"]),
			(2, 1, ["OTHER"]),
			(2, 1, []),
			(1, 1, ["S1"]),
			(1, 2, ["S1", "S1"]),
			(-1, 4, ["S1"]),
		]:
			with self.subTest(serials=serials), self.assertRaises(ValueError):
				validate_result(unit, accepted, rejected, serials)

	def test_progress_matches_units_and_refuses_ambiguous_legacy_data(self):
		units = split_units(3, ["S1", "S2", "S3"], {}, {}, "Serial Number")
		inspections = [
			{"name": "DRAFT", "docstatus": 0, "purchase_unit_qty": 1, "item_serial_no": "S1"},
			{"name": "DONE", "docstatus": 1, "purchase_unit_qty": 1, "item_serial_no": "S2"},
		]
		self.assertEqual(
			[part["state"] for part in inspection_progress(units, inspections)],
			["draft", "submitted", "missing"],
		)
		self.assertEqual(
			[part["state"] for part in inspection_progress(units, [{"name": "OLD", "docstatus": 1}])],
			["submitted", "submitted", "submitted"],
		)
		with self.assertRaises(ValueError):
			inspection_progress(units, [{"name": "OLD", "docstatus": 1}, inspections[0]])
		with self.assertRaises(ValueError):
			inspection_progress(units, [*inspections, inspections[0]])
		self.assertEqual(
			inspection_progress(units[:1], [{"name": "OLD", "docstatus": 1}])[0]["state"], "submitted"
		)

	def test_split_results_for_serialised_batch_and_legacy_inspections(self):
		unit = split_units(3, ["S1", "S2", "S3"], {"B": 3}, {"S1": "B", "S2": "B", "S3": "B"}, "Batch/Lot")[0]
		result = result_parts(
			unit,
			{
				"docstatus": 1,
				"status": "Rejected",
				"purchase_unit_qty": 3,
				"purchase_accepted_qty": 2,
				"purchase_rejected_qty": 1,
				"purchase_rejected_serials": "S2",
			},
		)
		self.assertEqual(result["accepted"]["serial_nos"], ["S1", "S3"])
		self.assertEqual(result["rejected"]["serial_nos"], ["S2"])
		self.assertEqual(result_parts(unit, {"docstatus": 1, "status": "Accepted"})["accepted"]["qty"], 3)
		self.assertEqual(result_parts(unit, {"docstatus": 1, "status": "Rejected"})["rejected"]["qty"], 3)
		with self.assertRaises(ValueError):
			result_parts(unit, {"docstatus": 0, "status": "Accepted"})

	def test_partial_movements_are_allocated_to_the_correct_result(self):
		unit = split_units(5, [], {"B": 5}, {}, "Batch/Lot")[0]
		inspection = {
			"docstatus": 1,
			"status": "Rejected",
			"purchase_unit_qty": 5,
			"purchase_accepted_qty": 3,
			"purchase_rejected_qty": 2,
		}
		remaining = remaining_result_parts(
			unit,
			inspection,
			[
				{"qty": 1, "disposition": False},
				{"qty": 1, "disposition": True},
			],
		)
		self.assertEqual(remaining["accepted"]["qty"], 2)
		self.assertEqual(remaining["rejected"]["qty"], 1)
		with self.assertRaises(ValueError):
			remaining_result_parts(unit, inspection, [{"qty": 4, "disposition": False}])

	def test_serial_movements_must_name_eligible_serials_once(self):
		unit = split_units(3, ["S1", "S2", "S3"], {}, {}, "Batch/Lot")[0]
		inspection = {
			"docstatus": 1,
			"status": "Rejected",
			"purchase_unit_qty": 3,
			"purchase_accepted_qty": 2,
			"purchase_rejected_qty": 1,
			"purchase_rejected_serials": "S2",
		}
		remaining = remaining_result_parts(
			unit,
			inspection,
			[
				{"qty": 1, "serial_nos": ["S1"]},
				{"qty": 1, "serial_nos": ["S2"], "disposition": True},
			],
		)
		self.assertEqual(remaining["accepted"]["serial_nos"], ["S3"])
		self.assertEqual(remaining["rejected"]["qty"], 0)
		for movement in [
			{"qty": 1, "serial_nos": ["S2"]},
			{"qty": 2, "serial_nos": ["S1", "S1"]},
			{"qty": 1, "serial_nos": ["S4"]},
		]:
			with self.subTest(movement=movement), self.assertRaises(ValueError):
				remaining_result_parts(unit, inspection, [movement])

	def test_progress_counts_only_submitted_results(self):
		units = split_units(3, ["S1", "S2", "S3"], {}, {}, "Serial Number")
		inspections = [
			{"docstatus": 0, "purchase_unit_qty": 1, "item_serial_no": "S1"},
			{
				"docstatus": 1,
				"status": "Rejected",
				"purchase_unit_qty": 1,
				"item_serial_no": "S2",
				"purchase_accepted_qty": 0,
				"purchase_rejected_qty": 1,
				"purchase_rejected_serials": "S2",
			},
		]
		self.assertEqual(
			summarize_progress(units, inspections),
			{"required": 3, "missing": 1, "draft": 1, "submitted": 1, "accepted_qty": 0, "rejected_qty": 1},
		)

	def test_remaining_result_reconciles_mixed_batch_after_partial_movements(self):
		units = split_units(5, [], {"B": 5}, {}, "Batch/Lot")
		qi = {
			"name": "QI-1",
			"docstatus": 1,
			"status": "Rejected",
			"batch_no": "B",
			"purchase_unit_qty": 5,
			"purchase_accepted_qty": 3,
			"purchase_rejected_qty": 2,
		}
		movements = [{"batch_no": "B", "qty": 1}, {"batch_no": "B", "qty": 1, "disposition": True}]
		[result] = remaining_inspection_results(units, [qi], [{"batch_no": "B", "qty": 3}], movements)
		self.assertEqual((result["accepted"]["qty"], result["rejected"]["qty"]), (2, 1))
		with self.assertRaises(ValueError):
			remaining_inspection_results(units, [qi], [{"batch_no": "B", "qty": 2}], movements)

	def test_remaining_serials_are_taken_from_current_stock(self):
		units = split_units(3, ["S1", "S2", "S3"], {}, {}, "Batch/Lot")
		qi = {
			"name": "QI-1",
			"docstatus": 1,
			"status": "Rejected",
			"purchase_unit_qty": 3,
			"purchase_accepted_qty": 2,
			"purchase_rejected_qty": 1,
			"purchase_rejected_serials": "S2",
		}
		[result] = remaining_inspection_results(
			units, [qi], [{"batch_no": None, "qty": 2, "serial_nos": ["S2", "S3"]}], []
		)
		self.assertEqual(result["accepted"]["serial_nos"], ["S3"])
		self.assertEqual(result["rejected"]["serial_nos"], ["S2"])

	def test_cancel_only_checks_movements_of_the_inspection_unit(self):
		units = split_units(2, ["S1", "S2"], {"B1": 1, "B2": 1}, {"S1": "B1", "S2": "B2"}, "Serial Number")
		held = [{"batch_no": "B1", "qty": 1, "serial_nos": ["S1"]}]
		self.assertFalse(unit_has_moved(units[0], held))
		self.assertTrue(unit_has_moved(units[1], held))
		lots = split_units(5, [], {"B1": 2, "B2": 3}, {}, "Batch/Lot")
		self.assertFalse(unit_has_moved(lots[0], [{"batch_no": "B1", "qty": 2}]))
		self.assertTrue(unit_has_moved(lots[1], [{"batch_no": "B2", "qty": 2}]))

	def test_reconciliation_detects_replaced_serials_even_when_quantity_is_unchanged(self):
		row = frappe._dict(current_qty=2, qty=2, current_serial_no="S1\nS2", serial_no="S2\nS3")
		qty, batches, serials = module.reconciliation_outgoing(row)
		self.assertEqual((qty, batches, serials), (1, {}, {"S1"}))

	def test_cancel_detects_unreversed_serial_movements_even_after_stock_returns(self):
		doc = frappe._dict(creation="2026-01-01 00:00:00")
		row = frappe._dict(item_code="ITEM", warehouse="MAIN", storage_location="BIN")
		unit = {"serials": ["S1"]}
		with patch.object(frappe, "db", Mock(sql=Mock(return_value=[("S1",)]))) as db:
			self.assertTrue(module.has_active_serial_outflow(doc, row, unit))
		self.assertIn("is_cancelled = 0", db.sql.call_args.args[0])
		with patch.object(frappe, "db", Mock(sql=Mock(side_effect=[[], [frappe._dict(serial_no="S1\nS2")]]))):
			self.assertTrue(module.has_active_serial_outflow(doc, row, unit))

	def test_submitted_lot_records_each_serial_and_its_result(self):
		class Inspection(frappe._dict):
			def set(self, field, value):
				self[field] = value

			def append(self, field, value):
				self[field].append(value)

		doc = Inspection(
			name="QI-1",
			item_code="ITEM",
			batch_no="B",
			purchase_unit_qty=2,
			sample_size=1,
			status="Rejected",
			readings=[frappe._dict(status="Accepted")],
			purchase_accepted_qty=1,
			purchase_rejected_qty=1,
			purchase_rejected_serials="S2",
		)
		unit = split_units(2, ["S1", "S2"], {"B": 2}, {"S1": "B", "S2": "B"}, "Batch/Lot")
		with (
			patch.object(module, "receipt_units", return_value=unit),
			patch.object(module, "inspections_for_row", return_value=[]),
		):
			module.validate_purchase_unit_inspection(
				doc, frappe._dict(), frappe._dict(item_code="ITEM"), "before_submit"
			)
		self.assertEqual(
			doc.purchase_serial_results,
			[{"serial_no": "S1", "result": "Accepted"}, {"serial_no": "S2", "result": "Rejected"}],
		)

	def test_submission_freezes_item_inspection_mode_on_receipt_row(self):
		row = frappe._dict(item_code="ITEM", purchase_inspection_granularity="Batch/Lot")
		doc = frappe._dict(
			doctype="Purchase Receipt", docstatus=1, is_return=0, items=[row], _action="submit"
		)
		with patch.object(
			frappe,
			"get_cached_value",
			return_value=frappe._dict(
				is_stock_item=1,
				inspection_required_before_purchase=1,
				purchase_inspection_granularity="Serial Number",
			),
		):
			module.freeze_purchase_inspection_granularity(doc)
		self.assertEqual(row.purchase_inspection_granularity, "Serial Number")
		doc._action = "update_after_submit"
		with patch.object(
			frappe,
			"get_cached_value",
			return_value=frappe._dict(
				is_stock_item=1,
				inspection_required_before_purchase=1,
				purchase_inspection_granularity="Batch/Lot",
			),
		):
			module.freeze_purchase_inspection_granularity(doc)
		self.assertEqual(row.purchase_inspection_granularity, "Serial Number")

	def test_uninspected_item_is_recorded_as_not_required(self):
		row = frappe._dict(item_code="ITEM")
		doc = frappe._dict(
			doctype="Purchase Invoice",
			docstatus=1,
			update_stock=1,
			is_return=0,
			items=[row],
			_action="submit",
		)
		with patch.object(
			frappe,
			"get_cached_value",
			return_value=frappe._dict(is_stock_item=1, inspection_required_before_purchase=0),
		):
			module.freeze_purchase_inspection_granularity(doc)
		self.assertEqual(row.purchase_inspection_granularity, "Not Required")

	def test_serial_history_only_returns_readable_quality_inspections(self):
		serial = Mock()
		child = [
			frappe._dict(parent="QI-visible", result="Rejected"),
			frappe._dict(parent="QI-private", result="Accepted"),
		]
		visible = frappe._dict(name="QI-visible", docstatus=1, status="Rejected")
		with (
			patch.object(frappe, "get_doc", return_value=serial),
			patch.object(frappe, "get_all", return_value=child),
			patch.object(frappe, "get_list", side_effect=[[], [visible]]),
		):
			result = module.serial_inspection_history("S1")
		serial.check_permission.assert_called_once_with("read")
		self.assertEqual([(row.name, row.serial_result) for row in result], [("QI-visible", "Rejected")])

	def test_new_draft_references_the_exact_serial_and_batch(self):
		doc = frappe._dict(doctype="Purchase Receipt", name="PR-1", company="COMPANY")
		row = frappe._dict(name="row-1", idx=1, item_code="ITEM")
		unit = {"batch_no": "B1", "serial_no": "S1", "qty": 1}
		with (
			patch.object(frappe, "get_doc", side_effect=lambda values: frappe._dict(values)),
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=None))),
			patch.object(frappe, "session", frappe._dict(user="inspector@example.test")),
		):
			qi = module.new_purchase_inspection(doc, row, unit)
		self.assertEqual(
			(qi.reference_name, qi.child_row_reference, qi.batch_no, qi.item_serial_no),
			("PR-1", "row-1", "B1", "S1"),
		)
		self.assertEqual((qi.purchase_unit_qty, qi.sample_size, qi.manual_inspection), (1, 1, 1))

	def test_creation_reports_a_failed_unit_and_continues_with_the_next_one(self):
		row = frappe._dict(idx=1, item_code="ITEM")
		units = split_units(2, ["S1", "S2"], {}, {}, "Serial Number")
		progress = inspection_progress(units, [])
		failed = Mock(name="failed")
		failed.insert.side_effect = frappe.ValidationError("Invalid serial")
		created = Mock(name="created")
		created.name = "QI-NEW"
		db = Mock()
		with (
			patch.object(frappe, "db", db),
			patch.object(module, "new_purchase_inspection", side_effect=[failed, created]),
		):
			result = module.create_purchase_unit_inspections(frappe._dict(), row, progress)
		self.assertEqual(result["created"], ["QI-NEW"])
		self.assertIn("S1", result["skipped"][0])
		db.rollback.assert_called_once_with(save_point="purchase_qi_unit")
		self.assertEqual(db.release_savepoint.call_count, 2)

	def test_row_is_complete_only_after_every_unit_has_a_final_result(self):
		units = split_units(2, ["S1", "S2"], {}, {}, "Serial Number")
		accepted = {
			"name": "QI-1",
			"docstatus": 1,
			"status": "Accepted",
			"purchase_unit_qty": 1,
			"item_serial_no": "S1",
			"purchase_accepted_qty": 1,
		}
		rejected = {
			"name": "QI-2",
			"docstatus": 1,
			"status": "Rejected",
			"purchase_unit_qty": 1,
			"item_serial_no": "S2",
			"purchase_rejected_qty": 1,
			"purchase_rejected_serials": "S2",
		}
		self.assertIsNone(completed_row_inspection(units, [accepted]))
		self.assertEqual(completed_row_inspection(units, [accepted, rejected]).status, "Rejected")
