"""Pure tests (no site or database) for the Regulatory Affairs Script Reports and their helpers."""

import inspect
import json
import re
import unittest
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs import constants, reporting
from erpnext.regulatory_affairs.report.regulatory_compliance_gap import (
	regulatory_compliance_gap as compliance_gap,
)
from erpnext.regulatory_affairs.report.regulatory_expiring_registration_affected_business import (
	regulatory_expiring_registration_affected_business as affected_business,
)
from erpnext.regulatory_affairs.report.regulatory_item_coverage_matrix import (
	regulatory_item_coverage_matrix as coverage_matrix,
)
from erpnext.regulatory_affairs.report.regulatory_registration_expiry_summary import (
	regulatory_registration_expiry_summary as expiry_summary,
)
from erpnext.regulatory_affairs.report.regulatory_renewal_workbench import (
	regulatory_renewal_workbench as renewal_workbench,
)

TODAY = date(2026, 10, 18)
REPORT_DIR = Path(reporting.__file__).parent / "report"
ROLES = {"Regulatory Viewer", "Regulatory Operator", "Regulatory Approver", "System Manager"}

REPORTS = {
	"regulatory_registration_expiry_summary": (
		"Regulatory Registration Expiry Summary",
		"Regulatory Product Registration",
		expiry_summary,
	),
	"regulatory_expiring_registration_affected_business": (
		"Regulatory Expiring Registration Affected Business",
		"Regulatory Product Registration",
		affected_business,
	),
	"regulatory_item_coverage_matrix": (
		"Regulatory Item Coverage Matrix",
		"Regulatory Product Registration",
		coverage_matrix,
	),
	"regulatory_compliance_gap": (
		"Regulatory Compliance Gap",
		"Regulatory Product Registration",
		compliance_gap,
	),
	"regulatory_renewal_workbench": (
		"Regulatory Renewal Workbench",
		"Regulatory Registration Action",
		renewal_workbench,
	),
}


def registration(name, status="Active", expiry="2027-01-01", basis="Expiry Date Specified", **extra):
	return frappe._dict(
		{
			"name": name,
			"official_status": status,
			"issue_date": "2025-01-01",
			"expiry_basis": basis,
			"expiry_date": expiry,
			"renewal_status": "Not Started",
			"responsible_user": "owner@example.com",
			**extra,
		}
	)


def line(parent, item_code, newcode=None, status="Active", **extra):
	return frappe._dict(
		{
			"parent": parent,
			"idx": 1,
			"item_code": item_code,
			"newcode": newcode,
			"line_status": status,
			"effective_from": None,
			"effective_to": None,
			**extra,
		}
	)


REQUIRED, EXEMPT, NOT_ASSESSED = "Registration Required", "Not Required / Exempt", "Not Assessed"


def item(name, status=REQUIRED, note=None, **extra):
	return frappe._dict(
		{
			"name": name,
			"item_name": f"{name} name",
			"custom_thai_fda_applicability": status,
			"custom_thai_fda_assessment_note": note,
			**extra,
		}
	)


class TestFilterAndDateHelpers(unittest.TestCase):
	def test_clean_filters_drops_blank_values_but_keeps_zero(self):
		filters = reporting.clean_filters({"a": "", "b": None, "c": 0, "d": "x", "e": []})
		self.assertEqual(dict(filters), {"c": 0, "d": "x"})
		self.assertEqual(dict(reporting.clean_filters(None)), {})
		self.assertEqual(dict(reporting.clean_filters('{"item": "A"}')), {"item": "A"})

	def test_int_filter(self):
		self.assertEqual(reporting.int_filter({"n": "30"}, "n"), 30)
		self.assertEqual(reporting.int_filter({"n": 0}, "n"), 0)
		self.assertEqual(reporting.int_filter({"n": 12.0}, "n"), 12)
		self.assertIsNone(reporting.int_filter({"n": ""}, "n"))
		self.assertIsNone(reporting.int_filter({"n": "abc"}, "n"))
		self.assertIsNone(reporting.int_filter({}, "n"))

	def test_days_between_is_null_safe_and_signed(self):
		self.assertEqual(reporting.days_between("2026-10-28", TODAY), 10)
		self.assertEqual(reporting.days_between(date(2026, 10, 10), TODAY), -8)
		self.assertEqual(reporting.days_between(TODAY, TODAY), 0)
		self.assertIsNone(reporting.days_between(None, TODAY))
		self.assertIsNone(reporting.days_between("", TODAY))

	def test_effective_expiry_follows_the_expiry_basis(self):
		self.assertEqual(reporting.effective_expiry(registration("R")), date(2027, 1, 1))
		self.assertIsNone(reporting.effective_expiry(registration("R", basis="No Expiry Date Specified")))
		self.assertIsNone(reporting.effective_expiry(registration("R", basis="Unconfirmed")))
		self.assertIsNone(reporting.effective_expiry(registration("R", expiry=None)))

	def test_sort_by_expiry_puts_blank_expiry_last_and_breaks_ties(self):
		rows = [
			{"expiry_date": None, "registration": "A"},
			{"expiry_date": date(2027, 5, 1), "registration": "C"},
			{"expiry_date": "2026-11-01", "registration": "Z"},
			{"expiry_date": date(2026, 11, 1), "registration": "B"},
			{"expiry_date": None, "registration": "0"},
		]
		ordered = [row["registration"] for row in reporting.sort_by_expiry(rows, "registration")]
		self.assertEqual(ordered, ["B", "Z", "C", "0", "A"])

	def test_registration_query_filters(self):
		filters = frappe._dict(
			responsible_user="u@x.com",
			product_category="Drug",
			device_category="IVD",
			product_owner="M",
			expiry_basis="Unconfirmed",
		)
		self.assertEqual(
			reporting.registration_query_filters(filters),
			{
				"responsible_user": "u@x.com",
				"product_category": "Drug",
				"device_category": "IVD",
				"product_owner": "M",
				"expiry_basis": "Unconfirmed",
			},
		)
		self.assertEqual(
			reporting.registration_query_filters(frappe._dict(), ("Active", "Expired")),
			{"official_status": ["in", ["Active", "Expired"]]},
		)
		self.assertEqual(
			reporting.registration_query_filters(
				frappe._dict(official_status="Revoked", registration="R-1"), ("Active", "Expired")
			),
			{"official_status": "Revoked", "name": "R-1"},
		)

	def test_is_expiring_within(self):
		soon = registration("R", expiry="2026-12-01")
		past = registration("R", expiry="2026-09-01")
		none = registration("R", basis="No Expiry Date Specified")
		self.assertTrue(reporting.is_expiring_within(soon, None, TODAY))
		self.assertTrue(reporting.is_expiring_within(none, None, TODAY))
		self.assertTrue(reporting.is_expiring_within(soon, 60, TODAY))
		self.assertFalse(reporting.is_expiring_within(soon, 30, TODAY))
		self.assertTrue(reporting.is_expiring_within(past, 0, TODAY))
		self.assertFalse(reporting.is_expiring_within(none, 365, TODAY))

	def test_affected_business_default_window(self):
		def window(filters):
			return reporting.affected_business_filters(filters)["expiry_within_days"]

		self.assertEqual(window({}), 365)
		self.assertEqual(window(None), 365)
		self.assertEqual(window({"expiry_within_days": "90"}), "90")
		self.assertEqual(window({"expiry_within_days": 0}), 0)
		self.assertEqual(window({"expiry_within_days": ""}), 365)

	def test_normalise_line_status(self):
		self.assertEqual(reporting.normalise_line_status(None), "Active")
		self.assertEqual(reporting.normalise_line_status(""), "Active")
		self.assertEqual(reporting.normalise_line_status("Removed"), "Removed")
		self.assertIsNone(reporting.normalise_line_status("All"))


