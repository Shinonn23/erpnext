import unittest
from unittest.mock import patch

import frappe

from erpnext.regulatory_affairs import item as ra_item
from erpnext.regulatory_affairs.constants import (
	ASSESSMENT_EXEMPT,
	ASSESSMENT_FIELD,
	ASSESSMENT_NOT_ASSESSED,
	ASSESSMENT_NOTE_FIELD,
	ASSESSMENT_REQUIRED,
	ITEM_COVERAGE_FIELDS,
)

NA, REQ, EXE = ASSESSMENT_NOT_ASSESSED, ASSESSMENT_REQUIRED, ASSESSMENT_EXEMPT


class Thrown(Exception):
	pass


def throws():
	return patch.object(frappe, "throw", side_effect=Thrown)


def item(status=REQ, note=None, **values):
	return frappe._dict({ASSESSMENT_FIELD: status, ASSESSMENT_NOTE_FIELD: note, **values})


class TestNormalizeClassification(unittest.TestCase):
	def test_blank_status_defaults_to_not_assessed(self):
		for blank in (None, "", "  "):
			doc = item(blank)
			self.assertEqual(ra_item.normalize_classification(doc), NA)
			self.assertEqual(doc[ASSESSMENT_FIELD], NA)
		missing = frappe._dict()
		self.assertEqual(ra_item.normalize_classification(missing), NA)
		self.assertEqual(missing[ASSESSMENT_FIELD], NA)

	def test_unknown_status_is_rejected(self):
		with throws(), self.assertRaises(Thrown):
			ra_item.normalize_classification(item("Required"))

	def test_exempt_needs_a_non_blank_note(self):
		with throws():
			for note in (None, "", "   "):
				with self.assertRaises(Thrown):
					ra_item.normalize_classification(item(EXE, note))
		doc = item(EXE, "Class A accessory")
		self.assertEqual(ra_item.normalize_classification(doc), EXE)
		self.assertEqual(doc[ASSESSMENT_NOTE_FIELD], "Class A accessory")

	def test_legacy_exempt_item_with_blank_note_stays_savable(self):
		legacy = item(EXE, None)
		self.assertEqual(ra_item.normalize_classification(item(EXE, ""), legacy), EXE)
		with throws():
			with self.assertRaises(Thrown):  # a new transition into Exempt still needs a note
				ra_item.normalize_classification(item(EXE, ""), item(NA))
			with self.assertRaises(Thrown):
				ra_item.normalize_classification(item(EXE, ""), None)
			with self.assertRaises(Thrown):
				ra_item.normalize_classification(item(EXE, " "), item(REQ))
		self.assertEqual(ra_item.normalize_classification(item(EXE, None), None, lenient=True), EXE)

	def test_legacy_blank_exempt_passes_validate_and_system_flags_skip_the_note_rule(self):
		self.assertFalse(ra_item.classification_changed(item(EXE, None), item(EXE, None)))
		TestApproverGate.run_validate(
			TestApproverGate(), FakeItem(item(EXE, None), **item(EXE, None)), approver=False
		)
		TestApproverGate.run_validate(
			TestApproverGate(), FakeItem(None, **item(EXE, None)), approver=False, system_flag="in_patch"
		)

	def test_coverage_is_cleared_unless_registration_is_required(self):
		coverage = dict(
			current_regulatory_registration="R1",
			current_newcode="NC",
			current_registration_expiry="2027-01-01",
			current_registration_status="Active",
		)
		self.assertEqual(set(coverage), set(ITEM_COVERAGE_FIELDS))
		for status, note in ((NA, None), (EXE, "reason")):
			doc = item(status, note, **coverage)
			ra_item.normalize_classification(doc)
			for fieldname in ITEM_COVERAGE_FIELDS:
				self.assertIsNone(doc[fieldname], (status, fieldname))
		required = item(REQ, **coverage)
		ra_item.normalize_classification(required)
		for fieldname, value in coverage.items():
			self.assertEqual(required[fieldname], value)

	def test_note_is_dropped_when_not_exempt_and_category_fields_are_kept(self):
		doc = item(REQ, "stale", regulatory_product_category="Drug", regulatory_owner="a@b.c")
		ra_item.normalize_classification(doc)
		self.assertIsNone(doc[ASSESSMENT_NOTE_FIELD])
		self.assertEqual(doc.regulatory_product_category, "Drug")
		self.assertEqual(doc.regulatory_owner, "a@b.c")


class FakeItem(frappe._dict):
	def __init__(self, previous=None, **values):
		super().__init__(**values)
		self._previous = previous

	def get_doc_before_save(self):
		return self._previous


