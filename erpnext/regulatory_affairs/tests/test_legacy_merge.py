"""Unit tests for the legacy Thai FDA -> Regulatory Affairs data patch (mocks only, no database)."""

import unittest
from unittest.mock import DEFAULT, MagicMock, patch

from erpnext.patches.v16_0 import merge_thai_fda_into_regulatory_affairs as merge
from erpnext.regulatory_affairs import coverage

TODAY = "2026-10-18"


def legacy(name, number="FDA-123", **values):
	row = {
		"name": name,
		"registration_number": number,
		"item": "ITEM-1",
		"item_name": "Item One",
		"regulatory_category": "Food",
		"status": "Active",
		"expiry_basis": "Expiry Date Specified",
		"expiry_date": "2027-01-31",
		"issue_date": "2024-02-01",
		"renewal_status": "Not Started",
		"creation": "2025-01-01 10:00:00",
		"modified": "2026-01-01 10:00:00",
		"modified_by": "a@example.com",
		"owner": "o@example.com",
	}
	row.update(values)
	return row


class TestValueNormalisation(unittest.TestCase):
	def test_english_and_thai_values_map_to_the_same_result(self):
		self.assertEqual(merge.translate("Thai FDA Registration", "status", "มีผล"), "Active")
		self.assertEqual(merge.translate("Thai FDA Registration", "status", "Active"), "Active")
		self.assertEqual(merge.map_category({"regulatory_category": "เครื่องมือแพทย์"}), ("Medical Device", None))
		self.assertEqual(
			merge.map_category({"regulatory_category": "Medical Device"}), ("Medical Device", None)
		)
		self.assertEqual(merge.map_activity("นำเข้า"), "Import")
		self.assertEqual(merge.map_activity("Product Registration"), "Product Registration")
		self.assertEqual(merge.map_activity(None), "")

	def test_other_and_blank_category_become_other(self):
		self.assertEqual(
			merge.map_category({"regulatory_category": "Other", "other_category": "Toys"}), ("Other", "Toys")
		)
		self.assertEqual(
			merge.map_category({"regulatory_category": "Other"}), ("Other", merge.UNSPECIFIED_CATEGORY)
		)
		self.assertEqual(merge.map_category({}), ("Other", merge.UNSPECIFIED_CATEGORY))

	def test_status_mapping_including_expired_active(self):
		for legacy_status, expected in (
			("Pending Approval", "Draft"),
			("รออนุมัติ", "Draft"),
			("Active", "Active"),
			("Suspended", "Suspended"),
			("เพิกถอน", "Revoked"),
			("Cancelled", "Cancelled"),
			("Superseded", "Superseded"),
			("", "Active"),
		):
			row = legacy("T-1", status=legacy_status)
			self.assertEqual(merge.map_status(row, TODAY), expected, legacy_status)
		expired = legacy("T-1", expiry_date="2026-10-17")
		self.assertEqual(merge.map_status(expired, TODAY), "Expired")
		self.assertEqual(merge.map_status(legacy("T-1", expiry_date="2026-10-18"), TODAY), "Active")
		unconfirmed = legacy("T-1", expiry_basis="Unconfirmed", expiry_date="2020-01-01")
		self.assertEqual(merge.map_status(unconfirmed, TODAY), "Active")
		revoked_past = legacy("T-1", status="Revoked", expiry_date="2020-01-01")
		self.assertEqual(merge.map_status(revoked_past, TODAY), "Revoked")

	def test_renewal_status_mapping(self):
		for value, expected in (
			("Not Started", "Not Started"),
			("In Progress", "Preparing"),
			("กำลังดำเนินการ", "Preparing"),
			("Submitted", "Submitted"),
			("New Document Received", "Approved"),
			("ได้รับฉบับใหม่", "Approved"),
			("", "Not Started"),
		):
			self.assertEqual(merge.map_renewal_status(value), expected)


class TestGrouping(unittest.TestCase):
	def test_groups_by_normalized_number_newest_first(self):
		rows = [
			legacy("T-1", "FDA 123", modified="2026-01-01"),
			legacy("T-2", "fda-123", modified="2026-03-01"),
			legacy("T-3", "OTHER-1"),
		]
		groups = merge.group_rows(rows)
		self.assertEqual(len(groups), 2)
		self.assertEqual([row["name"] for row in groups["FDA123"]], ["T-2", "T-1"])

	def test_blank_number_gets_a_legacy_placeholder_per_record(self):
		groups = merge.group_rows([legacy("T-1", ""), legacy("T-2", None), legacy("T-3", "  ")])
		self.assertEqual(len(groups), 3)
		self.assertEqual(merge.registration_number(legacy("T-9", "")), "LEGACY-T-9")
		registration = merge.build_registration(groups["LEGACYT1"], [], None, TODAY)
		self.assertEqual(registration["registration_number"], "LEGACY-T-1")
		self.assertEqual(registration["registered_products"][0]["identifier"], "LEGACY-T-1")

	def test_conflicts_are_reported_for_disagreements_only(self):
		agreeing = merge.group_rows([legacy("T-1"), legacy("T-2", item="ITEM-2")])["FDA123"]
		self.assertEqual(merge.find_conflicts(agreeing, TODAY), [])
		group = merge.group_rows(
			[
				legacy("T-1", expiry_date="2027-01-31"),
				legacy("T-2", item="ITEM-2", expiry_date="2028-01-31", status="Suspended"),
				legacy("T-3", item="ITEM-3", regulatory_category="Drug"),
			]
		)["FDA123"]
		text = "\n".join(merge.find_conflicts(group, TODAY))
		self.assertIn("different expiry date", text)
		self.assertIn("different status", text)
		self.assertIn("different category", text)

	def test_duplicate_item_and_missing_expiry_are_reported(self):
		group = merge.group_rows([legacy("T-1", expiry_date=None), legacy("T-2")])["FDA123"]
		group[0]["expiry_date"] = None
		text = "\n".join(merge.find_conflicts(group, TODAY))
		self.assertIn("Item ITEM-1 appears on several", text)
		self.assertIn("no expiry date is recorded", text)


