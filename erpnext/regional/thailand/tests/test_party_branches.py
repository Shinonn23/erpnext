"""Offline tests for branch codes returned for linked addresses."""

import unittest

from erpnext.regional.thailand.custom.branches import (
	build_row,
	normalise_branch_code,
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
