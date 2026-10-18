"""Idempotent RA setup: roles, Custom Fields, the Item assessment default and seed alert rules.

Runs after install and on every migrate; it never assigns users or owners.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext.regulatory_affairs.constants import (
	ASSESSMENT_FIELD,
	ASSESSMENT_NOT_ASSESSED,
	MANAGED_ROLES,
	SETTINGS,
)
from erpnext.regulatory_affairs.custom_fields import CUSTOM_FIELDS

DEFAULT_ALERT_RULES = (
	(365, "Info", "Create Renewal Action"),
	(180, "Info", "Reminder"),
	(120, "Warning", "Internal Review Reminder"),
	(60, "Warning", "Escalate"),
	(30, "Critical", "Escalate Affected Teams"),
	(14, "Critical", "Weekly Reminder"),
	(7, "Critical", "Daily Reminder"),
	(0, "Expired", "Trigger Compliance Check"),
)


def after_install():
	ensure_configuration()


def after_migrate():
	ensure_configuration()


def ensure_configuration():
	ensure_roles()
	ensure_custom_fields()
	ensure_item_assessment_default()
	ensure_settings()


def ensure_roles():
	for role_name in MANAGED_ROLES:
		if frappe.db.exists("Role", role_name):
			role = frappe.get_doc("Role", role_name)
			if role.disabled or not role.desk_access:
				role.update({"disabled": 0, "desk_access": 1})
				role.save(ignore_permissions=True)
			continue
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1, "is_custom": 1}).insert(
			ignore_permissions=True
		)


def ensure_custom_fields():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)


def ensure_item_assessment_default():
	"""Items without an assessment status (created before the field existed) are Not Assessed.

	One UPDATE; idempotent because only blank or NULL values match.
	"""
	if not frappe.db.has_column("Item", ASSESSMENT_FIELD):
		return
	frappe.db.sql(
		f"update `tabItem` set `{ASSESSMENT_FIELD}` = %s where ifnull(`{ASSESSMENT_FIELD}`, '') = ''",
		(ASSESSMENT_NOT_ASSESSED,),
	)


def ensure_settings():
	"""Seed the expiry thresholds once, only on a site that has never saved the settings."""
	if frappe.db.sql("select 1 from `tabSingles` where doctype = %s limit 1", (SETTINGS,)):
		return
	settings = frappe.get_single(SETTINGS)
	if settings.get("expiry_alert_rules"):
		return
	for days, severity, action in DEFAULT_ALERT_RULES:
		settings.append(
			"expiry_alert_rules", {"days_before_expiry": days, "severity": severity, "action": action}
		)
	settings.save(ignore_permissions=True)