class TestRegistrationBuilding(unittest.TestCase):
	def group(self):
		return merge.group_rows(
			[
				legacy("T-1", "FDA-123", item="ITEM-1", item_name="Item One", modified="2026-01-01"),
				legacy(
					"T-2",
					"FDA 123",
					item="ITEM-2",
					item_name="",
					modified="2026-05-01",
					approval_type="Licence",
					activity="นำเข้า",
					holder_name="ACME",
					source_url="https://example.com",
					verified_date="2026-04-01",
					responsible_user="r@example.com",
					renewal_status="In Progress",
				),
			]
		)["FDA123"]

	def test_attributes_come_from_the_newest_row_and_every_row_becomes_a_line(self):
		registration = merge.build_registration(self.group(), [], {"ITEM-1", "ITEM-2"}, TODAY)
		self.assertEqual(registration["registration_number"], "FDA 123")
		self.assertEqual(registration["registration_number_normalized"], "FDA123")
		self.assertEqual(registration["certificate_type"], "Licence")
		self.assertEqual(registration["activity_type"], "Import")
		self.assertEqual(registration["holder_name"], "ACME")
		self.assertEqual(registration["last_verified_on"], "2026-04-01")
		self.assertEqual(registration["renewal_status"], "Preparing")
		self.assertEqual(registration["official_status"], "Active")
		self.assertEqual(registration["days_to_expiry"], 105)
		self.assertEqual(registration["legacy_thai_fda_records"], "T-1, T-2")
		self.assertEqual(registration["creation"], "2025-01-01 10:00:00")
		self.assertEqual(registration["modified"], "2026-05-01")
		self.assertEqual(registration["owner"], "o@example.com")
		self.assertEqual(registration["status_changed_on"], "2026-05-01")

	def test_item_lines(self):
		lines = merge.build_registration(self.group(), [], {"ITEM-1"}, TODAY)["registered_products"]
		self.assertEqual([line["line_no"] for line in lines], [1, 2])
		self.assertEqual(lines[0]["item_code"], "ITEM-1")
		self.assertEqual(lines[0]["product_name"], "Item One")
		self.assertEqual(lines[0]["item_name_at_registration"], "Item One")
		self.assertEqual(lines[0]["identifier"], "FDA 123")
		self.assertEqual(lines[0]["line_status"], "Active")
		# ITEM-2 is not in the existing set: the link is dropped but the line is kept with a remark
		self.assertNotIn("item_code", lines[1])
		self.assertEqual(lines[1]["product_name"], "ITEM-2")
		self.assertIn("ITEM-2", lines[1]["remarks"])

	def test_duplicate_items_are_mapped_once(self):
		group = merge.group_rows([legacy("T-1"), legacy("T-2")])["FDA123"]
		self.assertEqual(len(merge.build_product_lines(group, {"ITEM-1"})), 1)

	def test_unspecified_expiry_clears_the_date(self):
		group = merge.group_rows(
			[legacy("T-1", expiry_basis="เอกสารไม่ระบุวันหมดอายุ", expiry_date="2020-01-01")]
		)["FDA123"]
		registration = merge.build_registration(group, [], None, TODAY)
		self.assertEqual(registration["expiry_basis"], "No Expiry Date Specified")
		self.assertIsNone(registration["expiry_date"])
		self.assertIsNone(registration["days_to_expiry"])
		self.assertEqual(registration["official_status"], "Active")

	def test_expired_active_and_draft(self):
		expired = merge.group_rows([legacy("T-1", expiry_date="2026-01-01")])["FDA123"]
		registration = merge.build_registration(expired, [], None, TODAY)
		self.assertEqual(registration["official_status"], "Expired")
		self.assertLess(registration["days_to_expiry"], 0)
		draft = merge.group_rows([legacy("T-1", status="รออนุมัติ")])["LEGACYT1"]
		registration = merge.build_registration(draft, [], None, TODAY)
		self.assertEqual(registration["official_status"], "Draft")
		self.assertIsNone(registration["status_reason"])

	def test_renewal_history_mapping(self):
		children = [
			{"parent": "T-1", "idx": 2, "event_date": "2026-03-01", "stage": "ไม่อนุมัติ/ยุติ", "notes": "x"},
			{
				"parent": "T-1",
				"idx": 1,
				"event_date": "2026-02-01",
				"stage": "Submitted",
				"application_number": "APP-1",
				"previous_registration_number": "A",
				"new_registration_number": "B",
				"previous_expiry_date": "2026-01-01",
				"new_expiry_date": "2027-01-01",
				"previous_document": "/files/a.pdf",
			},
		]
		history = merge.build_history(children)
		self.assertEqual([row["event_date"] for row in history], ["2026-02-01", "2026-03-01"])
		self.assertEqual(history[0]["application_number"], "APP-1")
		self.assertEqual(history[0]["new_expiry_date"], "2027-01-01")
		self.assertEqual(history[0]["previous_document"], "/files/a.pdf")
		self.assertEqual(history[1]["stage"], "Rejected / Discontinued")
		self.assertEqual(history[0]["doctype"], "Regulatory Renewal History")
		self.assertEqual(set(history[0]) - {"doctype"}, set(merge.HISTORY_FIELDS))

	def test_certificate_documents(self):
		group = merge.group_rows(
			[
				legacy("T-1", document="/files/old.pdf", verified_date=None),
				legacy("T-2", document="/files/new.pdf", verified_date="2026-04-01", modified="2026-05-01"),
				legacy("T-3", document="/files/new.pdf", modified="2026-04-01"),
				legacy("T-4", document=""),
			]
		)["FDA123"]
		registration = merge.build_registration(group, [], None, TODAY)
		documents = merge.build_documents(group, registration)
		self.assertEqual([doc["file"] for doc in documents], ["/files/new.pdf", "/files/old.pdf"])
		new, old = documents
		self.assertEqual(new["document_type"], "Certificate")
		self.assertEqual(new["verification_status"], "Verified")
		self.assertEqual(new["verified_on"], "2026-04-01")
		self.assertEqual(old["verification_status"], "Pending")
		self.assertIsNone(old["verified_on"])
		self.assertEqual(new["registration"], "FDA-123")
		self.assertEqual(new["expiry_date"], "2027-01-31")


