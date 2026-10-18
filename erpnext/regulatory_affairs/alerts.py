"""Scheduled expiry processing and alerts for Regulatory Affairs.

The daily job keeps expiry facts current (days to expiry, automatic Expired status), retries alerts that
were not delivered, creates renewal actions and raises one Regulatory Alert Log per
(kind, reference, expiry date, threshold). The weekly job emails a short digest. Decision logic lives in
small module-level functions that do not touch the database.
"""

import hashlib
from datetime import timedelta
from functools import cached_property
from typing import Any

import frappe
from frappe import _
from frappe.desk.doctype.notification_settings.notification_settings import is_notifications_enabled
from frappe.utils import cint, escape_html, formatdate, get_url_to_form, getdate, now_datetime, today
from frappe.utils.user import get_users_with_role

from erpnext.regulatory_affairs.constants import (
	ACTIVE,
	ALERT_DOCUMENT,
	ALERT_ESTABLISHMENT,
	ALERT_FDA_QUERY,
	ALERT_LOG,
	ALERT_REGISTRATION,
	CLOSED_STAGES,
	ESTABLISHMENT_REGISTRATION,
	EXPIRED,
	EXPIRY_SPECIFIED,
	PRODUCT_REGISTRATION,
	REGISTRATION_ACTION,
	REGULATORY_DOCUMENT,
	ROLE_APPROVER,
	SETTINGS,
)
from erpnext.regulatory_affairs.coverage import refresh_item_coverage_for_registration

# Registrations and establishments in these statuses are maintained and alerted.
ALERTABLE_STATUSES = (ACTIVE, EXPIRED)
VERIFIED = "Verified"
DOCUMENT_STATUSES = (VERIFIED, EXPIRED)
NOT_RENEWING = "Not Renewing"
NOT_STARTED = "Not Started"
PREPARING = "Preparing"
RENEWAL_APPROVED = "Approved"
FDA_QUERY_STAGE = "FDA Query"

# Alert Log statuses
STATUS_QUEUED = "Queued"
STATUS_SENT = "Sent"
STATUS_NO_RECIPIENTS = "No Recipients"
STATUS_FAILED = "Failed"
RETRY_STATUSES = (STATUS_QUEUED, STATUS_FAILED)
MAX_RETRIES = 3

DEFAULT_FDA_QUERY_ALERT_DAYS = (0, 1, 3, 7)
DEFAULT_OVERDUE_REPEAT_DAYS = 7
EXPIRED_SEVERITY = "Expired"
DIGEST_WINDOW_DAYS = 30
DIGEST_MAX_ROWS = 20
SAVEPOINT = "regulatory_alerts"

REGISTRATION_FIELDS = [
	"name",
	"official_status",
	"expiry_basis",
	"expiry_date",
	"days_to_expiry",
	"renewal_status",
	"superseded_by",
	"responsible_user",
]
ESTABLISHMENT_FIELDS = [
	"name",
	"establishment_name",
	"official_status",
	"expiry_basis",
	"expiry_date",
	"days_to_expiry",
	"renewal_status",
	"responsible_user",
]
DOCUMENT_FIELDS = [
	"name",
	"registration",
	"document_type",
	"document_number",
	"applicable_newcode",
	"expiry_date",
	"verification_status",
]
ACTION_FIELDS = [
	"name",
	"registration",
	"action_type",
	"current_stage",
	"response_due_date",
	"assigned_to",
]
LOG_FIELDS = [
	"name",
	"status",
	"alert_kind",
	"reference_name",
	"threshold_days",
	"expiry_date",
	"retry_count",
]


# --- Decision logic (no database) ---


def days_until(expiry_date, on_date) -> int:
	"""Whole days from `on_date` to `expiry_date`; negative once the date has passed."""
	return (getdate(expiry_date) - getdate(on_date)).days


def days_to_expiry_value(record, on_date) -> int | None:
	"""Value of `days_to_expiry`: only a stated expiry date has one."""
	if record.get("expiry_basis") == EXPIRY_SPECIFIED and record.get("expiry_date"):
		return days_until(record.get("expiry_date"), on_date)
	return None


def should_auto_expire(record, on_date) -> bool:
	"""An Active registration or establishment whose stated expiry date has passed becomes Expired."""
	days = days_to_expiry_value(record, on_date)
	return record.get("official_status") == ACTIVE and days is not None and days < 0


def needs_maintenance(record, on_date) -> bool:
	"""Whether a registration or establishment row differs from what the daily job would store."""
	expected = days_to_expiry_value(record, on_date)
	stored = record.get("days_to_expiry")
	# Frappe saves an empty Int as 0, so without a stated expiry NULL and 0 both mean "no value".
	differs = (stored not in (None, 0)) if expected is None else (stored != expected)
	return differs or should_auto_expire(record, on_date)


