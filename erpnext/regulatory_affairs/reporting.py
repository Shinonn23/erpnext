"""Shared helpers for the Regulatory Affairs Script Reports.

Filter handling, row builders and gap detectors are plain functions over dicts so they run without a
site. The ``fetch_*`` functions run the batched queries: one query per dataset and one grouped query per
measure (never one per row). The reports only describe the master data; ``coverage.py`` stays the single
source of truth for what covers an Item.
"""

from collections import defaultdict
from datetime import date, datetime
from typing import Any

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils import cint, flt, get_datetime, getdate

from erpnext.regulatory_affairs.constants import (
	ACTIVE,
	CLOSED_STAGES,
	DRAFT,
	ESTABLISHMENT_REGISTRATION,
	EXPIRED,
	EXPIRY_UNCONFIRMED,
	ITEM_CATEGORY_FIELD,
	PRODUCT_REGISTRATION,
	REGISTERED_PRODUCT,
	REGULATORY_DOCUMENT,
	SETTINGS,
	SUSPENDED,
)
from erpnext.regulatory_affairs.coverage import is_in_force, registration_window

# Registrations that are neither a draft nor in a terminal state.
LIVE_STATUSES = (ACTIVE, SUSPENDED, EXPIRED)

MEASURE_FIELDS = ("stock_qty", "stock_value", "open_po_qty", "open_so_qty")

REGISTRATION_FIELDS = (
	"name",
	"registration_number",
	"certificate_type",
	"product_category",
	"device_category",
	"product_family_name",
	"product_owner",
	"official_status",
	"issue_date",
	"expiry_basis",
	"expiry_date",
	"renewal_status",
	"responsible_user",
	"establishment_registration",
)

LINE_FIELDS = (
	"name",
	"parent",
	"idx",
	"line_no",
	"newcode",
	"product_name",
	"identifier",
	"item_code",
	"item_name_at_registration",
	"effective_from",
	"effective_to",
	"line_status",
)

# Gap types in display order, each with its fixed severity.
GAP_REQUIRED_ITEM = "Required Item Without Coverage"
GAP_NO_CERTIFICATE = "Active Registration Without Valid Certificate"
GAP_EXPIRED_WITH_STOCK = "Expired Registration With Stock"
GAP_ESTABLISHMENT = "Establishment Not Active"
GAP_UNCONFIRMED_EXPIRY = "Unconfirmed Expiry"
GAP_DOCUMENT_EXPIRED = "Supporting Document Expired"
GAP_LINE_WITHOUT_ITEM = "Mapped Line Without Item"

GAP_SEVERITY = {
	GAP_REQUIRED_ITEM: "Critical",
	GAP_NO_CERTIFICATE: "Critical",
	GAP_EXPIRED_WITH_STOCK: "Critical",
	GAP_ESTABLISHMENT: "Critical",
	GAP_UNCONFIRMED_EXPIRY: "Warning",
	GAP_DOCUMENT_EXPIRED: "Warning",
	GAP_LINE_WITHOUT_ITEM: "Info",
}
GAP_ORDER = {gap_type: index for index, gap_type in enumerate(GAP_SEVERITY)}
SEVERITY_ORDER = {"Critical": 0, "Warning": 1, "Info": 2}

# Datasets each gap type reads; the loader fetches only what the selected gap types need.
GAP_DATASETS = {
	GAP_REQUIRED_ITEM: {"items", "registrations", "lines"},
	GAP_NO_CERTIFICATE: {"registrations", "documents"},
	GAP_EXPIRED_WITH_STOCK: {"registrations", "lines", "stock", "item_names"},
	GAP_ESTABLISHMENT: {"registrations", "establishments"},
	GAP_UNCONFIRMED_EXPIRY: {"registrations"},
	GAP_DOCUMENT_EXPIRED: {"registrations", "documents"},
	GAP_LINE_WITHOUT_ITEM: {"registrations", "lines"},
}


# --------------------------------------------------------------------------------------------------
# Filters, dates and sorting
# --------------------------------------------------------------------------------------------------


def to_date(value: Any) -> date | None:
	"""A date from a date, datetime or ISO string; None for blank values."""
	return getdate(value) if value else None


def clean_filters(filters: Any) -> frappe._dict:
	"""Report filters as a dict without blank values (0 and False are kept: they are real choices)."""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	return frappe._dict({key: value for key, value in (filters or {}).items() if value not in (None, "", [])})


def int_filter(filters: Any, fieldname: str) -> int | None:
	"""An Int filter value; None when it is blank or not a number."""
	value = filters.get(fieldname)
	if value is None or value == "":
		return None
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return None


def days_between(target: Any, reference: Any) -> int | None:
	"""Whole days from ``reference`` to ``target`` (negative when ``target`` is in the past)."""
	if not target:
		return None
	return (getdate(target) - getdate(reference)).days


def effective_expiry(registration: Any) -> date | None:
	"""The stated expiry date; None when the registration states no expiry (same rule as coverage)."""
	return registration_window(registration)[1]


def expiry_sort_key(expiry: Any, *tie_breakers: Any) -> tuple:
	"""Sort key: earliest expiry first, rows without an expiry last."""
	expiry = to_date(expiry)
	return (expiry is None, expiry or date.min, *tie_breakers)


