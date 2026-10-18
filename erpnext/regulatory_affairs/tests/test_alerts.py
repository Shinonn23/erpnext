import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs import alerts
from erpnext.regulatory_affairs.constants import (
	ALERT_DOCUMENT,
	ALERT_ESTABLISHMENT,
	ALERT_FDA_QUERY,
	ALERT_LOG,
	ALERT_REGISTRATION,
	ESTABLISHMENT_REGISTRATION,
	PRODUCT_REGISTRATION,
	REGULATORY_DOCUMENT,
)

TODAY = date(2026, 10, 18)


def make_rule(days, severity="Info", **values):
	rule = frappe._dict(days_before_expiry=days, severity=severity, action=f"Action {days}")
	rule.update(values)
	return rule


def make_rules(days=(365, 180, 60, 30, 14, 7, 0)):
	return [make_rule(day) for day in days]


def make_registration(**values):
	row = frappe._dict(
		name="FDA-1",
		official_status="Active",
		expiry_basis="Expiry Date Specified",
		expiry_date=date(2026, 11, 12),  # 25 days after TODAY
		days_to_expiry=25,
		renewal_status="Not Started",
		superseded_by=None,
		responsible_user="owner@example.com",
	)
	row.update(values)
	return row


def make_document(**values):
	row = frappe._dict(
		name="DOC-1",
		registration="FDA-1",
		document_type="Certificate",
		document_number="C-77",
		expiry_date=date(2026, 10, 28),  # 10 days after TODAY
		verification_status="Verified",
	)
	row.update(values)
	return row


def make_action(**values):
	row = frappe._dict(
		name="ACT-1",
		registration="FDA-1",
		action_type="Renewal",
		current_stage="FDA Query",
		response_due_date=date(2026, 10, 20),  # 2 days after TODAY
		assigned_to=None,
	)
	row.update(values)
	return row


def make_run(**settings):
	values = {
		"enable_expiry_alerts": 1,
		"send_system_notification": 1,
		"send_email": 1,
		"overdue_repeat_days": 7,
		"fda_query_alert_days": "7,3,1,0",
		"auto_create_renewal_action": 1,
		"renewal_start_days_before_expiry": 365,
		"default_regulatory_owner": None,
		"send_weekly_digest": 1,
		"expiry_alert_rules": make_rules(),
	}
	values.update(settings)
	return alerts._Run(frappe._dict(values), TODAY)


class TestSelectRule(unittest.TestCase):
	def test_tightest_rule_catches_up_after_a_missed_run(self):
		threshold, rule = alerts.select_rule(25, make_rules(), 7)
		self.assertEqual(threshold, 30)
		self.assertEqual(rule.action, "Action 30")

	def test_exact_boundary_days(self):
		rules = make_rules()
		days = [365, 180, 60, 30, 14, 7, 0]
		for index, day in enumerate(days):
			with self.subTest(day=day):
				self.assertEqual(alerts.select_rule(day, rules, 7)[0], day)
				if index:
					# One day more than a threshold belongs to the next looser rule.
					self.assertEqual(alerts.select_rule(day + 1, rules, 7)[0], days[index - 1])
		self.assertIsNone(alerts.select_rule(366, rules, 7))

	def test_no_rule_when_there_are_no_rules(self):
		self.assertIsNone(alerts.select_rule(10, [], 7))

	def test_looser_thresholds_are_not_replayed_after_a_gap(self):
		rules = make_rules()
		fired = []
		for days in (400, 200, 25, 10, 0):
			selected = alerts.select_rule(days, rules, 7)
			if selected and selected[0] not in fired:
				fired.append(selected[0])
		# 180 and 60 were skipped by the gaps and are never sent late.
		self.assertEqual(fired, [365, 30, 14, 0])

	def test_daily_runs_fire_every_threshold_exactly_once(self):
		rules = make_rules()
		fired = []
		for days in range(400, -1, -1):
			selected = alerts.select_rule(days, rules, 7)
			if selected and selected[0] not in fired:
				fired.append(selected[0])
		self.assertEqual(fired, [365, 180, 60, 30, 14, 7, 0])

	def test_rule_order_and_duplicates_do_not_matter(self):
		rules = [make_rule(7), make_rule(30, action="first"), make_rule(0), make_rule(30, action="second")]
		threshold, rule = alerts.select_rule(20, rules, 7)
		self.assertEqual((threshold, rule.action), (30, "first"))
		self.assertEqual(alerts.select_rule(5, rules, 7)[0], 7)

	def test_overdue_alerts_on_day_one_then_every_interval(self):
		rules = make_rules()
		due = {}
		for days_overdue in range(1, 24):
			selected = alerts.select_rule(-days_overdue, rules, 7)
			if selected:
				due[days_overdue] = selected[0]
		self.assertEqual(due, {1: -1, 8: -8, 15: -15, 22: -22})

	def test_overdue_interval_falls_back_to_default_and_can_be_daily(self):
		for blank in (0, None, ""):
			self.assertIsNone(alerts.select_rule(-2, [], blank))
			self.assertEqual(alerts.select_rule(-8, [], blank)[0], -8)
		self.assertEqual(alerts.select_rule(-2, [], 1)[0], -2)

	def test_overdue_severity_is_expired_and_action_comes_from_zero_day_rule(self):
		rules = [make_rule(30), make_rule(0, severity="Critical", recipient_role="Regulatory Approver")]
		threshold, rule = alerts.select_rule(-1, rules, 7)
		self.assertEqual(threshold, -1)
		self.assertEqual(rule.severity, "Expired")
		self.assertEqual(rule.action, "Action 0")
		self.assertEqual(rule.recipient_role, "Regulatory Approver")
		self.assertEqual(alerts.select_rule(-1, rules, 7, "Critical")[1].severity, "Critical")

	def test_overdue_without_zero_day_rule_uses_defaults(self):
		_threshold, rule = alerts.select_rule(-1, [make_rule(30)], 7)
		self.assertEqual(rule.severity, "Expired")
		self.assertEqual(rule.action, "Overdue follow-up")
		self.assertFalse(rule.recipient_role)
		self.assertFalse(rule.recipient_user)

	def test_rule_for_threshold_finds_the_rule_behind_a_stored_threshold(self):
		rules = [make_rule(30), make_rule(0, recipient_user="a@example.com")]
		self.assertEqual(alerts.rule_for_threshold(30, rules).action, "Action 30")
		self.assertEqual(alerts.rule_for_threshold(31, rules), {})
		overdue = alerts.rule_for_threshold(-8, rules)
		self.assertEqual((overdue.severity, overdue.recipient_user), ("Expired", "a@example.com"))

	def test_days_until(self):
		self.assertEqual(alerts.days_until(date(2026, 11, 12), TODAY), 25)
		self.assertEqual(alerts.days_until("2026-10-18", TODAY), 0)
		self.assertEqual(alerts.days_until(date(2026, 10, 17), "2026-10-18"), -1)


