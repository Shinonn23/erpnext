import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs import item as ra_item
from erpnext.regulatory_affairs import transactions
from erpnext.regulatory_affairs.constants import ITEM_CATEGORY_FIELD, ITEM_COVERAGE_FIELDS

COVERAGE = dict(
	current_regulatory_registration="R1",
	current_newcode="NC",
	current_registration_expiry="2027-01-01",
	current_registration_status="Active",
)


class TestAssessmentVisibility(unittest.TestCase):
	def test_registration_assessment_has_no_qi_dependent_container(self):
		path = Path(__file__).resolve().parents[2] / "stock/doctype/item/item.json"
		metadata = json.loads(path.read_text())
		fields = {field["fieldname"]: field for field in metadata["fields"]}
		tab = section = None
		for name in metadata["field_order"]:
			field = fields[name]
			if field["fieldtype"] == "Tab Break":
				tab, section = field, None
			elif field["fieldtype"] == "Section Break":
				section = field
			if name == "registration_requirement":
				for container in (tab, section):
					if container:
						self.assertFalse(container.get("depends_on"))
						self.assertFalse(container.get("hidden"))
				self.assertIn("regulatory_product_category", field["depends_on"])
				break
		else:
			self.fail("Registration assessment field is missing")


class TestNotRegulatedTransactions(unittest.TestCase):
	def test_not_regulated_sales_and_receipts_do_not_look_up_registration(self):
		for doctype in (
			"Sales Order",
			"Delivery Note",
			"Sales Invoice",
			"Purchase Order",
			"Purchase Receipt",
		):
			doc = frappe._dict(doctype=doctype, items=[frappe._dict(idx=1, item_code="PLAIN-1")])

			def get_items(_doctype, *, filters, pluck):
				if ["Item", ITEM_CATEGORY_FIELD, "!=", "Not Regulated"] in filters:
					return []
				return ["PLAIN-1"]

			with (
				self.subTest(doctype=doctype),
				patch.object(
					transactions, "get_settings", return_value=frappe._dict(transactions.POLICY_DEFAULTS)
				),
				patch.object(frappe, "get_all", side_effect=get_items),
				patch.object(transactions, "_lookup_mapping") as lookup,
				patch.object(frappe, "throw") as throw,
			):
				transactions.validate_transaction(doc)
			lookup.assert_not_called()
			throw.assert_not_called()


def _raise(exc):
	raise (exc or frappe.ValidationError)


def item(category=None, **values):
	doc = frappe._dict({ITEM_CATEGORY_FIELD: category, **values})
	doc.get_doc_before_save = lambda: None
	return doc


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
			self.assertEqual(doc[ITEM_CATEGORY_FIELD], "Not Regulated")
			for fieldname in ITEM_COVERAGE_FIELDS:
				self.assertIsNone(doc[fieldname], (blank, fieldname))

	def test_other_fields_are_untouched(self):
		values = dict(regulatory_other_category="Toy", medical_device_category="IVD")
		for category in ("Other", None):
			doc = item(category, item_name="Gloves", **values, **COVERAGE)
			self.run_validate(doc)
			self.assertEqual(doc[ITEM_CATEGORY_FIELD], category or "Not Regulated")
			self.assertEqual(doc.item_name, "Gloves")
			for fieldname, value in values.items():
				self.assertEqual(doc[fieldname], value, (category, fieldname))


