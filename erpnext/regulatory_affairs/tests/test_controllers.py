import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs.doctype.regulatory_document import (
	regulatory_document as document_module,
)
from erpnext.regulatory_affairs.doctype.regulatory_document.regulatory_document import (
	RegulatoryDocument,
)
from erpnext.regulatory_affairs.doctype.regulatory_override.regulatory_override import (
	RegulatoryOverride,
)
from erpnext.regulatory_affairs.doctype.regulatory_product_registration import (
	regulatory_product_registration as registration_module,
)
from erpnext.regulatory_affairs.doctype.regulatory_registration_action import (
	regulatory_registration_action as action_module,
)
from erpnext.regulatory_affairs.doctype.regulatory_settings.regulatory_settings import (
	RegulatorySettings as SettingsClass,
)
from erpnext.regulatory_affairs.doctype.regulatory_settings.regulatory_settings import (
	parse_day_list,
)


class Thrown(Exception):
	pass


def throws():
	return patch.object(frappe, "throw", side_effect=Thrown)


def bare(cls, **values):
	"""A controller instance without database or meta, for testing pure validation methods."""
	doc = object.__new__(cls)
	doc.__dict__.update(values)
	return doc


class FakeRegistration(frappe._dict):
	"""Stands in for a registration document in `change_status`."""

	def __init__(self, **kwargs):
		values = dict(
			official_status="Active", registered_products=[], flags=frappe._dict(), saved=0, comments=[]
		)
		values.update(kwargs)
		super().__init__(**values)

	def set(self, key, value):
		self[key] = value

	def save(self, **kwargs):
		self.saved += 1

	def add_comment(self, kind, text):
		self.comments.append(text)