class TestDedupeKey(unittest.TestCase):
	def test_key_is_a_stable_sha256_of_the_documented_parts(self):
		import hashlib

		key = alerts.make_dedupe_key(
			ALERT_REGISTRATION, PRODUCT_REGISTRATION, "FDA-1", date(2026, 11, 12), 30
		)
		expected = hashlib.sha256(
			b"Registration Expiry|Regulatory Product Registration|FDA-1|2026-11-12|30"
		).hexdigest()
		self.assertEqual(key, expected)
		self.assertEqual(len(key), 64)

	def test_date_and_text_expiry_give_the_same_key(self):
		args = (ALERT_REGISTRATION, PRODUCT_REGISTRATION, "FDA-1")
		self.assertEqual(
			alerts.make_dedupe_key(*args, date(2026, 11, 12), 30),
			alerts.make_dedupe_key(*args, "2026-11-12", "30"),
		)

	def test_a_new_expiry_date_rearms_every_threshold(self):
		args = (ALERT_REGISTRATION, PRODUCT_REGISTRATION, "FDA-1")
		self.assertNotEqual(
			alerts.make_dedupe_key(*args, date(2026, 11, 12), 30),
			alerts.make_dedupe_key(*args, date(2031, 11, 12), 30),
		)

	def test_every_part_changes_the_key(self):
		base = (ALERT_REGISTRATION, PRODUCT_REGISTRATION, "FDA-1", date(2026, 11, 12), 30)
		keys = {alerts.make_dedupe_key(*base)}
		for index, other in enumerate(
			(ALERT_DOCUMENT, ESTABLISHMENT_REGISTRATION, "FDA-2", date(2026, 11, 13), 14)
		):
			changed = list(base)
			changed[index] = other
			keys.add(alerts.make_dedupe_key(*changed))
		self.assertEqual(len(keys), 6)


class TestFdaQueryDays(unittest.TestCase):
	def test_parse_alert_days(self):
		self.assertEqual(alerts.parse_alert_days("7,3,1,0"), [0, 1, 3, 7])
		self.assertEqual(alerts.parse_alert_days(" 10 , 5,,5 "), [5, 10])
		self.assertEqual(alerts.parse_alert_days("14"), [14])
		self.assertEqual(alerts.parse_alert_days(14), [14])

	def test_blank_value_means_the_default(self):
		for blank in (None, "", "   "):
			self.assertEqual(alerts.parse_alert_days(blank), [0, 1, 3, 7])

	def test_invalid_text_raises(self):
		for value in ("7;3", "a,b", "7,-1", "3.5", ",,"):
			with self.subTest(value=value), self.assertRaises(ValueError):
				alerts.parse_alert_days(value)

	def test_invalid_text_falls_back_and_is_logged(self):
		with patch.object(frappe, "log_error") as log_error:
			self.assertEqual(alerts.get_fda_query_alert_days("soon"), [0, 1, 3, 7])
			log_error.assert_called_once()
			log_error.reset_mock()
			self.assertEqual(alerts.get_fda_query_alert_days("5, 2"), [2, 5])
			log_error.assert_not_called()

	def test_query_rules_use_the_same_tightest_threshold_logic(self):
		rules = alerts.build_fda_query_rules(alerts.parse_alert_days("7,3,1,0"))
		self.assertEqual({rule.days_before_expiry: rule.severity for rule in rules}[7], "Warning")
		self.assertEqual({rule.days_before_expiry: rule.severity for rule in rules}[1], "Critical")
		expected = {8: None, 7: 7, 5: 7, 3: 3, 2: 3, 1: 1, 0: 0}
		for days, threshold in expected.items():
			selected = alerts.select_rule(days, rules, 7, alerts.overdue_severity_for(ALERT_FDA_QUERY))
			self.assertEqual(selected[0] if selected else None, threshold, days)
		overdue = alerts.select_rule(-1, rules, 7, alerts.overdue_severity_for(ALERT_FDA_QUERY))
		self.assertEqual((overdue[0], overdue[1].severity), (-1, "Critical"))

	def test_overdue_severity(self):
		self.assertEqual(alerts.overdue_severity_for(ALERT_FDA_QUERY), "Critical")
		for kind in (ALERT_REGISTRATION, ALERT_ESTABLISHMENT, ALERT_DOCUMENT):
			self.assertEqual(alerts.overdue_severity_for(kind), "Expired")


class TestEligibility(unittest.TestCase):
	def test_active_and_expired_registrations_with_a_stated_expiry_are_eligible(self):
		for status in ("Active", "Expired"):
			self.assertTrue(alerts.is_expiry_alert_eligible(make_registration(official_status=status)))

	def test_other_statuses_are_not_eligible(self):
		for status in ("Draft", "Suspended", "Revoked", "Cancelled", "Superseded"):
			with self.subTest(status=status):
				self.assertFalse(alerts.is_expiry_alert_eligible(make_registration(official_status=status)))

	def test_not_renewing_is_not_eligible(self):
		self.assertFalse(alerts.is_expiry_alert_eligible(make_registration(renewal_status="Not Renewing")))

	def test_superseded_registration_is_not_eligible(self):
		self.assertFalse(alerts.is_expiry_alert_eligible(make_registration(superseded_by="FDA-2")))

	def test_no_expiry_and_unconfirmed_are_not_eligible(self):
		for basis in ("No Expiry Date Specified", "Unconfirmed"):
			row = make_registration(expiry_basis=basis)
			self.assertFalse(alerts.is_expiry_alert_eligible(row))
		self.assertFalse(alerts.is_expiry_alert_eligible(make_registration(expiry_date=None)))

	def test_establishment_rows_use_the_same_predicate(self):
		row = make_registration()
		del row["superseded_by"]
		self.assertTrue(alerts.is_expiry_alert_eligible(row))
		row.official_status = "Cancelled"
		self.assertFalse(alerts.is_expiry_alert_eligible(row))

	def test_document_eligibility(self):
		registration = make_registration()
		for status in ("Verified", "Expired"):
			self.assertTrue(
				alerts.is_document_alert_eligible(make_document(verification_status=status), registration)
			)
		for status in ("Pending", "Rejected"):
			self.assertFalse(
				alerts.is_document_alert_eligible(make_document(verification_status=status), registration)
			)
		self.assertFalse(alerts.is_document_alert_eligible(make_document(expiry_date=None), registration))
		self.assertFalse(alerts.is_document_alert_eligible(make_document(), None))
		for status in ("Draft", "Suspended", "Revoked", "Cancelled", "Superseded"):
			self.assertFalse(
				alerts.is_document_alert_eligible(make_document(), make_registration(official_status=status))
			)
		self.assertFalse(
			alerts.is_document_alert_eligible(
				make_document(), make_registration(renewal_status="Not Renewing")
			)
		)

	def test_fda_query_eligibility(self):
		self.assertTrue(alerts.is_fda_query_eligible(make_action()))
		self.assertFalse(alerts.is_fda_query_eligible(make_action(response_due_date=None)))
		for stage in ("Preparing", "Submitted", "Approved", "Rejected", "Cancelled"):
			self.assertFalse(alerts.is_fda_query_eligible(make_action(current_stage=stage)))

	def test_contexts_are_none_when_not_eligible(self):
		self.assertIsNone(alerts.registration_context(make_registration(official_status="Draft")))
		self.assertIsNone(alerts.establishment_context(make_registration(expiry_basis="Unconfirmed")))
		self.assertIsNone(alerts.document_context(make_document(), None))
		self.assertIsNone(
			alerts.fda_query_context(make_action(current_stage="Approved"), make_registration())
		)

	def test_context_fields(self):
		context = alerts.registration_context(make_registration())
		self.assertEqual(
			(context.kind, context.reference_doctype, context.reference_name, context.registration),
			(ALERT_REGISTRATION, PRODUCT_REGISTRATION, "FDA-1", "FDA-1"),
		)
		self.assertEqual(context.expiry_date, date(2026, 11, 12))

		establishment = alerts.establishment_context(
			make_registration(name="EST-1", establishment_name="Main Office")
		)
		self.assertEqual((establishment.kind, establishment.registration), (ALERT_ESTABLISHMENT, None))
		self.assertEqual(establishment.label, "Main Office (EST-1)")

		document = alerts.document_context(
			make_document(), make_registration(responsible_user="reg@example.com")
		)
		self.assertEqual(
			(document.kind, document.registration, document.regulatory_document, document.responsible_user),
			(ALERT_DOCUMENT, "FDA-1", "DOC-1", "reg@example.com"),
		)

		query = alerts.fda_query_context(make_action(), make_registration())
		self.assertEqual(
			(query.kind, query.reference_name, query.registration), (ALERT_FDA_QUERY, "ACT-1", "FDA-1")
		)
		self.assertEqual(query.expiry_date, date(2026, 10, 20))
		# The assignee wins over the registration's responsible user.
		self.assertEqual(query.responsible_user, "owner@example.com")
		query = alerts.fda_query_context(make_action(assigned_to="me@example.com"), make_registration())
		self.assertEqual(query.responsible_user, "me@example.com")


