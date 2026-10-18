"""Retire the Item fields "Thai FDA Assessment Status" and "Thai FDA Assessment Reason / Notes".

Regulatory Affairs classifies an Item by its Product Category (`regulatory_product_category`): an Item with
one is checked on transactions. The merge patch, which runs first, copies each merged registration's
category to the Items it covers (not to Items assessed Not Required / Exempt). An Item still assessed as
Registration Required without a category gets Other / "Not specified (legacy)" here so it stays checked.

Then the two Custom Fields are deleted. Their columns and values stay in `tabItem`; nothing is dropped.
"""

import frappe

from erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs import (
	LEGACY_ASSESSMENT_FIELD,
	LEGACY_EXEMPT,
	UNSPECIFIED_CATEGORY,
	legacy_assessment_values,
)
from erpnext.regulatory_affairs.constants import ITEM_CATEGORY_FIELD, OTHER_CATEGORY

LEGACY_FIELDS = (LEGACY_ASSESSMENT_FIELD, "custom_thai_fda_assessment_note")
LEGACY_REQUIRED = "Registration Required"


def execute():
	custom_fields = frappe.get_all(
		"Custom Field",
		filters={"dt": "Item", "fieldname": ("in", LEGACY_FIELDS)},
		fields=["name", "fieldname"],
	)
	# Convert only while the status field is still live: once it is gone the Product Category alone decides,
	# and a re-run must not give a category back to an Item whose category was cleared on purpose.
	status_is_live = any(field.fieldname == LEGACY_ASSESSMENT_FIELD for field in custom_fields)
	if status_is_live and frappe.db.has_column("Item", LEGACY_ASSESSMENT_FIELD):
		keep_required_items_regulated()
		report_exempt_items_with_category()
	for field in custom_fields:
		frappe.delete_doc("Custom Field", field.name, ignore_missing=True, force=True)
	frappe.clear_cache(doctype="Item")


def keep_required_items_regulated() -> list[str]:
	"""Give Items assessed as Registration Required without a Product Category the Other category."""
	# The merge patch creates the Custom Fields only when it has legacy registrations to merge.
	from erpnext.regulatory_affairs.install import ensure_custom_fields

	ensure_custom_fields()
	rows = frappe.db.sql(
		f"""select name from `tabItem`
		where `{LEGACY_ASSESSMENT_FIELD}` in %(required)s and ifnull(`{ITEM_CATEGORY_FIELD}`, '') = ''""",
		{"required": legacy_assessment_values(LEGACY_REQUIRED)},
		as_dict=True,
	)
	items = [row["name"] for row in rows]
	if not items:
		return items
	frappe.db.sql(
		f"update `tabItem` set `{ITEM_CATEGORY_FIELD}` = %(category)s, regulatory_other_category = %(other)s"
		" where name in %(items)s",
		{"category": OTHER_CATEGORY, "other": UNSPECIFIED_CATEGORY, "items": tuple(items)},
	)
	print(
		f"{len(items)} Item(s) assessed as Registration Required had no Product Category and are now "
		f"{OTHER_CATEGORY} / {UNSPECIFIED_CATEGORY}; set their real category on the Regulatory tab."
	)
	return items


def report_exempt_items_with_category() -> int:
	"""Count (and print) Items assessed Not Required / Exempt that have a Product Category anyway.

	The merge patch gives them none, but a category set before this patch makes them checked from now on.
	"""
	count = frappe.db.sql(
		f"""select count(*) from `tabItem`
		where `{LEGACY_ASSESSMENT_FIELD}` in %(exempt)s and ifnull(`{ITEM_CATEGORY_FIELD}`, '') != ''""",
		{"exempt": legacy_assessment_values(LEGACY_EXEMPT)},
	)[0][0]
	if count:
		print(
			f"{count} Item(s) assessed as {LEGACY_EXEMPT} have a Product Category and are now checked on "
			"transactions; clear the category of those that need no FDA registration."
		)
	return count