class TestPlanAndIdempotency(unittest.TestCase):
	def plan(self, rows, merged=(), existing=None, children=None):
		return merge.plan_groups(rows, children or {}, set(merged), existing or {}, None, TODAY)

	def test_creates_new_groups(self):
		plan = self.plan([legacy("T-1"), legacy("T-2", "B-2")])
		self.assertEqual([entry["action"] for entry in plan], ["create", "create"])

	def test_already_merged_groups_are_skipped(self):
		plan = self.plan([legacy("T-1"), legacy("T-2", "B-2")], merged={"T-1"})
		self.assertEqual(
			{entry["key"]: entry["action"] for entry in plan}, {"FDA123": "skip_merged", "B2": "create"}
		)

	def test_partially_merged_group_is_reported_not_duplicated(self):
		plan = self.plan([legacy("T-1"), legacy("T-2", item="ITEM-2")], merged={"T-1"})
		self.assertEqual(plan[0]["action"], "skip_partially_merged")
		self.assertTrue(plan[0]["conflicts"])

	def test_existing_registration_with_same_number_is_not_overwritten(self):
		plan = self.plan([legacy("T-1")], existing={"FDA123": "FDA-123"})
		self.assertEqual(plan[0]["action"], "skip_existing_number")
		self.assertIn("already has this number", plan[0]["conflicts"][0])

	def test_children_are_collected_across_the_group(self):
		children = {"T-1": [{"parent": "T-1", "idx": 1, "event_date": "2026-01-01", "stage": "Submitted"}]}
		plan = self.plan([legacy("T-1"), legacy("T-2")], children=children)
		self.assertEqual(len(plan[0]["registration"]["legacy_renewal_history"]), 1)

	def test_skip_merged_groups_are_quiet(self):
		rows = [
			legacy("T-1", expiry_date="2027-01-31"),
			legacy("T-2", expiry_date="2028-01-31", item="ITEM-2"),
		]
		noisy = merge.plan_groups(rows, {}, set(), {}, None, TODAY)
		quiet = merge.plan_groups(rows, {}, {"T-1", "T-2"}, {}, None, TODAY)
		self.assertTrue(noisy[0]["conflicts"])
		self.assertEqual(quiet[0]["action"], "skip_merged")
		self.assertEqual(quiet[0]["conflicts"], [])


class TestItemCategories(unittest.TestCase):
	def row(self, name, category, item="ITEM-1", status="Active", line="Active", modified="2026-01-01"):
		return {
			"name": name,
			"item_code": item,
			"product_category": category,
			"other_category": None,
			"official_status": status,
			"line_status": line,
			"modified": modified,
		}

	def test_newest_modified_wins_among_equal_registrations(self):
		rows = [self.row("A", "Drug", modified="2026-01-01"), self.row("B", "Food", modified="2026-02-01")]
		self.assertEqual(merge.choose_item_categories(rows), {"ITEM-1": ("Food", None)})

	def test_draft_and_superseded_lines_do_not_decide_when_a_better_one_exists(self):
		rows = [
			self.row("A", "Drug", status="Draft", modified="2026-09-01"),
			self.row("B", "Cosmetic", status="Superseded", modified="2026-08-01"),
			self.row("C", "Food", line="Superseded", modified="2026-07-01"),
			self.row("D", "Herbal Product", modified="2026-01-01"),
		]
		self.assertEqual(merge.choose_item_categories(rows), {"ITEM-1": ("Herbal Product", None)})
		self.assertEqual(merge.choose_item_categories(rows[:2]), {"ITEM-1": ("Cosmetic", None)})

	def test_other_category_text_and_blank_rows(self):
		rows = [self.row("A", "Other"), self.row("B", None, item="ITEM-2")]
		rows[0]["other_category"] = "Toys"
		self.assertEqual(merge.choose_item_categories(rows), {"ITEM-1": ("Other", "Toys")})

	def test_read_merged_lines_queries_the_merged_registrations(self):
		db = MagicMock()
		db.table_exists.return_value = True
		db.sql.return_value = [{"item_code": "ITEM-1"}]
		with patch.object(merge.frappe, "db", db):
			self.assertEqual(merge.read_merged_lines(), [{"item_code": "ITEM-1"}])
		query = db.sql.call_args.args[0]
		self.assertIn("legacy_thai_fda_records", query)
		self.assertIn("tabRegulatory Registered Product", query)
		db.table_exists.return_value = False
		with patch.object(merge.frappe, "db", db):
			self.assertEqual(merge.read_merged_lines(), [])


