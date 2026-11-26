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
	def test_batch_at_inspection_location_is_releasable_from_its_own_receipt_row(self):
		location = "CD STOCK - CD / QI STAGING"
		row = frappe._dict(item_code="Item A", name="intake-row")
		inflows = [
			{"seq": 0, "row": "intake-row", "batches": {"Batch A": 3}},
			{"seq": 1, "row": "other-row", "batches": {"Batch A": 5}},
		]
		outflows = [{"seq": 2, "linked": "intake-row", "batches": {"Batch A": 2}}]
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=(0, 1)))),
			patch.object(module, "_ledger", return_value=(inflows, outflows)) as ledger,
			patch("erpnext.stock.doctype.batch.batch.get_batch_qty", return_value=0) as pickable,
		):
			parts = module.remaining_parts(None, row, "CD STOCK - CD", location=location)

		ledger.assert_called_once_with("Item A", "CD STOCK - CD", location)
		pickable.assert_not_called()
		self.assertEqual(parts, [{"qty": 1, "batch_no": "Batch A", "serial_nos": None}])

	def test_manual_numeric_result_cannot_accept_an_out_of_range_reading(self):
		from erpnext.stock.doctype.quality_inspection.quality_inspection import QualityInspection

		row = frappe._dict(
			numeric=1, manual_inspection=1, min_value=1.4, max_value=2, reading_1="5", status="Accepted"
		)
		doc = frappe._dict(
			purchase_unit_qty=1,
			purchase_rejected_qty=0,
			manual_inspection=0,
			readings=[row],
			status="Accepted",
		)
		doc.min_max_criteria_passed = lambda reading: False
		QualityInspection.inspect_and_set_status(doc)
		self.assertEqual((row.status, doc.status), ("Rejected", "Rejected"))

	def test_template_criteria_decide_the_default_manual_mode(self):
		from erpnext.stock.doctype.quality_inspection_reading.quality_inspection_reading import (
			has_automatic_criteria,
		)

		self.assertTrue(has_automatic_criteria(frappe._dict(numeric=0, value="Present")))
		self.assertTrue(has_automatic_criteria(frappe._dict(numeric=1, min_value=1, max_value=2)))
		self.assertTrue(
			has_automatic_criteria(frappe._dict(formula_based_criteria=1, acceptance_formula="reading_1 > 1"))
		)
		self.assertFalse(has_automatic_criteria(frappe._dict(numeric=1, min_value=0, max_value=0)))

	def test_batch_only_item_ignores_serial_values_in_bundle(self):
		row = frappe._dict(
			item_code="ITEM",
			qty=2,
			idx=1,
			serial_and_batch_bundle="BUNDLE",
			purchase_inspection_granularity="Batch/Lot",
		)
		with (
			patch.object(frappe, "get_cached_value", return_value=0),
			patch.object(frappe, "get_all", return_value=[]),
			patch("erpnext.stock.serial_batch_bundle.get_serial_nos", return_value=["S1", "S2"]),
			patch("erpnext.stock.serial_batch_bundle.get_batch_nos", return_value={"B": frappe._dict(qty=2)}),
		):
			self.assertEqual(
				module.receipt_units(row), [{"batch_no": "B", "serial_no": None, "qty": 2.0, "serials": []}]
			)

	def test_only_submitted_serials_and_batches_are_released(self):
		units = split_units(2, ["S1", "S2"], {"B": 2}, {"S1": "B", "S2": "B"}, "Serial Number")
		qi = frappe._dict(
			name="QI-1",
			docstatus=1,
			status="Accepted",
			purchase_unit_qty=1,
			item_serial_no="S1",
			batch_no="B",
			purchase_accepted_qty=1,
			purchase_rejected_qty=0,
		)
		held = [{"batch_no": "B", "qty": 2, "serial_nos": ["S1", "S2"]}]
		result = remaining_inspection_results(units, [qi], held, [])
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["accepted"]["serial_nos"], ["S1"])
		self.assertEqual(
			remaining_inspection_results(
				units, [qi], [{"batch_no": "B", "qty": 1, "serial_nos": ["S2"]}], []
			)[0]["accepted"]["qty"],
			0,
		)

		lots = split_units(5, [], {"B1": 2, "B2": 3}, {}, "Batch/Lot")
		lot_qi = frappe._dict(
			name="QI-2",
			docstatus=1,
			status="Accepted",
			purchase_unit_qty=2,
			batch_no="B1",
			purchase_accepted_qty=2,
			purchase_rejected_qty=0,
		)
		result = remaining_inspection_results(
			lots, [lot_qi], [{"batch_no": "B1", "qty": 2}, {"batch_no": "B2", "qty": 3}], []
		)
		self.assertEqual([(r["unit"]["batch_no"], r["accepted"]["qty"]) for r in result], [("B1", 2)])

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

	def test_missing_serials_are_counted_individually_for_batch_receipt(self):
		units = split_units(
			3, ["S1", "S2", "S3"], {"B": 3}, {s: "B" for s in ("S1", "S2", "S3")}, "Batch/Lot"
		)
		progress = summarize_progress(units, [])
		self.assertEqual((progress["required"], progress["missing"]), (3, 3))

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

	def test_serialized_lot_without_an_individual_serial_is_rejected(self):
		doc = frappe._dict(
			name="QI-1",
			item_code="ITEM",
			batch_no="B",
			purchase_unit_qty=2,
			sample_size=1,
			status="Rejected",
			readings=[frappe._dict(status="Accepted")],
		)
		units = split_units(2, ["S1", "S2"], {"B": 2}, {"S1": "B", "S2": "B"}, "Batch/Lot")
		with (
			patch.object(module, "receipt_units", return_value=units),
			patch.object(module, "inspections_for_row", return_value=[]),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			module.validate_purchase_unit_inspection(
				doc, frappe._dict(), frappe._dict(item_code="ITEM"), "before_submit"
			)

	def test_new_incoming_serialized_lot_cannot_be_saved_then_submitted(self):
		doc = frappe._dict(
			name="QI-1",
			reference_type="Purchase Receipt",
			item_code="ITEM",
			batch_no="B",
			purchase_unit_qty=2,
			sample_size=1,
		)
		units = split_units(2, ["S1", "S2"], {"B": 2}, {"S1": "B", "S2": "B"}, "Batch/Lot")
		with (
			patch.object(module, "receipt_units", return_value=units),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			module.validate_purchase_unit_inspection(
				doc, frappe._dict(), frappe._dict(item_code="ITEM"), "validate"
			)

	def test_existing_serialized_lot_and_outgoing_inspections_can_submit(self):
		units = split_units(2, ["S1", "S2"], {"B": 2}, {"S1": "B", "S2": "B"}, "Batch/Lot")
		for reference_type, existing in (("Purchase Receipt", True), ("Pick List", False)):
			with self.subTest(reference_type=reference_type):
				doc = frappe._dict(
					name="QI-1",
					reference_type=reference_type,
					item_code="ITEM",
					batch_no="B",
					purchase_unit_qty=2,
					sample_size=1,
					status="Rejected",
					readings=[frappe._dict(status="Accepted")],
				)
				if existing:
					doc.purchase_serial_results = [
						frappe._dict(serial_no="S1", result="Accepted"),
						frappe._dict(serial_no="S2", result="Rejected"),
					]
				previous = doc.copy() if existing else None
				doc.get_doc_before_save = lambda: previous
				doc.set = lambda field, value: doc.update({field: value})
				doc.append = lambda field, value: doc[field].append(frappe._dict(value))
				with (
					patch.object(module, "receipt_units", return_value=units),
					patch.object(module, "inspections_for_row", return_value=[]),
				):
					module.validate_purchase_unit_inspection(
						doc, frappe._dict(), frappe._dict(item_code="ITEM"), "before_submit"
					)
				self.assertEqual(doc.purchase_rejected_serials, "S2" if existing else "S1\nS2")
				self.assertEqual(doc.purchase_rejected_qty, 1 if existing else 2)
				self.assertEqual(len(doc.purchase_serial_results), 2)

	def test_cancelled_legacy_serialized_lot_can_be_amended_without_changing_coverage(self):
		from copy import deepcopy

		original = frappe._dict(
			name="QI-OLD",
			docstatus=2,
			reference_type="Purchase Receipt",
			reference_name="PR-1",
			child_row_reference="ROW-1",
			item_code="ITEM",
			batch_no="B",
			purchase_unit_qty=2,
			sample_size=1,
			status="Rejected",
			readings=[frappe._dict(status="Accepted", reading_value="Original measurement")],
			purchase_serial_results=[
				frappe._dict(serial_no="S1", result="Accepted"),
				frappe._dict(serial_no="S2", result="Rejected"),
			],
		)
		units = split_units(2, ["S1", "S2"], {"B": 2}, {"S1": "B", "S2": "B"}, "Batch/Lot")
		for changed in (
			{},
			{"reference_name": "PR-OTHER"},
			{"child_row_reference": "ROW-OTHER"},
			{"reference_type": "Purchase Invoice"},
			{"source_docstatus": 1},
			{"source_serial": "OTHER"},
			{"amend_serial": "OTHER"},
		):
			with self.subTest(changed=changed):
				source = deepcopy(original)
				doc = deepcopy(original)
				doc.update(name="QI-NEW", docstatus=0, amended_from="QI-OLD")
				if "source_docstatus" in changed:
					source.docstatus = changed["source_docstatus"]
				elif "source_serial" in changed:
					source.purchase_serial_results[0].serial_no = changed["source_serial"]
				elif "amend_serial" in changed:
					doc.purchase_serial_results[0].serial_no = changed["amend_serial"]
				else:
					doc.update(changed)
				doc.set = lambda field, value: doc.update({field: value})
				doc.append = lambda field, value: doc[field].append(frappe._dict(value))
				with (
					patch.object(module, "receipt_units", return_value=units),
					patch.object(module, "inspections_for_row", return_value=[]),
					patch.object(frappe, "get_doc", return_value=source),
					patch.object(frappe, "throw", side_effect=frappe.ValidationError),
				):
					if changed:
						with self.assertRaises(frappe.ValidationError):
							module.validate_purchase_unit_inspection(
								doc, frappe._dict(), frappe._dict(item_code="ITEM"), "validate"
							)
					else:
						module.validate_purchase_unit_inspection(
							doc, frappe._dict(), frappe._dict(item_code="ITEM"), "validate"
						)
						module.validate_purchase_unit_inspection(
							doc, frappe._dict(), frappe._dict(item_code="ITEM"), "before_submit"
						)
						self.assertEqual(doc.readings, original.readings)
						self.assertEqual((doc.reference_name, doc.child_row_reference), ("PR-1", "ROW-1"))
						self.assertEqual(
							(doc.purchase_accepted_qty, doc.purchase_rejected_serials), (1, "S2")
						)
		self.assertEqual(original.docstatus, 2)

	def test_serial_history_preserves_legacy_lot_results_and_filters_parent_access(self):
		visible = frappe._dict(name="QI-visible", docstatus=1, status="Rejected")
		with (
			patch.object(frappe, "get_doc", return_value=Mock()),
			patch.object(
				frappe,
				"get_all",
				return_value=[
					frappe._dict(parent="QI-visible", result="Accepted"),
					frappe._dict(parent="QI-hidden", result="Rejected"),
				],
			),
			patch.object(frappe, "get_list", side_effect=[[], [visible]]) as get_list,
		):
			result = module.serial_inspection_history("S1")
		self.assertEqual([(row.name, row.serial_result) for row in result], [("QI-visible", "Accepted")])
		self.assertEqual(get_list.call_args.kwargs["filters"]["docstatus"], ["<", 2])

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
		visible = frappe._dict(name="QI-visible", docstatus=1, status="Rejected")
		with (
			patch.object(frappe, "get_doc", return_value=serial),
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(frappe, "get_list", return_value=[visible]),
		):
			result = module.serial_inspection_history("S1")
		serial.check_permission.assert_called_once_with("read")
		self.assertEqual([(row.name, row.serial_result) for row in result], [("QI-visible", "Rejected")])

	def test_new_outgoing_lot_keeps_editable_serial_results(self):
		doc = frappe._dict(doctype="Pick List", name="PL-1", company="COMPANY")
		row = frappe._dict(name="row-1", idx=1, item_code="ITEM")
		unit = {"batch_no": "B", "serial_no": None, "qty": 2, "serials": ["S1", "S2"]}
		inspection = Mock(quality_inspection_template=None)
		with (
			patch.object(frappe, "get_doc", return_value=inspection),
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=None))),
			patch.object(frappe, "session", frappe._dict(user="inspector@example.test")),
		):
			module.new_purchase_inspection(doc, row, unit)
		self.assertEqual(
			[call.args for call in inspection.append.call_args_list],
			[
				("purchase_serial_results", {"serial_no": "S1", "result": "Accepted"}),
				("purchase_serial_results", {"serial_no": "S2", "result": "Accepted"}),
			],
		)

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
		self.assertEqual((qi.purchase_unit_qty, qi.sample_size, qi.manual_inspection), (1, 1, 0))

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