class TestCategoryChange(unittest.TestCase):
	"""Every Item has a category; only Regulatory Affairs changes an existing classification."""

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
			with self.assertRaises(frappe.ValidationError, msg=repr(roles)):
				self.check("Drug", "  ", roles)

	def test_an_operator_sets_and_changes_the_category(self):
		self.check(None, "Drug", ["Regulatory Operator"])
		self.check("Drug", "Food", ["Regulatory Operator"])

	def test_an_approver_sets_and_changes_but_cannot_clear_the_category(self):
		self.check(None, "Drug", ["Regulatory Approver"])
		self.check("Drug", "Food", ["Regulatory Approver"])
		for blank in ("", None):
			with self.assertRaises(frappe.ValidationError):
				self.check("Drug", blank, ["Regulatory Approver"])

	def test_an_unchanged_category_passes_for_any_editor(self):
		self.check("Drug", "Drug", ["Item Manager"])
		self.check("Drug", " Drug ", ["Item Manager"])
		self.check(None, "Not Regulated", ["Item Manager"])
		self.check("", "Not Regulated", ["Item Manager"])

	def test_administrator_and_install_runs_pass(self):
		self.check("Drug", "Food", ["Item Manager"], user="Administrator")
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

	def test_new_and_legacy_blank_items_default_without_an_ra_role(self):
		for previous in (None, frappe._dict(regulatory_product_category="")):
			doc = item()
			doc.get_doc_before_save = lambda: previous
			with (
				patch.object(frappe, "session", frappe._dict(user="creator@example.test")),
				patch.object(frappe, "flags", frappe._dict()),
				patch.object(ra_item.permissions, "is_operator", return_value=False),
				patch.object(ra_item.coverage, "refresh_item_coverage") as refresh,
			):
				ra_item.validate(doc)
			self.assertEqual(doc.regulatory_product_category, "Not Regulated")
			refresh.assert_not_called()

	def test_clearing_a_regulated_item_does_not_apply_the_default(self):
		doc = item("")
		doc.get_doc_before_save = lambda: frappe._dict(regulatory_product_category="Drug")
		with (
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(ra_item.coverage, "refresh_item_coverage") as refresh,
			self.assertRaises(frappe.ValidationError),
		):
			ra_item.validate(doc)
		refresh.assert_not_called()
		self.assertEqual(doc.regulatory_product_category, "")


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
		self.assertIn(ra_item.classify_item, frappe.whitelisted)

	@staticmethod
	def raise_error(message, exc=frappe.ValidationError, **kwargs):
		raise exc(message)

	def classify(self, *args, operator=True, **kwargs):
		doc = MagicMock()
		doc.get.side_effect = lambda field: {ITEM_CATEGORY_FIELD: kwargs.get("category")}.get(field)
		with (
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(ra_item.permissions, "is_operator", return_value=operator),
			patch.object(frappe, "throw", side_effect=self.raise_error),
		):
			ra_item.classify_item("ITEM-1", *args, **kwargs)
		return doc

	def test_one_dialog_classifies_a_medical_device(self):
		doc = self.classify(category="Medical Device", medical_device_category="IVD")
		doc.set.assert_called_once_with(ITEM_CATEGORY_FIELD, "Medical Device")
		self.assertEqual(doc.medical_device_category, "IVD")
		self.assertIsNone(doc.regulatory_other_category)
		self.assertTrue(doc.flags.ignore_permissions)
		doc.save.assert_called_once_with()

	def test_a_medical_device_needs_its_device_category(self):
		with self.assertRaises(frappe.ValidationError):
			self.classify(category="Medical Device")

	def test_other_needs_its_name_and_clears_the_device_category(self):
		with self.assertRaises(frappe.ValidationError):
			self.classify(category="Other")
		doc = self.classify(category="Other", other_category="Reagent kit")
		self.assertIsNone(doc.medical_device_category)
		self.assertEqual(doc.regulatory_other_category, "Reagent kit")

	def test_not_regulated_is_a_classification_without_a_registration(self):
		doc = self.classify(category="Not Regulated")
		doc.set.assert_called_once_with(ITEM_CATEGORY_FIELD, "Not Regulated")
		self.assertFalse(ra_item.is_regulated({ITEM_CATEGORY_FIELD: "Not Regulated"}))
		self.assertTrue(ra_item.is_regulated({ITEM_CATEGORY_FIELD: "Drug"}))
		self.assertFalse(ra_item.is_regulated({ITEM_CATEGORY_FIELD: ""}))

	def test_an_unknown_category_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.classify(category="Magic")

	def test_a_blank_classification_is_refused_without_saving(self):
		for blank in (None, "", "  "):
			with self.subTest(blank=blank), self.assertRaises(frappe.ValidationError):
				self.classify(category=blank)

	def test_set_product_category_cannot_clear_the_category(self):
		doc = MagicMock()
		with (
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			ra_item.set_product_category("ITEM-1", "")
		doc.save.assert_not_called()

	def test_bulk_classification_reports_what_failed(self):
		def fake(code, category, device):
			if code == "BAD":
				raise frappe.ValidationError("no device category")

		with (
			patch.object(ra_item, "classify_item", side_effect=fake),
			patch.object(frappe, "clear_last_message"),
			patch.object(frappe, "session", frappe._dict(user="ra@example.test"), create=True),
		):
			result = ra_item.classify_items('["A", "BAD"]', "Medical Device")
		self.assertEqual(result["done"], ["A"])
		self.assertEqual(list(result["failed"]), ["BAD"])
		self.assertIn(ra_item.classify_items, frappe.whitelisted)

	def test_only_the_regulatory_roles_classify(self):
		with self.assertRaises(frappe.PermissionError):
			self.classify(category="Drug", operator=False)

	def test_category_field_is_on_permission_level_one_for_the_regulatory_roles(self):
		import json
		import os

		from erpnext.regulatory_affairs.custom_fields import ITEM_CATEGORY_PERMLEVEL

		path = os.path.join(os.path.dirname(__file__), "../../stock/doctype/item/item.json")
		with open(path) as handle:
			document = json.load(handle)
		field = next(f for f in document["fields"] if f["fieldname"] == ITEM_CATEGORY_FIELD)
		self.assertEqual(field["permlevel"], ITEM_CATEGORY_PERMLEVEL)
		rows = [p for p in document["permissions"] if p.get("permlevel") == 1]
		writers = {p["role"] for p in rows if p.get("write")}
		self.assertEqual(writers, {"Regulatory Operator", "Regulatory Approver"})
		self.assertIn("Item Manager", {p["role"] for p in rows if p.get("read")})