def sort_by_expiry(rows: list[dict], *tie_fields: str) -> list[dict]:
	"""Rows ordered by ``expiry_date`` (blank last), then by the tie-break fields."""
	return sorted(
		rows,
		key=lambda row: expiry_sort_key(
			row.get("expiry_date"), *(str(row.get(field) or "") for field in tie_fields)
		),
	)


def column(
	fieldname: str, label: str, fieldtype: str = "Data", width: int = 120, options: str | None = None
) -> dict[str, Any]:
	"""A report column definition."""
	definition = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}
	if options:
		definition["options"] = options
	return definition


def registration_query_filters(
	filters: Any, default_statuses: tuple[str, ...] | None = None
) -> dict[str, Any]:
	"""``get_list`` conditions for the registration filters a report supports.

	``default_statuses`` applies only when no ``official_status`` filter was chosen.
	"""
	conditions: dict[str, Any] = {}
	for fieldname in (
		"responsible_user",
		"product_category",
		"device_category",
		"product_owner",
		"expiry_basis",
	):
		if filters.get(fieldname):
			conditions[fieldname] = filters[fieldname]
	if filters.get("registration"):
		conditions["name"] = filters["registration"]
	if filters.get("official_status"):
		conditions["official_status"] = filters["official_status"]
	elif default_statuses:
		conditions["official_status"] = ["in", list(default_statuses)]
	return conditions


def is_expiring_within(registration: Any, within_days: int | None, today_date: date) -> bool:
	"""Whether the stated expiry is at most ``within_days`` away (past expiries count)."""
	if within_days is None:
		return True
	remaining = days_between(effective_expiry(registration), today_date)
	return remaining is not None and remaining <= within_days


# --------------------------------------------------------------------------------------------------
# Batched queries
# --------------------------------------------------------------------------------------------------


def get_registrations(conditions: dict[str, Any]) -> list[frappe._dict]:
	"""Registrations the user may read (permission checked), unsorted."""
	return frappe.get_list(
		PRODUCT_REGISTRATION, filters=conditions, fields=list(REGISTRATION_FIELDS), limit_page_length=0
	)


def fetch_registered_lines(
	registration_names: Any, line_status: str | None = None, item_code: str | None = None
) -> list[frappe._dict]:
	"""Product lines of the given registrations in one query.

	Permission is decided on the parent: callers pass only registrations returned by ``get_registrations``.
	"""
	names = list(registration_names)
	if not names:
		return []
	filters: dict[str, Any] = {"parent": ["in", names], "parenttype": PRODUCT_REGISTRATION}
	if line_status:
		filters["line_status"] = line_status
	if item_code:
		filters["item_code"] = item_code
	return frappe.get_all(
		REGISTERED_PRODUCT,
		filters=filters,
		fields=list(LINE_FIELDS),
		order_by="parent asc, idx asc",
		limit_page_length=0,
	)


def fetch_item_names(item_codes: Any) -> dict[str, str]:
	"""Item names by code in one query (names of Items already mapped by a visible registration)."""
	codes = list(item_codes)
	if not codes:
		return {}
	rows = frappe.get_all(
		"Item", filters={"name": ["in", codes]}, fields=["name", "item_name"], limit_page_length=0
	)
	return {row.name: row.item_name for row in rows}


def fetch_stock_by_item(item_codes: Any) -> dict[str, dict[str, float]]:
	"""Actual quantity and stock value summed over all warehouses, one grouped Bin query."""
	codes = list(item_codes)
	if not codes:
		return {}
	bin_ = frappe.qb.DocType("Bin")
	rows = (
		frappe.qb.from_(bin_)
		.select(
			bin_.item_code,
			Sum(IfNull(bin_.actual_qty, 0)).as_("qty"),
			Sum(IfNull(bin_.stock_value, 0)).as_("value"),
		)
		.where(bin_.item_code.isin(codes))
		.groupby(bin_.item_code)
		.run(as_dict=True)
	)
	# Valuation is accounting data: only users who may read Bin see it.
	show_value = frappe.has_permission("Bin", "read")
	return {row.item_code: stock_measure(row, show_value) for row in rows}


def stock_measure(row: Any, show_value: bool) -> dict[str, float]:
	"""Quantity and (when permitted) value of one grouped Bin row."""
	return {"qty": flt(row.qty), "value": flt(row.value) if show_value else 0.0}


def _fetch_open_order_qty(
	item_doctype: str, parent_doctype: str, done_field: str, item_codes: Any
) -> dict[str, float]:
	"""Open quantity (stock UOM) per Item on submitted, not closed orders; one grouped query.

	Each row contributes ``max(qty - done, 0)`` converted with its conversion factor.
	"""
	codes = list(item_codes)
	if not codes:
		return {}
	child = frappe.qb.DocType(item_doctype)
	parent = frappe.qb.DocType(parent_doctype)
	pending = child.qty - IfNull(child[done_field], 0)
	factor = Case().when(child.conversion_factor > 0, child.conversion_factor).else_(1)
	open_qty = Case().when(pending > 0, pending * factor).else_(0)
	rows = (
		frappe.qb.from_(child)
		.inner_join(parent)
		.on(parent.name == child.parent)
		.select(child.item_code, Sum(open_qty).as_("qty"))
		.where(parent.docstatus == 1)
		.where(parent.status.notin(["Closed", "Cancelled"]))
		.where(child.item_code.isin(codes))
		.groupby(child.item_code)
		.run(as_dict=True)
	)
	return {row.item_code: flt(row.qty) for row in rows}