class TestLinesAndMeasures(unittest.TestCase):
	def test_open_line_requires_active_status_and_an_unclosed_range(self):
		self.assertTrue(reporting.is_open_line(line("R", "A"), TODAY))
		self.assertTrue(reporting.is_open_line(line("R", "A", effective_to="2026-10-18"), TODAY))
		self.assertFalse(reporting.is_open_line(line("R", "A", effective_to="2026-10-17"), TODAY))
		self.assertFalse(reporting.is_open_line(line("R", "A", status="Removed"), TODAY))
		self.assertTrue(reporting.is_open_line(line("R", "A", effective_from="2027-01-01"), TODAY))

	def test_open_items_by_registration_groups_newcodes_and_skips_closed_or_blank_lines(self):
		lines = [
			line("R1", "A", "N2"),
			line("R1", "A", "N1"),
			line("R1", "A", "N1"),
			line("R1", "B", None),
			line("R1", None, "N9"),
			line("R1", "C", "N3", status="Removed"),
			line("R1", "D", "N4", effective_to="2026-01-01"),
			line("R2", "A", "N5"),
		]
		self.assertEqual(
			reporting.open_items_by_registration(lines, TODAY),
			{"R1": {"A": ["N1", "N2"], "B": []}, "R2": {"A": ["N5"]}},
		)

	def test_item_measure_and_summary_are_null_safe_and_count_distinct_items(self):
		measures: ClassVar[dict] = {
			"stock": {"A": {"qty": 5, "value": 50.5}, "B": {"qty": None, "value": None}},
			"po": {"A": 2},
			"so": {"B": 3.5},
		}
		self.assertEqual(
			reporting.item_measure(measures, "A"),
			{"stock_qty": 5.0, "stock_value": 50.5, "open_po_qty": 2.0, "open_so_qty": 0.0},
		)
		self.assertEqual(reporting.item_measure({}, "X"), dict.fromkeys(reporting.MEASURE_FIELDS, 0.0))
		self.assertEqual(
			reporting.summarise_impact(["A", "B", "A", "NOPE"], measures),
			{
				"affected_items": 3,
				"stock_qty": 5.0,
				"stock_value": 50.5,
				"open_po_qty": 2.0,
				"open_so_qty": 3.5,
			},
		)
		self.assertEqual(reporting.summarise_impact([], measures)["affected_items"], 0)


class TestExpiryReportRows(unittest.TestCase):
	measures: ClassVar[dict] = {
		"stock": {"A": {"qty": 10, "value": 100}, "B": {"qty": 4, "value": 40}},
		"po": {"A": 1, "B": 2},
		"so": {"B": 7},
	}

	def test_summary_rows_total_distinct_items_and_sort_by_expiry(self):
		registrations = [
			registration("NO-EXP", basis="No Expiry Date Specified", expiry="2020-01-01"),
			registration("LATE", expiry="2027-06-01", certificate_type="CFS"),
			registration("SOON", expiry="2026-11-01", device_category="IVD", product_family_name="Fam"),
		]
		items = {"SOON": {"A": ["N1"], "B": []}, "LATE": {"A": []}}
		rows = reporting.build_expiry_summary_rows(registrations, items, self.measures, TODAY)
		self.assertEqual([row["registration"] for row in rows], ["SOON", "LATE", "NO-EXP"])
		soon, late, no_expiry = rows
		self.assertEqual(soon["days_remaining"], 14)
		self.assertEqual(soon["affected_items"], 2)
		self.assertEqual(
			(soon["stock_qty"], soon["stock_value"], soon["open_po_qty"], soon["open_so_qty"]),
			(14.0, 140.0, 3.0, 7.0),
		)
		self.assertEqual((soon["device_category"], soon["product_family_name"]), ("IVD", "Fam"))
		self.assertEqual((late["affected_items"], late["stock_qty"]), (1, 10.0))
		self.assertIsNone(no_expiry["expiry_date"])
		self.assertIsNone(no_expiry["days_remaining"])
		self.assertEqual(no_expiry["affected_items"], 0)
		self.assertEqual(no_expiry["stock_qty"], 0.0)

	def test_affected_business_rows_carry_each_items_own_measures(self):
		registrations = [registration("R1", expiry="2026-11-01", renewal_status="Preparing")]
		items = {"R1": {"A": ["N2", "N1"], "B": ["N3"]}}
		rows = reporting.build_affected_business_rows(
			registrations, items, {"A": "Alpha", "B": "Beta"}, self.measures, TODAY
		)
		self.assertEqual([row["item_code"] for row in rows], ["A", "B"])
		alpha, beta = rows
		self.assertEqual((alpha["stock_qty"], alpha["open_po_qty"], alpha["open_so_qty"]), (10.0, 1.0, 0.0))
		self.assertEqual((beta["stock_qty"], beta["open_po_qty"], beta["open_so_qty"]), (4.0, 2.0, 7.0))
		self.assertEqual((alpha["stock_value"], beta["stock_value"]), (100.0, 40.0))
		self.assertEqual((alpha["item_name"], alpha["newcode"]), ("Alpha", "N2, N1"))
		self.assertEqual((alpha["days_remaining"], alpha["renewal_status"]), (14, "Preparing"))
		self.assertEqual(alpha["responsible_user"], "owner@example.com")

	def test_affected_business_rows_sorted_by_expiry_then_registration_and_item(self):
		registrations = [
			registration("B", expiry="2026-12-01"),
			registration("A", expiry="2026-12-01"),
			registration("C", expiry="2026-11-01"),
		]
		items = {"A": {"Z": [], "Y": []}, "B": {"X": []}, "C": {"W": []}}
		rows = reporting.build_affected_business_rows(registrations, items, {}, {}, TODAY)
		self.assertEqual(
			[(row["registration"], row["item_code"]) for row in rows],
			[("C", "W"), ("A", "Y"), ("A", "Z"), ("B", "X")],
		)

	def test_registration_without_open_items_has_no_affected_business_rows(self):
		self.assertEqual(reporting.build_affected_business_rows([registration("R")], {}, {}, {}, TODAY), [])


class TestCoverageMatrix(unittest.TestCase):
	def test_line_in_force(self):
		def in_force(reg=None, **line_values):
			return reporting.line_in_force(line("R", "A", **line_values), reg or registration("R"), TODAY)

		self.assertTrue(in_force())
		self.assertFalse(in_force(status="Removed"))
		self.assertFalse(in_force(effective_from="2026-11-01"))
		self.assertFalse(in_force(effective_to="2026-10-17"))
		self.assertTrue(in_force(effective_to="2026-10-18"))
		self.assertFalse(in_force(registration("R", status="Suspended")))
		self.assertFalse(in_force(registration("R", expiry="2026-10-17")))
		self.assertTrue(in_force(registration("R", basis="No Expiry Date Specified")))

	def test_one_row_per_line_with_item_stock_from_the_grouped_dataset(self):
		registrations = {
			"R1": registration("R1"),
			"R2": registration("R2", status="Expired", expiry="2026-01-01"),
		}
		lines = [
			line("R1", "B", "N-B1"),
			line("R1", "A", "N-A1", identifier="ID-1"),
			line("R1", "A", "N-A2"),
			line("R2", "A", "N-A0", status="Superseded"),
			line("R1", None, "N-X", item_name_at_registration="Orphan"),
			line("MISSING", "A", "N-LOST"),
		]
		stock = {"A": {"qty": 12.5, "value": 1}, "B": {"qty": 0, "value": 0}}
		rows = reporting.build_coverage_matrix_rows(lines, registrations, {"A": "Alpha"}, stock, TODAY)
		self.assertEqual(len(rows), 5)
		self.assertEqual(
			[(row["item_code"], row["registration"], row["newcode"]) for row in rows],
			[
				("A", "R1", "N-A1"),
				("A", "R1", "N-A2"),
				("A", "R2", "N-A0"),
				("B", "R1", "N-B1"),
				(None, "R1", "N-X"),
			],
		)
		first = rows[0]
		self.assertEqual((first["item_name"], first["identifier"]), ("Alpha", "ID-1"))
		self.assertEqual(first["stock_qty"], 12.5)
		self.assertEqual((first["official_status"], first["in_force"]), ("Active", 1))
		third = rows[2]
		self.assertEqual(
			(third["official_status"], third["line_status"], third["in_force"]), ("Expired", "Superseded", 0)
		)
		self.assertEqual(rows[4]["item_name"], "Orphan")
		self.assertEqual(rows[4]["stock_qty"], 0.0)

	def test_only_with_stock_hides_items_without_stock_and_blank_items(self):
		registrations = {"R1": registration("R1")}
		lines = [line("R1", "A"), line("R1", "B"), line("R1", None), line("R1", "C")]
		stock = {"A": {"qty": 3}, "B": {"qty": 0}, "C": {"qty": -2}}
		rows = reporting.build_coverage_matrix_rows(
			lines, registrations, {}, stock, TODAY, only_with_stock=True
		)
		self.assertEqual([row["item_code"] for row in rows], ["A"])


