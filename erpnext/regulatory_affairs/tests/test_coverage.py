import unittest
from datetime import date
from unittest.mock import patch

import frappe

from erpnext.regulatory_affairs import coverage


class Thrown(Exception):
	pass


def registration(status="Active", issue=None, expiry=None, basis="Expiry Date Specified", **kwargs):
	return frappe._dict(
		official_status=status,
		issue_date=issue,
		expiry_date=expiry,
		expiry_basis=basis,
		item_code="ITEM-1",
		**kwargs,
	)


class TestCoverage(unittest.TestCase):
	def test_normalize_registration_number(self):
		self.assertEqual(coverage.normalize_registration_number(" 66-1-2/2565 (A) "), "66122565A")
		self.assertEqual(coverage.normalize_registration_number(None), "")

	def test_window_and_in_force(self):
		reg = registration(issue="2026-01-01", expiry="2026-12-31")
		self.assertEqual(coverage.registration_window(reg), (date(2026, 1, 1), date(2026, 12, 31)))
		for on_date, expected in (
			("2025-12-31", False),
			("2026-01-01", True),
			("2026-12-31", True),
			("2027-01-01", False),
		):
			self.assertEqual(coverage.is_in_force(reg, on_date), expected, on_date)

	def test_in_force_depends_on_status_and_basis(self):
		for status in ("Draft", "Suspended", "Revoked", "Cancelled"):
			self.assertFalse(coverage.is_in_force(registration(status), "2026-06-01"), status)
		for status in ("Active", "Expired", "Superseded"):
			self.assertTrue(
				coverage.is_in_force(registration(status, expiry="2026-12-31"), "2026-06-01"), status
			)
		for basis in ("No Expiry Date Specified", "Unconfirmed"):
			reg = registration(expiry="2020-01-01", basis=basis)
			self.assertTrue(coverage.is_in_force(reg, "2030-01-01"), basis)
			self.assertEqual(coverage.registration_window(reg)[1], None)

	def test_ranges_overlap_is_inclusive_and_open_ended(self):
		d = date
		self.assertTrue(
			coverage.ranges_overlap(d(2026, 1, 1), d(2026, 6, 30), d(2026, 6, 30), d(2026, 12, 31))
		)
		self.assertFalse(coverage.ranges_overlap(d(2026, 1, 1), d(2026, 6, 29), d(2026, 6, 30), None))
		self.assertTrue(coverage.ranges_overlap(None, None, d(2030, 1, 1), d(2030, 1, 2)))
		self.assertTrue(
			coverage.lines_overlap(
				frappe._dict(effective_from="2026-01-01", effective_to=None),
				frappe._dict(effective_from=None, effective_to="2026-01-01"),
			)
		)

	def test_effective_span_limits_line_by_registration_window(self):
		reg = registration(issue="2026-03-01", expiry="2026-09-30")
		line = frappe._dict(effective_from="2026-01-01", effective_to="2026-12-31")
		self.assertEqual(coverage.effective_span(line, reg), (date(2026, 3, 1), date(2026, 9, 30)))
		open_line = frappe._dict(effective_from=None, effective_to=None)
		self.assertEqual(coverage.effective_span(open_line, reg), (date(2026, 3, 1), date(2026, 9, 30)))
		no_dates = registration(basis="No Expiry Date Specified")
		self.assertEqual(coverage.effective_span(open_line, no_dates), (None, None))

	def test_renewal_successor_does_not_make_mapping_ambiguous(self):
		old = frappe._dict(registration("Superseded", "2021-01-01", "2026-06-30"), registration="OLD")
		new = frappe._dict(registration("Active", "2026-07-01", "2031-06-30"), registration="NEW")
		self.assertEqual(coverage.select_mapping([old, new], "2026-08-01").registration, "NEW")
		chosen = coverage.select_mapping([old, new], "2026-06-30")
		self.assertEqual(chosen.registration, "OLD")
		self.assertTrue(chosen.in_force)

	def test_two_active_in_force_rows_are_ambiguous(self):
		rows = [
			frappe._dict(registration("Active", "2026-01-01", "2026-12-31"), registration="A"),
			frappe._dict(registration("Active", "2026-02-01", "2027-12-31"), registration="B"),
		]
		with patch.object(frappe, "throw", side_effect=Thrown):
			with self.assertRaises(Thrown):
				coverage.select_mapping(rows, "2026-06-01")

	def test_active_preferred_over_other_in_force(self):
		rows = [
			frappe._dict(registration("Superseded", "2026-01-01", "2026-12-31"), registration="OLD"),
			frappe._dict(registration("Active", "2026-01-01", "2026-12-31"), registration="NEW"),
		]
		self.assertEqual(coverage.select_mapping(rows, "2026-06-01").registration, "NEW")

	def test_nothing_in_force_returns_last_expired_flagged(self):
		rows = [
			frappe._dict(registration("Expired", "2020-01-01", "2023-01-01"), registration="A"),
			frappe._dict(registration("Expired", "2023-02-01", "2025-01-01"), registration="B"),
		]
		chosen = coverage.select_mapping(rows, "2026-06-01")
		self.assertEqual(chosen.registration, "B")
		self.assertFalse(chosen.in_force)
		self.assertIsNone(coverage.select_mapping([], "2026-06-01"))

	def test_item_coverage_fields_do_not_use_the_retired_classification(self):
		from erpnext.regulatory_affairs.constants import ITEM_COVERAGE_FIELDS

		self.assertEqual(set(coverage.coverage_values(None)), set(ITEM_COVERAGE_FIELDS))
		owner = frappe._dict(regulatory_owner="o@example.com")
		self.assertEqual(coverage.get_regulatory_owner(owner), "o@example.com")

	def test_coverage_values(self):
		self.assertEqual(set(coverage.coverage_values(None).values()), {None})
		values = coverage.coverage_values(
			frappe._dict(registration="R1", newcode="NC", expiry_date="2027-01-01", official_status="Active")
		)
		self.assertEqual(values["current_regulatory_registration"], "R1")
		self.assertEqual(values["current_registration_status"], "Active")


if __name__ == "__main__":
	unittest.main()