def should_expire_document(document, on_date) -> bool:
	"""A Verified document whose expiry date has passed becomes Expired."""
	return (
		document.get("verification_status") == VERIFIED
		and bool(document.get("expiry_date"))
		and days_until(document.get("expiry_date"), on_date) < 0
	)


def is_expiry_alert_eligible(record) -> bool:
	"""Registrations and establishments that are alerted (and renewed) for a stated expiry date."""
	return (
		record.get("official_status") in ALERTABLE_STATUSES
		and record.get("expiry_basis") == EXPIRY_SPECIFIED
		and bool(record.get("expiry_date"))
		and record.get("renewal_status") != NOT_RENEWING
		and not record.get("superseded_by")
	)


def replacement_key(document) -> tuple:
	"""Documents of one registration, type and NEWCODE replace each other."""
	return (
		document.get("registration"),
		document.get("document_type"),
		document.get("applicable_newcode") or "",
	)


def valid_replacement_keys(documents, on_date) -> set[tuple]:
	"""Keys that have a Verified document that has not expired, whatever its own expiry date."""
	return {
		replacement_key(document)
		for document in documents
		if document.get("verification_status") == VERIFIED
		and (not document.get("expiry_date") or days_until(document.get("expiry_date"), on_date) >= 0)
	}


def is_replaced_document(document, replacements, on_date) -> bool:
	"""A lapsed document stops alerting once a valid Verified one of the same kind exists."""
	lapsed = bool(document.get("expiry_date")) and days_until(document.get("expiry_date"), on_date) < 0
	return lapsed and replacement_key(document) in replacements


def is_document_alert_eligible(document, registration) -> bool:
	"""Verified or Expired documents with an expiry date, for a live registration that is still renewed."""
	return (
		bool(registration)
		and document.get("verification_status") in DOCUMENT_STATUSES
		and bool(document.get("expiry_date"))
		and registration.get("official_status") in ALERTABLE_STATUSES
		and registration.get("renewal_status") != NOT_RENEWING
		and not registration.get("superseded_by")
	)


def is_fda_query_eligible(action) -> bool:
	"""Open FDA Query actions that have a response due date."""
	return action.get("current_stage") == FDA_QUERY_STAGE and bool(action.get("response_due_date"))


def renewal_declined_in_cycle(registration, declined_on, start_days_before_expiry) -> bool:
	"""Whether a renewal action was rejected or cancelled since this expiry's renewal window opened.

	Such a decision is the user's; the daily job does not start another action for the same cycle.
	"""
	if not declined_on:
		return False
	window_start = getdate(registration.get("expiry_date")) - timedelta(days=cint(start_days_before_expiry))
	return getdate(declined_on) >= window_start


def needs_renewal_action(registration, on_date, start_days_before_expiry) -> bool:
	"""Whether a registration is inside its renewal window (an open Renewal action is checked separately)."""
	return is_expiry_alert_eligible(registration) and days_until(
		registration.get("expiry_date"), on_date
	) <= cint(start_days_before_expiry)


def _overdue_rule(days_overdue, rules, severity):
	"""Rule-like for an overdue alert: action and recipients come from the 0-day rule when there is one."""
	zero_rule = next((rule for rule in rules if cint(rule.get("days_before_expiry")) == 0), None) or {}
	return frappe._dict(
		days_before_expiry=-days_overdue,
		severity=severity,
		action=zero_rule.get("action") or _("Overdue follow-up"),
		recipient_role=zero_rule.get("recipient_role"),
		recipient_user=zero_rule.get("recipient_user"),
	)


def select_rule(days_remaining, rules, overdue_repeat_days, overdue_severity=EXPIRED_SEVERITY):
	"""Pick the alert due today as (threshold_days, rule_like), or None when nothing is due.

	Before expiry the tightest rule with `days_before_expiry >= days_remaining` wins, so a missed run
	catches up with a single alert and looser thresholds are never replayed. After expiry an alert is due
	on day 1 and then every `overdue_repeat_days`; its threshold is stored as the negative days overdue.
	"""
	if days_remaining >= 0:
		candidates = [rule for rule in rules if cint(rule.get("days_before_expiry")) >= days_remaining]
		if not candidates:
			return None
		rule = min(candidates, key=lambda rule: cint(rule.get("days_before_expiry")))
		return cint(rule.get("days_before_expiry")), rule

	days_overdue = -days_remaining
	repeat_days = max(cint(overdue_repeat_days) or DEFAULT_OVERDUE_REPEAT_DAYS, 1)
	if (days_overdue - 1) % repeat_days:
		return None
	return -days_overdue, _overdue_rule(days_overdue, rules, overdue_severity)


def rule_for_threshold(threshold_days, rules, overdue_severity=EXPIRED_SEVERITY):
	"""The rule behind a stored threshold, used when an alert is retried; empty when it no longer exists."""
	threshold_days = cint(threshold_days)
	if threshold_days < 0:
		return _overdue_rule(-threshold_days, rules, overdue_severity)
	return next(
		(rule for rule in rules if cint(rule.get("days_before_expiry")) == threshold_days), frappe._dict()
	)