def fetch_open_po_qty(item_codes: Any) -> dict[str, float]:
	"""Quantity still to be received on open Purchase Orders, by Item."""
	return _fetch_open_order_qty("Purchase Order Item", "Purchase Order", "received_qty", item_codes)


def fetch_open_so_qty(item_codes: Any) -> dict[str, float]:
	"""Quantity still to be delivered on open Sales Orders, by Item."""
	return _fetch_open_order_qty("Sales Order Item", "Sales Order", "delivered_qty", item_codes)


def fetch_item_measures(item_codes: Any) -> dict[str, dict]:
	"""Stock, open PO and open SO per Item: three grouped queries for any number of Items."""
	codes = sorted(set(item_codes))
	return {
		"stock": fetch_stock_by_item(codes),
		"po": fetch_open_po_qty(codes),
		"so": fetch_open_so_qty(codes),
	}


# --------------------------------------------------------------------------------------------------
# Lines and measures
# --------------------------------------------------------------------------------------------------


def is_open_line(line: Any, today_date: date) -> bool:
	"""An Active product line that has not been closed before today."""
	if line.get("line_status") != "Active":
		return False
	end = to_date(line.get("effective_to"))
	return end is None or end >= today_date


def open_items_by_registration(lines: list[Any], today_date: date) -> dict[str, dict[str, list[str]]]:
	"""``{registration: {item_code: [newcode, ...]}}`` for open lines that name an Item."""
	result: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
	for line in lines:
		item_code = line.get("item_code")
		if not item_code or not is_open_line(line, today_date):
			continue
		newcodes = result[line.get("parent")][item_code]
		if line.get("newcode"):
			newcodes.add(line["newcode"])
	return {
		registration: {item_code: sorted(newcodes) for item_code, newcodes in items.items()}
		for registration, items in result.items()
	}


def item_measure(measures: dict, item_code: str) -> dict[str, float]:
	"""The four stock/order measures of one Item (zero when it has none)."""
	stock = (measures.get("stock") or {}).get(item_code) or {}
	return {
		"stock_qty": flt(stock.get("qty")),
		"stock_value": flt(stock.get("value")),
		"open_po_qty": flt((measures.get("po") or {}).get(item_code)),
		"open_so_qty": flt((measures.get("so") or {}).get(item_code)),
	}


def summarise_impact(item_codes: list[str], measures: dict) -> dict[str, float]:
	"""Item count and measure totals over distinct Items."""
	totals = dict.fromkeys(MEASURE_FIELDS, 0.0)
	for item_code in set(item_codes):
		for fieldname, value in item_measure(measures, item_code).items():
			totals[fieldname] += value
	return {"affected_items": len(set(item_codes)), **totals}


def load_registration_impact(
	conditions: dict[str, Any], within_days: int | None, today_date: date, item_code: str | None = None
) -> tuple[list[Any], dict[str, dict[str, list[str]]], dict[str, dict]]:
	"""Registrations, their open Items and the batched measures shared by the two expiry reports."""
	registrations = [
		registration
		for registration in get_registrations(conditions)
		if is_expiring_within(registration, within_days, today_date)
	]
	lines = fetch_registered_lines([row.name for row in registrations], "Active", item_code)
	items_by_registration = open_items_by_registration(lines, today_date)
	item_codes = {code for items in items_by_registration.values() for code in items}
	return registrations, items_by_registration, fetch_item_measures(item_codes)


# --------------------------------------------------------------------------------------------------
# Expiry Summary and Affected Business
# --------------------------------------------------------------------------------------------------


def build_expiry_summary_rows(
	registrations: list[Any],
	items_by_registration: dict[str, dict[str, list[str]]],
	measures: dict,
	today_date: date,
) -> list[dict]:
	"""One row per registration with its Item impact, earliest expiry first (no expiry last)."""
	rows = []
	for registration in registrations:
		expiry = effective_expiry(registration)
		item_codes = list(items_by_registration.get(registration.get("name"), {}))
		rows.append(
			{
				"registration": registration.get("name"),
				"certificate_type": registration.get("certificate_type"),
				"product_category": registration.get("product_category"),
				"device_category": registration.get("device_category"),
				"product_family_name": registration.get("product_family_name"),
				"official_status": registration.get("official_status"),
				"expiry_basis": registration.get("expiry_basis"),
				"expiry_date": expiry,
				"days_remaining": days_between(expiry, today_date),
				"renewal_status": registration.get("renewal_status"),
				"responsible_user": registration.get("responsible_user"),
				**summarise_impact(item_codes, measures),
			}
		)
	return sort_by_expiry(rows, "registration")


def affected_business_filters(filters: Any) -> frappe._dict:
	"""Affected Business filters with the default 365-day window applied when none was supplied."""
	filters = frappe._dict(filters or {})
	if int_filter(filters, "expiry_within_days") is None:
		filters["expiry_within_days"] = 365
	return filters