class TestMaintenancePredicates(unittest.TestCase):
	def test_days_to_expiry_value(self):
		self.assertEqual(alerts.days_to_expiry_value(make_registration(), TODAY), 25)
		self.assertEqual(
			alerts.days_to_expiry_value(make_registration(expiry_date=date(2026, 10, 1)), TODAY), -17
		)
		for basis in ("No Expiry Date Specified", "Unconfirmed"):
			self.assertIsNone(alerts.days_to_expiry_value(make_registration(expiry_basis=basis), TODAY))
		self.assertIsNone(alerts.days_to_expiry_value(make_registration(expiry_date=None), TODAY))

	def test_only_active_records_past_their_expiry_are_auto_expired(self):
		past = date(2026, 10, 17)
		self.assertTrue(alerts.should_auto_expire(make_registration(expiry_date=past), TODAY))
		# The expiry day itself is still in force.
		self.assertFalse(alerts.should_auto_expire(make_registration(expiry_date=TODAY), TODAY))
		for status in ("Expired", "Suspended", "Draft"):
			self.assertFalse(
				alerts.should_auto_expire(make_registration(expiry_date=past, official_status=status), TODAY)
			)
		self.assertFalse(
			alerts.should_auto_expire(make_registration(expiry_date=past, expiry_basis="Unconfirmed"), TODAY)
		)

	def test_only_verified_documents_past_their_expiry_expire(self):
		past = date(2026, 10, 17)
		self.assertTrue(alerts.should_expire_document(make_document(expiry_date=past), TODAY))
		self.assertFalse(alerts.should_expire_document(make_document(expiry_date=TODAY), TODAY))
		for status in ("Pending", "Rejected", "Expired"):
			self.assertFalse(
				alerts.should_expire_document(
					make_document(expiry_date=past, verification_status=status), TODAY
				)
			)
		self.assertFalse(alerts.should_expire_document(make_document(expiry_date=None), TODAY))

	def test_needs_maintenance_only_when_the_stored_values_differ(self):
		self.assertFalse(alerts.needs_maintenance(make_registration(), TODAY))
		self.assertTrue(alerts.needs_maintenance(make_registration(days_to_expiry=26), TODAY))
		self.assertTrue(
			alerts.needs_maintenance(
				make_registration(expiry_date=date(2026, 10, 1), days_to_expiry=-17), TODAY
			)
		)
		self.assertFalse(
			alerts.needs_maintenance(
				make_registration(expiry_basis="Unconfirmed", days_to_expiry=None), TODAY
			)
		)

	def test_renewal_window(self):
		row = make_registration()
		self.assertTrue(alerts.needs_renewal_action(row, TODAY, 365))
		self.assertTrue(alerts.needs_renewal_action(row, TODAY, 25))
		self.assertFalse(alerts.needs_renewal_action(row, TODAY, 24))
		self.assertTrue(alerts.needs_renewal_action(make_registration(official_status="Expired"), TODAY, 365))
		self.assertTrue(
			alerts.needs_renewal_action(make_registration(expiry_date=date(2026, 10, 1)), TODAY, 365)
		)
		for excluded in (
			{"renewal_status": "Not Renewing"},
			{"superseded_by": "FDA-2"},
			{"expiry_basis": "No Expiry Date Specified"},
			{"official_status": "Draft"},
		):
			self.assertFalse(alerts.needs_renewal_action(make_registration(**excluded), TODAY, 365))


class TestRecipients(unittest.TestCase):
	def test_primary_recipients_win_over_the_fallbacks(self):
		result = alerts.resolve_recipients(
			["b@x.com", "a@x.com", None, "b@x.com"], "owner@x.com", ["appr@x.com"]
		)
		self.assertEqual(result, ["a@x.com", "b@x.com"])

	def test_falls_back_to_the_default_owner_then_to_approvers(self):
		self.assertEqual(alerts.resolve_recipients([], "owner@x.com", ["appr@x.com"]), ["owner@x.com"])
		self.assertEqual(
			alerts.resolve_recipients([None], None, ["z@x.com", "appr@x.com"]), ["appr@x.com", "z@x.com"]
		)
		self.assertEqual(alerts.resolve_recipients([], None, []), [])

	def test_disabled_users_are_dropped_before_falling_back(self):
		enabled = {"owner@x.com", "appr@x.com"}
		self.assertEqual(
			alerts.resolve_recipients(["gone@x.com"], "owner@x.com", ["appr@x.com"], enabled), ["owner@x.com"]
		)
		self.assertEqual(
			alerts.resolve_recipients(["gone@x.com"], "off@x.com", ["appr@x.com"], enabled), ["appr@x.com"]
		)
		self.assertEqual(alerts.resolve_recipients(["gone@x.com"], "off@x.com", ["off2@x.com"], enabled), [])

	def _users(self, *names, disabled=()):
		return [frappe._dict(name=name, email=name, enabled=0 if name in disabled else 1) for name in names]

	def test_run_resolves_rule_user_role_members_and_responsible_user(self):
		run = make_run()
		roles = {"Regulatory Operator": ["role@x.com"], "Regulatory Approver": ["appr@x.com"]}
		users = self._users("rule@x.com", "role@x.com", "resp@x.com", "owner@x.com", "appr@x.com")
		rule = make_rule(30, recipient_user="rule@x.com", recipient_role="Regulatory Operator")
		with (
			patch.object(alerts, "get_users_with_role", side_effect=lambda role: roles[role]),
			patch.object(frappe, "get_all", return_value=users) as get_all,
		):
			result = run.recipients(rule, "resp@x.com")
			self.assertEqual(result, ["resp@x.com", "role@x.com", "rule@x.com"])
			# Users and roles are cached for the rest of the run.
			run.recipients(rule, "resp@x.com")
			get_all.assert_called_once()

	def test_run_falls_back_to_default_owner_then_approvers(self):
		roles = {"Regulatory Approver": ["appr@x.com"]}
		users = self._users("owner@x.com", "appr@x.com", "resp@x.com", disabled=("resp@x.com",))
		with (
			patch.object(alerts, "get_users_with_role", side_effect=lambda role: roles[role]),
			patch.object(frappe, "get_all", return_value=users),
		):
			run = make_run(default_regulatory_owner="owner@x.com")
			self.assertEqual(run.recipients(frappe._dict(), "resp@x.com"), ["owner@x.com"])
			run = make_run()
			self.assertEqual(run.recipients(frappe._dict(), "resp@x.com"), ["appr@x.com"])
			self.assertEqual(run.email("appr@x.com"), "appr@x.com")

	def test_settings_recipients_are_added_deduplicated_and_must_be_enabled(self):
		rows = [
			frappe._dict(user="extra@x.com", enabled=1),
			frappe._dict(user="extra@x.com", enabled=1),
			frappe._dict(user="off@x.com", enabled=0),
			frappe._dict(user="gone@x.com", enabled=1),
		]
		self.assertEqual(alerts.settings_recipients(rows), ["extra@x.com", "gone@x.com"])
		self.assertEqual(alerts.settings_recipients(None), [])
		enabled = {"a@x.com", "extra@x.com"}
		self.assertEqual(
			alerts.resolve_recipients(
				["a@x.com"], None, [], enabled, ["extra@x.com", "gone@x.com", "a@x.com"]
			),
			["a@x.com", "extra@x.com"],
		)
		self.assertEqual(alerts.resolve_recipients([], None, [], enabled, ["extra@x.com"]), ["extra@x.com"])

	def test_run_includes_enabled_settings_recipients(self):
		roles = {"Regulatory Approver": ["appr@x.com"]}
		users = self._users("owner@x.com", "appr@x.com", "extra@x.com", "off@x.com", disabled=("off@x.com",))
		recipients = [
			frappe._dict(user="extra@x.com", enabled=1),
			frappe._dict(user="off@x.com", enabled=1),
			frappe._dict(user="appr@x.com", enabled=0),
		]
		with (
			patch.object(alerts, "get_users_with_role", side_effect=lambda role: roles[role]),
			patch.object(frappe, "get_all", return_value=users),
		):
			run = make_run(default_regulatory_owner="owner@x.com", recipients=recipients)
			self.assertEqual(run.recipients(frappe._dict(), None), ["extra@x.com", "owner@x.com"])

	def test_delivery_status(self):
		self.assertEqual(alerts.delivery_status([], []), "No Recipients")
		self.assertEqual(alerts.delivery_status([], ["x"]), "No Recipients")
		self.assertEqual(alerts.delivery_status(["a"], ["x"]), "Failed")
		self.assertEqual(alerts.delivery_status(["a"], []), "Sent")


