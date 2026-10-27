"""Role checks shared by the RA controllers."""

import frappe
from frappe import _

from erpnext.regulatory_affairs.constants import ROLE_APPROVER, ROLE_OPERATOR


def is_approver(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = frappe.get_roles(user)
	return ROLE_APPROVER in roles or "System Manager" in roles


def is_operator(user: str | None = None) -> bool:
	"""A Regulatory Operator, or anyone who may act as an Approver (an Approver can do all an Operator can)."""
	user = user or frappe.session.user
	if is_approver(user):
		return True
	return ROLE_OPERATOR in frappe.get_roles(user)


def require_approver(message: str) -> None:
	if not is_approver():
		frappe.throw(message, frappe.PermissionError)


def approver_only_message(action: str) -> str:
	return _("Only a Regulatory Approver can {0}.").format(action)