class TestOverlapResolution(unittest.TestCase):
	def plan(self, rows, existing_lines=None):
		return merge.plan_groups(rows, {}, set(), {}, {"ITEM-1", "ITEM-2"}, TODAY, existing_lines)

	def statuses(self, plan):
		return {
			entry["registration"]["registration_number"]: [
				line["line_status"] for line in entry["registration"]["registered_products"]
			]
			for entry in plan
		}

	def test_overlapping_groups_keep_the_line_that_expires_last(self):
		plan = self.plan(
			[
				legacy("T-1", "A-1", expiry_date="2027-01-31"),
				legacy("T-2", "B-1", expiry_date="2028-01-31", modified="2025-01-01"),
			]
		)
		self.assertEqual(self.statuses(plan), {"A-1": ["Superseded"], "B-1": ["Active"]})
		loser = plan[0]
		self.assertIn(
			"Overlaps B-1; resolved automatically", loser["registration"]["registered_products"][0]["remarks"]
		)
		self.assertTrue(any("Superseded" in message for message in loser["conflicts"]))
		self.assertIn("Superseded", merge.format_plan(plan))

	def test_open_ended_expiry_wins_and_ties_use_newest_modified(self):
		plan = self.plan(
			[
				legacy("T-1", "A-1", expiry_date="2030-01-01"),
				legacy("T-2", "B-1", expiry_basis="Unconfirmed", expiry_date=None),
			]
		)
		self.assertEqual(self.statuses(plan), {"A-1": ["Superseded"], "B-1": ["Active"]})
		plan = self.plan(
			[legacy("T-1", "A-1", modified="2026-05-01"), legacy("T-2", "B-1", modified="2026-02-01")]
		)
		self.assertEqual(self.statuses(plan), {"A-1": ["Active"], "B-1": ["Superseded"]})

	def test_non_overlapping_windows_and_other_items_are_untouched(self):
		plan = self.plan(
			[
				legacy("T-1", "A-1", issue_date="2024-01-01", expiry_date="2025-12-31"),
				legacy("T-2", "B-1", issue_date="2026-01-01", expiry_date="2027-12-31"),
				legacy("T-3", "C-1", item="ITEM-2"),
			]
		)
		self.assertEqual(self.statuses(plan), {"A-1": ["Active"], "B-1": ["Active"], "C-1": ["Active"]})
		self.assertFalse(any(entry["conflicts"] for entry in plan))

	def test_item_in_three_groups(self):
		plan = self.plan(
			[
				legacy("T-1", "A-1", issue_date="2024-01-01", expiry_date="2026-12-31"),
				legacy("T-2", "B-1", issue_date="2025-06-01", expiry_date="2028-12-31"),
				legacy("T-3", "C-1", issue_date="2029-01-01", expiry_date="2030-12-31"),
			]
		)
		# B wins over A, which starts earlier and is trimmed; C starts after B ends and is untouched
		self.assertEqual(self.statuses(plan), {"A-1": ["Active"], "B-1": ["Active"], "C-1": ["Active"]})
		lines = {
			entry["registration"]["registration_number"]: entry["registration"]["registered_products"][0]
			for entry in plan
		}
		self.assertEqual(str(lines["A-1"]["effective_to"]), "2025-05-31")
		self.assertIsNone(lines["B-1"].get("effective_to"))
		self.assertIn("trimmed", "\n".join(plan[0]["conflicts"]))

	def test_partial_overlap_is_trimmed_so_the_spans_no_longer_overlap(self):
		plan = self.plan(
			[
				legacy("T-1", "A-1", issue_date="2024-01-01", expiry_date="2026-12-31"),
				legacy("T-2", "B-1", issue_date="2025-06-01", expiry_date="2028-12-31"),
			]
		)
		loser = plan[0]
		winner = plan[1]
		loser_line = loser["registration"]["registered_products"][0]
		winner_line = winner["registration"]["registered_products"][0]
		self.assertEqual(loser_line["line_status"], "Active")
		self.assertIn("Overlaps B-1", loser_line["remarks"])
		self.assertFalse(
			coverage.ranges_overlap(
				*coverage.effective_span(loser_line, loser["registration"]),
				*coverage.effective_span(winner_line, winner["registration"]),
			)
		)

	def test_loser_inside_the_winner_or_with_the_same_start_is_superseded(self):
		contained = self.plan(
			[
				legacy("T-1", "A-1", issue_date="2025-07-01", expiry_date="2026-12-31"),
				legacy("T-2", "B-1", issue_date="2025-06-01", expiry_date="2028-12-31"),
			]
		)
		self.assertEqual(self.statuses(contained), {"A-1": ["Superseded"], "B-1": ["Active"]})
		same_start = self.plan(
			[
				legacy("T-1", "A-1", issue_date="2025-06-01", expiry_date="2026-12-31"),
				legacy("T-2", "B-1", issue_date="2025-06-01", expiry_date="2028-12-31"),
			]
		)
		self.assertEqual(self.statuses(same_start), {"A-1": ["Superseded"], "B-1": ["Active"]})

	def test_active_beats_superseded_or_expired_in_both_orders(self):
		for superseded_first in (True, False):
			rows = [
				legacy("T-1", "A-1", status="Superseded", expiry_date="2030-01-01", modified="2026-09-01"),
				legacy("T-2", "B-1", expiry_date="2027-01-31", modified="2026-01-01"),
			]
			if not superseded_first:
				rows.reverse()
			plan = self.plan(rows)
			self.assertEqual(self.statuses(plan)["B-1"], ["Active"], superseded_first)
			self.assertEqual(self.statuses(plan)["A-1"], ["Superseded"])
			self.assertIn("Overlaps B-1", plan[0]["registration"]["registered_products"][0]["remarks"])

	def test_active_beats_an_open_ended_superseded_registration(self):
		plan = self.plan(
			[
				legacy("T-1", "A-1", status="Superseded", expiry_basis="Unconfirmed", expiry_date=None),
				legacy("T-2", "B-1", issue_date="2026-01-01", expiry_date="2027-01-31"),
			]
		)
		by_number = {entry["registration"]["registration_number"]: entry for entry in plan}
		self.assertEqual(by_number["B-1"]["registration"]["registered_products"][0]["line_status"], "Active")
		self.assertNotIn("remarks", by_number["B-1"]["registration"]["registered_products"][0])
		self.assertIn("Overlaps B-1", by_number["A-1"]["registration"]["registered_products"][0]["remarks"])

	def test_equal_rank_higher_name_wins(self):
		plan = self.plan([legacy("T-1", "A-1"), legacy("T-2", "B-1")])
		self.assertEqual(self.statuses(plan), {"A-1": ["Superseded"], "B-1": ["Active"]})

	def test_not_in_force_registrations_are_ignored(self):
		plan = self.plan([legacy("T-1", "A-1", status="Suspended"), legacy("T-2", "B-1")])
		self.assertEqual(self.statuses(plan), {"A-1": ["Active"], "B-1": ["Active"]})

	def existing_line(self, status="Active"):
		return {
			"parent": "RA-1",
			"item_code": "ITEM-1",
			"effective_from": None,
			"effective_to": None,
			"official_status": status,
			"issue_date": "2025-01-01",
			"expiry_basis": "No Expiry Date Specified",
			"expiry_date": None,
		}

	def test_overlap_with_an_existing_active_registration(self):
		plan = self.plan(
			[legacy("T-1", "A-1", issue_date="2025-01-01"), legacy("T-2", "B-1", item="ITEM-2")],
			[self.existing_line()],
		)
		self.assertEqual(self.statuses(plan), {"A-1": ["Superseded"], "B-1": ["Active"]})
		self.assertIn("Overlaps RA-1", plan[0]["registration"]["registered_products"][0]["remarks"])
		self.assertEqual(plan[0]["resolved_against"][0]["existing"], True)

	def test_an_existing_non_active_registration_loses_to_a_planned_active_line(self):
		for status in ("Expired", "Superseded"):
			plan = self.plan([legacy("T-1", "A-1")], [self.existing_line(status)])
			self.assertEqual(self.statuses(plan), {"A-1": ["Active"]}, status)
			self.assertFalse(plan[0]["conflicts"])

	def test_a_planned_non_active_line_still_loses_to_an_equal_existing_one(self):
		plan = self.plan(
			[legacy("T-1", "A-1", status="Superseded", issue_date="2025-01-01")],
			[self.existing_line("Superseded")],
		)
		self.assertEqual(self.statuses(plan), {"A-1": ["Superseded"]})

	def test_overlap_warnings_name_the_losers_of_a_failed_winner(self):
		plan = self.plan(
			[
				legacy("T-1", "A-1", issue_date="2025-07-01", expiry_date="2026-12-31"),
				legacy("T-2", "B-1", issue_date="2025-06-01", expiry_date="2028-12-31"),
			]
		)
		warnings = merge.overlap_warnings(plan, {"B-1"})
		self.assertEqual(len(warnings), 1)
		self.assertIn("B-1", warnings[0])
		self.assertIn("Item ITEM-1 on A-1", warnings[0])
		self.assertEqual(merge.overlap_warnings(plan, {"A-1"}), [])
		self.assertEqual(merge.overlap_warnings(plan, set()), [])