def build_affected_business_rows(
	registrations: list[Any],
	items_by_registration: dict[str, dict[str, list[str]]],
	item_names: dict[str, str],
	measures: dict,
	today_date: date,
) -> list[dict]:
	"""One row per (registration, Item) carrying that Item's own stock and open orders."""
	rows = []
	for registration in registrations:
		expiry = effective_expiry(registration)
		days = days_between(expiry, today_date)
		for item_code, newcodes in sorted(items_by_registration.get(registration.get("name"), {}).items()):
			rows.append(
				{
					"registration": registration.get("name"),
					"official_status": registration.get("official_status"),
					"item_code": item_code,
					"item_name": item_names.get(item_code),
					"newcode": ", ".join(newcodes),
					"expiry_date": expiry,
					"days_remaining": days,
					**item_measure(measures, item_code),
					"responsible_user": registration.get("responsible_user"),
					"renewal_status": registration.get("renewal_status"),
				}
			)
	return sort_by_expiry(rows, "registration", "item_code")


# --------------------------------------------------------------------------------------------------
# Item Coverage Matrix
# --------------------------------------------------------------------------------------------------


def normalise_line_status(value: str | None) -> str | None:
	"""Line status to filter on: Active when blank, None (no filter) for "All"."""
	if not value:
		return "Active"
	return None if value == "All" else value


def line_in_force(line: Any, registration: Any, on_date: date) -> bool:
	"""Whether an Active line is inside its effective dates and its registration is in force."""
	if line.get("line_status") != "Active":
		return False
	start, end = to_date(line.get("effective_from")), to_date(line.get("effective_to"))
	if (start and start > on_date) or (end and end < on_date):
		return False
	return is_in_force(registration, on_date)


def build_coverage_matrix_rows(
	lines: list[Any],
	registrations_by_name: dict[str, Any],
	item_names: dict[str, str],
	stock: dict[str, dict[str, float]],
	today_date: date,
	only_with_stock: bool = False,
) -> list[dict]:
	"""One row per product line; Item stock comes from a pre-grouped dataset so lines never multiply."""
	rows = []
	for line in lines:
		registration = registrations_by_name.get(line.get("parent"))
		if not registration:
			continue
		item_code = line.get("item_code")
		stock_qty = flt((stock.get(item_code) or {}).get("qty")) if item_code else 0.0
		if only_with_stock and stock_qty <= 0:
			continue
		rows.append(
			{
				"item_code": item_code,
				"item_name": item_names.get(item_code) or line.get("item_name_at_registration"),
				"newcode": line.get("newcode"),
				"identifier": line.get("identifier"),
				"registration": registration.get("name"),
				"official_status": registration.get("official_status"),
				"line_status": line.get("line_status"),
				"expiry_date": effective_expiry(registration),
				"effective_from": to_date(line.get("effective_from")),
				"effective_to": to_date(line.get("effective_to")),
				"in_force": 1 if line_in_force(line, registration, today_date) else 0,
				"stock_qty": stock_qty,
			}
		)
	return sorted(
		rows,
		key=lambda row: (
			not row["item_code"],
			row["item_code"] or "",
			row["registration"] or "",
			row["newcode"] or "",
		),
	)


# --------------------------------------------------------------------------------------------------
# Compliance Gap
# --------------------------------------------------------------------------------------------------


def make_gap(
	gap_type: str,
	subject_type: str,
	subject: str,
	detail: str,
	owner: str | None = None,
	item: str | None = None,
	item_name: str | None = None,
	registration: str | None = None,
	product_category: str | None = None,
) -> dict[str, Any]:
	"""One gap row. ``item``, ``registration`` and ``product_category`` are filter keys, not columns."""
	return {
		"severity": GAP_SEVERITY[gap_type],
		"gap_type": gap_type,
		"subject_type": subject_type,
		"subject": subject,
		"item_name": item_name,
		"detail": detail,
		"owner": owner or None,
		"item": item,
		"registration": registration,
		"product_category": product_category or None,
	}


def registration_owner(registration: Any, default_owner: str | None = None) -> str | None:
	"""Responsible user of a registration, falling back to the configured default owner."""
	return registration.get("responsible_user") or default_owner


def item_owner(item: Any, default_owner: str | None = None) -> str | None:
	"""Regulatory owner of an Item, falling back to the configured default owner."""
	return item.get("regulatory_owner") or default_owner


def is_lapsed(registration: Any, today_date: date) -> bool:
	"""Expired, or Active although the stated expiry has passed (the daily job has not run yet)."""
	status = registration.get("official_status")
	if status == EXPIRED:
		return True
	if status != ACTIVE:
		return False
	expiry = effective_expiry(registration)
	return expiry is not None and expiry < today_date