@patch.object(alerts, "formatdate", side_effect=str)
class TestMessages(unittest.TestCase):
	def test_subject_is_stable_for_one_alert_and_differs_by_threshold_and_expiry(self, _formatdate):
		context = alerts.registration_context(make_registration())
		first, _body = alerts.build_alert_message(context, 30, 25, "/app/x")
		later, _body = alerts.build_alert_message(context, 30, 20, "/app/x")
		self.assertEqual(first, later)
		self.assertIn("Registration Expiry: FDA-1", first)
		self.assertIn("2026-11-12", first)
		self.assertIn("30-day alert", first)
		tighter, _body = alerts.build_alert_message(context, 14, 12, "/app/x")
		self.assertNotEqual(first, tighter)
		renewed = alerts.registration_context(make_registration(expiry_date=date(2031, 11, 12)))
		self.assertNotEqual(first, alerts.build_alert_message(renewed, 30, 25, "/app/x")[0])

	def test_body_escapes_values_and_lists_the_required_action(self, _formatdate):
		context = alerts.registration_context(
			make_registration(name="FDA-<1>", renewal_status="Submitted & waiting")
		)
		_subject, body = alerts.build_alert_message(context, 30, 25, "/app/fda?a=1&b=2", "Escalate <now>")
		self.assertIn('<a href="/app/fda?a=1&amp;b=2">FDA-&lt;1&gt;</a>', body)
		self.assertIn("Days remaining: 25", body)
		self.assertIn("Submitted &amp; waiting", body)
		self.assertIn("Required action: Escalate &lt;now&gt;", body)

	def test_overdue_and_due_today_wording(self, _formatdate):
		context = alerts.registration_context(make_registration(expiry_date=date(2026, 10, 10)))
		subject, body = alerts.build_alert_message(context, -8, -8, "/app/x")
		self.assertIn("overdue day 8", subject)
		self.assertIn("Days overdue: 8", body)
		subject, _body = alerts.build_alert_message(context, 0, 0, "/app/x")
		self.assertIn("due-date alert", subject)

	def test_fda_query_message_names_the_registration_and_due_date(self, _formatdate):
		context = alerts.fda_query_context(make_action(), make_registration())
		subject, body = alerts.build_alert_message(context, 3, 2, "/app/act")
		self.assertIn("FDA Query Due: ACT-1 (FDA-1) - Response due date 2026-10-20", subject)
		self.assertIn("Registration: FDA-1", body)


class TestDigest(unittest.TestCase):
	def test_counts_and_urgent_rows(self):
		rows = [
			make_registration(name="SOON", expiry_date=date(2026, 11, 1)),  # 14 days, Not Started
			make_registration(
				name="SOON-SUBMITTED", expiry_date=date(2026, 11, 10), renewal_status="Submitted"
			),
			make_registration(name="TODAY", expiry_date=TODAY),
			make_registration(name="LATER", expiry_date=date(2026, 12, 1)),
			make_registration(name="LAPSED", expiry_date=date(2026, 10, 1), official_status="Expired"),
			make_registration(
				name="LAPSED-RENEWED", expiry_date=date(2026, 10, 2), renewal_status="Approved"
			),
			make_registration(
				name="NOT-RENEWING", expiry_date=date(2026, 10, 2), renewal_status="Not Renewing"
			),
			make_registration(name="NO-EXPIRY", expiry_basis="No Expiry Date Specified"),
			make_registration(name="SUPERSEDED", expiry_date=date(2026, 10, 3), superseded_by="SOON"),
		]
		digest = alerts.build_digest(rows, 2, TODAY)
		self.assertEqual(digest.expiring_soon, 3)
		self.assertEqual(digest.expired_without_renewal, 1)
		self.assertEqual(digest.renewal_not_started, 2)
		self.assertEqual(digest.fda_queries_past_due, 2)
		self.assertEqual([row.name for row in digest.rows], ["LAPSED", "TODAY", "SOON", "SOON-SUBMITTED"])
		self.assertEqual(digest.rows[0].days, -17)
		self.assertEqual(digest.more, 0)

	def test_table_is_limited_to_twenty_rows(self):
		rows = [
			make_registration(name=f"R-{index:02d}", expiry_date=date(2026, 10, 18 + index % 10))
			for index in range(25)
		]
		digest = alerts.build_digest(rows, 0, TODAY)
		self.assertEqual(digest.expiring_soon, 25)
		self.assertEqual(len(digest.rows), 20)
		self.assertEqual(digest.more, 5)

	def test_rendering_escapes_html_and_links_the_forms(self):
		digest = alerts.build_digest([make_registration(name="FDA-<1>")], 1, TODAY)
		with (
			patch.object(alerts, "formatdate", side_effect=str),
			patch.object(alerts, "get_url_to_form", return_value="/app/fda?a=1&b=2") as get_url,
		):
			subject, message = alerts.render_digest(digest)
		get_url.assert_called_once_with(PRODUCT_REGISTRATION, "FDA-<1>")
		self.assertIn("1 expiring soon", subject)
		self.assertIn('<a href="/app/fda?a=1&amp;b=2">FDA-&lt;1&gt;</a>', message)
		self.assertIn("FDA queries past their response due date: <b>1</b>", message)
		self.assertNotIn("more not shown", message)