class TestPlaceholders(unittest.TestCase):
	def test_placeholder_numbers_and_pending_rows_are_not_grouped(self):
		rows = [
			legacy("T-1", "-"),
			legacy("T-2", "N/A"),
			legacy("T-3", "TBA"),
			legacy("T-4", "pending"),
			legacy("T-5", "FDA-9", status="Pending Approval"),
			legacy("T-6", "FDA-9", status="รออนุมัติ"),
			legacy("T-7", "FDA-9"),
			legacy("T-8", "FDA-9"),
			legacy("T-9", "***"),
		]
		groups = merge.group_rows(rows)
		self.assertEqual(len(groups), 8)
		self.assertTrue(all(key for key in groups))
		self.assertEqual(sorted(row["name"] for row in groups["FDA9"]), ["T-7", "T-8"])
		self.assertEqual(
			merge.registration_number(legacy("T-5", "FDA-9", status="Pending Approval")), "LEGACY-T-5"
		)
		self.assertEqual(merge.registration_number(legacy("T-9", "***")), "LEGACY-T-9")

	def test_placeholder_keeps_the_typed_number_as_remark(self):
		group = merge.group_rows([legacy("T-1", "N/A")])["LEGACYT1"]
		line = merge.build_registration(group, [], None, TODAY)["registered_products"][0]
		self.assertEqual(line["identifier"], "LEGACY-T-1")
		self.assertIn("N/A", line["remarks"])

	def test_more_attributes_are_compared_inside_a_group(self):
		group = merge.group_rows(
			[
				legacy("T-1", approval_type="A", holder_name="X", activity="Import", issue_date="2024-01-01"),
				legacy(
					"T-2",
					item="ITEM-2",
					approval_type="B",
					holder_name="Y",
					activity="Sale",
					issue_date="2025-01-01",
				),
			]
		)["FDA123"]
		text = "\n".join(merge.find_conflicts(group, TODAY))
		for label in ("license / notification type", "holder name", "activity", "issue date"):
			self.assertIn(f"different {label}", text)