class TestGapDetectors(unittest.TestCase):
	def test_required_item_without_coverage(self):
		registrations = [
			registration("OK"),
			registration("LAPSED", expiry="2026-09-01"),
			registration("SUSP", status="Suspended"),
			registration("DRAFT", status="Draft"),
			registration("OLD", status="Expired", expiry="2026-06-30"),
			registration("NEW", expiry="2028-01-01", issue_date="2026-07-01"),
			registration("TWIN-1"),
			registration("TWIN-2"),
		]
		lines = [
			line("OK", "I-OK"),
			line("LAPSED", "I-LAPSED"),
			line("SUSP", "I-SUSP"),
			line("DRAFT", "I-DRAFT"),
			line("OLD", "I-RENEWED", effective_to="2026-06-30"),
			line("NEW", "I-RENEWED", effective_from="2026-07-01"),
			line("OK", "I-REMOVED", status="Removed"),
			line("OK", "I-FUTURE", effective_from="2027-01-01"),
			line("TWIN-1", "I-TWIN"),
			line("TWIN-2", "I-TWIN"),
			line("OK", "I-EXEMPT"),
		]
		items = [
			item("I-OK"),
			item("I-LAPSED"),
			item("I-SUSP"),
			item("I-DRAFT"),
			item("I-RENEWED"),
			item("I-REMOVED"),
			item("I-FUTURE"),
			item("I-NONE", regulatory_owner="item.owner@example.com"),
			item("I-TWIN"),
			item("I-EXEMPT", EXEMPT, "ok"),
			item("I-NA", NOT_ASSESSED),
		]
		gaps = reporting.detect_required_items_without_coverage(
			items, registrations, lines, TODAY, default_owner="default@example.com"
		)
		by_item = {gap["subject"]: gap for gap in gaps}
		self.assertEqual(
			set(by_item), {"I-LAPSED", "I-SUSP", "I-DRAFT", "I-REMOVED", "I-FUTURE", "I-NONE", "I-TWIN"}
		)
		self.assertTrue(all(gap["gap_type"] == reporting.GAP_REQUIRED_ITEM for gap in gaps))
		self.assertTrue(all(gap["severity"] == "Critical" for gap in gaps))
		self.assertTrue(all(gap["subject_type"] == "Item" for gap in gaps))
		self.assertIn("expired on 2026-09-01", by_item["I-LAPSED"]["detail"])
		self.assertEqual(by_item["I-LAPSED"]["registration"], "LAPSED")
		self.assertIn("is Suspended", by_item["I-SUSP"]["detail"])
		self.assertIn("No registered product line", by_item["I-DRAFT"]["detail"])
		self.assertIsNone(by_item["I-DRAFT"]["registration"])
		self.assertIn("TWIN-1", by_item["I-TWIN"]["detail"])
		self.assertIn("TWIN-2", by_item["I-TWIN"]["detail"])
		self.assertEqual(by_item["I-NONE"]["owner"], "item.owner@example.com")
		self.assertEqual(by_item["I-NONE"]["item_name"], "I-NONE name")
		self.assertEqual(by_item["I-LAPSED"]["owner"], "default@example.com")

	def test_ambiguity_is_reported_not_raised(self):
		registrations = [registration("A"), registration("B")]
		lines = [line("A", "I"), line("B", "I")]
		mapping, ambiguous = reporting.resolve_item_mapping(
			reporting.build_mapping_candidates(lines, {row.name: row for row in registrations}, TODAY)["I"],
			TODAY,
		)
		self.assertEqual(sorted(ambiguous), ["A", "B"])
		self.assertIn(mapping["registration"], ("A", "B"))

	def test_resolve_prefers_active_and_falls_back_to_last_expired(self):
		def candidate(name, status, expiry):
			return {
				"registration": name,
				"official_status": status,
				"issue_date": "2020-01-01",
				"expiry_basis": "Expiry Date Specified",
				"expiry_date": expiry,
			}

		mapping, ambiguous = reporting.resolve_item_mapping(
			[candidate("SUP", "Superseded", "2027-01-01"), candidate("ACT", "Active", "2027-01-01")], TODAY
		)
		self.assertEqual((mapping["registration"], ambiguous), ("ACT", []))
		mapping, ambiguous = reporting.resolve_item_mapping(
			[candidate("E1", "Expired", "2026-01-01"), candidate("E2", "Expired", "2026-06-01")], TODAY
		)
		self.assertEqual((mapping["registration"], ambiguous), ("E2", []))
		self.assertEqual(reporting.resolve_item_mapping([], TODAY), (None, []))

	def test_registrations_without_valid_certificate(self):
		registrations = [
			registration("GOOD"),
			registration("NO-EXPIRY-CERT"),
			registration("TODAY-CERT"),
			registration("EXPIRED-CERT"),
			registration("PENDING-CERT"),
			registration("NO-DOCS"),
			registration("OTHER-DOC-ONLY"),
			registration("INACTIVE", status="Suspended"),
		]

		def certificate(status="Verified", expiry=None, document_type="Certificate"):
			return {"document_type": document_type, "verification_status": status, "expiry_date": expiry}

		documents = {
			"GOOD": [certificate(expiry="2027-05-01")],
			"NO-EXPIRY-CERT": [certificate()],
			"TODAY-CERT": [certificate(expiry="2026-10-18")],
			"EXPIRED-CERT": [certificate(expiry="2026-10-17")],
			"PENDING-CERT": [certificate("Pending")],
			"OTHER-DOC-ONLY": [certificate(document_type="IFU")],
		}
		gaps = reporting.detect_registrations_without_certificate(
			registrations, documents, TODAY, "def@example.com"
		)
		by_registration = {gap["subject"]: gap for gap in gaps}
		self.assertEqual(set(by_registration), {"EXPIRED-CERT", "PENDING-CERT", "NO-DOCS", "OTHER-DOC-ONLY"})
		self.assertIn("none Verified and unexpired", by_registration["EXPIRED-CERT"]["detail"])
		self.assertIn("No Certificate document", by_registration["NO-DOCS"]["detail"])
		self.assertIn("No Certificate document", by_registration["OTHER-DOC-ONLY"]["detail"])
		first = by_registration["NO-DOCS"]
		self.assertEqual(first["gap_type"], reporting.GAP_NO_CERTIFICATE)
		self.assertEqual(first["subject_type"], "Regulatory Product Registration")
		self.assertEqual((first["severity"], first["owner"]), ("Critical", "owner@example.com"))

	def test_unconfirmed_expiry_only_flags_active_registrations(self):
		registrations = [
			registration("A", basis="Unconfirmed", expiry=None),
			registration("B", basis="Unconfirmed", expiry=None, status="Draft"),
			registration("C"),
			registration("D", basis="No Expiry Date Specified"),
			registration("E", basis="Unconfirmed", expiry=None, responsible_user=None),
		]
		gaps = reporting.detect_unconfirmed_expiry(registrations, "def@example.com")
		self.assertEqual([gap["subject"] for gap in gaps], ["A", "E"])
		self.assertEqual({gap["severity"] for gap in gaps}, {"Warning"})
		self.assertEqual([gap["owner"] for gap in gaps], ["owner@example.com", "def@example.com"])

	def test_expired_registration_with_stock(self):
		registrations = [
			registration("EXP", status="Expired", expiry="2026-09-01"),
			registration("PASSED", expiry="2026-10-17"),
			registration("FUTURE", expiry="2026-10-18"),
			registration("NOEXP", status="Expired", basis="No Expiry Date Specified"),
			registration("SUP", status="Superseded", expiry="2026-01-01"),
		]
		lines = [
			line("EXP", "A"),
			line("EXP", "B"),
			line("EXP", "C"),
			line("EXP", "CLOSED", effective_to="2026-09-02"),
			line("EXP", "REMOVED", status="Removed"),
			line("PASSED", "D"),
			line("FUTURE", "E"),
			line("NOEXP", "F"),
			line("SUP", "G"),
		]
		stock = {
			"A": {"qty": 5},
			"B": {"qty": 0},
			"C": {"qty": -1},
			"CLOSED": {"qty": 9},
			"REMOVED": {"qty": 9},
			"D": {"qty": 2.5},
			"E": {"qty": 9},
			"F": {"qty": 1},
			"G": {"qty": 9},
		}
		gaps = reporting.detect_expired_registrations_with_stock(
			registrations, lines, stock, {"A": "Alpha"}, TODAY
		)
		self.assertEqual(
			[(gap["registration"], gap["item"]) for gap in gaps],
			[("EXP", "A"), ("PASSED", "D"), ("NOEXP", "F")],
		)
		first = gaps[0]
		self.assertEqual(first["gap_type"], reporting.GAP_EXPIRED_WITH_STOCK)
		self.assertEqual(first["severity"], "Critical")
		self.assertEqual((first["subject"], first["item_name"]), ("EXP", "Alpha"))
		self.assertIn("expired on 2026-09-01", first["detail"])
		self.assertIn("5.0 in stock", first["detail"])
		self.assertIn("Registration is Expired", gaps[2]["detail"])
		self.assertEqual(reporting.lapsed_item_codes(registrations, lines, TODAY), {"A", "B", "C", "D", "F"})

	def test_mapped_line_without_item(self):
		registrations = [
			registration("ACT"),
			registration("SUSP", status="Suspended"),
			registration("EXPD", status="Expired"),
			registration("DRAFT", status="Draft"),
			registration("CANC", status="Cancelled"),
		]
		lines = [
			line("ACT", None, "N-1"),
			line("ACT", "", None, identifier="MODEL-9"),
			line("ACT", "I", "N-2"),
			line("ACT", None, "N-3", status="Removed"),
			line("SUSP", None, None, product_name="Widget"),
			line("EXPD", None, None, idx=4),
			line("DRAFT", None, "N-4"),
			line("CANC", None, "N-5"),
		]
		gaps = reporting.detect_lines_without_item(registrations, lines)
		self.assertEqual([gap["subject"] for gap in gaps], ["ACT", "ACT", "SUSP", "EXPD"])
		self.assertIn("N-1", gaps[0]["detail"])
		self.assertIn("MODEL-9", gaps[1]["detail"])
		self.assertIn("Widget", gaps[2]["detail"])
		self.assertIn("line 4", gaps[3]["detail"])
		self.assertEqual({gap["severity"] for gap in gaps}, {"Info"})
		self.assertEqual({gap["gap_type"] for gap in gaps}, {reporting.GAP_LINE_WITHOUT_ITEM})

	def test_establishment_not_active(self):
		def establishment(name, status="Active", expiry="2027-01-01", basis="Expiry Date Specified"):
			return frappe._dict(
				name=name,
				official_status=status,
				issue_date="2025-01-01",
				expiry_basis=basis,
				expiry_date=expiry,
			)

		establishments = [
			establishment("EST-OK"),
			establishment("EST-SUSP", status="Suspended"),
			establishment("EST-LAPSED", expiry="2026-10-17"),
			establishment("EST-TODAY", expiry="2026-10-18"),
			establishment("EST-NOEXP", expiry="2020-01-01", basis="No Expiry Date Specified"),
		]
		registrations = [
			registration("R-OK", establishment_registration="EST-OK"),
			registration("R-SUSP", establishment_registration="EST-SUSP"),
			registration("R-LAPSED", establishment_registration="EST-LAPSED"),
			registration("R-TODAY", establishment_registration="EST-TODAY"),
			registration("R-NOEXP", establishment_registration="EST-NOEXP"),
			registration("R-UNKNOWN", establishment_registration="EST-GONE"),
			registration("R-BLANK", establishment_registration=None),
			registration("R-INACTIVE", status="Draft", establishment_registration="EST-SUSP"),
		]
		gaps = reporting.detect_establishments_not_active(registrations, establishments, TODAY)
		self.assertEqual([gap["subject"] for gap in gaps], ["R-SUSP", "R-LAPSED"])
		self.assertIn("EST-SUSP is Suspended", gaps[0]["detail"])
		self.assertIn("EST-LAPSED expired on 2026-10-17", gaps[1]["detail"])
		self.assertEqual({gap["severity"] for gap in gaps}, {"Critical"})

	def test_supporting_document_expired(self):
		def document(name, document_type="LOA", status="Verified", expiry="2026-10-17", newcode=None):
			return {
				"name": name,
				"document_type": document_type,
				"document_number": f"{name}-NO",
				"applicable_newcode": newcode,
				"verification_status": status,
				"expiry_date": expiry,
			}

		registrations = [registration("R"), registration("INACTIVE", status="Revoked")]
		documents = {
			"R": [
				document("D-EXPIRED"),
				document("D-STATUS-EXPIRED", "ISO", "Expired"),
				document("D-CERT", "Certificate"),
				document("D-TODAY", "Annex", expiry="2026-10-18"),
				document("D-NO-EXPIRY", "Declaration of Conformity", expiry=None),
				document("D-PENDING", "GMP", "Pending"),
				document("D-OLD-IFU", "IFU", expiry="2026-01-01"),
				document("D-NEW-IFU", "IFU", expiry="2028-01-01"),
				document("D-OLD-LABEL", "Label", expiry="2026-01-01", newcode="N-1"),
				document("D-NEW-LABEL", "Label", expiry="2028-01-01", newcode="N-2"),
			],
			"INACTIVE": [document("D-INACTIVE")],
		}
		gaps = reporting.detect_expired_supporting_documents([*registrations], documents, TODAY)
		self.assertEqual([gap["subject"] for gap in gaps], ["D-EXPIRED", "D-STATUS-EXPIRED", "D-OLD-LABEL"])
		first = gaps[0]
		self.assertEqual(first["subject_type"], "Regulatory Document")
		self.assertEqual((first["gap_type"], first["severity"]), (reporting.GAP_DOCUMENT_EXPIRED, "Warning"))
		self.assertEqual((first["registration"], first["owner"]), ("R", "owner@example.com"))
		self.assertIn("LOA D-EXPIRED-NO of registration R expired on 2026-10-17", first["detail"])

	def test_items_not_assessed(self):
		items = [
			item("NA", NOT_ASSESSED, regulatory_product_category="Drug", regulatory_owner="o@example.com"),
			item("BLANK", None),
			item("REQ", REQUIRED),
			item("EXE", EXEMPT, "why"),
		]
		gaps = reporting.detect_items_not_assessed(items, "def@example.com")
		self.assertEqual([gap["subject"] for gap in gaps], ["NA", "BLANK"])
		self.assertEqual([gap["owner"] for gap in gaps], ["o@example.com", "def@example.com"])
		self.assertEqual([gap["product_category"] for gap in gaps], ["Drug", None])
		self.assertEqual({gap["gap_type"] for gap in gaps}, {reporting.GAP_NOT_ASSESSED})
		self.assertEqual({gap["severity"] for gap in gaps}, {"Info"})
		self.assertEqual({gap["subject_type"] for gap in gaps}, {"Item"})

	def test_required_gap_ignores_other_statuses_and_carries_the_category(self):
		registrations = [registration("OK")]
		lines = [line("OK", "COVERED")]
		items = [
			item("COVERED"),
			item("MISSING", regulatory_product_category="Cosmetic"),
			item("NA", NOT_ASSESSED),
			item("EXE", EXEMPT, "why"),
		]
		gaps = reporting.detect_required_items_without_coverage(items, registrations, lines, TODAY)
		self.assertEqual(
			[(gap["subject"], gap["product_category"]) for gap in gaps], [("MISSING", "Cosmetic")]
		)

	def test_exempt_item_without_reason(self):
		items = [
			item("E-BLANK", EXEMPT),
			item("E-SPACES", EXEMPT, "   ", regulatory_owner="o@example.com"),
			item("E-OK", EXEMPT, "Class I accessory"),
			item("REQ", REQUIRED),
			item("NA", NOT_ASSESSED),
		]
		gaps = reporting.detect_exempt_items_without_reason(items, "def@example.com")
		self.assertEqual([gap["subject"] for gap in gaps], ["E-BLANK", "E-SPACES"])
		self.assertEqual([gap["owner"] for gap in gaps], ["def@example.com", "o@example.com"])
		self.assertEqual({gap["gap_type"] for gap in gaps}, {reporting.GAP_EXEMPT_NO_REASON})
		self.assertEqual({gap["severity"] for gap in gaps}, {"Warning"})

	def test_detectors_never_query_the_database(self):
		class ForbiddenDatabase:
			def __getattr__(self, name):
				raise AssertionError(f"detectors must not touch the database ({name})")

		registrations = [registration("R", basis="Unconfirmed", expiry=None)]
		with (
			patch.object(frappe, "db", new=ForbiddenDatabase()),
			patch.object(frappe, "get_list", side_effect=AssertionError("get_list")),
			patch.object(frappe, "get_all", side_effect=AssertionError("get_all")),
		):
			gaps = reporting.build_gaps(
				{"registrations": registrations, "items": [item("I")]}, list(reporting.GAP_SEVERITY), TODAY
			)
		self.assertEqual(
			{gap["gap_type"] for gap in gaps},
			{reporting.GAP_NO_CERTIFICATE, reporting.GAP_UNCONFIRMED_EXPIRY, reporting.GAP_REQUIRED_ITEM},
		)