def overdue_severity_for(kind) -> str:
	"""Severity of overdue alerts: an unanswered FDA query is Critical, every expiry is Expired."""
	return "Critical" if kind == ALERT_FDA_QUERY else EXPIRED_SEVERITY


def make_dedupe_key(kind, reference_doctype, reference_name, expiry_date, threshold_days) -> str:
	"""Idempotency key of an alert; a new expiry date therefore re-arms every threshold."""
	expiry = getdate(expiry_date).isoformat()
	raw = f"{kind}|{reference_doctype}|{reference_name}|{expiry}|{cint(threshold_days)}"
	return hashlib.sha256(raw.encode()).hexdigest()


def parse_alert_days(value) -> list[int]:
	"""Sorted unique days from a comma separated list; blank gives the default, bad text raises ValueError."""
	text = "" if value is None else str(value)
	if not text.strip():
		return list(DEFAULT_FDA_QUERY_ALERT_DAYS)
	days = {int(part.strip()) for part in text.split(",") if part.strip()}
	if not days or min(days) < 0:
		raise ValueError(f"Invalid alert days: {value}")
	return sorted(days)


def get_fda_query_alert_days(value) -> list[int]:
	"""`parse_alert_days` that logs invalid text and falls back to the default days."""
	try:
		return parse_alert_days(value)
	except (TypeError, ValueError):
		frappe.log_error(
			title="Invalid FDA query alert days",
			message=f"Settings value {value!r} is not a comma separated list of days; using the default.",
		)
		return list(DEFAULT_FDA_QUERY_ALERT_DAYS)


def build_fda_query_rules(days) -> list:
	"""Rule-likes for the FDA Query alert days (no recipients of their own)."""
	return [
		frappe._dict(
			days_before_expiry=day,
			severity="Critical" if day <= 1 else "Warning",
			action=_("Respond to the FDA query"),
			recipient_role=None,
			recipient_user=None,
		)
		for day in days
	]


def resolve_recipients(primary, default_owner, approvers, enabled_users=None, extra=()) -> list[str]:
	"""Enabled users to alert: the primary users, else the default owner, else the approvers.

	`extra` (the recipients listed in Regulatory Settings) is always added. `enabled_users` is the set of
	users that may be notified; None means every user is enabled.
	"""

	def usable(users):
		return sorted({user for user in users if user and (enabled_users is None or user in enabled_users)})

	chosen = usable(primary) or usable([default_owner]) or usable(approvers)
	return sorted({*chosen, *usable(extra)})


def settings_recipients(rows) -> list[str]:
	"""Users of the enabled rows of the Regulatory Settings recipient table, without duplicates."""
	return sorted({row.get("user") for row in rows or () if row.get("user") and row.get("enabled")})


def delivery_status(recipients, errors) -> str:
	"""Alert Log status for one delivery attempt."""
	if not recipients:
		return STATUS_NO_RECIPIENTS
	return STATUS_FAILED if errors else STATUS_SENT


def registration_context(row):
	"""Alert context of a product registration, or None when it is not alerted."""
	if not is_expiry_alert_eligible(row):
		return None
	return frappe._dict(
		kind=ALERT_REGISTRATION,
		reference_doctype=PRODUCT_REGISTRATION,
		reference_name=row.get("name"),
		registration=row.get("name"),
		regulatory_document=None,
		expiry_date=getdate(row.get("expiry_date")),
		responsible_user=row.get("responsible_user"),
		renewal_status=row.get("renewal_status"),
		label=row.get("name"),
	)


def establishment_context(row):
	"""Alert context of an establishment licence, or None when it is not alerted."""
	if not is_expiry_alert_eligible(row):
		return None
	name = row.get("name")
	establishment_name = row.get("establishment_name")
	return frappe._dict(
		kind=ALERT_ESTABLISHMENT,
		reference_doctype=ESTABLISHMENT_REGISTRATION,
		reference_name=name,
		registration=None,
		regulatory_document=None,
		expiry_date=getdate(row.get("expiry_date")),
		responsible_user=row.get("responsible_user"),
		renewal_status=row.get("renewal_status"),
		label=f"{establishment_name} ({name})" if establishment_name else name,
	)


def document_context(row, registration):
	"""Alert context of a supporting document (owned by its registration), or None when not alerted."""
	if not is_document_alert_eligible(row, registration):
		return None
	name = row.get("name")
	return frappe._dict(
		kind=ALERT_DOCUMENT,
		reference_doctype=REGULATORY_DOCUMENT,
		reference_name=name,
		registration=row.get("registration"),
		regulatory_document=name,
		expiry_date=getdate(row.get("expiry_date")),
		responsible_user=registration.get("responsible_user"),
		renewal_status=registration.get("renewal_status"),
		label=f"{row.get('document_type')} {row.get('document_number') or name} ({row.get('registration')})",
	)