class TestJobs(unittest.TestCase):
	"""The database layer with Frappe's database, mail and document functions replaced by mocks."""

	def setUp(self):
		self.db = MagicMock()
		# No channel has delivered yet; a MagicMock would read as "already sent".
		self.db.get_value.return_value = frappe._dict(system_sent=0, email_sent=0)
		patches = {
			"db": patch.object(frappe, "db", self.db),
			"session": patch.object(frappe, "session", frappe._dict(user="Administrator")),
			"log_error": patch.object(frappe, "log_error"),
			"get_doc": patch.object(frappe, "get_doc"),
			"get_all": patch.object(frappe, "get_all", return_value=[]),
			"sendmail": patch.object(frappe, "sendmail"),
			"clear_last_message": patch.object(frappe, "clear_last_message"),
			"formatdate": patch.object(alerts, "formatdate", side_effect=str),
			"get_url_to_form": patch.object(alerts, "get_url_to_form", return_value="/app/form"),
			"now_datetime": patch.object(alerts, "now_datetime", return_value="NOW"),
			"today": patch.object(alerts, "today", return_value="2026-10-18"),
		}
		self.mocks = {}
		for name, patcher in patches.items():
			self.mocks[name] = patcher.start()
			self.addCleanup(patcher.stop)

	def set_value_calls(self, doctype=None, name=None):
		calls = [call.args for call in self.db.set_value.call_args_list]
		return [
			args
			for args in calls
			if (doctype is None or args[0] == doctype) and (name is None or args[1] == name)
		]

	def settings(self, **values):
		return make_run(**values).settings

	# daily job

	def run_daily(self, registrations=(), establishments=(), documents=(), **settings):
		rows = {
			PRODUCT_REGISTRATION: list(registrations),
			ESTABLISHMENT_REGISTRATION: list(establishments),
			REGULATORY_DOCUMENT: list(documents),
		}
		with (
			patch.object(frappe, "get_single", return_value=self.settings(**settings)),
			patch.object(alerts, "_get_rows", side_effect=lambda doctype, *args, **kwargs: rows[doctype]),
			patch.object(alerts, "refresh_item_coverage_for_registration") as coverage,
			patch.object(alerts, "_retry_pending_alerts") as retry,
			patch.object(alerts, "_send_new_alerts") as send,
			patch.object(alerts, "_create_renewal_actions") as renewals,
		):
			alerts.run_daily_expiry_processing()
		return frappe._dict(coverage=coverage, retry=retry, send=send, renewals=renewals)

	def test_maintenance_expires_lapsed_records_and_refreshes_days(self):
		result = self.run_daily(
			registrations=[
				make_registration(name="LAPSED", expiry_date=date(2026, 10, 10), days_to_expiry=3),
				make_registration(name="STALE", days_to_expiry=30),
				make_registration(name="CURRENT"),
				make_registration(
					name="NONE", expiry_basis="Unconfirmed", expiry_date=None, days_to_expiry=12
				),
			],
			establishments=[
				make_registration(name="EST-OLD", expiry_date=date(2026, 10, 1), days_to_expiry=None)
			],
			documents=[
				make_document(name="DOC-OLD", expiry_date=date(2026, 10, 1)),
				make_document(name="DOC-FINE"),
			],
		)
		set_value = self.db.set_value
		set_value.assert_any_call(PRODUCT_REGISTRATION, "LAPSED", "days_to_expiry", -8, update_modified=False)
		set_value.assert_any_call(
			PRODUCT_REGISTRATION, "LAPSED", "official_status", "Expired", update_modified=False
		)
		set_value.assert_any_call(PRODUCT_REGISTRATION, "STALE", "days_to_expiry", 25, update_modified=False)
		set_value.assert_any_call(PRODUCT_REGISTRATION, "NONE", "days_to_expiry", None, update_modified=False)
		self.assertEqual(self.set_value_calls(PRODUCT_REGISTRATION, "CURRENT"), [])
		set_value.assert_any_call(
			ESTABLISHMENT_REGISTRATION, "EST-OLD", "official_status", "Expired", update_modified=False
		)
		set_value.assert_any_call(
			REGULATORY_DOCUMENT, "DOC-OLD", "verification_status", "Expired", update_modified=False
		)
		self.assertEqual(self.set_value_calls(REGULATORY_DOCUMENT, "DOC-FINE"), [])
		# Nothing but the status is touched on a document; registrations are never saved as documents.
		self.assertEqual(len(self.set_value_calls(REGULATORY_DOCUMENT)), 1)

		comments = [call.args[0] for call in self.mocks["get_doc"].call_args_list]
		self.assertEqual(
			sorted((comment["reference_doctype"], comment["reference_name"]) for comment in comments),
			sorted(
				[
					(PRODUCT_REGISTRATION, "LAPSED"),
					(ESTABLISHMENT_REGISTRATION, "EST-OLD"),
					(REGULATORY_DOCUMENT, "DOC-OLD"),
				]
			),
		)
		self.assertTrue(all(c["doctype"] == "Comment" and c["comment_type"] == "Info" for c in comments))
		# Only the expired registration has its Item coverage refreshed.
		result.coverage.assert_called_once_with("LAPSED")

	def test_maintenance_runs_but_alerts_do_not_when_alerts_are_disabled(self):
		result = self.run_daily(
			registrations=[make_registration(name="LAPSED", expiry_date=date(2026, 10, 10))],
			enable_expiry_alerts=0,
		)
		self.db.set_value.assert_any_call(
			PRODUCT_REGISTRATION, "LAPSED", "official_status", "Expired", update_modified=False
		)
		result.coverage.assert_called_once_with("LAPSED")
		result.retry.assert_not_called()
		result.send.assert_not_called()
		result.renewals.assert_called_once()

	def test_alerts_are_retried_before_new_alerts_are_sent(self):
		order = []
		with (
			patch.object(frappe, "get_single", return_value=self.settings()),
			patch.object(alerts, "_get_rows", return_value=[]),
			patch.object(alerts, "_retry_pending_alerts", side_effect=lambda run: order.append("retry")),
			patch.object(alerts, "_create_renewal_actions", side_effect=lambda *args: order.append("renew")),
			patch.object(alerts, "_send_new_alerts", side_effect=lambda *args: order.append("send")),
		):
			alerts.run_daily_expiry_processing()
		self.assertEqual(order, ["retry", "renew", "send"])

	def test_one_failing_record_is_logged_and_does_not_stop_the_run(self):
		def set_value(doctype, name, *args, **kwargs):
			if name == "BROKEN":
				raise RuntimeError("boom")

		self.db.set_value.side_effect = set_value
		result = self.run_daily(
			registrations=[
				make_registration(name="BROKEN", expiry_date=date(2026, 10, 10)),
				make_registration(name="LAPSED", expiry_date=date(2026, 10, 10)),
			]
		)
		self.mocks["log_error"].assert_called_once()
		self.assertEqual(self.mocks["log_error"].call_args.kwargs["reference_name"], "BROKEN")
		self.db.rollback.assert_called_once_with(save_point=alerts.SAVEPOINT)
		self.db.set_value.assert_any_call(
			PRODUCT_REGISTRATION, "LAPSED", "official_status", "Expired", update_modified=False
		)
		result.coverage.assert_called_once_with("LAPSED")
		self.db.commit.assert_not_called()

	def test_guarded_returns_the_result_or_none_after_rolling_back(self):
		self.assertEqual(alerts._guarded(lambda value: value, 5, title="t"), 5)
		self.db.rollback.assert_not_called()

		def fail():
			raise ValueError("bad")

		self.assertIsNone(alerts._guarded(fail, title="Step failed", doctype="D", name="N"))
		self.db.rollback.assert_called_once_with(save_point=alerts.SAVEPOINT)
		kwargs = self.mocks["log_error"].call_args.kwargs
		self.assertEqual(
			(kwargs["title"], kwargs["reference_doctype"], kwargs["reference_name"]),
			("Step failed", "D", "N"),
		)

	# renewal actions

	def route_get_all(self, open_renewals=(), declined=(), pending=(), no_recipients=()):
		"""Answer `frappe.get_all` according to the filters the job passes."""

		def answer(doctype, *args, **kwargs):
			filters = kwargs.get("filters") or {}
			stage = filters.get("current_stage")
			if doctype == "Regulatory Registration Action" and stage and stage[0] == "not in":
				return list(open_renewals)
			if doctype == "Regulatory Registration Action" and stage and stage[0] == "in":
				return list(declined)
			if filters.get("status") == "No Recipients":
				return list(no_recipients)
			if isinstance(filters.get("status"), tuple):
				return list(pending)
			return []

		self.mocks["get_all"].side_effect = answer

	def test_renewal_action_is_created_unless_one_is_open(self):
		self.route_get_all(open_renewals=["OPEN"])
		run = make_run()
		registrations = [
			make_registration(name="OPEN"),
			make_registration(name="NEW", responsible_user=None),
			make_registration(name="STARTED", renewal_status="Internal Review"),
			make_registration(name="FAR", expiry_date=date(2028, 1, 1)),
			make_registration(name="NOT-RENEWING", renewal_status="Not Renewing"),
			make_registration(name="SUPERSEDED", superseded_by="OPEN"),
			make_registration(name="NO-EXPIRY", expiry_basis="No Expiry Date Specified"),
			make_registration(name="DRAFT", official_status="Draft"),
		]
		run.settings.default_regulatory_owner = "default@example.com"
		alerts._create_renewal_actions(run, registrations)

		filters = self.mocks["get_all"].call_args_list[0].kwargs["filters"]
		self.assertEqual(filters["action_type"], "Renewal")
		self.assertEqual(filters["current_stage"], ("not in", ("Approved", "Rejected", "Cancelled")))
		created = [call.args[0] for call in self.mocks["get_doc"].call_args_list]
		self.assertEqual([values["registration"] for values in created], ["NEW", "STARTED"])
		self.assertEqual(created[0]["doctype"], "Regulatory Registration Action")
		self.assertEqual(created[0]["action_type"], "Renewal")
		self.assertEqual(created[0]["current_stage"], "Preparing")
		self.assertEqual(created[0]["assigned_to"], "default@example.com")
		self.assertEqual(created[0]["remarks"], "Created automatically by expiry processing.")
		self.assertEqual(created[1]["assigned_to"], "owner@example.com")
		self.assertEqual(self.mocks["get_doc"].return_value.insert.call_count, 2)
		self.mocks["get_doc"].return_value.insert.assert_called_with(ignore_permissions=True)
		# Only a registration whose renewal has not started moves to Preparing.
		self.assertEqual(
			self.set_value_calls(PRODUCT_REGISTRATION),
			[(PRODUCT_REGISTRATION, "NEW", "renewal_status", "Preparing")],
		)

	def test_renewal_actions_respect_the_setting_and_the_window(self):
		run = make_run(auto_create_renewal_action=0)
		alerts._create_renewal_actions(run, [make_registration()])
		self.mocks["get_doc"].assert_not_called()

		run = make_run(renewal_start_days_before_expiry=10)
		alerts._create_renewal_actions(run, [make_registration()])
		self.mocks["get_doc"].assert_not_called()
		self.mocks["get_all"].assert_not_called()

	# new alerts

	def test_collects_the_tightest_alert_for_every_kind(self):
		run = make_run()
		candidates = alerts._collect_candidates(
			run,
			registrations=[
				make_registration(),
				make_registration(name="FAR", expiry_date=date(2030, 1, 1)),
				make_registration(name="NOT-RENEWING", renewal_status="Not Renewing"),
			],
			establishments=[make_registration(name="EST-1", expiry_date=date(2026, 10, 25))],
			documents=[make_document(), make_document(name="DOC-ORPHAN", registration="MISSING")],
			actions=[make_action()],
		)
		found = {(c.context.kind, c.context.reference_name): c.threshold for c in candidates}
		self.assertEqual(
			found,
			{
				(ALERT_REGISTRATION, "FDA-1"): 30,
				(ALERT_ESTABLISHMENT, "EST-1"): 7,
				(ALERT_DOCUMENT, "DOC-1"): 14,
				(ALERT_FDA_QUERY, "ACT-1"): 3,
			},
		)
		by_kind = {c.context.kind: c for c in candidates}
		self.assertEqual(by_kind[ALERT_FDA_QUERY].rule.severity, "Warning")
		self.assertEqual(
			by_kind[ALERT_REGISTRATION].key,
			alerts.make_dedupe_key(ALERT_REGISTRATION, PRODUCT_REGISTRATION, "FDA-1", date(2026, 11, 12), 30),
		)

	def test_overdue_candidates_repeat_on_the_interval(self):
		run = make_run()
		day_one = alerts._collect_candidates(
			run, [make_registration(expiry_date=date(2026, 10, 17))], [], [], []
		)
		self.assertEqual([(c.threshold, c.rule.severity) for c in day_one], [(-1, "Expired")])
		off_day = alerts._collect_candidates(
			run, [make_registration(expiry_date=date(2026, 10, 14))], [], [], []
		)
		self.assertEqual(off_day, [])
		week_later = alerts._collect_candidates(
			run, [make_registration(expiry_date=date(2026, 10, 10))], [], [], []
		)
		self.assertEqual([c.threshold for c in week_later], [-8])

	def test_a_bad_row_is_logged_without_stopping_the_collection(self):
		run = make_run()
		bad = make_registration(name="BAD", expiry_date="not-a-date")
		candidates = alerts._collect_candidates(run, [bad, make_registration()], [], [], [])
		self.assertEqual([c.context.reference_name for c in candidates], ["FDA-1"])
		self.mocks["log_error"].assert_called_once()

	def test_alerts_already_logged_are_not_created_again(self):
		run = make_run()
		registrations = [make_registration(name="LOGGED"), make_registration(name="NEW")]
		logged_key = alerts.make_dedupe_key(
			ALERT_REGISTRATION, PRODUCT_REGISTRATION, "LOGGED", date(2026, 11, 12), 30
		)
		with (
			patch.object(alerts, "_get_rows", return_value=[]),
			patch.object(alerts, "_existing_keys", return_value={logged_key}) as existing,
			patch.object(alerts, "_alert_candidate") as alert_candidate,
		):
			alerts._send_new_alerts(run, registrations, [], [])
		self.assertEqual(len(existing.call_args.args[0]), 2)
		alert_candidate.assert_called_once()
		self.assertEqual(alert_candidate.call_args.args[1].context.reference_name, "NEW")

	def test_existing_keys_are_read_in_chunks(self):
		self.mocks["get_all"].side_effect = [["a"], ["b"], []]
		self.assertEqual(alerts._existing_keys(["a", "b", "c", "d", "e"], chunk_size=2), {"a", "b"})
		self.assertEqual(self.mocks["get_all"].call_count, 3)
		self.assertEqual(alerts._existing_keys([]), set())

	def test_insert_log_returns_none_for_a_duplicate_key(self):
		candidate = alerts._candidate(make_run(), alerts.registration_context(make_registration()))
		log = self.mocks["get_doc"].return_value
		log.insert.side_effect = frappe.UniqueValidationError
		self.assertIsNone(alerts._insert_log(candidate))
		values = self.mocks["get_doc"].call_args.args[0]
		self.assertEqual(
			(values["doctype"], values["status"], values["retry_count"], values["threshold_days"]),
			(ALERT_LOG, "Queued", 0, 30),
		)
		self.assertEqual(values["dedupe_key"], candidate.key)
		self.assertEqual(values["severity"], "Info")
		log.insert.side_effect = None
		self.assertIs(alerts._insert_log(candidate), log)

	# delivery

	def deliver(self, recipients=("a@example.com", "b@example.com"), emails=True, **settings):
		run = make_run(**settings)
		run.recipients = MagicMock(return_value=list(recipients))
		run.email = MagicMock(side_effect=lambda user: user if emails else None)
		context = alerts.registration_context(make_registration())
		with patch.object(alerts, "_create_notifications") as notifications:
			alerts._deliver(run, "LOG-1", context, 30, make_rule(30))
		values = self.set_value_calls(ALERT_LOG, "LOG-1")[-1][2]
		return values, notifications

	def test_delivery_through_both_channels_is_sent(self):
		values, notifications = self.deliver()
		self.assertEqual(values["status"], "Sent")
		self.assertEqual(values["recipient_summary"], "a@example.com, b@example.com")
		self.assertEqual(values["alerted_on"], "NOW")
		self.assertIsNone(values["delivery_note"])
		notifications.assert_called_once()
		self.assertEqual(notifications.call_args.args[4], ["a@example.com", "b@example.com"])
		kwargs = self.mocks["sendmail"].call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["a@example.com", "b@example.com"])
		self.assertEqual(
			(kwargs["reference_doctype"], kwargs["reference_name"]), (PRODUCT_REGISTRATION, "FDA-1")
		)
		self.assertIn("Registration Expiry: FDA-1", kwargs["subject"])

	def test_only_the_enabled_channels_are_used(self):
		values, notifications = self.deliver(send_email=0)
		self.assertEqual(values["status"], "Sent")
		notifications.assert_called_once()
		self.mocks["sendmail"].assert_not_called()

		self.mocks["sendmail"].reset_mock()
		values, notifications = self.deliver(send_system_notification=0)
		self.assertEqual(values["status"], "Sent")
		notifications.assert_not_called()
		self.mocks["sendmail"].assert_called_once()

	def test_no_enabled_channel_is_noted(self):
		values, _notifications = self.deliver(send_email=0, send_system_notification=0)
		self.assertEqual(values["status"], "Sent")
		self.assertIn("No notification channel is enabled", values["delivery_note"])

	def test_failed_email_is_logged_and_the_alert_log_is_failed(self):
		self.mocks["sendmail"].side_effect = frappe.OutgoingEmailError
		values, notifications = self.deliver()
		self.assertEqual(values["status"], "Failed")
		self.assertIn("The email could not be sent.", values["delivery_note"])
		notifications.assert_called_once()
		self.mocks["log_error"].assert_called_once()
		self.assertEqual(self.mocks["log_error"].call_args.kwargs["reference_name"], "LOG-1")

	def test_failed_system_notification_is_logged_and_the_alert_log_is_failed(self):
		run = make_run()
		run.recipients = MagicMock(return_value=["a@example.com"])
		run.email = MagicMock(return_value="a@example.com")
		context = alerts.registration_context(make_registration())
		with patch.object(alerts, "_create_notifications", side_effect=RuntimeError):
			alerts._deliver(run, "LOG-1", context, 30, make_rule(30))
		values = self.set_value_calls(ALERT_LOG, "LOG-1")[-1][2]
		self.assertEqual(values["status"], "Failed")
		self.assertIn("system notification", values["delivery_note"])
		# The email still goes out.
		self.mocks["sendmail"].assert_called_once()

	def test_no_recipients_sends_nothing(self):
		values, notifications = self.deliver(recipients=())
		self.assertEqual(values["status"], "No Recipients")
		self.assertEqual(values["recipient_summary"], "")
		self.assertIn("No enabled recipient", values["delivery_note"])
		notifications.assert_not_called()
		self.mocks["sendmail"].assert_not_called()

	def test_recipients_without_email_addresses_still_get_the_notification(self):
		values, notifications = self.deliver(emails=False)
		self.assertEqual(values["status"], "Sent")
		self.assertIn("No recipient has an email address.", values["delivery_note"])
		notifications.assert_called_once()
		self.mocks["sendmail"].assert_not_called()

	def test_notifications_are_not_duplicated_for_the_same_user_and_subject(self):
		context = alerts.registration_context(make_registration())
		self.mocks["get_all"].return_value = ["a@example.com"]
		with patch.object(
			alerts, "is_notifications_enabled", side_effect=lambda user: user != "off@example.com"
		):
			alerts._create_notifications(
				context,
				"Subject <1>",
				"Body",
				"/app/form",
				["a@example.com", "b@example.com", "off@example.com"],
			)
		filters = self.mocks["get_all"].call_args.kwargs["filters"]
		self.assertEqual(filters["subject"], "Subject &lt;1&gt;")
		self.assertEqual(
			(filters["document_type"], filters["document_name"]), (PRODUCT_REGISTRATION, "FDA-1")
		)
		created = [call.args[0] for call in self.mocks["get_doc"].call_args_list]
		self.assertEqual([values["for_user"] for values in created], ["b@example.com"])
		self.assertEqual(created[0]["type"], "Alert")
		self.assertEqual(created[0]["link"], "/app/form")

	# retries

	def test_pending_alerts_are_retried_and_the_attempt_is_counted_first(self):
		order = []
		log = frappe._dict(name="LOG-1", status="Failed", retry_count=1)
		self.route_get_all(pending=[log])
		self.db.set_value.side_effect = lambda *args, **kwargs: order.append(("count", args[3]))
		with patch.object(alerts, "_retry_alert", side_effect=lambda *args: order.append("retry")):
			alerts._retry_pending_alerts(make_run())
		self.assertEqual(order, [("count", 2), "retry"])
		first = self.mocks["get_all"].call_args_list[0].kwargs
		self.assertEqual(first["filters"], {"status": ("in", ("Queued", "Failed")), "retry_count": ("<", 3)})

	def test_a_retry_that_keeps_raising_is_still_counted(self):
		self.route_get_all(pending=[frappe._dict(name="LOG-1", status="Queued", retry_count=2)])
		with patch.object(alerts, "_retry_alert", side_effect=RuntimeError("boom")):
			alerts._retry_pending_alerts(make_run())
		self.db.set_value.assert_called_once_with(ALERT_LOG, "LOG-1", "retry_count", 3, update_modified=False)
		self.mocks["log_error"].assert_called_once()

	def test_retry_delivers_with_the_rule_behind_the_stored_threshold(self):
		log = frappe._dict(
			name="LOG-1",
			alert_kind=ALERT_REGISTRATION,
			threshold_days=30,
			expiry_date=date(2026, 11, 12),
			reference_name="FDA-1",
		)
		run = make_run()
		context = alerts.registration_context(make_registration())
		with (
			patch.object(alerts, "_load_context", return_value=context),
			patch.object(alerts, "_deliver") as deliver,
		):
			alerts._retry_alert(run, log)
		args = deliver.call_args.args
		self.assertEqual(args[:4], (run, "LOG-1", context, 30))
		self.assertEqual(args[4].action, "Action 30")

	def test_retry_is_dropped_when_the_alert_no_longer_applies(self):
		log = frappe._dict(
			name="LOG-1",
			alert_kind=ALERT_REGISTRATION,
			threshold_days=30,
			expiry_date=date(2026, 11, 12),
			reference_name="FDA-1",
		)
		renewed = alerts.registration_context(make_registration(expiry_date=date(2031, 11, 12)))
		for context in (None, renewed):
			self.db.set_value.reset_mock()
			with (
				patch.object(alerts, "_load_context", return_value=context),
				patch.object(alerts, "_deliver") as deliver,
			):
				alerts._retry_alert(make_run(), log)
			deliver.assert_not_called()
			values = self.set_value_calls(ALERT_LOG, "LOG-1")[0][2]
			self.assertEqual((values["status"], values["retry_count"]), ("Failed", 3))

	# weekly digest

	def test_weekly_digest_goes_to_the_default_owner(self):
		users = [frappe._dict(name="owner@example.com", email="owner@example.com", enabled=1)]
		self.mocks["get_all"].return_value = users
		self.db.count.return_value = 2
		with (
			patch.object(
				frappe, "get_single", return_value=self.settings(default_regulatory_owner="owner@example.com")
			),
			patch.object(alerts, "get_users_with_role", return_value=[]),
			patch.object(alerts, "_get_rows", return_value=[make_registration()]) as get_rows,
		):
			alerts.send_weekly_regulatory_digest()
		self.assertEqual(get_rows.call_args.args[2], {"official_status": ("in", ("Active", "Expired"))})
		self.db.count.assert_called_once()
		kwargs = self.mocks["sendmail"].call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["owner@example.com"])
		self.assertIn("1 expiring soon", kwargs["subject"])
		self.assertIn("FDA queries past their response due date: <b>2</b>", kwargs["message"])

	def test_weekly_digest_falls_back_to_approvers(self):
		users = [frappe._dict(name="appr@example.com", email="appr@example.com", enabled=1)]
		self.mocks["get_all"].return_value = users
		with (
			patch.object(frappe, "get_single", return_value=self.settings()),
			patch.object(alerts, "get_users_with_role", return_value=["appr@example.com"]),
			patch.object(alerts, "_get_rows", return_value=[]),
		):
			alerts.send_weekly_regulatory_digest()
		self.assertEqual(self.mocks["sendmail"].call_args.kwargs["recipients"], ["appr@example.com"])

	def test_weekly_digest_respects_the_setting_and_needs_a_recipient(self):
		with patch.object(frappe, "get_single", return_value=self.settings(send_weekly_digest=0)):
			alerts.send_weekly_regulatory_digest()
		self.mocks["sendmail"].assert_not_called()
		self.mocks["get_all"].assert_not_called()

		with (
			patch.object(frappe, "get_single", return_value=self.settings()),
			patch.object(alerts, "get_users_with_role", return_value=[]),
		):
			alerts.send_weekly_regulatory_digest()
		self.mocks["sendmail"].assert_not_called()