class TestGapAssembly(unittest.TestCase):
	def sample_data(self):
		return {
			"items": [item("I-NONE"), item("I-EXEMPT", EXEMPT)],
			"items_not_assessed": [item("I-NA", NOT_ASSESSED, regulatory_product_category="Food")],
			"registrations": [
				registration("R-UNC", basis="Unconfirmed", expiry=None, product_category="Drug")
			],
			"lines": [line("R-UNC", "I-A")],
			"documents": [],
			"default_owner": "def@example.com",
		}

	def test_build_gaps_runs_only_selected_types(self):
		data = self.sample_data()
		only_exempt = reporting.build_gaps(data, [reporting.GAP_EXEMPT_NO_REASON], TODAY)
		self.assertEqual([gap["subject"] for gap in only_exempt], ["I-EXEMPT"])
		everything = reporting.build_gaps(data, list(reporting.GAP_SEVERITY), TODAY)
		self.assertEqual(
			{gap["gap_type"] for gap in everything},
			{
				reporting.GAP_REQUIRED_ITEM,
				reporting.GAP_NO_CERTIFICATE,
				reporting.GAP_UNCONFIRMED_EXPIRY,
				reporting.GAP_EXEMPT_NO_REASON,
				reporting.GAP_NOT_ASSESSED,
			},
		)

	def test_sort_gaps_orders_by_severity_then_gap_type_then_subject(self):
		gaps = reporting.build_gaps(self.sample_data(), list(reporting.GAP_SEVERITY), TODAY)
		ordered = reporting.sort_gaps(list(reversed(gaps)))
		self.assertEqual(
			[gap["severity"] for gap in ordered], ["Critical", "Critical", "Warning", "Warning", "Info"]
		)
		self.assertEqual(
			[gap["gap_type"] for gap in ordered],
			[
				reporting.GAP_REQUIRED_ITEM,
				reporting.GAP_NO_CERTIFICATE,
				reporting.GAP_UNCONFIRMED_EXPIRY,
				reporting.GAP_EXEMPT_NO_REASON,
				reporting.GAP_NOT_ASSESSED,
			],
		)

	def test_severity_catalogue(self):
		self.assertEqual(set(reporting.GAP_SEVERITY.values()), {"Critical", "Warning", "Info"})
		self.assertEqual(len(reporting.GAP_SEVERITY), 9)
		self.assertEqual(reporting.GAP_SEVERITY[reporting.GAP_NOT_ASSESSED], "Info")
		self.assertEqual(set(reporting.GAP_DATASETS), set(reporting.GAP_SEVERITY))

	def test_gap_filters(self):
		gaps = reporting.build_gaps(self.sample_data(), list(reporting.GAP_SEVERITY), TODAY)
		reporting.attach_registration_items(gaps, self.sample_data()["lines"], TODAY)

		def subjects(**filters):
			cleaned = reporting.clean_filters(filters)
			return sorted(gap["subject"] for gap in gaps if reporting.gap_matches_filters(gap, cleaned))

		self.assertEqual(subjects(), ["I-EXEMPT", "I-NA", "I-NONE", "R-UNC", "R-UNC"])
		self.assertEqual(subjects(severity="Info"), ["I-NA"])
		self.assertEqual(subjects(gap_type=reporting.GAP_NOT_ASSESSED), ["I-NA"])
		self.assertEqual(subjects(product_category="Food"), ["I-NA"])
		self.assertEqual(subjects(product_category="Drug"), ["R-UNC", "R-UNC"])
		self.assertEqual(subjects(product_category="Cosmetic"), [])
		self.assertEqual(subjects(severity="Warning"), ["I-EXEMPT", "R-UNC"])
		self.assertEqual(subjects(gap_type=reporting.GAP_EXEMPT_NO_REASON), ["I-EXEMPT"])
		self.assertEqual(subjects(registration="R-UNC"), ["R-UNC", "R-UNC"])
		self.assertEqual(subjects(item="I-NONE"), ["I-NONE"])
		self.assertEqual(subjects(item="I-A"), ["R-UNC", "R-UNC"])
		self.assertEqual(subjects(responsible_user="owner@example.com"), ["R-UNC", "R-UNC"])
		self.assertEqual(subjects(responsible_user="def@example.com"), ["I-EXEMPT", "I-NA", "I-NONE"])
		self.assertEqual(subjects(responsible_user="nobody@example.com"), [])
		self.assertEqual(subjects(item="UNKNOWN"), [])

	def test_dataset_selection_loads_unassessed_items_only_for_their_gap(self):
		self.assertEqual(
			reporting.required_datasets([reporting.GAP_NOT_ASSESSED], frappe._dict()), {"items_not_assessed"}
		)
		for gap_type in reporting.GAP_SEVERITY:
			if gap_type != reporting.GAP_NOT_ASSESSED:
				self.assertNotIn("items_not_assessed", reporting.GAP_DATASETS[gap_type], gap_type)
		default = reporting.required_datasets(reporting.selected_gap_types(frappe._dict()), frappe._dict())
		self.assertNotIn("items_not_assessed", default)
		explicit = reporting.selected_gap_types(frappe._dict(gap_type=reporting.GAP_NOT_ASSESSED))
		self.assertEqual(explicit, [reporting.GAP_NOT_ASSESSED])

	def test_default_run_excludes_the_not_assessed_gap_but_it_stays_selectable(self):
		default = reporting.selected_gap_types(frappe._dict())
		self.assertNotIn(reporting.GAP_NOT_ASSESSED, default)
		self.assertEqual(len(default), len(reporting.GAP_SEVERITY) - 1)
		data = self.sample_data()
		gaps = reporting.build_gaps(data, default, TODAY)
		self.assertNotIn(reporting.GAP_NOT_ASSESSED, {gap["gap_type"] for gap in gaps})
		picked = reporting.build_gaps(data, [reporting.GAP_NOT_ASSESSED], TODAY)
		self.assertEqual([gap["subject"] for gap in picked], ["I-NA"])
		cleaned = reporting.clean_filters(
			{"gap_type": reporting.GAP_NOT_ASSESSED, "product_category": "Food"}
		)
		self.assertEqual(
			[g["subject"] for g in picked if reporting.gap_matches_filters(g, cleaned)], ["I-NA"]
		)
		cleaned = reporting.clean_filters(
			{"gap_type": reporting.GAP_NOT_ASSESSED, "product_category": "Drug"}
		)
		self.assertEqual([g for g in picked if reporting.gap_matches_filters(g, cleaned)], [])

	def test_dataset_selection(self):
		self.assertEqual(
			reporting.required_datasets([reporting.GAP_EXEMPT_NO_REASON], frappe._dict()), {"items"}
		)
		self.assertEqual(
			reporting.required_datasets([reporting.GAP_UNCONFIRMED_EXPIRY], frappe._dict(item="I-A")),
			{"registrations", "lines"},
		)
		self.assertEqual(
			reporting.selected_gap_types(frappe._dict(gap_type="Nope")),
			reporting.selected_gap_types(frappe._dict()),
		)
		self.assertEqual(
			reporting.selected_gap_types(frappe._dict(gap_type=reporting.GAP_ESTABLISHMENT)),
			[reporting.GAP_ESTABLISHMENT],
		)