def fda_query_context(row, registration):
	"""Alert context of an open FDA query action, or None when it is not alerted."""
	if not is_fda_query_eligible(row):
		return None
	registration = registration or {}
	return frappe._dict(
		kind=ALERT_FDA_QUERY,
		reference_doctype=REGISTRATION_ACTION,
		reference_name=row.get("name"),
		registration=row.get("registration"),
		regulatory_document=None,
		expiry_date=getdate(row.get("response_due_date")),
		responsible_user=row.get("assigned_to") or registration.get("responsible_user"),
		renewal_status=registration.get("renewal_status"),
		label=f"{row.get('name')} ({row.get('registration')})",
	)


def build_alert_message(context, threshold_days, days_left, url, action=None) -> tuple[str, str]:
	"""Subject (stable for one Alert Log, so notifications can be de-duplicated) and HTML body."""
	due_label = _("Response due date") if context.kind == ALERT_FDA_QUERY else _("Expiry date")
	if threshold_days < 0:
		timing = _("overdue day {0}").format(-threshold_days)
	elif threshold_days == 0:
		timing = _("due-date alert")
	else:
		timing = _("{0}-day alert").format(threshold_days)
	subject = _("{0}: {1} - {2} {3} ({4})").format(
		_(context.kind), context.label, due_label, formatdate(context.expiry_date), timing
	)

	lines = [
		_("Record: {0}").format(f'<a href="{escape_html(url)}">{escape_html(context.label)}</a>'),
	]
	if context.registration and context.registration != context.reference_name:
		lines.append(_("Registration: {0}").format(escape_html(context.registration)))
	lines.append(_("{0}: {1}").format(due_label, formatdate(context.expiry_date)))
	if days_left < 0:
		lines.append(_("Days overdue: {0}").format(-days_left))
	else:
		lines.append(_("Days remaining: {0}").format(days_left))
	if context.renewal_status:
		lines.append(_("Renewal status: {0}").format(escape_html(context.renewal_status)))
	if action:
		lines.append(_("Required action: {0}").format(escape_html(action)))
	return subject, "<br>".join(lines)


def build_digest(registrations, past_due_queries, on_date) -> frappe._dict:
	"""Counts and the most urgent rows for the weekly digest from registration rows."""
	expiring, expired = [], []
	for row in registrations:
		if not is_expiry_alert_eligible(row):
			continue
		days = days_until(row.get("expiry_date"), on_date)
		if 0 <= days <= DIGEST_WINDOW_DAYS:
			expiring.append((days, row))
		elif days < 0 and row.get("renewal_status") != RENEWAL_APPROVED:
			expired.append((days, row))

	urgent = sorted(
		[*expired, *expiring], key=lambda item: (getdate(item[1].get("expiry_date")), item[1].get("name"))
	)
	return frappe._dict(
		expiring_soon=len(expiring),
		expired_without_renewal=len(expired),
		renewal_not_started=sum(
			1 for _days, row in expiring if (row.get("renewal_status") or NOT_STARTED) == NOT_STARTED
		),
		fda_queries_past_due=cint(past_due_queries),
		rows=[
			frappe._dict(
				name=row.get("name"),
				expiry_date=row.get("expiry_date"),
				days=days,
				renewal_status=row.get("renewal_status"),
				responsible_user=row.get("responsible_user"),
			)
			for days, row in urgent[:DIGEST_MAX_ROWS]
		],
		more=max(len(urgent) - DIGEST_MAX_ROWS, 0),
	)


def render_digest(digest) -> tuple[str, str]:
	"""Subject and HTML body of the weekly digest."""
	subject = _("Weekly regulatory digest: {0} expiring soon, {1} expired").format(
		digest.expiring_soon, digest.expired_without_renewal
	)
	summary = [
		(_("Registrations expiring within {0} days").format(DIGEST_WINDOW_DAYS), digest.expiring_soon),
		(_("Expired registrations without an approved renewal"), digest.expired_without_renewal),
		(
			_("Registrations expiring within {0} days with renewal not started").format(DIGEST_WINDOW_DAYS),
			digest.renewal_not_started,
		),
		(_("FDA queries past their response due date"), digest.fda_queries_past_due),
	]
	parts = ["<ul>", *(f"<li>{escape_html(label)}: <b>{count}</b></li>" for label, count in summary), "</ul>"]
	if digest.rows:
		headers = [_("Registration"), _("Expiry date"), _("Days"), _("Renewal status"), _("Responsible")]
		parts.append('<table border="1" cellpadding="4" cellspacing="0"><tr>')
		parts.extend(f"<th>{escape_html(header)}</th>" for header in headers)
		parts.append("</tr>")
		for row in digest.rows:
			link = get_url_to_form(PRODUCT_REGISTRATION, row.name)
			cells = [
				f'<a href="{escape_html(link)}">{escape_html(row.name)}</a>',
				escape_html(str(formatdate(row.expiry_date))),
				str(row.days),
				escape_html(row.renewal_status or ""),
				escape_html(row.responsible_user or ""),
			]
			parts.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
		parts.append("</table>")
		if digest.more:
			parts.append("<p>" + _("{0} more not shown.").format(digest.more) + "</p>")
	return subject, "".join(parts)