class TestSettingsMapping(unittest.TestCase):
	def test_scalar_settings(self):
		values = merge.map_settings(
			{"enabled": "0", "overdue_repeat_days": "14", "send_system_notification": "1", "send_email": "0"}
		)
		self.assertEqual(
			values,
			{
				"enable_expiry_alerts": 0,
				"overdue_repeat_days": 14,
				"send_system_notification": 1,
				"send_email": 0,
			},
		)
		self.assertEqual(merge.map_settings({"reminder_days": "90"}), {})

	def test_alert_rules_only_for_missing_days(self):
		rules = merge.plan_alert_rules("90,60,30,7", {60, 30, 7})
		self.assertEqual(rules, [{"days_before_expiry": 90, "severity": "Warning", "action": "Reminder"}])
		all_rules = merge.plan_alert_rules("180, 120;45;x;45;-3;0", set())
		self.assertEqual(
			[(rule["days_before_expiry"], rule["severity"]) for rule in all_rules],
			[(180, "Info"), (120, "Info"), (45, "Critical")],
		)
		self.assertEqual(merge.severity_for(119), "Warning")
		self.assertEqual(merge.severity_for(60), "Warning")
		self.assertEqual(merge.severity_for(59), "Critical")
		self.assertEqual(merge.plan_alert_rules("", set()), [])

	def test_recipients_are_deduplicated(self):
		rows = [
			{"user": "a@example.com", "enabled": 1},
			{"user": "a@example.com", "enabled": 1},
			{"user": "b@example.com", "enabled": 0},
			{"user": "c@example.com"},
			{"user": ""},
		]
		recipients, skipped = merge.plan_recipients(rows, {"c@example.com"})
		self.assertEqual(
			recipients,
			[{"user": "a@example.com", "enabled": 1}, {"user": "b@example.com", "enabled": 0}],
		)
		self.assertEqual(skipped, [])

	def test_recipients_of_deleted_users_are_skipped(self):
		rows = [{"user": "a@example.com"}, {"user": "gone@example.com"}]
		recipients, skipped = merge.plan_recipients(rows, set(), lambda user: user == "a@example.com")
		self.assertEqual([row["user"] for row in recipients], ["a@example.com"])
		self.assertEqual(skipped, ["gone@example.com"])

	def test_merge_settings_without_legacy_single_writes_nothing(self):
		db = MagicMock()
		db.sql.return_value = []
		with patch.object(merge.frappe, "db", db), patch.object(merge.frappe, "get_single") as get_single:
			merge.merge_settings()
		get_single.assert_not_called()

	def test_merge_settings_copies_values_rules_and_recipients_once(self):
		settings = MagicMock()
		rules = [MagicMock(days_before_expiry=30)]
		settings.get.side_effect = lambda key: {"expiry_alert_rules": rules, "recipients": []}[key]
		db = MagicMock()
		db.sql.side_effect = [
			[{"field": "enabled", "value": "1"}, {"field": "reminder_days", "value": "90,30"}],
			[{"user": "a@example.com", "enabled": 1}],
		]
		db.table_exists.return_value = True
		db.get_default.return_value = None
		db.exists.side_effect = lambda doctype, user: user != "gone@example.com"
		with (
			patch.object(merge.frappe, "db", db),
			patch.object(merge.frappe, "get_single", return_value=settings),
		):
			self.assertEqual(merge.merge_settings(), [])
		settings.update.assert_called_once_with({"enable_expiry_alerts": 1})
		db.set_default.assert_called_once_with(merge.SETTINGS_MARKER, "1")
		appended = {call.args[0]: call.args[1] for call in settings.append.call_args_list}
		self.assertEqual(appended["expiry_alert_rules"]["days_before_expiry"], 90)
		self.assertEqual(appended["recipients"]["user"], "a@example.com")
		settings.save.assert_called_once_with(ignore_permissions=True)

	def test_scalar_settings_are_not_copied_again(self):
		settings = MagicMock()
		settings.get.return_value = []
		db = MagicMock()
		db.sql.side_effect = [[{"field": "enabled", "value": "0"}], []]
		db.table_exists.return_value = True
		db.get_default.return_value = "1"
		with (
			patch.object(merge.frappe, "db", db),
			patch.object(merge.frappe, "get_single", return_value=settings),
		):
			merge.merge_settings()
		settings.update.assert_not_called()
		db.set_default.assert_not_called()


