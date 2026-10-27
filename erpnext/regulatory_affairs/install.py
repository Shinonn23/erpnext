"""Idempotent RA setup: roles, Custom Fields and seed alert rules.

Runs after install and on every migrate; it never assigns users or owners.
"""

import frappe
from frappe.cache_manager import clear_global_cache
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext.regulatory_affairs.constants import (
	MANAGED_ROLES,
	MODULE,
	ROLE_APPROVER,
	ROLE_OPERATOR,
	ROLE_VIEWER,
	SETTINGS,
)
from erpnext.regulatory_affairs.custom_fields import CUSTOM_FIELDS, ITEM_CATEGORY_PERMLEVEL

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


def before_migrate():
	"""Rebuild the module map so model sync sees a module added by the code just pulled.

	`bench migrate` loads the module map from the Redis cache in `frappe.init`, and `frappe.clear_cache()` does
	not rebuild it. After a pull that adds this module the map is stale, model sync skips the module and no
	DocType of it is created.
	"""
	clear_global_cache()


def after_install():
	ensure_configuration()


def after_migrate():
	ensure_configuration()


def ensure_configuration():
	ensure_module_def()
	ensure_roles()
	ensure_custom_fields()
	ensure_item_category_permissions()
	ensure_settings()


def ensure_module_def():
	"""Custom Fields name this module, so its Module Def must exist even when DocType sync has not made it."""
	if frappe.db.exists("Module Def", MODULE):
		return
	frappe.get_doc({"doctype": "Module Def", "module_name": MODULE, "app_name": "erpnext"}).insert(
		ignore_permissions=True
	)


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


def ensure_item_category_permissions():
	"""Level-1 rights for the Product Category on a site whose Item permissions are Custom DocPerms.

	`item.json` carries them for a site that uses the standard permissions; once any Custom DocPerm exists for
	Item (the privacy layer creates them) the JSON rows are ignored, so the same rows are added there:
	level-1 read for every role that reads Item, level-1 write for Operator and Approver, and the level-0 read
	a level-1 rule needs for the Regulatory roles. Idempotent; it never removes or lowers a right.
	"""
	from frappe.permissions import add_permission

	if not frappe.db.exists("Custom DocPerm", {"parent": "Item"}):
		return
	readers = set(
		frappe.get_all(
			"Custom DocPerm",
			filters={"parent": "Item", "permlevel": 0, "read": 1, "if_owner": 0},
			pluck="role",
		)
	)
	regulatory = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_APPROVER)
	wanted = {(role, 0, "read") for role in regulatory}
	wanted |= {(role, ITEM_CATEGORY_PERMLEVEL, "read") for role in readers | set(regulatory)}
	wanted |= {(role, ITEM_CATEGORY_PERMLEVEL, "write") for role in (ROLE_OPERATOR, ROLE_APPROVER)}
	for role, permlevel, ptype in sorted(wanted, key=lambda w: (w[1], w[0], w[2])):
		filters = {"parent": "Item", "role": role, "permlevel": permlevel, "if_owner": 0}
		if not frappe.db.exists("Custom DocPerm", filters):
			add_permission("Item", role, permlevel=permlevel, ptype=ptype)
		name = frappe.db.get_value("Custom DocPerm", filters)
		if name and not frappe.db.get_value("Custom DocPerm", name, ptype):
			frappe.db.set_value("Custom DocPerm", name, ptype, 1, update_modified=False)
	frappe.clear_cache(doctype="Item")


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