# --- Run state ---


class _Run:
	"""One job run: settings, alert rules and per-run caches of users and role members."""

	def __init__(self, settings, on_date=None):
		self.settings = settings
		self.today = getdate(on_date or today())
		self.rules = list(settings.get("expiry_alert_rules") or [])
		self._users: dict[str, Any] = {}
		self._role_users: dict[str, list[str]] = {}

	@cached_property
	def fda_rules(self):
		return build_fda_query_rules(get_fda_query_alert_days(self.settings.get("fda_query_alert_days")))

	def rules_for(self, kind):
		return self.fda_rules if kind == ALERT_FDA_QUERY else self.rules

	def select(self, context):
		"""The alert due today for a context as (threshold_days, rule_like), or None."""
		return select_rule(
			days_until(context.expiry_date, self.today),
			self.rules_for(context.kind),
			self.settings.get("overdue_repeat_days"),
			overdue_severity_for(context.kind),
		)

	def role_users(self, role):
		if role not in self._role_users:
			self._role_users[role] = get_users_with_role(role)
		return self._role_users[role]

	def _load_users(self, names):
		missing = {name for name in names if name and name not in self._users}
		if not missing:
			return
		for user in frappe.get_all(
			"User", filters={"name": ("in", sorted(missing))}, fields=["name", "email", "enabled"]
		):
			self._users[user.name] = user
		for name in missing:
			self._users.setdefault(name, None)

	def email(self, name):
		return (self._users.get(name) or {}).get("email")

	def recipients(self, rule, responsible_user) -> list[str]:
		"""Resolve the enabled recipients of an alert from its rule and the responsible user."""
		role = rule.get("recipient_role")
		primary = [
			rule.get("recipient_user"),
			responsible_user,
			*(self.role_users(role) if role else ()),
		]
		default_owner = self.settings.get("default_regulatory_owner")
		approvers = self.role_users(ROLE_APPROVER)
		extra = settings_recipients(self.settings.get("recipients"))
		everyone = {*primary, default_owner, *approvers, *extra}
		self._load_users(everyone)
		enabled = {name for name in everyone if name and (self._users.get(name) or {}).get("enabled")}
		return resolve_recipients(primary, default_owner, approvers, enabled, extra)


# --- Database steps ---


def _guarded(step, *args, title, doctype=None, name=None):
	"""Run one step for one document in a savepoint; on failure undo it, log it and carry on."""
	frappe.db.savepoint(SAVEPOINT)
	try:
		return step(*args)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(
			title=title, message=frappe.get_traceback(), reference_doctype=doctype, reference_name=name
		)
		return None


def _get_rows(doctype, fields, filters=None):
	return frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0)


def _add_info_comment(doctype, name, text):
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"comment_email": frappe.session.user,
			"reference_doctype": doctype,
			"reference_name": name,
			"content": text,
		}
	).insert(ignore_permissions=True)


def _maintain_record(run, doctype, row) -> bool:
	"""Refresh `days_to_expiry` and expire a lapsed Active record; return whether it was expired."""
	days = days_to_expiry_value(row, run.today)
	stored = row.get("days_to_expiry")
	if (stored not in (None, 0)) if days is None else (stored != days):
		frappe.db.set_value(doctype, row.name, "days_to_expiry", days, update_modified=False)
		row["days_to_expiry"] = days
	if not should_auto_expire(row, run.today):
		return False
	frappe.db.set_value(doctype, row.name, "official_status", EXPIRED, update_modified=False)
	_add_info_comment(
		doctype,
		row.name,
		_("Status set to Expired automatically because the expiry date {0} has passed.").format(
			formatdate(row.expiry_date)
		),
	)
	row["official_status"] = EXPIRED
	return True


def _maintain_document(run, row) -> bool:
	"""Expire a Verified document whose expiry date has passed; nothing else on it changes."""
	if not should_expire_document(row, run.today):
		return False
	frappe.db.set_value(REGULATORY_DOCUMENT, row.name, "verification_status", EXPIRED, update_modified=False)
	_add_info_comment(
		REGULATORY_DOCUMENT,
		row.name,
		_("Verification status set to Expired automatically because the expiry date {0} has passed.").format(
			formatdate(row.expiry_date)
		),
	)
	row["verification_status"] = EXPIRED
	return True