class TestRun(unittest.TestCase):
	def recorder(self, order, name, effect=None):
		def record(*args):
			if order is not None:
				order.append(name)
			if isinstance(effect, Exception):
				raise effect
			return DEFAULT

		return record if effect is not None or order is not None else None

	def make_db(self, order):
		db = MagicMock()
		if order is not None:
			db.commit.side_effect = lambda: order.append("commit")
			db.rollback.side_effect = lambda: order.append("rollback")
		return db

	def run_patch(
		self,
		dry_run,
		tables_exist=True,
		rows=None,
		merged=(),
		insert_effect=None,
		settings_effect=None,
		settings_result=None,
		merged_lines=None,
		categories_effect=None,
		order=None,
	):
		if merged_lines is None:
			merged_lines = [
				{
					"item_code": "ITEM-1",
					"product_category": "Food",
					"other_category": None,
					"official_status": "Active",
					"line_status": "Active",
					"modified": "2026-01-01",
				}
			]
		rows = [legacy("T-1")] if rows is None else rows
		with (
			patch.object(merge, "legacy_tables_exist", return_value=tables_exist),
			patch.object(merge, "read_legacy_rows", return_value=rows),
			patch.object(merge, "read_children", return_value={}),
			patch.object(merge, "read_merged_names", return_value=set(merged)),
			patch.object(merge, "read_existing_numbers", return_value={}),
			patch.object(merge, "read_existing_items", return_value={"ITEM-1"}),
			patch.object(merge, "read_existing_lines", return_value=[]),
			patch.object(merge, "read_merged_lines", return_value=merged_lines),
			patch.object(merge, "insert_group", side_effect=insert_effect) as insert_group,
			patch.object(
				merge,
				"apply_item_categories",
				side_effect=self.recorder(order, "categories", categories_effect),
			) as apply_item_categories,
			patch.object(
				merge,
				"merge_settings",
				side_effect=self.recorder(order, "settings", settings_effect),
				return_value=settings_result or [],
			) as merge_settings,
			patch("erpnext.regulatory_affairs.install.ensure_configuration") as ensure_configuration,
			patch.object(merge.frappe, "db", self.make_db(order)) as db,
			patch.object(merge.frappe, "log_error") as log_error,
			patch.object(merge.frappe, "get_traceback", return_value="Traceback\nValueError: boom"),
			patch.object(merge, "today", return_value=TODAY),
			patch("builtins.print") as printed,
		):
			result = merge.run(dry_run=dry_run)
		self.printed = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
		return result, {
			"insert_group": insert_group,
			"apply_item_categories": apply_item_categories,
			"merge_settings": merge_settings,
			"ensure_configuration": ensure_configuration,
			"db": db,
			"log_error": log_error,
		}

	def test_commit_follows_ensure_configuration_before_any_group(self):
		order = []
		with patch.object(merge, "insert_group", side_effect=lambda entry: order.append("insert")):
			with patch(
				"erpnext.regulatory_affairs.install.ensure_configuration",
				side_effect=lambda: order.append("configure"),
			):
				self.run_with_commit_log(order)
		self.assertEqual(order[:3], ["configure", "commit", "insert"])

	def run_with_commit_log(self, order):
		db = MagicMock()
		db.commit.side_effect = lambda: order.append("commit")
		with (
			patch.object(merge, "legacy_tables_exist", return_value=True),
			patch.object(merge, "read_legacy_rows", return_value=[legacy("T-1")]),
			patch.object(merge, "read_children", return_value={}),
			patch.object(merge, "read_merged_names", return_value=set()),
			patch.object(merge, "read_existing_numbers", return_value={}),
			patch.object(merge, "read_existing_items", return_value={"ITEM-1"}),
			patch.object(merge, "read_existing_lines", return_value=[]),
			patch.object(merge, "read_merged_lines", return_value=[]),
			patch.object(merge, "apply_item_categories"),
			patch.object(merge, "merge_settings", return_value=[]),
			patch.object(merge.frappe, "db", db),
			patch.object(merge.frappe, "log_error"),
			patch.object(merge, "today", return_value=TODAY),
			patch("builtins.print"),
		):
			merge.run(dry_run=False)

	def test_failed_groups_print_a_prominent_message_and_do_not_raise(self):
		result, mocks = self.run_patch(dry_run=False, insert_effect=ValueError("boom"))
		self.assertEqual(result["failed"], 1)
		self.assertEqual(result["created"], [])
		self.assertIn("1 legacy group(s) were NOT merged", self.printed)
		self.assertIn(merge.CONFLICT_TITLE, self.printed)
		self.assertIn(
			"execute erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs.execute", self.printed
		)
		mocks["db"].rollback.assert_called_once_with()

	def test_no_failure_message_when_everything_merged(self):
		self.run_patch(dry_run=False)
		self.assertNotIn("NOT merged", self.printed)

	def test_settings_failure_is_recorded_without_aborting(self):
		result, mocks = self.run_patch(dry_run=False, settings_effect=RuntimeError("settings"))
		self.assertEqual(result["created"], ["FDA-123"])
		self.assertEqual(len(result["errors"]), 1)
		self.assertIn("Settings merge failed", result["errors"][0])
		titles = [call.kwargs["title"] for call in mocks["log_error"].call_args_list]
		self.assertIn("Thai FDA to RA settings merge failed", titles)
		self.assertEqual(result["failed"], 0)

	def test_skipped_recipient_users_are_reported(self):
		result, mocks = self.run_patch(dry_run=False, settings_result=["gone@example.com"])
		self.assertEqual(result["skipped_users"], ["gone@example.com"])
		self.assertIn("gone@example.com", self.printed)
		self.assertIn("gone@example.com", mocks["log_error"].call_args.kwargs["message"])

	def test_no_legacy_tables_is_a_noop(self):
		result, mocks = self.run_patch(dry_run=False, tables_exist=False)
		self.assertEqual(result["plan"], [])
		for name in ("insert_group", "apply_item_categories", "merge_settings", "ensure_configuration"):
			mocks[name].assert_not_called()
		mocks["db"].commit.assert_not_called()

	def test_dry_run_writes_nothing(self):
		result, mocks = self.run_patch(dry_run=True)
		self.assertEqual(result["plan"][0]["action"], "create")
		self.assertTrue(result["dry_run"])
		for name in (
			"insert_group",
			"apply_item_categories",
			"merge_settings",
			"ensure_configuration",
			"log_error",
		):
			mocks[name].assert_not_called()
		mocks["db"].commit.assert_not_called()

	def test_real_run_inserts_configures_and_finishes_items_and_settings(self):
		result, mocks = self.run_patch(dry_run=False)
		self.assertEqual(result["created"], ["FDA-123"])
		mocks["ensure_configuration"].assert_called_once_with()
		mocks["insert_group"].assert_called_once()
		mocks["apply_item_categories"].assert_called_once_with({"ITEM-1": ("Food", None)})
		mocks["merge_settings"].assert_called_once_with()
		self.assertEqual(result["warnings"], [])
		mocks["log_error"].assert_not_called()

	def test_rerun_skips_merged_records(self):
		result, mocks = self.run_patch(dry_run=False, merged={"T-1"})
		self.assertEqual(result["created"], [])
		mocks["insert_group"].assert_not_called()
		mocks["apply_item_categories"].assert_called_once_with({"ITEM-1": ("Food", None)})

	def test_conflicts_are_logged_and_a_failed_group_does_not_stop_the_run(self):
		rows = [
			legacy("T-1", expiry_date="2027-01-31"),
			legacy("T-2", expiry_date="2028-01-31", item="ITEM-2"),
		]
		rows.append(legacy("T-3", "B-2"))
		with patch.object(merge.frappe, "get_traceback", return_value="Traceback\nValueError: boom"):
			with patch.object(merge, "insert_group", side_effect=[ValueError("boom"), None]):
				result, mocks = self.run_with_failing_insert(rows)
		self.assertEqual(result["created"], ["B-2"])
		self.assertEqual(len(result["errors"]), 1)
		mocks["db"].rollback.assert_called_once_with()
		mocks["log_error"].assert_called_once()
		self.assertEqual(mocks["log_error"].call_args.kwargs["title"], merge.CONFLICT_TITLE)
		self.assertIn("different expiry date", mocks["log_error"].call_args.kwargs["message"])

	def run_with_failing_insert(self, rows):
		with (
			patch.object(merge, "legacy_tables_exist", return_value=True),
			patch.object(merge, "read_legacy_rows", return_value=rows),
			patch.object(merge, "read_children", return_value={}),
			patch.object(merge, "read_merged_names", return_value=set()),
			patch.object(merge, "read_existing_numbers", return_value={}),
			patch.object(merge, "read_existing_items", return_value={"ITEM-1", "ITEM-2"}),
			patch.object(merge, "read_existing_lines", return_value=[]),
			patch.object(merge, "read_merged_lines", return_value=[]),
			patch.object(merge, "apply_item_categories"),
			patch.object(merge, "merge_settings"),
			patch("erpnext.regulatory_affairs.install.ensure_configuration"),
			patch.object(merge.frappe, "db", MagicMock()) as db,
			patch.object(merge.frappe, "log_error") as log_error,
			patch.object(merge, "today", return_value=TODAY),
			patch("builtins.print"),
		):
			result = merge.run(dry_run=False)
		return result, {"db": db, "log_error": log_error}

	def test_rerun_repairs_a_missed_category_copy(self):
		# every group is already merged (skip_merged) but the categories are still applied from the database
		result, mocks = self.run_patch(dry_run=False, merged={"T-1"})
		self.assertEqual(result["created"], [])
		mocks["apply_item_categories"].assert_called_once_with({"ITEM-1": ("Food", None)})

	def test_settings_failure_does_not_lose_the_categories(self):
		order = []
		result, _mocks = self.run_patch(dry_run=False, settings_effect=RuntimeError("settings"), order=order)
		categories, settings = order.index("categories"), order.index("settings")
		# a commit separates the category copy from the settings step whose failure rolls back
		self.assertIn("commit", order[categories:settings])
		self.assertLess(settings, order.index("rollback", settings))
		self.assertEqual(len(result["errors"]), 1)

	def test_category_copy_failure_is_recorded_and_the_run_continues(self):
		result, mocks = self.run_patch(dry_run=False, categories_effect=RuntimeError("x"))
		self.assertTrue(any("Item category copy failed" in error for error in result["errors"]))
		mocks["merge_settings"].assert_called_once_with()
		self.assertEqual(result["created"], ["FDA-123"])

	def test_failed_winner_produces_a_warning(self):
		rows = [
			legacy("T-1", "A-1", issue_date="2025-07-01", expiry_date="2026-12-31"),
			legacy("T-2", "B-1", issue_date="2025-06-01", expiry_date="2028-12-31"),
		]
		result, _mocks = self.run_patch(
			dry_run=False, rows=rows, insert_effect=[None, ValueError("boom")], order=None
		)
		self.assertEqual(result["created"], ["A-1"])
		self.assertEqual(len(result["warnings"]), 1)
		self.assertIn("Item ITEM-1 on A-1", result["warnings"][0])
		self.assertIn("B-1", self.printed)

	def test_execute_runs_for_real(self):
		with patch.object(merge, "run") as run:
			merge.execute()
		run.assert_called_once_with(dry_run=False)


