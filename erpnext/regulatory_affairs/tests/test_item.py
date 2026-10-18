import unittest
from unittest.mock import patch

import frappe

from erpnext.regulatory_affairs import item as ra_item
from erpnext.regulatory_affairs.constants import ITEM_CATEGORY_FIELD, ITEM_COVERAGE_FIELDS

COVERAGE = dict(
	current_regulatory_registration="R1",
	current_newcode="NC",
	current_registration_expiry="2027-01-01",
	current_registration_status="Active",
)


def item(category=None, **values):
	return frappe._dict({ITEM_CATEGORY_FIELD: category, **values})


class TestIsRegulated(unittest.TestCase):
	def test_an_item_with_a_product_category_is_regulated(self):
		for category in ("Drug", "Medical Device", " Food "):
			self.assertTrue(ra_item.is_regulated(item(category)), category)

	def test_a_blank_or_missing_product_category_is_unregulated(self):
		for blank in (None, "", "   "):
			self.assertFalse(ra_item.is_regulated(item(blank)), repr(blank))
		self.assertFalse(ra_item.is_regulated(frappe._dict()))


class TestValidate(unittest.TestCase):
	def run_validate(self, doc):
		with patch.object(ra_item.coverage, "refresh_item_coverage") as refresh:
			ra_item.validate(doc)
		return refresh

	def test_coverage_fixture_matches_the_coverage_fields(self):
		self.assertEqual(set(COVERAGE), set(ITEM_COVERAGE_FIELDS))

	def test_regulated_item_refreshes_its_coverage(self):
		doc = item("Drug", **COVERAGE)
		refresh = self.run_validate(doc)
		refresh.assert_called_once_with(doc)
		for fieldname, value in COVERAGE.items():
			self.assertEqual(doc[fieldname], value)

	def test_unregulated_item_clears_its_coverage(self):
		for blank in (None, "", "  "):
			doc = item(blank, **COVERAGE)
			self.assertEqual(self.run_validate(doc).call_count, 0, repr(blank))
			for fieldname in ITEM_COVERAGE_FIELDS:
				self.assertIsNone(doc[fieldname], (blank, fieldname))

	def test_other_fields_are_untouched(self):
		values = dict(
			regulatory_other_category="Toy", medical_device_category="IVD", regulatory_owner="a@b.c"
		)
		for category in ("Other", None):
			doc = item(category, item_name="Gloves", **values, **COVERAGE)
			self.run_validate(doc)
			self.assertEqual(doc[ITEM_CATEGORY_FIELD], category)
			self.assertEqual(doc.item_name, "Gloves")
			for fieldname, value in values.items():
				self.assertEqual(doc[fieldname], value, (category, fieldname))