class TestGapLoader(unittest.TestCase):
	def load(self, datasets):
		with (
			patch.object(frappe, "get_all", return_value=[]) as get_all,
			patch.object(frappe, "db", MagicMock(**{"get_single_value.return_value": None})),
		):
			data = reporting.load_gap_data(datasets, TODAY)
		return data, get_all

	def test_classified_items_are_fetched_with_filters_and_only_needed_columns(self):
		data, get_all = self.load({"items"})
		self.assertEqual(data["items"], [])
		self.assertNotIn("items_not_assessed", data)
		get_all.assert_called_once()
		self.assertEqual(get_all.call_args.args[0], "Item")
		kwargs = get_all.call_args.kwargs
		self.assertEqual(
			kwargs["filters"],
			{"disabled": 0, "custom_thai_fda_applicability": ["in", [REQUIRED, EXEMPT]]},
		)
		self.assertEqual(
			set(kwargs["fields"]),
			{
				"name",
				"item_name",
				"custom_thai_fda_applicability",
				"custom_thai_fda_assessment_note",
				"regulatory_product_category",
				"regulatory_owner",
			},
		)

	def test_unassessed_items_are_loaded_only_when_that_dataset_is_requested(self):
		data, get_all = self.load({"items_not_assessed"})
		self.assertIn("items_not_assessed", data)
		self.assertNotIn("items", data)
		kwargs = get_all.call_args.kwargs
		self.assertEqual(kwargs["filters"], {"disabled": 0})
		self.assertIn(["custom_thai_fda_applicability", "is", "not set"], kwargs["or_filters"])
		self.assertIn(["custom_thai_fda_applicability", "=", NOT_ASSESSED], kwargs["or_filters"])
		none_loaded, get_all = self.load({"documents"})
		get_all.assert_not_called()
		self.assertNotIn("items_not_assessed", none_loaded)