def _maintain(run, registrations, establishments, documents):
	"""Expiry facts; always runs, whether or not alerts are enabled."""
	title = "Regulatory expiry maintenance failed"
	expired_registrations = []
	for row in registrations:
		if needs_maintenance(row, run.today) and _guarded(
			_maintain_record,
			run,
			PRODUCT_REGISTRATION,
			row,
			title=title,
			doctype=PRODUCT_REGISTRATION,
			name=row.name,
		):
			expired_registrations.append(row.name)
	for row in establishments:
		if needs_maintenance(row, run.today):
			_guarded(
				_maintain_record,
				run,
				ESTABLISHMENT_REGISTRATION,
				row,
				title=title,
				doctype=ESTABLISHMENT_REGISTRATION,
				name=row.name,
			)
	for row in documents:
		if should_expire_document(row, run.today):
			_guarded(_maintain_document, run, row, title=title, doctype=REGULATORY_DOCUMENT, name=row.name)
	for name in expired_registrations:
		_guarded(
			refresh_item_coverage_for_registration,
			name,
			title="Regulatory item coverage refresh failed",
			doctype=PRODUCT_REGISTRATION,
			name=name,
		)


def _create_renewal_action(run, row):
	frappe.get_doc(
		{
			"doctype": REGISTRATION_ACTION,
			"registration": row.name,
			"action_type": "Renewal",
			"current_stage": PREPARING,
			"assigned_to": row.get("responsible_user") or run.settings.get("default_regulatory_owner"),
			"remarks": _("Created automatically by expiry processing."),
		}
	).insert(ignore_permissions=True)
	if row.get("renewal_status") == NOT_STARTED:
		frappe.db.set_value(
			PRODUCT_REGISTRATION, row.name, "renewal_status", PREPARING, update_modified=False
		)
		row["renewal_status"] = PREPARING


def _create_renewal_actions(run, registrations):
	"""Create a Renewal action for registrations in their renewal window unless one is already open."""
	if not run.settings.get("auto_create_renewal_action"):
		return
	start_days = run.settings.get("renewal_start_days_before_expiry")
	due = [row for row in registrations if needs_renewal_action(row, run.today, start_days)]
	if not due:
		return
	with_open_renewal = set(
		frappe.get_all(
			REGISTRATION_ACTION,
			filters={"action_type": "Renewal", "current_stage": ("not in", CLOSED_STAGES)},
			pluck="registration",
			limit_page_length=0,
		)
	)
	declined = {}
	for action in frappe.get_all(
		REGISTRATION_ACTION,
		filters={"action_type": "Renewal", "current_stage": ("in", ("Rejected", "Cancelled"))},
		fields=["registration", "creation"],
		limit_page_length=0,
	):
		declined[action.registration] = max(
			declined.get(action.registration) or action.creation, action.creation
		)
	for row in due:
		if row.name in with_open_renewal or renewal_declined_in_cycle(
			row, declined.get(row.name), start_days
		):
			continue
		_guarded(
			_create_renewal_action,
			run,
			row,
			title="Regulatory renewal action failed",
			doctype=PRODUCT_REGISTRATION,
			name=row.name,
		)


def _create_notifications(context, subject, body, url, users):
	"""One Alert notification per user, skipping users who already have this subject for the record."""
	notification_subject = escape_html(subject)
	notified = set(
		frappe.get_all(
			"Notification Log",
			filters={
				"type": "Alert",
				"document_type": context.reference_doctype,
				"document_name": context.reference_name,
				"subject": notification_subject,
				"for_user": ("in", users),
			},
			pluck="for_user",
			limit_page_length=0,
		)
	)
	for user in users:
		if user in notified or not is_notifications_enabled(user):
			continue
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"type": "Alert",
				"subject": notification_subject,
				"email_content": body,
				"document_type": context.reference_doctype,
				"document_name": context.reference_name,
				"for_user": user,
				"from_user": "Administrator",
				"link": url,
			}
		).insert(ignore_permissions=True)


def _deliver(run, log_name, context, threshold_days, rule):
	"""Send one alert through the enabled channels and record the outcome on its Alert Log."""
	settings = run.settings
	recipients = run.recipients(rule, context.responsible_user)
	already = frappe.db.get_value(ALERT_LOG, log_name, ["system_sent", "email_sent"], as_dict=True) or {}
	sent = {"system_sent": cint(already.get("system_sent")), "email_sent": cint(already.get("email_sent"))}
	errors, notes = [], []
	if not recipients:
		notes.append(_("No enabled recipient was found."))
	else:
		url = get_url_to_form(context.reference_doctype, context.reference_name)
		subject, body = build_alert_message(
			context, threshold_days, days_until(context.expiry_date, run.today), url, rule.get("action")
		)
		if settings.get("send_system_notification") and not sent["system_sent"]:
			try:
				_create_notifications(context, subject, body, url, recipients)
				sent["system_sent"] = 1
			except Exception:
				errors.append(_("The system notification could not be created."))
				frappe.log_error(
					title="Regulatory alert notification failed",
					message=frappe.get_traceback(),
					reference_doctype=ALERT_LOG,
					reference_name=log_name,
				)
		if settings.get("send_email") and not sent["email_sent"]:
			emails = sorted({email for email in map(run.email, recipients) if email})
			if not emails:
				notes.append(_("No recipient has an email address."))
			else:
				try:
					frappe.sendmail(
						recipients=emails,
						subject=subject,
						message=body,
						reference_doctype=context.reference_doctype,
						reference_name=context.reference_name,
					)
					sent["email_sent"] = 1
				except Exception:
					errors.append(_("The email could not be sent."))
					frappe.log_error(
						title="Regulatory alert email failed",
						message=frappe.get_traceback(),
						reference_doctype=ALERT_LOG,
						reference_name=log_name,
					)
		if not (settings.get("send_system_notification") or settings.get("send_email")):
			notes.append(_("No notification channel is enabled in the settings."))

	frappe.db.set_value(
		ALERT_LOG,
		log_name,
		{
			**sent,
			"status": delivery_status(recipients, errors),
			"alerted_on": now_datetime(),
			"recipient_summary": ", ".join(recipients),
			"delivery_note": " ".join([*errors, *notes]) or None,
		},
		update_modified=False,
	)


