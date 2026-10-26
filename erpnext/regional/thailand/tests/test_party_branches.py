"""Offline tests of the party Branches box: branch code normalisation, rendering and warnings."""

import unittest

from erpnext.regional.thailand.custom.branches import (
	build_row,
	get_branch_warnings,
	normalise_branch_code,
	render_branches_html,
)


def row(name, code, primary=0, disabled=0, **extra):
	return build_row(
		{
			"name": name,
			"address_title": extra.get("title", name),
			"address_type": "Billing",
			"branch_code": code,
			"is_primary_address": primary,
			"city": extra.get("city", "Bangkok"),
			"disabled": disabled,
		}
	)


class TestPartyBranches(unittest.TestCase):
	def test_normalise(self):
		self.assertEqual(normalise_branch_code("0"), ("00000", "ok"))
		self.assertEqual(normalise_branch_code(" 2 "), ("00002", "ok"))
		self.assertEqual(normalise_branch_code("00012"), ("00012", "ok"))
		self.assertEqual(normalise_branch_code(None), ("", "blank"))
		self.assertEqual(normalise_branch_code("  "), ("", "blank"))
		self.assertEqual(normalise_branch_code("123456"), ("123456", "invalid"))
		self.assertEqual(normalise_branch_code("abc"), ("", "invalid"))

	def test_blank_primary_is_head_office(self):
		self.assertEqual(row("A", "", primary=1)["branch_code"], "00000")
		self.assertEqual(row("A", "", primary=0)["branch_state"], "blank")

	def test_render_labels(self):
		html = render_branches_html([row("A", "00000"), row("B", "00003")], "0105500000000")
		self.assertIn("สำนักงานใหญ่", html)
		self.assertIn("สาขาที่ 00003", html)
		self.assertNotIn("text-danger", html)

	def test_blank_non_primary_shows_dash_and_warning(self):
		html = render_branches_html([row("A", "00000"), row("B", "")], "1")
		self.assertIn("—", html)
		self.assertIn("text-danger", html)

	def test_duplicate_warning_ignores_disabled(self):
		rows = [row("A", "00000"), row("B", "1"), row("C", "00001")]
		self.assertTrue(any("00001" in w for w in get_branch_warnings(rows)))
		rows[2] = row("C", "00001", disabled=1)
		self.assertEqual(get_branch_warnings(rows), [])

	def test_missing_head_office_only_with_tax_id(self):
		rows = [row("B", "00001")]
		self.assertEqual(get_branch_warnings(rows, None), [])
		self.assertEqual(len(get_branch_warnings(rows, "0105500000000")), 1)

	def test_invalid_code_warning(self):
		warnings = get_branch_warnings([row("A", "00000"), row("B", "1234567")])
		self.assertEqual(len(warnings), 1)
		self.assertIn("1234567", warnings[0])

	def test_xss_escaped(self):
		evil = '<script>alert(1)</script>"'
		html = render_branches_html([row('X"><b>', "00000", title=evil, city=evil)], "1")
		self.assertNotIn("<script>", html)
		self.assertNotIn("<b>", html)

	def test_empty(self):
		self.assertIn("text-muted", render_branches_html([]))