class TestRenewalWorkbench(unittest.TestCase):
	def test_product_category_filter_keeps_only_matching_registrations(self):
		actions = [
			{"name": "A-DRUG", "registration": "R-DRUG", "current_stage": "Preparing"},
			{"name": "A-FOOD", "registration": "R-FOOD", "current_stage": "Preparing"},
			{"name": "A-GONE", "registration": "R-GONE", "current_stage": "Preparing"},
		]
		registrations = {
			"R-DRUG": registration("R-DRUG", product_category="Drug"),
			"R-FOOD": registration("R-FOOD", product_category="Food"),
		}
		everything = reporting.build_workbench_rows(actions, registrations, TODAY)
		self.assertEqual({row["action"] for row in everything}, {"A-DRUG", "A-FOOD", "A-GONE"})
		self.assertEqual(
			{row["action"]: row["product_category"] for row in everything},
			{"A-DRUG": "Drug", "A-FOOD": "Food", "A-GONE": None},
		)
		drugs = reporting.build_workbench_rows(actions, registrations, TODAY, "Drug")
		self.assertEqual([row["action"] for row in drugs], ["A-DRUG"])

	def test_default_filters(self):
		self.assertEqual(
			reporting.workbench_action_filters(frappe._dict()),
			{"action_type": "Renewal", "current_stage": ["not in", ["Approved", "Rejected", "Cancelled"]]},
		)
		self.assertEqual(reporting.workbench_action_filters(frappe._dict(open_only=0, action_type="All")), {})
		self.assertEqual(
			reporting.workbench_action_filters(
				frappe._dict(
					open_only=1, current_stage="Approved", assigned_to="u@x.com", action_type="Variation"
				)
			),
			{"action_type": "Variation", "assigned_to": "u@x.com", "current_stage": "Approved"},
		)

	def test_rows_days_and_sort_order(self):
		registrations = {
			"R-SOON": registration("R-SOON", expiry="2026-11-01"),
			"R-LATE": registration("R-LATE", expiry="2027-03-01"),
			"R-NONE": registration("R-NONE", basis="No Expiry Date Specified", expiry=None),
		}
		actions = [
			{
				"name": "ACT-1",
				"registration": "R-NONE",
				"current_stage": "Preparing",
				"modified": datetime(2026, 10, 1),
			},
			{
				"name": "ACT-2",
				"registration": "R-LATE",
				"current_stage": "FDA Query",
				"response_due_date": "2026-10-10",
				"modified": datetime(2026, 10, 2),
			},
			{
				"name": "ACT-3",
				"registration": "R-SOON",
				"current_stage": "FDA Query",
				"response_due_date": "2026-10-25",
				"fda_application_number": "FDA-1",
				"new_expiry_date": "2031-11-01",
				"modified": datetime(2026, 9, 1),
			},
			{
				"name": "ACT-4",
				"registration": "R-SOON",
				"current_stage": "Submitted",
				"modified": datetime(2026, 10, 5),
			},
			{
				"name": "ACT-5",
				"registration": "R-GONE",
				"current_stage": "Approved",
				"response_due_date": "2026-01-01",
				"modified": datetime(2026, 10, 9),
			},
		]
		rows = reporting.build_workbench_rows(actions, registrations, TODAY)
		self.assertEqual([row["action"] for row in rows], ["ACT-4", "ACT-3", "ACT-2", "ACT-5", "ACT-1"])
		by_action = {row["action"]: row for row in rows}
		self.assertEqual(by_action["ACT-3"]["days_to_expiry"], 14)
		self.assertEqual(by_action["ACT-3"]["days_to_query_due"], 7)
		self.assertEqual(by_action["ACT-3"]["fda_application_number"], "FDA-1")
		self.assertEqual(by_action["ACT-2"]["days_to_query_due"], -8)
		self.assertIsNone(by_action["ACT-4"]["days_to_query_due"])
		self.assertIsNone(by_action["ACT-5"]["days_to_query_due"])
		self.assertIsNone(by_action["ACT-5"]["expiry_date"])
		self.assertIsNone(by_action["ACT-1"]["days_to_expiry"])