class TestApproverGate(unittest.TestCase):
	"""Non-approvers may only keep the classification or set Registration Required."""

	changed = staticmethod(ra_item.classification_changed)

	def test_setting_required_never_needs_an_approver(self):
		for old in (NA, EXE, REQ, "", None):
			self.assertFalse(self.changed(item(REQ), item(old, "n" if old == EXE else None)), old)
		self.assertFalse(self.changed(item(REQ), None))

	def test_loosening_needs_an_approver(self):
		self.assertTrue(self.changed(item(NA), item(REQ)))
		self.assertTrue(self.changed(item(EXE, "reason"), item(REQ)))
		self.assertTrue(self.changed(item(NA), item(EXE, "reason")))
		self.assertTrue(self.changed(item(EXE, "reason"), item(NA)))
		self.assertTrue(self.changed(item(None), item(REQ)))

	def test_new_item_compares_against_not_assessed(self):
		self.assertFalse(self.changed(item(NA), None))
		self.assertFalse(self.changed(item(None), None))
		self.assertFalse(self.changed(item(REQ), None))
		self.assertTrue(self.changed(item(EXE, "reason"), None))

	def test_unchanged_status_needs_no_approver(self):
		for status in (NA, REQ):
			self.assertFalse(self.changed(item(status), item(status)), status)
		self.assertFalse(self.changed(item(None), item(NA)))
		self.assertFalse(self.changed(item(NA), item("")))
		self.assertFalse(self.changed(item(EXE, "same"), item(EXE, "same")))
		self.assertFalse(self.changed(item(EXE, " same "), item(EXE, "same")))

	def test_editing_the_note_while_exempt_needs_an_approver(self):
		self.assertTrue(self.changed(item(EXE, "B"), item(EXE, "A")))
		self.assertTrue(self.changed(item(EXE, "A"), item(EXE, None)))

	def test_note_edits_outside_exempt_are_ignored(self):
		self.assertFalse(self.changed(item(REQ, "x"), item(REQ, "y")))
		self.assertFalse(self.changed(item(NA, "x"), item(NA)))

	def test_category_owner_and_device_category_edits_need_no_approver(self):
		previous = item(REQ, regulatory_product_category="Drug")
		for change in (
			{"regulatory_product_category": "Food"},
			{"regulatory_other_category": "Toy"},
			{"medical_device_category": "IVD"},
			{"regulatory_owner": "x@y.z"},
		):
			self.assertFalse(self.changed(item(REQ, **{**previous, **change}), previous), change)

	def run_validate(self, doc, approver, system_flag=None):
		flags = frappe._dict({system_flag: True} if system_flag else {})
		with (
			patch.object(ra_item, "is_approver", return_value=approver),
			patch.object(ra_item.coverage, "refresh_item_coverage") as refresh,
			patch.object(frappe, "flags", flags),
		):
			ra_item.validate(doc)
		return refresh

	def test_non_approver_is_blocked_from_loosening(self):
		with throws():
			for doc in (
				FakeItem(item(REQ), **item(NA)),
				FakeItem(item(NA), **item(EXE, "reason")),
				FakeItem(item(EXE, "A"), **item(EXE, "B")),
				FakeItem(None, **item(EXE, "reason")),
			):
				with self.assertRaises(Thrown):
					self.run_validate(doc, approver=False)

	def test_block_raises_a_permission_error_naming_the_approver(self):
		with patch.object(frappe, "throw", side_effect=Thrown) as throw:
			with self.assertRaises(Thrown):
				self.run_validate(FakeItem(item(REQ), **item(NA)), approver=False)
		self.assertIn("Regulatory Approver", throw.call_args.args[0])
		self.assertIs(throw.call_args.args[1], frappe.PermissionError)

	def test_non_approver_may_tighten_and_keep(self):
		self.run_validate(FakeItem(item(NA), **item(REQ)), approver=False)
		self.run_validate(FakeItem(item(EXE, "reason"), **item(REQ)), approver=False)
		self.run_validate(FakeItem(None, **item(REQ)), approver=False)
		self.run_validate(FakeItem(None, **item(NA)), approver=False)
		self.run_validate(FakeItem(item(NA), **item(NA)), approver=False)
		self.run_validate(FakeItem(item(EXE, "reason"), **item(EXE, "reason")), approver=False)
		self.run_validate(
			FakeItem(item(REQ), **item(REQ, regulatory_product_category="Food")), approver=False
		)

	def test_approver_may_loosen(self):
		self.run_validate(FakeItem(item(REQ), **item(NA)), approver=True)
		self.run_validate(FakeItem(item(REQ), **item(EXE, "reason")), approver=True)
		self.run_validate(FakeItem(item(EXE, "A"), **item(EXE, "B")), approver=True)

	def test_import_patch_and_install_flags_bypass_the_gate(self):
		for flag in ("in_import", "in_patch", "in_install"):
			self.run_validate(FakeItem(item(REQ), **item(EXE, "reason")), approver=False, system_flag=flag)

	def test_exempt_still_needs_a_note_for_an_approver(self):
		with throws(), self.assertRaises(Thrown):
			self.run_validate(FakeItem(item(NA), **item(EXE, " ")), approver=True)

	def test_coverage_is_refreshed_only_for_required_items(self):
		self.assertEqual(self.run_validate(FakeItem(None, **item(REQ)), approver=True).call_count, 1)
		self.assertEqual(self.run_validate(FakeItem(None, **item(NA)), approver=True).call_count, 0)
		self.assertEqual(self.run_validate(FakeItem(None, **item(EXE, "r")), approver=True).call_count, 0)