def build_mapping_candidates(
	lines: list[Any], registrations_by_name: dict[str, Any], on_date: date
) -> dict[str, list[dict]]:
	"""Candidate mappings per Item from one preloaded dataset (same filters as ``get_mapping_at_date``)."""
	candidates: dict[str, list[dict]] = defaultdict(list)
	for line in lines:
		item_code = line.get("item_code")
		registration = registrations_by_name.get(line.get("parent"))
		if not item_code or not registration or line.get("line_status") != "Active":
			continue
		if registration.get("official_status") == DRAFT:
			continue
		start, end = to_date(line.get("effective_from")), to_date(line.get("effective_to"))
		if (start and start > on_date) or (end and end < on_date):
			continue
		issue_date = to_date(registration.get("issue_date"))
		if issue_date and issue_date > on_date:
			continue
		candidates[item_code].append(
			{
				"registration": registration.get("name"),
				"item_code": item_code,
				"newcode": line.get("newcode"),
				"official_status": registration.get("official_status"),
				"issue_date": registration.get("issue_date"),
				"expiry_basis": registration.get("expiry_basis"),
				"expiry_date": registration.get("expiry_date"),
			}
		)
	return candidates


def resolve_item_mapping(candidates: list[dict], on_date: date) -> tuple[dict | None, list[str]]:
	"""Non-raising twin of ``coverage.select_mapping``: ``(mapping, ambiguous registrations)``.

	The list is empty unless more than one in-force candidate competes; the first candidate is returned then.
	When nothing is in force the registration that expired last is returned.
	"""
	if not candidates:
		return None, []
	in_force = [candidate for candidate in candidates if is_in_force(candidate, on_date)]
	if in_force:
		active = [candidate for candidate in in_force if candidate["official_status"] == ACTIVE]
		preferred = active or in_force
		ambiguous = [candidate["registration"] for candidate in preferred] if len(preferred) > 1 else []
		return preferred[0], ambiguous

	def last_expiry(candidate: dict) -> tuple[date, str]:
		return (to_date(candidate["expiry_date"]) or date.max, candidate["registration"])

	return max(candidates, key=last_expiry), []


def coverage_gap_reason(mapping: dict | None, ambiguous: list[str], on_date: date) -> str | None:
	"""Why an Item is not covered on a date; None when an Active in-force registration covers it."""
	if mapping is None:
		return _("No registered product line covers this Item.")
	if ambiguous:
		return _("More than one registration covers this Item on {0}: {1}.").format(
			on_date, ", ".join(sorted(ambiguous))
		)
	if mapping["official_status"] == ACTIVE and is_in_force(mapping, on_date):
		return None
	if mapping["official_status"] != ACTIVE:
		return _("Registration {0} is {1}.").format(mapping["registration"], mapping["official_status"])
	return _("Registration {0} expired on {1}.").format(mapping["registration"], effective_expiry(mapping))


def detect_required_items_without_coverage(
	items: list[Any],
	registrations: list[Any],
	lines: list[Any],
	today_date: date,
	default_owner: str | None = None,
) -> list[dict]:
	"""Items with a Product Category (regulated Items) that have no Active, in-force coverage today."""
	registrations_by_name = {registration.get("name"): registration for registration in registrations}
	candidates = build_mapping_candidates(lines, registrations_by_name, today_date)
	gaps = []
	for item in items:
		if not item.get(ITEM_CATEGORY_FIELD):
			continue
		mapping, ambiguous = resolve_item_mapping(candidates.get(item.get("name"), []), today_date)
		reason = coverage_gap_reason(mapping, ambiguous, today_date)
		if reason:
			gaps.append(
				make_gap(
					GAP_REQUIRED_ITEM,
					"Item",
					item.get("name"),
					reason,
					item_owner(item, default_owner),
					item=item.get("name"),
					item_name=item.get("item_name"),
					registration=mapping["registration"] if mapping else None,
					product_category=item.get(ITEM_CATEGORY_FIELD),
				)
			)
	return gaps


def has_valid_certificate(documents: list[Any], today_date: date) -> bool:
	"""Whether a Verified, unexpired Certificate document exists."""
	for document in documents:
		if document.get("document_type") != "Certificate":
			continue
		if document.get("verification_status") != "Verified":
			continue
		expiry = to_date(document.get("expiry_date"))
		if expiry is None or expiry >= today_date:
			return True
	return False


def detect_registrations_without_certificate(
	registrations: list[Any],
	documents_by_registration: dict[str, list[Any]],
	today_date: date,
	default_owner: str | None = None,
) -> list[dict]:
	"""Active registrations with no Verified, unexpired Certificate document."""
	gaps = []
	for registration in registrations:
		if registration.get("official_status") != ACTIVE:
			continue
		documents = documents_by_registration.get(registration.get("name"), [])
		if has_valid_certificate(documents, today_date):
			continue
		certificates = [document for document in documents if document.get("document_type") == "Certificate"]
		if certificates:
			detail = _("{0} Certificate document(s) on file, none Verified and unexpired.").format(
				len(certificates)
			)
		else:
			detail = _("No Certificate document is on file.")
		gaps.append(
			make_gap(
				GAP_NO_CERTIFICATE,
				PRODUCT_REGISTRATION,
				registration.get("name"),
				detail,
				registration_owner(registration, default_owner),
				registration=registration.get("name"),
				product_category=registration.get("product_category"),
			)
		)
	return gaps