class TestReportDefinitions(unittest.TestCase):
	def load(self, folder):
		path = REPORT_DIR / folder / f"{folder}.json"
		return json.loads(path.read_text())

	def test_report_json_is_a_standard_script_report(self):
		for folder, (name, ref_doctype, _module) in REPORTS.items():
			with self.subTest(report=name):
				definition = self.load(folder)
				self.assertEqual(definition["doctype"], "Report")
				self.assertEqual(definition["name"], name)
				self.assertEqual(definition["report_name"], name)
				self.assertEqual(definition["ref_doctype"], ref_doctype)
				self.assertEqual(definition["module"], "Regulatory Affairs")
				self.assertEqual(definition["report_type"], "Script Report")
				self.assertEqual(definition["is_standard"], "Yes")
				self.assertEqual(definition["add_total_row"], 0)
				self.assertEqual(definition["prepared_report"], 0)
				self.assertEqual({row["role"] for row in definition["roles"]}, ROLES)
				self.assertEqual(len(definition["roles"]), len(ROLES))
				self.assertTrue((REPORT_DIR / folder / "__init__.py").exists())
				self.assertEqual(frappe.scrub(name), folder)

	def test_filter_definitions_are_well_formed(self):
		for folder, (name, _doctype, _module) in REPORTS.items():
			definition = self.load(folder)
			names = [row["fieldname"] for row in definition["filters"]]
			with self.subTest(report=name):
				self.assertEqual(len(names), len(set(names)))
				for row in definition["filters"]:
					self.assertTrue(row["label"])
					self.assertIn(row["fieldtype"], ("Data", "Link", "Select", "Int", "Check", "Date"))
					if row["fieldtype"] in ("Link", "Select"):
						self.assertTrue(row.get("options"))
					if row["fieldtype"] == "Select" and row.get("default"):
						self.assertIn(row["default"], row["options"].split("\n"))

	@staticmethod
	def filter_names(source):
		names = set(re.findall(r'filters(?:\.get\(|\[)\s*"(\w+)"', source))
		names |= set(re.findall(r'int_filter\(\s*filters,\s*"(\w+)"', source))
		return names

	def test_every_filter_used_in_python_exists_in_json(self):
		exclusive_helpers = {
			"regulatory_expiring_registration_affected_business": [reporting.affected_business_filters],
			"regulatory_compliance_gap": [
				reporting.selected_gap_types,
				reporting.required_datasets,
				reporting.gap_matches_filters,
			],
			"regulatory_renewal_workbench": [reporting.workbench_action_filters],
		}
		helper_source = inspect.getsource(reporting.registration_query_filters)
		registration_filter_names = self.filter_names(helper_source)
		registration_filter_names |= set(
			re.findall(r'"(\w+)"', re.search(r"for fieldname in \(([^)]*)\)", helper_source).group(1))
		)
		self.assertTrue({"responsible_user", "device_category"} <= registration_filter_names)
		self.assertTrue({"official_status", "registration"} <= registration_filter_names)
		uses_registration_filters = {
			"regulatory_registration_expiry_summary",
			"regulatory_expiring_registration_affected_business",
			"regulatory_item_coverage_matrix",
		}
		for folder, (name, _doctype, module) in REPORTS.items():
			json_names = {row["fieldname"] for row in self.load(folder)["filters"]}
			source = inspect.getsource(module) + "".join(
				inspect.getsource(helper) for helper in exclusive_helpers.get(folder, [])
			)
			used = self.filter_names(source)
			if folder in uses_registration_filters:
				self.assertIn("registration_query_filters", source)
				used |= registration_filter_names & json_names
			with self.subTest(report=name):
				self.assertTrue(used, "the report reads no filters")
				self.assertEqual(used - json_names, set(), "filter used in Python but missing in JSON")
				self.assertEqual(json_names - used, set(), "filter declared in JSON but never read")

	def test_product_category_filter_is_offered_where_it_makes_sense(self):
		expected = {
			"regulatory_registration_expiry_summary": True,
			"regulatory_expiring_registration_affected_business": True,
			"regulatory_compliance_gap": True,
			"regulatory_renewal_workbench": True,
			"regulatory_item_coverage_matrix": False,
		}
		for folder, offered in expected.items():
			filters = {row["fieldname"]: row for row in self.load(folder)["filters"]}
			self.assertEqual("product_category" in filters, offered, folder)
			if offered:
				self.assertEqual(
					filters["product_category"]["options"].split("\n"), ["", *constants.PRODUCT_CATEGORIES]
				)

	def test_gap_type_options_match_the_detector_catalogue(self):
		definition = self.load("regulatory_compliance_gap")
		options = {row["fieldname"]: row for row in definition["filters"]}
		self.assertEqual(options["gap_type"]["options"].split("\n"), ["", *reporting.GAP_SEVERITY])
		self.assertEqual(options["severity"]["options"].split("\n"), ["", "Critical", "Warning", "Info"])

	def test_default_filters_in_json_match_python_defaults(self):
		def filters_of(folder):
			return {row["fieldname"]: row for row in self.load(folder)["filters"]}

		affected = filters_of("regulatory_expiring_registration_affected_business")
		self.assertEqual(affected["expiry_within_days"]["default"], "365")
		matrix = filters_of("regulatory_item_coverage_matrix")
		self.assertEqual(matrix["line_status"]["default"], "Active")
		workbench = filters_of("regulatory_renewal_workbench")
		self.assertEqual(workbench["action_type"]["default"], "Renewal")
		self.assertEqual(workbench["open_only"]["default"], "1")

	def test_columns_cover_the_row_keys_and_links_are_typed(self):
		sample_rows = {
			"regulatory_registration_expiry_summary": reporting.build_expiry_summary_rows(
				[registration("R")], {"R": {"A": []}}, {}, TODAY
			),
			"regulatory_expiring_registration_affected_business": reporting.build_affected_business_rows(
				[registration("R")], {"R": {"A": ["N"]}}, {"A": "Alpha"}, {}, TODAY
			),
			"regulatory_item_coverage_matrix": reporting.build_coverage_matrix_rows(
				[line("R", "A", "N")], {"R": registration("R")}, {}, {}, TODAY
			),
			"regulatory_compliance_gap": [
				reporting.make_gap(
					reporting.GAP_UNCONFIRMED_EXPIRY, "Regulatory Product Registration", "R", "detail"
				)
			],
			"regulatory_renewal_workbench": reporting.build_workbench_rows(
				[{"name": "ACT-1", "registration": "R", "current_stage": "Preparing"}],
				{"R": registration("R")},
				TODAY,
			),
		}
		for folder, (name, _doctype, module) in REPORTS.items():
			columns = module.get_columns()
			fieldnames = [column["fieldname"] for column in columns]
			with self.subTest(report=name):
				self.assertEqual(len(fieldnames), len(set(fieldnames)))
				self.assertEqual(set(fieldnames) - set(sample_rows[folder][0]), set())
				for column in columns:
					self.assertTrue(column["label"])
					if column["fieldtype"] in ("Link", "Dynamic Link"):
						self.assertTrue(column.get("options"))

	def test_expiry_columns_match_the_requested_layout(self):
		self.assertEqual(
			[column["fieldname"] for column in expiry_summary.get_columns()],
			[
				"registration",
				"certificate_type",
				"product_category",
				"device_category",
				"product_family_name",
				"official_status",
				"expiry_basis",
				"expiry_date",
				"days_remaining",
				"renewal_status",
				"responsible_user",
				"affected_items",
				"stock_qty",
				"stock_value",
				"open_po_qty",
				"open_so_qty",
			],
		)
		self.assertEqual(expiry_summary.get_columns()[0]["fieldtype"], "Link")
		subject = next(c for c in compliance_gap.get_columns() if c["fieldname"] == "subject")
		self.assertEqual((subject["fieldtype"], subject["options"]), ("Dynamic Link", "subject_type"))