def _insert_log(candidate):
	"""Create the Queued Alert Log of a candidate; None when the same alert already exists."""
	context = candidate.context
	log = frappe.get_doc(
		{
			"doctype": ALERT_LOG,
			"alert_kind": context.kind,
			"reference_doctype": context.reference_doctype,
			"reference_name": context.reference_name,
			"registration": context.registration,
			"regulatory_document": context.regulatory_document,
			"threshold_days": candidate.threshold,
			"expiry_date": context.expiry_date,
			"severity": candidate.rule.get("severity") or "Warning",
			"dedupe_key": candidate.key,
			"status": STATUS_QUEUED,
			"retry_count": 0,
		}
	)
	try:
		log.insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		frappe.clear_last_message()
		return None
	return log


def _alert_candidate(run, candidate):
	log = _insert_log(candidate)
	if log:
		_deliver(run, log.name, candidate.context, candidate.threshold, candidate.rule)


def _candidate(run, context):
	"""The alert due today for a context as (context, threshold, rule, dedupe key), or None."""
	selected = run.select(context) if context else None
	if not selected:
		return None
	threshold_days, rule = selected
	return frappe._dict(
		context=context,
		threshold=threshold_days,
		rule=rule,
		key=make_dedupe_key(
			context.kind,
			context.reference_doctype,
			context.reference_name,
			context.expiry_date,
			threshold_days,
		),
	)


def _collect_candidates(run, registrations, establishments, documents, actions, replacements=frozenset()):
	"""Alerts due today across all record kinds; a record that cannot be evaluated is logged and skipped."""
	registrations_by_name = {row.name: row for row in registrations}

	def for_document(row):
		if is_replaced_document(row, replacements, run.today):
			return None
		return document_context(row, registrations_by_name.get(row.get("registration")))

	def for_action(row):
		return fda_query_context(row, registrations_by_name.get(row.get("registration")))

	candidates = []
	for build, rows in (
		(registration_context, registrations),
		(establishment_context, establishments),
		(for_document, documents),
		(for_action, actions),
	):
		for row in rows:
			try:
				candidate = _candidate(run, build(row))
			except Exception:
				frappe.log_error(
					title="Regulatory alert selection failed",
					message=f"{row.get('name')}\n{frappe.get_traceback()}",
				)
				continue
			if candidate:
				candidates.append(candidate)
	return candidates


def _existing_keys(keys, chunk_size=500):
	"""The dedupe keys that already have an Alert Log, fetched in a few queries."""
	existing = set()
	for start in range(0, len(keys), chunk_size):
		existing.update(
			frappe.get_all(
				ALERT_LOG,
				filters={"dedupe_key": ("in", keys[start : start + chunk_size])},
				pluck="dedupe_key",
				limit_page_length=0,
			)
		)
	return existing


def _send_new_alerts(run, registrations, establishments, documents):
	"""Create and deliver every alert that is due today and has not been logged yet."""
	actions = _get_rows(
		REGISTRATION_ACTION,
		ACTION_FIELDS,
		{"current_stage": FDA_QUERY_STAGE, "response_due_date": ("is", "set")},
	)
	replacements = valid_replacement_keys(
		_get_rows(REGULATORY_DOCUMENT, DOCUMENT_FIELDS, {"verification_status": VERIFIED}), run.today
	)
	candidates = _collect_candidates(run, registrations, establishments, documents, actions, replacements)
	existing = _existing_keys([candidate.key for candidate in candidates])
	for candidate in candidates:
		if candidate.key not in existing:
			_guarded(
				_alert_candidate,
				run,
				candidate,
				title="Regulatory alert failed",
				doctype=candidate.context.reference_doctype,
				name=candidate.context.reference_name,
			)


def _get_row(doctype, name, fields):
	return frappe.db.get_value(doctype, name, fields, as_dict=True) if name else None