def detect_unconfirmed_expiry(registrations: list[Any], default_owner: str | None = None) -> list[dict]:
	"""Active registrations whose expiry basis is Unconfirmed (treated as non-expiring, no alerts)."""
	detail = _("Expiry is unconfirmed: the registration is treated as non-expiring and sends no alerts.")
	return [
		make_gap(
			GAP_UNCONFIRMED_EXPIRY,
			PRODUCT_REGISTRATION,
			registration.get("name"),
			detail,
			registration_owner(registration, default_owner),
			registration=registration.get("name"),
			product_category=registration.get("product_category"),
		)
		for registration in registrations
		if registration.get("official_status") == ACTIVE
		and registration.get("expiry_basis") == EXPIRY_UNCONFIRMED
	]


def lapsed_item_codes(registrations: list[Any], lines: list[Any], today_date: date) -> set[str]:
	"""Items with an open line on a lapsed registration (the Items whose stock must be looked up)."""
	items_by_registration = open_items_by_registration(lines, today_date)
	return {
		item_code
		for registration in registrations
		if is_lapsed(registration, today_date)
		for item_code in items_by_registration.get(registration.get("name"), {})
	}


def detect_expired_registrations_with_stock(
	registrations: list[Any],
	lines: list[Any],
	stock: dict[str, dict[str, float]],
	item_names: dict[str, str],
	today_date: date,
	default_owner: str | None = None,
) -> list[dict]:
	"""One gap per (lapsed registration, mapped Item) that still has stock on hand."""
	items_by_registration = open_items_by_registration(lines, today_date)
	gaps = []
	for registration in registrations:
		if not is_lapsed(registration, today_date):
			continue
		expiry = effective_expiry(registration)
		for item_code in sorted(items_by_registration.get(registration.get("name"), {})):
			quantity = flt((stock.get(item_code) or {}).get("qty"))
			if quantity <= 0:
				continue
			if expiry:
				detail = _("Registration expired on {0}; {1} in stock.").format(expiry, quantity)
			else:
				detail = _("Registration is {0}; {1} in stock.").format(
					registration.get("official_status"), quantity
				)
			gaps.append(
				make_gap(
					GAP_EXPIRED_WITH_STOCK,
					PRODUCT_REGISTRATION,
					registration.get("name"),
					detail,
					registration_owner(registration, default_owner),
					item=item_code,
					item_name=item_names.get(item_code),
					registration=registration.get("name"),
					product_category=registration.get("product_category"),
				)
			)
	return gaps


def describe_line(line: Any) -> str:
	"""Short label of a product line for messages."""
	label = line.get("newcode") or line.get("identifier") or line.get("product_name")
	return label or _("line {0}").format(line.get("idx"))


def detect_lines_without_item(
	registrations: list[Any], lines: list[Any], default_owner: str | None = None
) -> list[dict]:
	"""Active product lines of live registrations that do not name an ERPNext Item."""
	live = {
		registration.get("name"): registration
		for registration in registrations
		if registration.get("official_status") in LIVE_STATUSES
	}
	gaps = []
	for line in lines:
		registration = live.get(line.get("parent"))
		if not registration or line.get("line_status") != "Active" or line.get("item_code"):
			continue
		gaps.append(
			make_gap(
				GAP_LINE_WITHOUT_ITEM,
				PRODUCT_REGISTRATION,
				registration.get("name"),
				_("Product line {0} is not mapped to an Item.").format(describe_line(line)),
				registration_owner(registration, default_owner),
				registration=registration.get("name"),
				product_category=registration.get("product_category"),
			)
		)
	return gaps


def detect_establishments_not_active(
	registrations: list[Any],
	establishments: list[Any],
	today_date: date,
	default_owner: str | None = None,
) -> list[dict]:
	"""Active registrations whose establishment licence is not Active or has passed its expiry."""
	establishments_by_name = {establishment.get("name"): establishment for establishment in establishments}
	gaps = []
	for registration in registrations:
		establishment = establishments_by_name.get(registration.get("establishment_registration"))
		if registration.get("official_status") != ACTIVE or not establishment:
			continue
		expiry = effective_expiry(establishment)
		if establishment.get("official_status") != ACTIVE:
			detail = _("Establishment {0} is {1}.").format(
				establishment["name"], establishment.get("official_status")
			)
		elif expiry is not None and expiry < today_date:
			detail = _("Establishment {0} expired on {1}.").format(establishment["name"], expiry)
		else:
			continue
		gaps.append(
			make_gap(
				GAP_ESTABLISHMENT,
				PRODUCT_REGISTRATION,
				registration.get("name"),
				detail,
				registration_owner(registration, default_owner),
				registration=registration.get("name"),
				product_category=registration.get("product_category"),
			)
		)
	return gaps