class TestChannelFlagsAndReplacements(unittest.TestCase):
	"""Retries must not resend a channel that already delivered; lapsed old versions stop alerting."""

	setUp = TestJobs.setUp
	set_value_calls = TestJobs.set_value_calls
	deliver = TestJobs.deliver

	def deliver_with_flags(self, system_sent, email_sent, **settings):
		self.db.get_value.return_value = frappe._dict(system_sent=system_sent, email_sent=email_sent)
		return self.deliver(**settings)

	def test_retry_after_email_succeeded_only_resends_the_notification(self):
		values, notifications = self.deliver_with_flags(0, 1)
		notifications.assert_called_once()
		self.mocks["sendmail"].assert_not_called()
		self.assertEqual((values["system_sent"], values["email_sent"]), (1, 1))
		self.assertEqual(values["status"], "Sent")

	def test_retry_after_notification_succeeded_only_resends_the_email(self):
		values, notifications = self.deliver_with_flags(1, 0)
		notifications.assert_not_called()
		self.mocks["sendmail"].assert_called_once()
		self.assertEqual((values["system_sent"], values["email_sent"]), (1, 1))

	def test_failed_channel_keeps_its_flag_clear(self):
		run = make_run()
		run.recipients = MagicMock(return_value=["a@example.com"])
		run.email = MagicMock(return_value="a@example.com")
		self.db.get_value.return_value = frappe._dict(system_sent=0, email_sent=0)
		self.mocks["sendmail"].side_effect = RuntimeError
		context = alerts.registration_context(make_registration())
		with patch.object(alerts, "_create_notifications"):
			alerts._deliver(run, "LOG-1", context, 30, make_rule(30))
		values = self.set_value_calls(ALERT_LOG, "LOG-1")[-1][2]
		self.assertEqual((values["system_sent"], values["email_sent"]), (1, 0))
		self.assertEqual(values["status"], "Failed")

	def test_null_and_zero_days_are_equal_without_a_stated_expiry(self):
		unconfirmed = {"expiry_basis": "Unconfirmed", "expiry_date": None}
		self.assertFalse(alerts.needs_maintenance(make_registration(days_to_expiry=0, **unconfirmed), TODAY))
		self.assertFalse(
			alerts.needs_maintenance(make_registration(days_to_expiry=None, **unconfirmed), TODAY)
		)
		self.assertTrue(alerts.needs_maintenance(make_registration(days_to_expiry=12, **unconfirmed), TODAY))

	def test_lapsed_document_is_replaced_by_a_valid_one_of_the_same_kind(self):
		old = make_document(
			name="OLD", expiry_date=date(2026, 1, 1), verification_status="Expired", applicable_newcode=None
		)
		new = make_document(
			name="NEW", expiry_date=None, verification_status="Verified", applicable_newcode=None
		)
		replacements = alerts.valid_replacement_keys([old, new], TODAY)
		self.assertTrue(alerts.is_replaced_document(old, replacements, TODAY))
		# The valid document itself, a different NEWCODE and an unreplaced lapse still alert.
		self.assertFalse(alerts.is_replaced_document(new, replacements, TODAY))
		other = make_document(name="OTHER", expiry_date=date(2026, 1, 1), applicable_newcode="NC-2")
		self.assertFalse(alerts.is_replaced_document(other, replacements, TODAY))
		self.assertFalse(alerts.is_replaced_document(old, alerts.valid_replacement_keys([old], TODAY), TODAY))

	def test_replacement_must_itself_be_unexpired(self):
		stale = make_document(name="STALE", expiry_date=date(2026, 6, 1), verification_status="Verified")
		self.assertEqual(alerts.valid_replacement_keys([stale], TODAY), set())

	def test_a_document_still_inside_its_validity_is_never_replaced(self):
		current = make_document(name="CURRENT", expiry_date=date(2027, 1, 1), verification_status="Verified")
		replacements = alerts.valid_replacement_keys([current], TODAY)
		self.assertFalse(alerts.is_replaced_document(current, replacements, TODAY))


