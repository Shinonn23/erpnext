"""Resolve which FDA registration covers an Item on a date.

A registration is *in force* on a date when its status can carry coverage (Active, Expired or
Superseded), the date is on or after its issue date and, when it states an expiry, on or before it.
Renewals therefore never make two registrations ambiguous: the expired predecessor and its
successor have different in-force windows.
"""

from datetime import date
from typing import Any

import frappe
from frappe import _
from frappe.utils import getdate, today

from erpnext.regulatory_affairs.constants import (
	ACTIVE,
	DRAFT,
	EXPIRY_SPECIFIED,
	IN_FORCE_STATUSES,
	ITEM_COVERAGE_FIELDS,
	PRODUCT_REGISTRATION,
	REGISTERED_PRODUCT,
	SETTINGS,
)


def normalize_registration_number(value: str | None) -> str:
	"""Comparison-only form of a registration number; the entered value is kept as typed."""
	return "".join(character for character in (value or "").upper() if character.isalnum())


def _as_date(value: Any) -> date | None:
	return getdate(value) if value else None


def registration_window(registration: Any) -> tuple[date | None, date | None]:
	"""Return (start, end) of the dates a registration can cover; None means open-ended."""
	start = _as_date(registration.get("issue_date"))
	end = None
	if registration.get("expiry_basis") == EXPIRY_SPECIFIED:
		end = _as_date(registration.get("expiry_date"))
	return start, end


def ranges_overlap(
	left_start: date | None, left_end: date | None, right_start: date | None, right_end: date | None
) -> bool:
	"""Inclusive overlap where a missing endpoint is open."""
	return (left_start or date.min) <= (right_end or date.max) and (right_start or date.min) <= (
		left_end or date.max
	)


def lines_overlap(left: Any, right: Any) -> bool:
	"""Whether two registered-product lines' effective ranges overlap."""
	return ranges_overlap(
		_as_date(left.get("effective_from")),
		_as_date(left.get("effective_to")),
		_as_date(right.get("effective_from")),
		_as_date(right.get("effective_to")),
	)


def effective_span(line: Any, registration: Any) -> tuple[date | None, date | None]:
	"""Dates a product line can really cover: its own range limited by its registration's window."""
	starts = [d for d in (_as_date(line.get("effective_from")), registration_window(registration)[0]) if d]
	ends = [d for d in (_as_date(line.get("effective_to")), registration_window(registration)[1]) if d]
	return (max(starts) if starts else None, min(ends) if ends else None)


def is_in_force(registration: Any, on_date: date | str | None = None) -> bool:
	"""Whether the registration (a row with status, issue_date, expiry_basis, expiry_date) covers a date."""
	if registration.get("official_status") not in IN_FORCE_STATUSES:
		return False
	effective_date = getdate(on_date or today())
	start, end = registration_window(registration)
	return (start is None or start <= effective_date) and (end is None or end >= effective_date)


def select_mapping(rows: list[Any], on_date: date | str | None = None) -> Any | None:
	"""Pick the single mapping that applies on a date from candidate rows.

	Prefers rows whose registration is in force, then Active ones. More than one remaining candidate
	is invalid master data and raises. When nothing is in force the row that expired last is returned
	so callers can report an expired, suspended or revoked registration instead of "missing".
	"""
	if not rows:
		return None
	effective_date = getdate(on_date or today())
	in_force = [row for row in rows if is_in_force(row, effective_date)]
	if in_force:
		preferred = [row for row in in_force if row.get("official_status") == ACTIVE] or in_force
		if len(preferred) > 1:
			frappe.throw(
				_("Item {0} has more than one effective regulatory registration on {1}.").format(
					frappe.bold(preferred[0].get("item_code") or ""), effective_date
				),
				title=_("Ambiguous regulatory mapping"),
			)
		chosen = preferred[0]
		chosen["in_force"] = True
		return chosen

	def last_expiry(row: Any) -> tuple[date, str]:
		return (_as_date(row.get("expiry_date")) or date.max, row.get("registration") or "")

	chosen = max(rows, key=last_expiry)
	chosen["in_force"] = False
	return chosen