class TestExecuteWiring(unittest.TestCase):
	"""``execute`` with every query helper mocked: checks the orchestration and the default filters."""

	def run_with_today(self, module, filters=None):
		with patch.object(module, "today", return_value="2026-10-18"):
			return module.execute(filters)

	def test_expiry_summary_applies_filters_and_within_days(self):
		registrations = [
			registration("SOON", expiry="2026-12-01"),
			registration("FAR", expiry="2028-01-01"),
			registration("NONE", basis="No Expiry Date Specified"),
		]
		with (
			patch.object(reporting, "get_registrations", return_value=registrations) as get_registrations,
			patch.object(reporting, "fetch_registered_lines", return_value=[line("SOON", "A", "N")]) as lines,
			patch.object(
				reporting,
				"fetch_item_measures",
				return_value={"stock": {"A": {"qty": 3, "value": 30}}, "po": {}, "so": {"A": 1}},
			) as fetch_measures,
		):
			columns, data = self.run_with_today(
				expiry_summary,
				{"responsible_user": "u@x.com", "expiry_within_days": "90", "official_status": ""},
			)
		get_registrations.assert_called_once_with({"responsible_user": "u@x.com"})
		self.assertEqual(lines.call_args.args[0], ["SOON"])
		fetch_measures.assert_called_once_with({"A"})
		self.assertEqual([row["registration"] for row in data], ["SOON"])
		self.assertEqual(
			(data[0]["stock_qty"], data[0]["open_so_qty"], data[0]["days_remaining"]), (3.0, 1.0, 44)
		)
		self.assertEqual(len(columns), 16)

		with (
			patch.object(reporting, "get_registrations", return_value=registrations),
			patch.object(reporting, "fetch_registered_lines", return_value=[]),
			patch.object(reporting, "fetch_item_measures", return_value={}),
		):
			_columns, everything = self.run_with_today(expiry_summary)
		self.assertEqual([row["registration"] for row in everything], ["SOON", "FAR", "NONE"])

	def test_affected_business_defaults_and_per_item_measures(self):
		registrations = [
			registration("SOON", expiry="2026-12-01"),
			registration("BEYOND", expiry="2028-01-01"),
		]
		lines = [line("SOON", "A", "N1"), line("SOON", "B", "N2"), line("BEYOND", "A", "N3")]
		measures = {
			"stock": {"A": {"qty": 5, "value": 50}, "B": {"qty": 1, "value": 10}},
			"po": {"A": 4},
			"so": {"B": 2},
		}
		with (
			patch.object(reporting, "get_registrations", return_value=registrations) as get_registrations,
			patch.object(reporting, "fetch_registered_lines", return_value=lines) as fetch_lines,
			patch.object(reporting, "fetch_item_measures", return_value=measures),
			patch.object(reporting, "fetch_item_names", return_value={"A": "Alpha", "B": "Beta"}),
		):
			_columns, data = self.run_with_today(affected_business)
		get_registrations.assert_called_once_with({"official_status": ["in", ["Active", "Expired"]]})
		self.assertEqual(fetch_lines.call_args.args[0], ["SOON"])
		self.assertEqual(fetch_lines.call_args.args[1:], ("Active", None))
		self.assertEqual(
			[(row["registration"], row["item_code"]) for row in data], [("SOON", "A"), ("SOON", "B")]
		)
		alpha, beta = data
		self.assertEqual((alpha["stock_qty"], alpha["open_po_qty"], alpha["open_so_qty"]), (5.0, 4.0, 0.0))
		self.assertEqual((beta["stock_qty"], beta["open_po_qty"], beta["open_so_qty"]), (1.0, 0.0, 2.0))
		self.assertEqual(data[0]["item_name"], "Alpha")

		with (
			patch.object(reporting, "get_registrations", return_value=registrations) as get_registrations,
			patch.object(reporting, "fetch_registered_lines", return_value=lines) as fetch_lines,
			patch.object(reporting, "fetch_item_measures", return_value=measures),
			patch.object(reporting, "fetch_item_names", return_value={}),
		):
			_columns, wide = self.run_with_today(
				affected_business,
				{"expiry_within_days": 1000, "official_status": "Revoked", "item": "A"},
			)
		get_registrations.assert_called_once_with({"official_status": "Revoked"})
		self.assertEqual(fetch_lines.call_args.args[1:], ("Active", "A"))
		self.assertEqual({row["registration"] for row in wide}, {"SOON", "BEYOND"})

	def test_coverage_matrix_uses_one_stock_lookup(self):
		registrations = [registration("R1"), registration("R2", status="Expired", expiry="2026-01-01")]
		lines = [line("R1", "A", "N1"), line("R1", "A", "N2"), line("R2", "A", "N0"), line("R1", None, "N9")]
		with (
			patch.object(reporting, "get_registrations", return_value=registrations) as get_registrations,
			patch.object(reporting, "fetch_registered_lines", return_value=lines) as fetch_lines,
			patch.object(reporting, "fetch_item_names", return_value={"A": "Alpha"}),
			patch.object(reporting, "fetch_stock_by_item", return_value={"A": {"qty": 8}}) as fetch_stock,
		):
			_columns, data = self.run_with_today(
				coverage_matrix, {"registration": "R1", "line_status": "All", "only_with_stock": 1}
			)
		get_registrations.assert_called_once_with({"name": "R1"})
		self.assertEqual(fetch_lines.call_args.args, (["R1", "R2"], None, None))
		fetch_stock.assert_called_once_with({"A"})
		self.assertEqual([row["newcode"] for row in data], ["N1", "N2", "N0"])
		self.assertEqual({row["stock_qty"] for row in data}, {8.0})

		with (
			patch.object(reporting, "get_registrations", return_value=registrations),
			patch.object(reporting, "fetch_registered_lines", return_value=lines) as fetch_lines,
			patch.object(reporting, "fetch_item_names", return_value={}),
			patch.object(reporting, "fetch_stock_by_item", return_value={}),
		):
			_columns, default_rows = self.run_with_today(coverage_matrix)
		self.assertEqual(fetch_lines.call_args.args[1:], ("Active", None))
		self.assertEqual(len(default_rows), 4)

	def test_compliance_gap_pipeline(self):
		data = {
			"items": [item("I-NONE"), item("I-EXEMPT", EXEMPT)],
			"items_not_assessed": [item("I-NA", NOT_ASSESSED, regulatory_product_category="Food")],
			"registrations": [
				registration("R-UNC", basis="Unconfirmed", expiry=None, product_category="Drug")
			],
			"lines": [line("R-UNC", "I-A")],
			"documents": [],
			"default_owner": None,
		}
		with patch.object(reporting, "load_gap_data", return_value=data) as load_gap_data:
			_columns, everything = self.run_with_today(compliance_gap)
			datasets = reporting.required_datasets(reporting.selected_gap_types({}), {})
			self.assertEqual(load_gap_data.call_args.args[0], datasets)
			_columns, filtered = self.run_with_today(compliance_gap, {"severity": "Warning", "item": "I-A"})
		self.assertEqual(
			[(row["severity"], row["gap_type"], row["subject"]) for row in everything],
			[
				("Critical", reporting.GAP_REQUIRED_ITEM, "I-NONE"),
				("Critical", reporting.GAP_NO_CERTIFICATE, "R-UNC"),
				("Warning", reporting.GAP_UNCONFIRMED_EXPIRY, "R-UNC"),
				("Warning", reporting.GAP_EXEMPT_NO_REASON, "I-EXEMPT"),
			],
		)
		self.assertEqual([row["gap_type"] for row in filtered], [reporting.GAP_UNCONFIRMED_EXPIRY])
		with patch.object(reporting, "load_gap_data", return_value=data) as load_gap_data:
			_columns, worklist = self.run_with_today(
				compliance_gap, {"gap_type": reporting.GAP_NOT_ASSESSED, "product_category": "Food"}
			)
			self.assertEqual(load_gap_data.call_args.args[0], {"items_not_assessed"})
			_columns, other = self.run_with_today(
				compliance_gap, {"gap_type": reporting.GAP_NOT_ASSESSED, "product_category": "Drug"}
			)
		self.assertEqual([row["subject"] for row in worklist], ["I-NA"])
		self.assertEqual(other, [])

	def test_renewal_workbench_pipeline(self):
		actions = [
			frappe._dict(
				name="ACT-1",
				registration="R-LATE",
				current_stage="Preparing",
				modified=datetime(2026, 10, 1),
			),
			frappe._dict(
				name="ACT-2",
				registration="R-SOON",
				current_stage="FDA Query",
				response_due_date="2026-10-15",
				modified=datetime(2026, 10, 2),
			),
		]
		registrations = {
			"R-SOON": registration("R-SOON", expiry="2026-11-01"),
			"R-LATE": registration("R-LATE", expiry="2027-11-01"),
		}
		with (
			patch.object(frappe, "get_list", return_value=actions) as get_list,
			patch.object(reporting, "load_workbench_registrations", return_value=registrations),
		):
			_columns, data = self.run_with_today(renewal_workbench, {"assigned_to": "u@x.com"})
		self.assertEqual(
			get_list.call_args.kwargs["filters"],
			{
				"action_type": "Renewal",
				"assigned_to": "u@x.com",
				"current_stage": ["not in", ["Approved", "Rejected", "Cancelled"]],
			},
		)
		self.assertEqual([row["action"] for row in data], ["ACT-2", "ACT-1"])
		self.assertEqual(data[0]["days_to_query_due"], -3)


if __name__ == "__main__":
	unittest.main()


class TestStockMeasure(unittest.TestCase):
	def test_value_is_hidden_without_permission(self):
		row = frappe._dict(qty=4, value=120.5)
		self.assertEqual(reporting.stock_measure(row, True), {"qty": 4.0, "value": 120.5})
		self.assertEqual(reporting.stock_measure(row, False), {"qty": 4.0, "value": 0.0})