class TestRenewalAndRecipientRules(unittest.TestCase):
	setUp = TestJobs.setUp
	set_value_calls = TestJobs.set_value_calls
	route_get_all = TestJobs.route_get_all

	def test_declined_renewal_is_not_recreated_in_the_same_cycle(self):
		registration = make_registration(expiry_date=date(2026, 12, 31))
		# Window opens 365 days before expiry: 2025-12-31.
		self.assertTrue(alerts.renewal_declined_in_cycle(registration, datetime(2026, 3, 1, 10, 0), 365))
		self.assertTrue(alerts.renewal_declined_in_cycle(registration, date(2025, 12, 31), 365))
		self.assertFalse(alerts.renewal_declined_in_cycle(registration, date(2025, 12, 30), 365))
		self.assertFalse(alerts.renewal_declined_in_cycle(registration, None, 365))

	def test_a_previous_cycle_does_not_stop_the_next_renewal(self):
		# Renewed to 2031: the old rejected action (2024) is long before the new window.
		registration = make_registration(expiry_date=date(2031, 6, 30))
		self.assertFalse(alerts.renewal_declined_in_cycle(registration, datetime(2024, 5, 1), 365))

	def test_job_skips_a_registration_whose_renewal_was_declined(self):
		self.route_get_all(
			declined=[frappe._dict(registration="DECLINED", creation=datetime(2026, 5, 1, 9, 0))]
		)
		run = make_run()
		registrations = [make_registration(name="DECLINED"), make_registration(name="FRESH")]
		alerts._create_renewal_actions(run, registrations)
		created = [call.args[0]["registration"] for call in self.mocks["get_doc"].call_args_list]
		self.assertEqual(created, ["FRESH"])

	def test_no_recipient_alerts_are_retried_without_using_up_retries(self):
		waiting = frappe._dict(name="LOG-2", status="No Recipients", retry_count=0)
		failed = frappe._dict(name="LOG-3", status="Failed", retry_count=1)
		self.route_get_all(pending=[failed], no_recipients=[waiting])
		retried = []
		with patch.object(alerts, "_retry_alert", side_effect=lambda run, log: retried.append(log.name)):
			alerts._retry_pending_alerts(make_run())
		self.assertEqual(retried, ["LOG-3", "LOG-2"])
		self.assertEqual(
			self.set_value_calls(ALERT_LOG),
			[(ALERT_LOG, "LOG-3", "retry_count", 2)],
		)


if __name__ == "__main__":
	unittest.main()