def get_mapping_at_date(item_code: str, on_date: date | str | None = None) -> frappe._dict | None:
	"""Resolve one mapping for an Item by effective dates, without deciding regulatory eligibility.

	The result carries ``in_force``; ``official_status`` and ``expiry_date`` let a caller word the
	reason when the registration is not eligible.
	"""
	effective_date = getdate(on_date or today())
	product = frappe.qb.DocType(REGISTERED_PRODUCT)
	registration = frappe.qb.DocType(PRODUCT_REGISTRATION)
	query = (
		frappe.qb.from_(product)
		.inner_join(registration)
		.on(registration.name == product.parent)
		.select(
			product.parent.as_("registration"),
			product.item_code,
			product.newcode,
			product.identifier,
			product.physical_manufacturer,
			product.manufacturer_country,
			registration.issue_date,
			registration.expiry_date,
			registration.expiry_basis,
			registration.official_status,
		)
		.where(product.parenttype == PRODUCT_REGISTRATION)
		.where(product.item_code == item_code)
		.where(product.line_status == "Active")
		.where(registration.official_status != DRAFT)
		.where(product.effective_from.isnull() | (product.effective_from <= effective_date))
		.where(product.effective_to.isnull() | (product.effective_to >= effective_date))
		.where(registration.issue_date.isnull() | (registration.issue_date <= effective_date))
	)
	rows = [frappe._dict(row) for row in query.run(as_dict=True)]
	return select_mapping(rows, effective_date)


def get_effective_mapping(item_code: str, on_date: date | str | None = None) -> frappe._dict | None:
	"""The mapping only when its registration is Active and in force on the date."""
	mapping = get_mapping_at_date(item_code, on_date)
	if mapping and mapping.official_status == ACTIVE and mapping.in_force:
		return mapping
	return None


def coverage_values(mapping: Any | None) -> dict[str, Any]:
	"""Item coverage field values for a mapping (blank when there is none)."""
	if not mapping:
		return dict.fromkeys(ITEM_COVERAGE_FIELDS, None)
	return {
		"current_regulatory_registration": mapping.get("registration"),
		"current_newcode": mapping.get("newcode"),
		"current_registration_expiry": mapping.get("expiry_date"),
		"current_registration_status": mapping.get("official_status"),
	}


def refresh_item_coverage(item: Any, on_date: date | str | None = None) -> None:
	"""Set an Item document's read-only coverage fields from the registration master."""
	mapping = get_effective_mapping(item.name, on_date) if item.name and not item.is_new() else None
	item.update(coverage_values(mapping))


def refresh_item_coverage_for_items(item_codes: set[str]) -> None:
	"""Recompute stored coverage for Items after a registration changes, without touching `modified`."""
	for item_code in sorted(item_codes):
		if not frappe.db.exists("Item", item_code):
			continue
		try:
			mapping = get_effective_mapping(item_code)
		except frappe.ValidationError:
			frappe.log_error(
				title=f"Ambiguous regulatory registration for {item_code}", message=frappe.get_traceback()
			)
			mapping = None
		frappe.db.set_value("Item", item_code, coverage_values(mapping), update_modified=False)


def refresh_item_coverage_for_registration(registration: str) -> None:
	"""Refresh every Item mapped by a registration (any line status, so removed lines are cleared too)."""
	item_codes = frappe.get_all(
		REGISTERED_PRODUCT,
		filters={"parent": registration, "parenttype": PRODUCT_REGISTRATION},
		pluck="item_code",
	)
	refresh_item_coverage_for_items({item_code for item_code in item_codes if item_code})


def get_regulatory_owner(item: Any) -> str | None:
	"""Item-level owner, falling back to the configured default owner."""
	owner = item.get("regulatory_owner")
	if owner:
		return owner
	return frappe.db.get_single_value(SETTINGS, "default_regulatory_owner")