def _load_context(log):
	"""Current alert context of a logged alert's reference; None when it is no longer alerted."""
	kind = log.alert_kind
	if kind == ALERT_REGISTRATION:
		row = _get_row(PRODUCT_REGISTRATION, log.reference_name, REGISTRATION_FIELDS)
		return registration_context(row) if row else None
	if kind == ALERT_ESTABLISHMENT:
		row = _get_row(ESTABLISHMENT_REGISTRATION, log.reference_name, ESTABLISHMENT_FIELDS)
		return establishment_context(row) if row else None
	if kind == ALERT_DOCUMENT:
		row = _get_row(REGULATORY_DOCUMENT, log.reference_name, DOCUMENT_FIELDS)
		registration = _get_row(PRODUCT_REGISTRATION, row.registration, REGISTRATION_FIELDS) if row else None
		return document_context(row, registration) if row else None
	if kind == ALERT_FDA_QUERY:
		row = _get_row(REGISTRATION_ACTION, log.reference_name, ACTION_FIELDS)
		registration = _get_row(PRODUCT_REGISTRATION, row.registration, REGISTRATION_FIELDS) if row else None
		return fda_query_context(row, registration) if row else None
	return None


def _retry_alert(run, log):
	"""Deliver a logged alert again, unless its reference changed so that the alert no longer applies."""
	context = _load_context(log)
	if not context or context.expiry_date != getdate(log.expiry_date):
		frappe.db.set_value(
			ALERT_LOG,
			log.name,
			{
				"status": STATUS_FAILED,
				"retry_count": MAX_RETRIES,
				"delivery_note": _("Not retried because the alert no longer applies to the record."),
			},
			update_modified=False,
		)
		return
	threshold_days = cint(log.threshold_days)
	rule = rule_for_threshold(threshold_days, run.rules_for(context.kind), overdue_severity_for(context.kind))
	_deliver(run, log.name, context, threshold_days, rule)


def _retry_pending_alerts(run):
	"""Retry Queued and Failed alerts (at most MAX_RETRIES times) and, without limit, those that found no
	recipient; they never create duplicates."""
	logs = frappe.get_all(
		ALERT_LOG,
		filters={"status": ("in", RETRY_STATUSES), "retry_count": ("<", MAX_RETRIES)},
		fields=LOG_FIELDS,
		order_by="creation asc",
		limit_page_length=0,
	)
	logs += frappe.get_all(
		ALERT_LOG,
		filters={"status": STATUS_NO_RECIPIENTS},
		fields=LOG_FIELDS,
		order_by="creation asc",
		limit_page_length=0,
	)
	for log in logs:
		# Count the attempt first so that an alert that keeps raising cannot be retried forever.
		if log.get("status") != STATUS_NO_RECIPIENTS:
			frappe.db.set_value(
				ALERT_LOG, log.name, "retry_count", cint(log.retry_count) + 1, update_modified=False
			)
		_guarded(
			_retry_alert,
			run,
			log,
			title="Regulatory alert retry failed",
			doctype=ALERT_LOG,
			name=log.name,
		)


# --- Scheduled jobs ---


def run_daily_expiry_processing():
	"""Daily job: refresh expiry facts, retry unsent alerts, create renewal actions and new alerts."""
	run = _Run(frappe.get_single(SETTINGS))
	registrations = _get_rows(PRODUCT_REGISTRATION, REGISTRATION_FIELDS)
	establishments = _get_rows(ESTABLISHMENT_REGISTRATION, ESTABLISHMENT_FIELDS)
	documents = _get_rows(
		REGULATORY_DOCUMENT,
		DOCUMENT_FIELDS,
		{"expiry_date": ("is", "set"), "verification_status": ("in", DOCUMENT_STATUSES)},
	)

	_maintain(run, registrations, establishments, documents)
	alerts_enabled = run.settings.get("enable_expiry_alerts")
	if alerts_enabled:
		_retry_pending_alerts(run)
	_create_renewal_actions(run, registrations)
	if alerts_enabled:
		_send_new_alerts(run, registrations, establishments, documents)


def send_weekly_regulatory_digest():
	"""Weekly job: email the default owner (or the Regulatory Approvers) and the settings recipients."""
	run = _Run(frappe.get_single(SETTINGS))
	if not run.settings.get("send_weekly_digest"):
		return
	recipients = run.recipients(frappe._dict(), None)
	emails = sorted({email for email in map(run.email, recipients) if email})
	if not emails:
		return

	registrations = _get_rows(
		PRODUCT_REGISTRATION, REGISTRATION_FIELDS, {"official_status": ("in", ALERTABLE_STATUSES)}
	)
	past_due_queries = frappe.db.count(
		REGISTRATION_ACTION,
		{"current_stage": FDA_QUERY_STAGE, "response_due_date": ("<", run.today)},
	)
	subject, message = render_digest(build_digest(registrations, past_due_queries, run.today))
	frappe.sendmail(recipients=emails, subject=subject, message=message)