def detect_expired_supporting_documents(
	registrations: list[Any],
	documents_by_registration: dict[str, list[Any]],
	today_date: date,
	default_owner: str | None = None,
) -> list[dict]:
	"""Verified/Expired non-Certificate documents past expiry on Active registrations.

	A document is not reported when a valid document of the same type and NEWCODE replaces it; expired
	Certificates are covered by the missing-certificate gap.
	"""

	def is_valid(document: Any) -> bool:
		expiry = to_date(document.get("expiry_date"))
		return document.get("verification_status") == "Verified" and (expiry is None or expiry >= today_date)

	gaps = []
	for registration in registrations:
		if registration.get("official_status") != ACTIVE:
			continue
		documents = documents_by_registration.get(registration.get("name"), [])
		replaced = {
			(document.get("document_type"), document.get("applicable_newcode") or "")
			for document in documents
			if is_valid(document)
		}
		for document in documents:
			expiry = to_date(document.get("expiry_date"))
			if (
				document.get("document_type") == "Certificate"
				or document.get("verification_status") not in ("Verified", "Expired")
				or expiry is None
				or expiry >= today_date
				or (document.get("document_type"), document.get("applicable_newcode") or "") in replaced
			):
				continue
			gaps.append(
				make_gap(
					GAP_DOCUMENT_EXPIRED,
					REGULATORY_DOCUMENT,
					document.get("name"),
					_("{0} {1} of registration {2} expired on {3}.").format(
						document.get("document_type"),
						document.get("document_number") or document.get("name"),
						registration.get("name"),
						expiry,
					),
					registration_owner(registration, default_owner),
					registration=registration.get("name"),
					product_category=registration.get("product_category"),
				)
			)
	return gaps


def group_by_registration(documents: list[Any]) -> dict[str, list[Any]]:
	"""Documents keyed by their registration."""
	grouped: dict[str, list[Any]] = defaultdict(list)
	for document in documents:
		grouped[document.get("registration")].append(document)
	return grouped


def build_gaps(data: dict[str, Any], gap_types: list[str], today_date: date) -> list[dict]:
	"""Run the selected detectors over preloaded datasets (no queries)."""
	items = data.get("items") or []
	registrations = data.get("registrations") or []
	lines = data.get("lines") or []
	default_owner = data.get("default_owner")
	documents = group_by_registration(data.get("documents") or [])
	gaps: list[dict] = []
	if GAP_REQUIRED_ITEM in gap_types:
		gaps += detect_required_items_without_coverage(items, registrations, lines, today_date, default_owner)
	if GAP_NO_CERTIFICATE in gap_types:
		gaps += detect_registrations_without_certificate(registrations, documents, today_date, default_owner)
	if GAP_EXPIRED_WITH_STOCK in gap_types:
		gaps += detect_expired_registrations_with_stock(
			registrations,
			lines,
			data.get("stock") or {},
			data.get("item_names") or {},
			today_date,
			default_owner,
		)
	if GAP_ESTABLISHMENT in gap_types:
		gaps += detect_establishments_not_active(
			registrations, data.get("establishments") or [], today_date, default_owner
		)
	if GAP_UNCONFIRMED_EXPIRY in gap_types:
		gaps += detect_unconfirmed_expiry(registrations, default_owner)
	if GAP_DOCUMENT_EXPIRED in gap_types:
		gaps += detect_expired_supporting_documents(registrations, documents, today_date, default_owner)
	if GAP_LINE_WITHOUT_ITEM in gap_types:
		gaps += detect_lines_without_item(registrations, lines, default_owner)
	return gaps


def attach_registration_items(gaps: list[dict], lines: list[Any], today_date: date) -> None:
	"""Give registration-level gaps the Items of their registration so the Item filter can match them."""
	items_by_registration = open_items_by_registration(lines, today_date)
	for gap in gaps:
		if gap["item"] is None and gap["registration"]:
			gap["items"] = sorted(items_by_registration.get(gap["registration"], {}))


def gap_matches_filters(gap: dict, filters: Any) -> bool:
	"""Whether a gap passes the gap type, severity, registration, owner, product category and Item filters."""
	if filters.get("gap_type") and gap.get("gap_type") != filters["gap_type"]:
		return False
	if filters.get("severity") and gap.get("severity") != filters["severity"]:
		return False
	if filters.get("registration") and gap.get("registration") != filters["registration"]:
		return False
	if filters.get("responsible_user") and gap.get("owner") != filters["responsible_user"]:
		return False
	if filters.get("product_category") and gap.get("product_category") != filters["product_category"]:
		return False
	item = filters.get("item")
	return not item or gap.get("item") == item or item in (gap.get("items") or ())


def sort_gaps(gaps: list[dict]) -> list[dict]:
	"""Critical first, then by gap type, subject and Item."""
	return sorted(
		gaps,
		key=lambda gap: (
			SEVERITY_ORDER.get(gap["severity"], len(SEVERITY_ORDER)),
			GAP_ORDER.get(gap["gap_type"], len(GAP_ORDER)),
			gap.get("subject") or "",
			gap.get("item") or "",
		),
	)


def selected_gap_types(filters: Any) -> list[str]:
	"""The gap types to run: the filtered one or all of them."""
	gap_type = filters.get("gap_type")
	if gap_type in GAP_SEVERITY:
		return [gap_type]
	return list(GAP_SEVERITY)


def required_datasets(gap_types: list[str], filters: Any) -> set[str]:
	"""Datasets the loader must fetch for the selected gap types (and the Item filter)."""
	datasets = set().union(*(GAP_DATASETS[gap_type] for gap_type in gap_types))
	if filters.get("item"):
		datasets |= {"registrations", "lines"}
	return datasets


ITEM_GAP_FIELDS = ("name", "item_name", ITEM_CATEGORY_FIELD, "regulatory_owner")


