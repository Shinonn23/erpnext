"""Item validation hook for the regulatory classification.

Product Category defaults to Not Regulated and is never blank. Other categories are regulated: transactions
validate the registration that covers the Item and it keeps the read-only coverage fields.
"""

from typing import Any

import frappe
from frappe import _

from erpnext.regulatory_affairs import coverage, permissions
from erpnext.regulatory_affairs.constants import (
	ITEM_CATEGORIES,
	ITEM_CATEGORY_FIELD,
	ITEM_COVERAGE_FIELDS,
	MEDICAL_DEVICE,
	NOT_REGULATED,
	OTHER_CATEGORY,
)


def is_regulated(item: Any) -> bool:
	"""Whether the Item has a Thai FDA Product Category, i.e. needs an FDA registration (Not Regulated needs none)."""
	category = (item.get(ITEM_CATEGORY_FIELD) or "").strip()
	return bool(category) and category != NOT_REGULATED


def category_of(value: Any) -> str:
	return (value or "").strip()


def required_category(value: Any) -> str:
	category = category_of(value)
	if not category:
		frappe.throw(
			_("Product Category is required. Choose Not Regulated when registration is not required.")
		)
	return category


def check_category_change(doc) -> None:
	"""The default is available to every Item creator; only Regulatory Affairs changes a classified Item.

	The field also sits on permission level 1, so a form save by anyone else never changes it; this check
	closes the server-side paths that skip permission levels (scripts, API calls with ignore_permissions).
	Install, patch and migrate runs are not a user action and pass.
	"""
	before = doc.get_doc_before_save()
	old = category_of(before.get(ITEM_CATEGORY_FIELD)) if before else ""
	new = required_category(doc.get(ITEM_CATEGORY_FIELD))
	if old == new or (not old and new == NOT_REGULATED):
		return
	flags = frappe.flags
	if flags.in_install or flags.in_patch or flags.in_migrate or frappe.session.user == "Administrator":
		return
	if not permissions.is_operator():
		frappe.throw(
			_("Only a Regulatory Operator can set or change the Product Category of an Item."),
			frappe.PermissionError,
		)


@frappe.whitelist()
def classify_item(
	item_code: str,
	category: str | None = None,
	medical_device_category: str | None = None,
	other_category: str | None = None,
) -> dict:
	"""Classify an Item in one step (Regulatory Operator): Product Category, Medical Device Category
	and, for the category Other, its name. Nobody needs Item write rights: the change goes through this method;
	`validate` still enforces the role rule. A category cannot be cleared.

	The Medical Device Category is kept only for a Medical Device; the Other Category only for Other.
	"""
	if not permissions.is_operator():
		frappe.throw(
			_("Only a Regulatory Operator can set or change the Product Category of an Item."),
			frappe.PermissionError,
		)
	frappe.has_permission("Item", "read", item_code, throw=True)
	doc = frappe.get_doc("Item", item_code)
	category = required_category(category)
	doc.set(ITEM_CATEGORY_FIELD, category)
	if category == MEDICAL_DEVICE:
		device = category_of(medical_device_category)
		if not device:
			frappe.throw(_("Choose the Medical Device Category (IVD, Non-IVD or Other)."))
		doc.medical_device_category = device
	else:
		doc.medical_device_category = None
	doc.regulatory_other_category = category_of(other_category) if category == OTHER_CATEGORY else None
	if category and category not in ITEM_CATEGORIES:
		frappe.throw(_("Product Category {0} is not valid.").format(frappe.bold(category)))
	if category == OTHER_CATEGORY and not doc.regulatory_other_category:
		frappe.throw(_("Enter the Other Category when the Product Category is Other."))
	doc.flags.ignore_permissions = True
	doc.save()
	return {
		"category": doc.get(ITEM_CATEGORY_FIELD) or "",
		"medical_device_category": doc.get("medical_device_category"),
	}


@frappe.whitelist()
def set_product_category(item_code: str, category: str | None = None) -> str:
	"""Set only the Product Category of an Item the caller can read; blank categories are refused."""
	frappe.has_permission("Item", "read", item_code, throw=True)
	doc = frappe.get_doc("Item", item_code)
	doc.set(ITEM_CATEGORY_FIELD, required_category(category))
	doc.flags.ignore_permissions = True
	doc.save()
	return doc.get(ITEM_CATEGORY_FIELD) or ""


def validate(doc, method=None):
	"""Item `validate` hook: guard the Product Category, then refresh or clear the derived coverage."""
	before = doc.get_doc_before_save()
	if not category_of(doc.get(ITEM_CATEGORY_FIELD)) and not (
		before and category_of(before.get(ITEM_CATEGORY_FIELD))
	):
		doc[ITEM_CATEGORY_FIELD] = NOT_REGULATED
	check_category_change(doc)
	if is_regulated(doc):
		coverage.refresh_item_coverage(doc)
	else:
		doc.update(dict.fromkeys(ITEM_COVERAGE_FIELDS))


@frappe.whitelist()
def classify_items(item_codes: str | list, category: str, medical_device_category: str | None = None) -> dict:
	"""Classify many Items at once (list view bulk action, Regulatory Operator): the same category for each.

	Items that fail (a Medical Device without its Device Category ...) are listed and the rest are saved.
	"""
	codes = frappe.parse_json(item_codes) if isinstance(item_codes, str) else item_codes
	done, failed = [], {}
	for code in codes or []:
		try:
			classify_item(code, category, medical_device_category)
			done.append(code)
		except Exception as error:
			frappe.clear_last_message()
			failed[code] = str(error)
	return {"done": done, "failed": failed}


def unclassified_count() -> int:
	"""Enabled sales Items that RA has not classified yet."""
	return frappe.db.count(
		"Item",
		{"disabled": 0, "is_sales_item": 1, ITEM_CATEGORY_FIELD: ["in", ["", None]]},
	)
