"""Item validation hook for the regulatory classification.

An Item with a Product Category (``regulatory_product_category``) is regulated: transactions validate it
against the registration that covers it and the Item keeps the read-only coverage fields. An Item without
a Product Category is not checked and carries no coverage.
"""

from typing import Any

import frappe
from frappe import _

from erpnext.regulatory_affairs import coverage, permissions
from erpnext.regulatory_affairs.constants import ITEM_CATEGORY_FIELD, ITEM_COVERAGE_FIELDS


def is_regulated(item: Any) -> bool:
	"""Whether the Item has a Product Category, i.e. needs an FDA registration."""
	return bool((item.get(ITEM_CATEGORY_FIELD) or "").strip())


def category_of(value: Any) -> str:
	return (value or "").strip()


def check_category_change(doc) -> None:
	"""Only a Regulatory Operator sets or changes the Product Category and only a Regulatory Approver clears it.

	The field also sits on permission level 1, so a form save by anyone else never changes it; this check
	closes the server-side paths that skip permission levels (scripts, API calls with ignore_permissions).
	Install, patch and migrate runs are not a user action and pass.
	"""
	flags = frappe.flags
	if flags.in_install or flags.in_patch or flags.in_migrate or frappe.session.user == "Administrator":
		return
	before = doc.get_doc_before_save()
	old = category_of(before.get(ITEM_CATEGORY_FIELD)) if before else ""
	new = category_of(doc.get(ITEM_CATEGORY_FIELD))
	if old == new:
		return
	if not new:
		if not permissions.is_approver():
			frappe.throw(
				permissions.approver_only_message(_("clear the Product Category of an Item")),
				frappe.PermissionError,
			)
	elif not permissions.is_operator():
		frappe.throw(
			_("Only a Regulatory Operator can set or change the Product Category of an Item."),
			frappe.PermissionError,
		)


@frappe.whitelist()
def set_product_category(item_code: str, category: str | None = None) -> str:
	"""Set (Operator) or clear (Approver) the Product Category of an Item the caller can read.

	The Regulatory roles cannot edit the Item form, so the change goes through this method; `validate` still
	enforces the role rule.
	"""
	frappe.has_permission("Item", "read", item_code, throw=True)
	doc = frappe.get_doc("Item", item_code)
	doc.set(ITEM_CATEGORY_FIELD, category_of(category))
	doc.flags.ignore_permissions = True
	doc.save()
	return doc.get(ITEM_CATEGORY_FIELD) or ""


def validate(doc, method=None):
	"""Item `validate` hook: guard the Product Category, then refresh or clear the derived coverage."""
	check_category_change(doc)
	if is_regulated(doc):
		coverage.refresh_item_coverage(doc)
	else:
		doc.update(dict.fromkeys(ITEM_COVERAGE_FIELDS))