def fetch_regulated_items() -> list[frappe._dict]:
	"""Enabled Items with a Product Category (only the columns the gaps use)."""
	return frappe.get_all(
		"Item",
		filters={"disabled": 0, ITEM_CATEGORY_FIELD: ["is", "set"]},
		fields=list(ITEM_GAP_FIELDS),
		limit_page_length=0,
	)


def load_gap_data(datasets: set[str], today_date: date) -> dict[str, Any]:
	"""Fetch each required dataset once; every query is batched, none runs per row."""
	data: dict[str, Any] = {"default_owner": frappe.db.get_single_value(SETTINGS, "default_regulatory_owner")}
	if "items" in datasets:
		data["items"] = fetch_regulated_items()
	registrations: list[Any] = []
	if "registrations" in datasets:
		registrations = get_registrations({})
		data["registrations"] = registrations
	names = [registration.name for registration in registrations]
	if "lines" in datasets:
		data["lines"] = fetch_registered_lines(names, "Active")
	if "documents" in datasets and names:
		data["documents"] = frappe.get_list(
			REGULATORY_DOCUMENT,
			filters={"registration": ["in", names]},
			fields=[
				"name",
				"registration",
				"document_type",
				"document_number",
				"applicable_newcode",
				"verification_status",
				"expiry_date",
			],
			limit_page_length=0,
		)
	if "establishments" in datasets:
		establishment_names = sorted(
			{row.establishment_registration for row in registrations if row.establishment_registration}
		)
		if establishment_names:
			data["establishments"] = frappe.get_list(
				ESTABLISHMENT_REGISTRATION,
				filters={"name": ["in", establishment_names]},
				fields=["name", "official_status", "issue_date", "expiry_basis", "expiry_date"],
				limit_page_length=0,
			)
	if "stock" in datasets:
		stock_items = lapsed_item_codes(registrations, data.get("lines") or [], today_date)
		data["stock"] = fetch_stock_by_item(stock_items)
		data["item_names"] = fetch_item_names(stock_items)
	return data


# --------------------------------------------------------------------------------------------------
# Renewal Workbench
# --------------------------------------------------------------------------------------------------


def workbench_action_filters(filters: Any) -> dict[str, Any]:
	"""``get_list`` conditions for the Renewal Workbench.

	Action type defaults to Renewal ("All" lifts it). An explicit stage wins over ``open_only``,
	which otherwise (default on) hides Approved, Rejected and Cancelled actions.
	"""
	conditions: dict[str, Any] = {}
	action_type = filters.get("action_type") or "Renewal"
	if action_type != "All":
		conditions["action_type"] = action_type
	if filters.get("assigned_to"):
		conditions["assigned_to"] = filters["assigned_to"]
	if filters.get("current_stage"):
		conditions["current_stage"] = filters["current_stage"]
	elif cint(filters.get("open_only", 1)):
		conditions["current_stage"] = ["not in", list(CLOSED_STAGES)]
	return conditions


def build_workbench_rows(
	actions: list[Any],
	registrations_by_name: dict[str, Any],
	today_date: date,
	product_category: str | None = None,
) -> list[dict]:
	"""Action rows with the registration expiry and the days to expiry / FDA query due date.

	``product_category`` keeps only actions whose registration belongs to that category.
	"""
	rows = []
	for action in actions:
		registration = registrations_by_name.get(action.get("registration")) or {}
		if product_category and registration.get("product_category") != product_category:
			continue
		expiry = effective_expiry(registration) if registration else None
		is_open = action.get("current_stage") not in CLOSED_STAGES
		days_to_due = days_between(action.get("response_due_date"), today_date) if is_open else None
		rows.append(
			{
				"action": action.get("name"),
				"registration": action.get("registration"),
				"product_category": registration.get("product_category"),
				"action_type": action.get("action_type"),
				"expiry_date": expiry,
				"days_to_expiry": days_between(expiry, today_date),
				"current_stage": action.get("current_stage"),
				"assigned_to": action.get("assigned_to"),
				"fda_application_number": action.get("fda_application_number"),
				"submitted_date": action.get("submitted_date"),
				"fda_query_date": action.get("fda_query_date"),
				"response_due_date": action.get("response_due_date"),
				"days_to_query_due": days_to_due,
				"new_expiry_date": action.get("new_expiry_date"),
				"modified": action.get("modified"),
			}
		)
	return sort_workbench_rows(rows)


def sort_workbench_rows(rows: list[dict]) -> list[dict]:
	"""Registration expiry ascending (blank last), then most recently modified first."""
	by_modified = sorted(
		rows, key=lambda row: get_datetime(row.get("modified") or datetime.min), reverse=True
	)
	return sorted(by_modified, key=lambda row: expiry_sort_key(row.get("expiry_date")))


def load_workbench_registrations(actions: list[Any]) -> dict[str, Any]:
	"""Registrations (permission checked) referenced by the actions, in one query."""
	names = sorted({action.registration for action in actions if action.registration})
	if not names:
		return {}
	registrations = frappe.get_list(
		PRODUCT_REGISTRATION,
		filters={"name": ["in", names]},
		fields=["name", "product_category", "issue_date", "expiry_basis", "expiry_date"],
		limit_page_length=0,
	)
	return {registration.name: registration for registration in registrations}