class TestInsert(unittest.TestCase):
	def test_insert_skips_validation_and_preserves_stamps(self):
		plan = merge.plan_groups(
			[legacy("T-1", document="/files/c.pdf", verified_date="2026-04-01")],
			{},
			set(),
			{},
			{"ITEM-1"},
			TODAY,
		)
		documents = []

		def get_doc(values):
			doc = MagicMock()
			doc.name = values.get("registration_number") or "DOC-00001"
			doc.values = values
			documents.append(doc)
			return doc

		db = MagicMock()
		with patch.object(merge.frappe, "get_doc", side_effect=get_doc), patch.object(merge.frappe, "db", db):
			merge.insert_group(plan[0])
		registration, certificate = documents
		self.assertTrue(registration.flags.ignore_validate)
		self.assertTrue(registration.flags.allow_status_change)
		self.assertNotIn("creation", registration.values)
		self.assertEqual(certificate.values["registration"], "FDA-123")
		registration.insert.assert_called_once_with(ignore_permissions=True)
		stamped = [call.args[:2] for call in db.set_value.call_args_list]
		self.assertEqual(
			stamped, [("Regulatory Product Registration", "FDA-123"), ("Regulatory Document", "DOC-00001")]
		)
		self.assertEqual(db.set_value.call_args_list[0].args[2]["owner"], "o@example.com")
		self.assertIs(db.set_value.call_args_list[0].kwargs["update_modified"], False)


class TestLegacyTables(unittest.TestCase):
	def test_missing_table_means_no_legacy_data(self):
		db = MagicMock()
		db.table_exists.return_value = False
		with patch.object(merge.frappe, "db", db):
			self.assertFalse(merge.legacy_tables_exist())
			self.assertEqual(merge.read_children(), {})
			self.assertEqual(merge.read_merged_names(), set())
		db.sql.assert_not_called()

	def test_merged_names_are_read_from_the_comma_separated_field(self):
		db = MagicMock()
		db.table_exists.return_value = True
		db.has_column.return_value = True
		db.sql.return_value = [{"legacy_thai_fda_records": "T-1, T-2"}, {"legacy_thai_fda_records": "T-3"}]
		with patch.object(merge.frappe, "db", db):
			self.assertEqual(merge.read_merged_names(), {"T-1", "T-2", "T-3"})

	def test_read_children_groups_by_parent(self):
		db = MagicMock()
		db.table_exists.return_value = True
		db.sql.return_value = [
			{"parent": "T-1", "idx": 1},
			{"parent": "T-1", "idx": 2},
			{"parent": "T-2", "idx": 1},
		]
		with patch.object(merge.frappe, "db", db):
			children = merge.read_children()
		self.assertEqual({key: len(value) for key, value in children.items()}, {"T-1": 2, "T-2": 1})

	def test_read_existing_lines_queries_lines_with_the_registration_status(self):
		db = MagicMock()
		db.table_exists.return_value = True
		db.sql.return_value = [{"parent": "RA-1"}]
		with patch.object(merge.frappe, "db", db):
			self.assertEqual(merge.read_existing_lines({"ITEM-1"}), [{"parent": "RA-1"}])
			self.assertEqual(merge.read_existing_lines(set()), [])
		query, params = db.sql.call_args.args
		self.assertIn("`tabRegulatory Registered Product`", query)
		self.assertIn("parenttype", query)
		for column in ("official_status", "effective_from", "effective_to", "expiry_basis", "issue_date"):
			self.assertIn(column, query)
		self.assertEqual(params["parenttype"], "Regulatory Product Registration")
		self.assertEqual(params["items"], ("ITEM-1",))
		self.assertTrue(set(params["statuses"]) <= {"Active", "Expired", "Superseded"})

	def test_patch_is_registered_after_the_translation_patch(self):
		from pathlib import Path

		lines = (Path(merge.__file__).resolve().parents[2] / "patches.txt").read_text().splitlines()
		translate = lines.index("erpnext.patches.v16_0.translate_thai_fda_to_english")
		self.assertEqual(
			lines.index("erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs"), translate + 1
		)
		self.assertGreater(translate, lines.index("[post_model_sync]"))
