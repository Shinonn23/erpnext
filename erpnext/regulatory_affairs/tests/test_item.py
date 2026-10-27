import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs import item as ra_item
from erpnext.regulatory_affairs.constants import ITEM_CATEGORY_FIELD, ITEM_COVERAGE_FIELDS

COVERAGE = dict(
	current_regulatory_registration="R1",
	current_newcode="NC",
	current_registration_expiry="2027-01-01",
	current_registration_status="Active",
)


def _raise(exc):
	raise (exc or frappe.ValidationError)


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
		with (
			patch.object(ra_item, "check_category_change"),
			patch.object(ra_item.coverage, "refresh_item_coverage") as refresh,
		):
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


class TestCategoryChange(unittest.TestCase):
	"""Operator sets or changes the Product Category, Approver clears it, everyone else is refused."""

	def check(self, old, new, roles, user="someone@example.com", **flags):
		doc = MagicMock()
		doc.get.side_effect = lambda fieldname: new if fieldname == ITEM_CATEGORY_FIELD else None
		doc.get_doc_before_save.return_value = (
			None if old is None else frappe._dict({ITEM_CATEGORY_FIELD: old})
		)
		session = frappe._dict(user=user)
		with (
			patch.object(frappe, "session", session, create=True),
			patch.object(frappe, "flags", frappe._dict(flags), create=True),
			patch.object(frappe, "get_roles", return_value=list(roles)),
			patch.object(frappe, "throw", side_effect=lambda message, exc=None: _raise(exc)),
		):
			ra_item.check_category_change(doc)

	def test_an_item_editor_without_a_regulatory_role_cannot_set_the_category(self):
		for old in (None, "", "Drug"):
			with self.assertRaises(frappe.PermissionError, msg=repr(old)):
				self.check(old, "Medical Device", ["Item Manager", "Stock Manager"])

	def test_an_item_editor_cannot_clear_the_category(self):
		for roles in (["Item Manager"], ["Regulatory Operator"], ["Regulatory Viewer"]):
			with self.assertRaises(frappe.PermissionError, msg=repr(roles)):
				self.check("Drug", "  ", roles)

	def test_an_operator_sets_and_changes_the_category(self):
		self.check(None, "Drug", ["Regulatory Operator"])
		self.check("Drug", "Food", ["Regulatory Operator"])

	def test_an_approver_sets_changes_and_clears_the_category(self):
		self.check(None, "Drug", ["Regulatory Approver"])
		self.check("Drug", "Food", ["Regulatory Approver"])
		self.check("Drug", "", ["Regulatory Approver"])
		self.check("Drug", None, ["Regulatory Approver"])

	def test_an_unchanged_category_passes_for_any_editor(self):
		self.check("Drug", "Drug", ["Item Manager"])
		self.check("Drug", " Drug ", ["Item Manager"])
		self.check(None, None, ["Item Manager"])
		self.check("", None, ["Item Manager"])

	def test_administrator_and_install_runs_pass(self):
		self.check("Drug", "", ["Item Manager"], user="Administrator")
		for flag in ("in_install", "in_patch", "in_migrate"):
			self.check(None, "Drug", ["Item Manager"], **{flag: True})

	def test_validate_runs_the_category_check_first(self):
		doc = item("Drug")
		with (
			patch.object(ra_item, "check_category_change", side_effect=frappe.PermissionError) as check,
			patch.object(ra_item.coverage, "refresh_item_coverage") as refresh,
		):
			with self.assertRaises(frappe.PermissionError):
				ra_item.validate(doc)
		check.assert_called_once_with(doc)
		refresh.assert_not_called()


class TestSetProductCategory(unittest.TestCase):
	def test_saves_the_stripped_category_without_form_permissions(self):
		doc = MagicMock()
		doc.get.return_value = "Drug"
		with (
			patch.object(frappe, "has_permission") as has_permission,
			patch.object(frappe, "get_doc", return_value=doc),
		):
			result = ra_item.set_product_category("ITEM-1", " Drug ")
		has_permission.assert_called_once_with("Item", "read", "ITEM-1", throw=True)
		doc.set.assert_called_once_with(ITEM_CATEGORY_FIELD, "Drug")
		self.assertTrue(doc.flags.ignore_permissions)
		doc.save.assert_called_once_with()
		self.assertEqual(result, "Drug")

	def test_method_is_whitelisted(self):
		self.assertIn(ra_item.set_product_category, frappe.whitelisted)

	def test_category_field_is_on_permission_level_one_for_the_regulatory_roles(self):
		import json
		import os

		from erpnext.regulatory_affairs.custom_fields import ITEM_CATEGORY_PERMLEVEL, item_fields

		field = next(f for f in item_fields() if f["fieldname"] == ITEM_CATEGORY_FIELD)
		self.assertEqual(field["permlevel"], ITEM_CATEGORY_PERMLEVEL)
		path = os.path.join(os.path.dirname(__file__), "../../stock/doctype/item/item.json")
		with open(path) as handle:
			rows = [p for p in json.load(handle)["permissions"] if p.get("permlevel") == 1]
		writers = {p["role"] for p in rows if p.get("write")}
		self.assertEqual(writers, {"Regulatory Operator", "Regulatory Approver"})
		self.assertIn("Item Manager", {p["role"] for p in rows if p.get("read")})