class TestStatusLifecycle(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(
			registration_module, "now_datetime", return_value=datetime(2026, 10, 18, 12, 0)
		)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_transition_matrix(self):
		t = registration_module.STATUS_TRANSITIONS
		self.assertEqual(t["Draft"], ("Active",))
		for terminal in ("Revoked", "Cancelled", "Superseded"):
			self.assertEqual(t[terminal], ())
		self.assertIn("Active", t["Expired"])
		self.assertIn("Superseded", t["Expired"])
		self.assertNotIn("Active", t["Revoked"])

	def test_change_status_applies_fields_and_flags(self):
		registration = FakeRegistration()
		with patch.object(frappe, "session", frappe._dict(user="approver@example.com"), create=True):
			registration_module.change_status(registration, "Suspended", reason="Recall")
		self.assertEqual(registration.official_status, "Suspended")
		self.assertEqual(registration.status_reason, "Recall")
		self.assertEqual(registration.status_changed_by, "approver@example.com")
		self.assertTrue(registration.flags.allow_status_change)
		self.assertEqual(registration.saved, 1)
		self.assertEqual(len(registration.comments), 1)

	def test_invalid_transition_and_missing_reason_are_rejected(self):
		registration = FakeRegistration(official_status="Revoked")
		with throws(), self.assertRaises(Thrown):
			registration_module.change_status(registration, "Active")
		self.assertEqual(registration.saved, 0)
		registration = FakeRegistration()
		for status in ("Suspended", "Revoked", "Cancelled"):
			with throws(), self.assertRaises(Thrown):
				registration_module.change_status(registration, status, reason="  ")

	def test_activation_checks_only_when_enforced(self):
		registration = FakeRegistration(official_status="Draft")
		with patch.object(registration_module, "get_activation_problems", return_value=["No certificate"]):
			with throws(), self.assertRaises(Thrown):
				registration_module.change_status(registration, "Active", enforce_activation=True)
			with patch.object(frappe, "session", frappe._dict(user="u"), create=True):
				registration_module.change_status(registration, "Active", enforce_activation=False)
		self.assertEqual(registration.official_status, "Active")

	def test_changes_without_status_and_line_closing(self):
		registration = FakeRegistration(
			registered_products=[
				frappe._dict(line_status="Active", effective_from=None, effective_to=None),
				frappe._dict(line_status="Active", effective_from="2026-08-01", effective_to="2026-12-31"),
				frappe._dict(line_status="Active", effective_from="2026-01-01", effective_to="2026-03-01"),
				frappe._dict(line_status="Removed", effective_from=None, effective_to=None),
			]
		)
		registration_module.change_status(
			registration, None, changes={"renewal_status": "Approved"}, close_lines_after="2026-06-30"
		)
		self.assertEqual(registration.renewal_status, "Approved")
		self.assertEqual(registration.official_status, "Active")
		lines = registration.registered_products
		self.assertEqual(str(lines[0].effective_to), "2026-06-30")
		self.assertEqual(str(lines[1].effective_to), "2026-08-01")  # never before its own start
		self.assertEqual(str(lines[2].effective_to), "2026-03-01")  # already ends earlier
		self.assertIsNone(lines[3].effective_to)  # removed lines are untouched


class TestMappingConflict(unittest.TestCase):
	def reg(self, issue, expiry):
		return frappe._dict(issue_date=issue, expiry_date=expiry, expiry_basis="Expiry Date Specified")

	def line(self, item="ITEM-1", parent="OTHER", issue="2021-01-01", expiry="2026-06-30", **kwargs):
		return frappe._dict(
			item_code=item,
			parent=parent,
			effective_from=None,
			effective_to=None,
			issue_date=issue,
			expiry_date=expiry,
			expiry_basis="Expiry Date Specified",
			**kwargs,
		)

	def test_successor_with_later_window_does_not_conflict(self):
		own = [frappe._dict(item_code="ITEM-1", effective_from=None, effective_to=None)]
		result = registration_module.find_mapping_conflict(
			own, self.reg("2026-07-01", "2031-06-30"), [self.line()]
		)
		self.assertIsNone(result)

	def test_overlapping_window_and_lines_conflict(self):
		own = [frappe._dict(item_code="ITEM-1", effective_from=None, effective_to=None)]
		result = registration_module.find_mapping_conflict(
			own, self.reg("2026-05-01", "2031-06-30"), [self.line()]
		)
		self.assertEqual(result, ("ITEM-1", "OTHER"))

	def test_other_item_or_disjoint_lines_do_not_conflict(self):
		own = [frappe._dict(item_code="ITEM-1", effective_from="2026-07-01", effective_to=None)]
		self.assertIsNone(
			registration_module.find_mapping_conflict(
				own,
				self.reg("2021-01-01", "2031-06-30"),
				[self.line(item="ITEM-2"), self.line(expiry="2026-06-30")],
			)
		)


class TestSettingsAndTables(unittest.TestCase):
	def test_parse_day_list(self):
		self.assertEqual(parse_day_list("7, 3,1 ,0"), [7, 3, 1, 0])
		self.assertEqual(parse_day_list(""), [])
		with self.assertRaises(ValueError):
			parse_day_list("7,soon")

	def test_stage_mappings(self):
		self.assertEqual(
			set(action_module.STAGE_RENEWAL_STATUS),
			{"Preparing", "Internal Review", "Submitted", "FDA Query"},
		)
		self.assertNotIn("Approved", action_module.STAGE_RENEWAL_STATUS)


class TestOverrideWorkflow(unittest.TestCase):
	def setUp(self):
		from erpnext.regulatory_affairs.doctype.regulatory_override import (
			regulatory_override as override_module,
		)

		patcher = patch.object(override_module, "today", return_value="2026-10-18")
		patcher.start()
		self.addCleanup(patcher.stop)

	def override(self, **values):
		defaults = dict(
			status="Requested",
			requested_by="maker@example.com",
			transaction_doctype="Sales Invoice",
			transaction_name="SINV-1",
			item_code=None,
			reason="Urgent",
		)
		defaults.update(values)
		return bare(RegulatoryOverride, **defaults)

	def session(self, user):
		return patch.object(frappe, "session", frappe._dict(user=user), create=True)

	def test_requester_cannot_approve_own_override(self):
		override = self.override(status="Approved")
		previous = frappe._dict(status="Requested", requested_by="maker@example.com")
		with (
			self.session("maker@example.com"),
			patch.object(frappe, "get_roles", return_value=["Regulatory Approver"]),
			throws(),
		):
			with self.assertRaises(Thrown):
				override._validate_status(previous)

	def test_other_approver_can_approve_and_is_stamped(self):
		override = self.override(status="Approved")
		previous = frappe._dict(status="Requested", requested_by="maker@example.com")
		with (
			self.session("checker@example.com"),
			patch.object(frappe, "get_roles", return_value=["Regulatory Approver"]),
		):
			override._validate_status(previous)
		self.assertEqual(override.approved_by, "checker@example.com")

	def test_non_approver_cannot_approve_or_reject(self):
		for status in ("Approved", "Rejected"):
			override = self.override(status=status)
			previous = frappe._dict(status="Requested", requested_by="maker@example.com")
			with (
				self.session("checker@example.com"),
				patch.object(frappe, "get_roles", return_value=["Regulatory Operator"]),
				throws(),
			):
				with self.assertRaises(Thrown):
					override._validate_status(previous)

	def test_approved_override_is_frozen_and_terminal_states_stay(self):
		approved_before = frappe._dict(
			status="Approved",
			requested_by="maker@example.com",
			transaction_doctype="Sales Invoice",
			transaction_name="SINV-1",
			item_code=None,
			reason="Urgent",
		)
		edited = self.override(status="Approved", reason="Changed")
		with (
			self.session("checker@example.com"),
			patch.object(frappe, "get_roles", return_value=["Regulatory Approver"]),
			throws(),
		):
			with self.assertRaises(Thrown):
				edited._validate_status(approved_before)
			rejected_before = frappe._dict(status="Rejected", requested_by="maker@example.com")
			with self.assertRaises(Thrown):
				self.override(status="Approved")._validate_status(rejected_before)


class TestDocumentVerification(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(document_module, "today", return_value="2026-10-18")
		patcher.start()
		self.addCleanup(patcher.stop)

	def document(self, status, previous_status, **values):
		defaults = dict(verification_status=status, expiry_date=None, verified_by=None, verified_on=None)
		defaults.update(values)
		doc = bare(RegulatoryDocument, **defaults)
		doc._doc_before_save = frappe._dict(verification_status=previous_status) if previous_status else None
		return doc

	def test_only_approver_can_verify_and_stamp_happens_once(self):
		with patch.object(frappe, "session", frappe._dict(user="checker@example.com"), create=True):
			with patch.object(frappe, "get_roles", return_value=["Regulatory Operator"]), throws():
				with self.assertRaises(Thrown):
					self.document("Verified", "Pending")._validate_verification()
			with patch.object(frappe, "get_roles", return_value=["Regulatory Approver"]):
				verified = self.document("Verified", "Pending")
				verified._validate_verification()
				self.assertEqual(verified.verified_by, "checker@example.com")
				# saving again without a status change must not restamp
				again = self.document("Verified", "Verified", verified_by="someone@example.com")
				again._validate_verification()
				self.assertEqual(again.verified_by, "someone@example.com")

	def test_expired_document_cannot_be_verified(self):
		doc = self.document("Verified", "Pending")
		doc.expiry_date = "2020-01-01"
		with (
			patch.object(frappe, "get_roles", return_value=["Regulatory Approver"]),
			patch.object(frappe, "session", frappe._dict(user="checker@example.com"), create=True),
			throws(),
		):
			with self.assertRaises(Thrown):
				doc._validate_verification()


class TestRegistrationCategory(unittest.TestCase):
	def category(self, **values):
		doc = bare(registration_module.RegulatoryProductRegistration, **values)
		doc.set = lambda key, value: setattr(doc, key, value)
		doc._validate_category()
		return doc

	def test_blank_category_defaults_to_medical_device(self):
		self.assertEqual(self.category(product_category=None).product_category, "Medical Device")

	def test_other_needs_a_description_and_other_categories_clear_it(self):
		with throws(), self.assertRaises(Thrown):
			self.category(product_category="Other", other_category="  ")
		self.assertEqual(
			self.category(product_category="Other", other_category=" Pesticide ").other_category, "Pesticide"
		)
		self.assertIsNone(self.category(product_category="Food", other_category="stale").other_category)

	def test_device_only_fields_are_cleared_for_other_categories(self):
		values = {name: "x" for name in registration_module.DEVICE_ONLY_FIELDS}
		food = self.category(product_category="Food", **values)
		for name in registration_module.DEVICE_ONLY_FIELDS:
			self.assertIsNone(getattr(food, name), name)
		device = self.category(product_category="Medical Device", **values)
		self.assertEqual(device.risk_class, "x")

	def test_unknown_category_is_rejected(self):
		with throws(), self.assertRaises(Thrown):
			self.category(product_category="Toy")

	def test_non_device_categories_need_no_device_data(self):
		for category in ("Food", "Drug", "Cosmetic", "Hazardous Substance", "Herbal Product"):
			doc = self.category(product_category=category)
			self.assertEqual(doc.product_category, category)

	def test_json_shape(self):
		import json
		from pathlib import Path

		path = Path(registration_module.__file__).with_suffix(".json")
		fields = {f["fieldname"]: f for f in json.loads(path.read_text())["fields"]}
		self.assertEqual(fields["product_category"]["reqd"], 1)
		self.assertEqual(fields["product_category"]["default"], "Medical Device")
		self.assertIn("Other", fields["activity_type"]["options"])
		for name in ("device_category", "risk_class", "grouping_type", "device_description", "intended_use"):
			self.assertIn("Medical Device", fields[name]["depends_on"], name)
		self.assertEqual(fields["legacy_renewal_history"]["options"], "Regulatory Renewal History")
		self.assertEqual(fields["legacy_thai_fda_records"]["read_only"], 1)

	def test_registered_rows_warn_for_items_not_marked_required(self):
		rows = [
			frappe._dict(
				idx=i + 1,
				item_code=code,
				effective_from=None,
				effective_to=None,
				line_status="Active",
				newcode="N",
				identifier=None,
			)
			for i, code in enumerate(("REQ", "EXEMPT"))
		]
		doc = bare(registration_module.RegulatoryProductRegistration, registered_products=rows)
		doc.get = lambda key, default=None: getattr(doc, key, default)
		items = [
			frappe._dict(name="REQ", item_name="Req", custom_thai_fda_applicability="Registration Required"),
			frappe._dict(
				name="EXEMPT", item_name="Ex", custom_thai_fda_applicability="Not Required / Exempt"
			),
		]
		with (
			patch.object(frappe, "get_all", return_value=items),
			patch.object(frappe, "msgprint") as msgprint,
			patch.object(registration_module, "get_other_lines", return_value=[]),
		):
			doc._validate_registered_product_rows()
		msgprint.assert_called_once()
		message = msgprint.call_args.args[0]
		self.assertIn("EXEMPT", message)
		self.assertNotIn("REQ</b>", message)
		self.assertIn("Registration Required", message)


class TestSettingsRecipients(unittest.TestCase):
	def test_duplicate_recipient_is_rejected(self):
		rows = [frappe._dict(idx=1, user="a@x.com"), frappe._dict(idx=2, user="a@x.com")]
		doc = bare(SettingsClass, recipients=rows)
		doc.get = lambda key, default=None: getattr(doc, key, default)
		with throws(), self.assertRaises(Thrown):
			doc._validate_recipients()
		doc.recipients = rows[:1]
		doc._validate_recipients()


class TestRegistrationHardening(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "flags", frappe._dict())
		patcher.start()
		self.addCleanup(patcher.stop)

	def registration(self, previous_status="Active", **values):
		defaults = dict(
			flags=frappe._dict(),
			official_status="Active",
			issue_date="2026-01-01",
			expiry_date="2031-01-01",
			expiry_basis="Expiry Date Specified",
		)
		defaults.update(values)
		doc = bare(registration_module.RegulatoryProductRegistration, **defaults)
		doc._doc_before_save = frappe._dict(
			official_status=previous_status,
			issue_date="2026-01-01",
			expiry_date="2031-01-01",
			expiry_basis="Expiry Date Specified",
		)
		return doc

	def validity_edit(self, doc, approver):
		with patch.object(registration_module, "is_approver", return_value=approver):
			doc._validate_validity_edit()

	def test_operator_cannot_edit_expiry_of_a_live_registration(self):
		doc = self.registration(expiry_date="2099-01-01")
		with throws(), self.assertRaises(Thrown):
			self.validity_edit(doc, approver=False)

	def test_expiry_edit_is_allowed_for_approver_system_and_drafts(self):
		self.validity_edit(self.registration(expiry_date="2099-01-01"), approver=True)
		system = self.registration(expiry_date="2099-01-01")
		system.flags.allow_status_change = True
		self.validity_edit(system, approver=False)
		self.validity_edit(
			self.registration(previous_status="Draft", expiry_date="2099-01-01"), approver=False
		)
		self.validity_edit(self.registration(), approver=False)

	def test_expired_and_superseded_cannot_be_set_by_hand(self):
		for status in ("Expired", "Superseded"):
			doc = self.registration()
			doc.check_permission = lambda *args: None
			with throws(), self.assertRaises(Thrown):
				doc.set_official_status(status, "x")

	def test_activating_a_successor_ends_the_predecessor_lines(self):
		successor = FakeRegistration(
			previous_registration="OLD", issue_date="2026-07-01", official_status="Draft"
		)
		predecessor = FakeRegistration(
			official_status="Active",
			registered_products=[frappe._dict(line_status="Active", effective_from=None, effective_to=None)],
		)
		with (
			patch.object(frappe, "get_doc", return_value=predecessor),
			patch.object(registration_module, "change_status") as change,
		):
			registration_module.close_predecessor_lines(successor)
		change.assert_called_once()
		self.assertEqual(change.call_args.args[1], None)
		self.assertEqual(str(change.call_args.kwargs["close_lines_after"]), "2026-06-30")

	def test_predecessor_is_left_alone_when_nothing_to_close(self):
		cases = (
			(FakeRegistration(previous_registration=None, issue_date="2026-07-01"), None),
			(FakeRegistration(previous_registration="OLD", issue_date=None), None),
			(
				FakeRegistration(previous_registration="OLD", issue_date="2026-07-01"),
				FakeRegistration(
					official_status="Cancelled",
					registered_products=[frappe._dict(line_status="Active", effective_to=None)],
				),
			),
			(
				FakeRegistration(previous_registration="OLD", issue_date="2026-07-01"),
				FakeRegistration(
					registered_products=[
						frappe._dict(line_status="Active", effective_from=None, effective_to="2026-06-30")
					]
				),
			),
		)
		for successor, predecessor in cases:
			with (
				patch.object(frappe, "get_doc", return_value=predecessor),
				patch.object(registration_module, "change_status") as change,
			):
				registration_module.close_predecessor_lines(successor)
			change.assert_not_called()


class TestHardenedAudit(unittest.TestCase):
	def test_override_audit_stamps_cannot_be_changed(self):
		override = bare(
			RegulatoryOverride,
			status="Approved",
			requested_by="forged@example.com",
			requested_on="2000-01-01",
			approved_by="forged@example.com",
			approved_on="2000-01-01",
		)
		previous = frappe._dict(
			status="Approved",
			requested_by="maker@example.com",
			requested_on="2026-10-01",
			approved_by="checker@example.com",
			approved_on="2026-10-02",
		)
		override._protect_audit_stamps(previous)
		self.assertEqual(
			(override.requested_by, override.requested_on, override.approved_by, override.approved_on),
			("maker@example.com", "2026-10-01", "checker@example.com", "2026-10-02"),
		)

	def test_new_override_cannot_arrive_pre_approved_by_stamp(self):
		override = bare(
			RegulatoryOverride,
			status="Requested",
			approved_by="x@example.com",
			approved_on="2026-01-01",
		)
		override._protect_audit_stamps(None)
		self.assertEqual((override.approved_by, override.approved_on), (None, None))

	def test_editing_a_verified_document_voids_the_verification(self):
		doc = bare(
			RegulatoryDocument,
			verification_status="Verified",
			verified_by="checker@example.com",
			verified_on="2026-10-01",
			document_type="Certificate",
			file="/files/new.pdf",
			expiry_date=None,
			registration="FDA-1",
		)
		doc._doc_before_save = frappe._dict(
			verification_status="Verified",
			document_type="Certificate",
			file="/files/old.pdf",
			expiry_date=None,
			registration="FDA-1",
		)
		with patch.object(frappe, "msgprint") as message:
			doc._validate_verification()
		self.assertEqual(doc.verification_status, "Pending")
		self.assertIsNone(doc.verified_by)
		message.assert_called_once()

	def test_untouched_verified_document_stays_verified(self):
		doc = bare(
			RegulatoryDocument,
			verification_status="Verified",
			verified_by="checker@example.com",
			document_type="Certificate",
			file="/files/old.pdf",
			expiry_date=None,
			registration="FDA-1",
			remarks="added a note",
		)
		doc._doc_before_save = frappe._dict(
			verification_status="Verified",
			document_type="Certificate",
			file="/files/old.pdf",
			expiry_date=None,
			registration="FDA-1",
			remarks="",
		)
		doc._validate_verification()
		self.assertEqual(doc.verification_status, "Verified")
		self.assertEqual(doc.verified_by, "checker@example.com")


class TestActionSync(unittest.TestCase):
	def action(self, stage="Submitted"):
		return bare(
			action_module.RegulatoryRegistrationAction,
			action_type="Renewal",
			current_stage=stage,
			registration="FDA-1",
			name="ACT-1",
		)

	def test_stage_updates_renewal_status_unless_not_renewing(self):
		for current, writes in (("Preparing", 1), ("Not Renewing", 0)):
			db = MagicMock()
			db.get_value.return_value = current
			with patch.object(frappe, "db", db):
				self.action()._sync_registration()
			self.assertEqual(db.set_value.call_count, writes, current)
			if writes:
				self.assertEqual(db.set_value.call_args.args[3], "Submitted")


if __name__ == "__main__":
	unittest.main()
