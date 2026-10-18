"""Merge the legacy Thai FDA Registration data into Regulatory Affairs (RA).

The legacy DocTypes are retired, so everything here reads the legacy tables with plain SQL and never touches
the ORM for them. Nothing is dropped or deleted: the legacy tables stay in the database untouched.

The patch is idempotent (groups whose legacy records are already listed in a registration's
`legacy_thai_fda_records` are skipped) and a no-op when the legacy tables do not exist.

Dry run (prints the plan, writes nothing):
	bench --site <site> execute erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs.run \
		--kwargs "{'dry_run': True}"
"""

from collections import OrderedDict
from datetime import date
from typing import Any

import frappe
from frappe.utils import add_days, cint, getdate, today

from erpnext.patches.v16_0.translate_thai_fda_to_english import SELECT_TRANSLATIONS
from erpnext.regulatory_affairs.constants import (
	EXPIRY_SPECIFIED,
	EXPIRY_UNCONFIRMED,
	IN_FORCE_STATUSES,
	OTHER_CATEGORY,
	PRODUCT_CATEGORIES,
	PRODUCT_REGISTRATION,
	RENEWAL_HISTORY,
	SETTINGS,
)
from erpnext.regulatory_affairs.coverage import (
	effective_span,
	normalize_registration_number,
	ranges_overlap,
	registration_window,
)

LEGACY_REGISTRATION = "Thai FDA Registration"
LEGACY_RENEWAL_LOG = "Thai FDA Renewal Log"
LEGACY_SETTINGS = "Thai FDA Settings"
LEGACY_RECIPIENT = "Thai FDA Reminder Recipient"
REGULATORY_DOCUMENT = "Regulatory Document"
CONFLICT_TITLE = "Thai FDA to RA migration conflicts"
EXECUTE_PATH = "erpnext.patches.v16_0.merge_thai_fda_into_regulatory_affairs.execute"
SETTINGS_MARKER = "regulatory_legacy_settings_merged"
UNSPECIFIED_CATEGORY = "Not specified (legacy)"

STATUS_MAP = {
	"Pending Approval": "Draft",
	"Active": "Active",
	"Suspended": "Suspended",
	"Revoked": "Revoked",
	"Cancelled": "Cancelled",
	"Superseded": "Superseded",
}
LEGACY_DEFAULT_STATUS = "Active"
RENEWAL_STATUS_MAP = {
	"Not Started": "Not Started",
	"In Progress": "Preparing",
	"Submitted": "Submitted",
	"New Document Received": "Approved",
}
HISTORY_FIELDS = (
	"event_date",
	"stage",
	"application_number",
	"previous_registration_number",
	"new_registration_number",
	"previous_issue_date",
	"new_issue_date",
	"previous_expiry_date",
	"new_expiry_date",
	"previous_document",
	"new_document",
	"notes",
)
SETTINGS_COPY = {
	"enabled": "enable_expiry_alerts",
	"overdue_repeat_days": "overdue_repeat_days",
	"send_system_notification": "send_system_notification",
	"send_email": "send_email",
}


# ---------------------------------------------------------------------------------------------------------
# Pure helpers (no database access)
# ---------------------------------------------------------------------------------------------------------


def clean(value: Any) -> str:
	return str(value).strip() if value is not None else ""


def translate(doctype: str, fieldname: str, value: Any) -> str:
	"""Return the English option value for a legacy select value (English values pass through)."""
	text = clean(value)
	return SELECT_TRANSLATIONS.get(doctype, {}).get(fieldname, {}).get(text, text)


def is_placeholder(row: dict) -> bool:
	"""Rows that must not be grouped by number: pending approval, or a number that is not a real number."""
	number = clean(row.get("registration_number"))
	if translate(LEGACY_REGISTRATION, "status", row.get("status")) == "Pending Approval":
		return True
	return not any(character.isdigit() for character in number) or not normalize_registration_number(number)


def registration_number(row: dict) -> str:
	"""The number the merged registration gets: the typed number, or LEGACY-<name> for placeholders."""
	if is_placeholder(row):
		return f"LEGACY-{row.get('name')}"
	return clean(row.get("registration_number"))


def group_key(row: dict) -> str:
	return normalize_registration_number(registration_number(row)) or normalize_registration_number(
		f"LEGACY-{row.get('name')}"
	)


def newest_first(rows: list[dict]) -> list[dict]:
	return sorted(rows, key=lambda row: (str(row.get("modified") or ""), str(row.get("name"))), reverse=True)


def group_rows(rows: list[dict]) -> "OrderedDict[str, list[dict]]":
	"""Group legacy rows by normalized registration number; each group is sorted newest first."""
	groups: dict[str, list[dict]] = OrderedDict()
	for row in sorted(rows, key=lambda row: str(row.get("name"))):
		groups.setdefault(group_key(row), []).append(row)
	return OrderedDict((key, newest_first(group)) for key, group in groups.items())


def map_category(row: dict) -> tuple[str, str | None]:
	"""Return (product_category, other_category) for a legacy row."""
	category = translate(LEGACY_REGISTRATION, "regulatory_category", row.get("regulatory_category"))
	other = clean(row.get("other_category")) or None
	if category in PRODUCT_CATEGORIES and category != OTHER_CATEGORY:
		return category, None
	return OTHER_CATEGORY, other or (category if category and category != OTHER_CATEGORY else None) or (
		UNSPECIFIED_CATEGORY
	)


def map_expiry_basis(row: dict) -> str:
	return translate(LEGACY_REGISTRATION, "expiry_basis", row.get("expiry_basis")) or EXPIRY_UNCONFIRMED


def map_status(row: dict, on_date: Any = None) -> str:
	"""Legacy status -> RA official status; an Active registration whose stated expiry has passed is Expired."""
	legacy = translate(LEGACY_REGISTRATION, "status", row.get("status")) or LEGACY_DEFAULT_STATUS
	status = STATUS_MAP.get(legacy, "Draft")
	if (
		status == "Active"
		and map_expiry_basis(row) == EXPIRY_SPECIFIED
		and row.get("expiry_date")
		and getdate(row.get("expiry_date")) < getdate(on_date or today())
	):
		return "Expired"
	return status


def map_renewal_status(value: Any) -> str:
	legacy = translate(LEGACY_REGISTRATION, "renewal_status", value) or "Not Started"
	return RENEWAL_STATUS_MAP.get(legacy, "Not Started")


def map_activity(value: Any) -> str:
	return translate(LEGACY_REGISTRATION, "activity", value)


def find_conflicts(group: list[dict], on_date: Any = None) -> list[str]:
	"""Disagreements inside one group of legacy rows (attributes come from the newest row)."""
	number = registration_number(group[0])
	conflicts = []
	checks = (
		("expiry date", lambda row: clean(row.get("expiry_date"))),
		("status", lambda row: map_status(row, on_date)),
		("category", lambda row: map_category(row)[0]),
		("license / notification type", lambda row: clean(row.get("approval_type"))),
		("holder name", lambda row: clean(row.get("holder_name"))),
		("activity", lambda row: map_activity(row.get("activity"))),
		("issue date", lambda row: clean(row.get("issue_date"))),
	)
	for label, getter in checks:
		values = {getter(row) for row in group}
		if len(values) > 1:
			conflicts.append(
				f"{number}: different {label} on legacy records "
				+ ", ".join(f"{row.get('name')}={getter(row) or '-'}" for row in group)
			)
	items = [clean(row.get("item")) for row in group if clean(row.get("item"))]
	for item in sorted({item for item in items if items.count(item) > 1}):
		conflicts.append(f"{number}: Item {item} appears on several legacy records; mapped once")
	newest = group[0]
	if map_expiry_basis(newest) == EXPIRY_SPECIFIED and not newest.get("expiry_date"):
		conflicts.append(f"{number}: expiry is specified but no expiry date is recorded")
	if not clean(newest.get("regulatory_category")):
		conflicts.append(f"{number}: no category recorded; set to Other")
	return conflicts


def build_product_lines(group: list[dict], existing_items: set[str] | None = None) -> list[dict]:
	"""One registered-product line per legacy row (one per Item); identifier = the registration number."""
	number = registration_number(group[0])
	lines, seen = [], set()
	for row in reversed(group):
		item = clean(row.get("item"))
		if item and item in seen:
			continue
		seen.add(item)
		item_name = clean(row.get("item_name"))
		line = {
			"doctype": "Regulatory Registered Product",
			"line_no": len(lines) + 1,
			"product_name": item_name or item or number,
			"identifier": number,
			"line_status": "Active",
			"item_name_at_registration": item_name or None,
		}
		remarks = []
		if is_placeholder(row) and clean(row.get("registration_number")):
			remarks.append(f"Legacy registration number: {clean(row.get('registration_number'))}.")
		if item and (existing_items is None or item in existing_items):
			line["item_code"] = item
		elif item:
			remarks.append(f"Legacy Item {item} no longer exists.")
		if remarks:
			line["remarks"] = " ".join(remarks)
		lines.append(line)
	return lines


def build_history(children: list[dict]) -> list[dict]:
	"""Legacy renewal-log rows -> Regulatory Renewal History rows, oldest event first."""
	ordered = sorted(
		children,
		key=lambda row: (str(row.get("event_date") or ""), str(row.get("parent")), cint(row.get("idx"))),
	)
	history = []
	for child in ordered:
		entry = {"doctype": RENEWAL_HISTORY}
		for fieldname in HISTORY_FIELDS:
			value = child.get(fieldname)
			entry[fieldname] = (
				translate(LEGACY_RENEWAL_LOG, "stage", value) if fieldname == "stage" else value
			)
		history.append(entry)
	return history


def build_registration(
	group: list[dict],
	children: list[dict],
	existing_items: set[str] | None = None,
	on_date: Any = None,
) -> dict:
	"""The `Regulatory Product Registration` document dict for one group of legacy rows."""
	newest = group[0]
	number = registration_number(newest)
	category, other_category = map_category(newest)
	basis = map_expiry_basis(newest)
	status = map_status(newest, on_date)
	expiry_date = newest.get("expiry_date") if basis == EXPIRY_SPECIFIED else None
	names = [row.get("name") for row in sorted(group, key=lambda row: str(row.get("name")))]
	registration = {
		"doctype": PRODUCT_REGISTRATION,
		"registration_number": number,
		"registration_number_normalized": normalize_registration_number(number),
		"certificate_type": clean(newest.get("approval_type")) or None,
		"activity_type": map_activity(newest.get("activity")) or None,
		"official_status": status,
		"status_reason": None
		if status == "Draft"
		else f"Migrated from Thai FDA Registration {', '.join(names)}",
		"status_changed_on": newest.get("modified"),
		"status_changed_by": newest.get("modified_by") or newest.get("owner"),
		"product_category": category,
		"other_category": other_category,
		"issuing_authority": clean(newest.get("issuing_authority")) or None,
		"holder_name": clean(newest.get("holder_name")) or None,
		"source_url": clean(newest.get("source_url")) or None,
		"last_verified_on": newest.get("verified_date"),
		"responsible_user": newest.get("responsible_user") or None,
		"issue_date": newest.get("issue_date"),
		"expiry_basis": basis,
		"expiry_date": expiry_date,
		"days_to_expiry": (getdate(expiry_date) - getdate(on_date or today())).days if expiry_date else None,
		"renewal_status": map_renewal_status(newest.get("renewal_status")),
		"legacy_thai_fda_records": ", ".join(names),
		"registered_products": build_product_lines(group, existing_items),
		"legacy_renewal_history": build_history(children),
		"creation": min(str(row.get("creation") or "") for row in group) or None,
		"modified": newest.get("modified"),
		"modified_by": newest.get("modified_by"),
		"owner": newest.get("owner"),
	}
	return registration


def build_documents(group: list[dict], registration: dict) -> list[dict]:
	"""One Certificate `Regulatory Document` per distinct attached file on the legacy rows."""
	documents, seen = [], set()
	for row in group:
		file = clean(row.get("document"))
		if not file or file in seen:
			continue
		seen.add(file)
		verified_on = row.get("verified_date")
		documents.append(
			{
				"doctype": REGULATORY_DOCUMENT,
				"registration": registration["registration_number"],
				"document_type": "Certificate",
				"document_number": registration["registration_number"],
				"issuer": registration.get("issuing_authority"),
				"issue_date": row.get("issue_date"),
				"expiry_date": registration.get("expiry_date"),
				"file": file,
				"verification_status": "Verified" if verified_on else "Pending",
				"verified_on": verified_on,
				"remarks": f"Migrated from Thai FDA Registration {row.get('name')}",
				"creation": row.get("creation"),
				"modified": row.get("modified"),
				"owner": row.get("owner"),
			}
		)
	return documents


def is_merged(group: list[dict], merged_names: set[str]) -> bool:
	return all(row.get("name") in merged_names for row in group)


def plan_groups(
	rows: list[dict],
	children_by_parent: dict[str, list[dict]],
	merged_names: set[str],
	existing_numbers: dict[str, str],
	existing_items: set[str] | None = None,
	on_date: Any = None,
	existing_lines: list[dict] | None = None,
) -> list[dict]:
	"""Build the plan: one entry per group with its action and, when created, the documents to insert."""
	plan = []
	for key, group in group_rows(rows).items():
		names = sorted(row.get("name") for row in group)
		entry = {
			"key": key,
			"legacy_names": names,
			"modified": str(group[0].get("modified") or ""),
			"conflicts": [] if is_merged(group, merged_names) else find_conflicts(group, on_date),
		}
		if is_merged(group, merged_names):
			entry["action"] = "skip_merged"
		elif any(row.get("name") in merged_names for row in group):
			entry["action"] = "skip_partially_merged"
			entry["conflicts"].append(
				f"{registration_number(group[0])}: only some legacy records are merged already ({', '.join(names)});"
				" merge the rest by hand"
			)
		elif key in existing_numbers:
			entry["action"] = "skip_existing_number"
			entry["conflicts"].append(
				f"{registration_number(group[0])}: Regulatory Product Registration {existing_numbers[key]} "
				f"already has this number; legacy records {', '.join(names)} were not merged"
			)
		else:
			children = [child for row in group for child in children_by_parent.get(row.get("name"), [])]
			registration = build_registration(group, children, existing_items, on_date)
			entry.update(
				action="create",
				registration=registration,
				documents=build_documents(group, registration),
			)
		plan.append(entry)
	resolve_overlaps(plan, existing_lines or [])
	return plan


def _in_force(registration: dict) -> bool:
	return registration["official_status"] in IN_FORCE_STATUSES


STATUS_RANK = {"Active": 2, "Expired": 1, "Superseded": 0}


def _rank(entry: dict) -> tuple:
	"""Winner ordering, best first: planned status (Active beats Expired/Superseded), expires last
	(open-ended last of all), newest legacy modified, then the higher name."""
	registration = entry["registration"]
	end = registration_window(registration)[1]
	return (
		STATUS_RANK.get(registration["official_status"], -1),
		end or date.max,
		entry["modified"],
		entry["legacy_names"][-1],
	)


def resolve_overlaps(plan: list[dict], existing_lines: list[dict]) -> list[str]:
	"""Make sure no Item has two Active lines covering the same dates (that makes coverage ambiguous).

	Only planned lines are ever modified; RA records that already exist are never touched. Among planned
	registrations the best-ranked one keeps its line (see `_rank`). A loser whose span starts before the
	winner's start is trimmed to end the day before it (it stays Active); otherwise (no span would remain)
	it becomes Superseded. A planned line also loses against an existing Active line of an RA
	registration that ranks at least as well; against an existing registration of a lower rank (e.g.
	Expired versus a planned Active line) nothing is done. Every action is added to the entry's conflicts
	and recorded in `entry["resolved_against"]`.
	"""
	resolutions = []
	by_item: dict[str, list[tuple[dict, dict]]] = {}
	for entry in plan:
		if entry["action"] != "create" or not _in_force(entry["registration"]):
			continue
		for line in entry["registration"]["registered_products"]:
			if line.get("item_code") and line["line_status"] == "Active":
				by_item.setdefault(line["item_code"], []).append((entry, line))

	def act(entry, line, rival_name, rival_start, rival_existing):
		span = effective_span(line, entry["registration"])
		note = f"Overlaps {rival_name}; resolved automatically by the Thai FDA merge, review manually"
		if rival_start and (span[0] is None or rival_start > span[0]):
			cutoff = add_days(rival_start, -1)
			line["effective_to"] = cutoff
			action = f"trimmed to end on {cutoff}"
		else:
			line["line_status"] = "Superseded"
			action = "set to Superseded"
		line["remarks"] = " ".join(part for part in (line.get("remarks"), note + ".") if part)
		number = entry["registration"]["registration_number"]
		message = f"{number}: Item {line['item_code']} line {action} - {note}"
		entry["conflicts"].append(message)
		entry.setdefault("resolved_against", []).append(
			{"item": line["item_code"], "winner": rival_name, "existing": rival_existing}
		)
		resolutions.append(message)
		return line["line_status"] != "Active"

	existing_by_item: dict[str, list[dict]] = {}
	for existing in existing_lines:
		existing_by_item.setdefault(existing["item_code"], []).append(existing)

	for item, pairs in by_item.items():
		kept: list[tuple[dict, dict]] = []
		for entry, line in sorted(pairs, key=lambda pair: _rank(pair[0]), reverse=True):
			planned_rank = STATUS_RANK.get(entry["registration"]["official_status"], -1)
			rivals = [
				(existing["parent"], effective_span(existing, existing), True)
				for existing in existing_by_item.get(item, [])
				if STATUS_RANK.get(existing.get("official_status") or "Active", 2) >= planned_rank
			] + [
				(
					other["registration"]["registration_number"],
					effective_span(kept_line, other["registration"]),
					False,
				)
				for other, kept_line in kept
			]
			gone = False
			for name, rival_span, is_existing in rivals:
				if ranges_overlap(*effective_span(line, entry["registration"]), *rival_span):
					gone = act(entry, line, name, rival_span[0], is_existing)
					if gone:
						break
			if not gone:
				kept.append((entry, line))
	return resolutions


def choose_item_categories(rows: list[dict]) -> dict[str, tuple[str, str | None]]:
	"""Item -> (category, other category) from merged registrations (rows: registration + one line each).

	An Active line of an Active registration beats Expired/Superseded ones, which beat Draft or
	otherwise not-in-force registrations; ties go to the newest `modified`.
	"""

	def rank(row):
		return (
			row.get("line_status") == "Active",
			STATUS_RANK.get(row.get("official_status"), -1),
			str(row.get("modified") or ""),
		)

	categories: dict[str, tuple[str, str | None]] = {}
	for row in sorted(rows, key=rank, reverse=True):
		if row.get("item_code") and row.get("product_category"):
			categories.setdefault(row["item_code"], (row["product_category"], row.get("other_category")))
	return categories


def overlap_warnings(plan: list[dict], failed_numbers: set[str]) -> list[str]:
	"""Overlaps that were resolved in favour of a registration which then failed to insert."""
	losers: dict[str, list[tuple[str, str]]] = {}
	for entry in plan:
		for resolved in entry.get("resolved_against", []):
			if not resolved["existing"] and resolved["winner"] in failed_numbers:
				losers.setdefault(resolved["winner"], []).append(
					(resolved["item"], entry["registration"]["registration_number"])
				)
	return [
		f"Registration {winner} was NOT merged but its overlap with other registrations was already "
		"resolved in its favour: "
		+ "; ".join(f"Item {item} on {loser}" for item, loser in sorted(pairs))
		+ ". Fix the cause, re-run, then review those lines (they are Superseded or trimmed) by hand."
		for winner, pairs in sorted(losers.items())
	]


def parse_reminder_days(value: Any) -> list[int]:
	"""'90,60, 30;7' -> [90, 60, 30, 7] (positive integers, unique, in the given order)."""
	days = []
	for part in clean(value).replace(";", ",").split(","):
		part = part.strip()
		if part.isdigit() and int(part) > 0 and int(part) not in days:
			days.append(int(part))
	return days


def severity_for(days: int) -> str:
	if days >= 120:
		return "Info"
	if days >= 60:
		return "Warning"
	return "Critical"


def plan_alert_rules(reminder_days: Any, existing_days: set[int]) -> list[dict]:
	return [
		{"days_before_expiry": days, "severity": severity_for(days), "action": "Reminder"}
		for days in parse_reminder_days(reminder_days)
		if days not in existing_days
	]


def map_settings(legacy: dict) -> dict:
	"""Scalar legacy settings -> Regulatory Settings fields (only the keys the legacy Single holds)."""
	values = {}
	for source, target in SETTINGS_COPY.items():
		if legacy.get(source) in (None, ""):
			continue
		values[target] = cint(legacy[source])
	return values


def plan_recipients(
	rows: list[dict], existing_users: set[str], user_exists: Any = None
) -> tuple[list[dict], list[str]]:
	"""Return (recipients to add, legacy users skipped because they no longer exist)."""
	recipients, skipped, seen = [], [], set(existing_users)
	for row in rows:
		user = clean(row.get("user"))
		if not user or user in seen:
			continue
		seen.add(user)
		if user_exists is not None and not user_exists(user):
			skipped.append(user)
			continue
		recipients.append({"user": user, "enabled": cint(row.get("enabled", 1))})
	return recipients, skipped


def format_plan(plan: list[dict]) -> str:
	counts: dict[str, int] = {}
	for entry in plan:
		counts[entry["action"]] = counts.get(entry["action"], 0) + 1
	totals = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "nothing to merge"
	lines = [f"Thai FDA -> Regulatory Affairs: {totals}"]
	for entry in plan:
		if entry["action"] == "create":
			registration = entry["registration"]
			lines.append(
				f"  create {registration['registration_number']} [{registration['official_status']}, "
				f"{registration['product_category']}] from {', '.join(entry['legacy_names'])}: "
				f"{len(registration['registered_products'])} product line(s), "
				f"{len(registration['legacy_renewal_history'])} history row(s), "
				f"{len(entry['documents'])} document(s)"
			)
		else:
			lines.append(f"  {entry['action']}: {', '.join(entry['legacy_names'])}")
	for entry in plan:
		lines.extend(f"  conflict: {message}" for message in entry["conflicts"])
	return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------------------------------------


def legacy_tables_exist() -> bool:
	return bool(frappe.db.table_exists(LEGACY_REGISTRATION))


def read_legacy_rows() -> list[dict]:
	return frappe.db.sql(f"select * from `tab{LEGACY_REGISTRATION}`", as_dict=True) or []


def read_children() -> dict[str, list[dict]]:
	if not frappe.db.table_exists(LEGACY_RENEWAL_LOG):
		return {}
	children: dict[str, list[dict]] = {}
	for child in frappe.db.sql(
		f"select * from `tab{LEGACY_RENEWAL_LOG}` where parenttype = %s order by parent, idx",
		(LEGACY_REGISTRATION,),
		as_dict=True,
	):
		children.setdefault(child.get("parent"), []).append(child)
	return children


def read_merged_names() -> set[str]:
	if not (
		frappe.db.table_exists(PRODUCT_REGISTRATION)
		and frappe.db.has_column(PRODUCT_REGISTRATION, "legacy_thai_fda_records")
	):
		return set()
	rows = frappe.db.sql(
		f"select legacy_thai_fda_records from `tab{PRODUCT_REGISTRATION}`"
		" where ifnull(legacy_thai_fda_records, '') != ''",
		as_dict=True,
	)
	return {
		name.strip() for row in rows for name in row["legacy_thai_fda_records"].split(",") if name.strip()
	}


def read_existing_numbers() -> dict[str, str]:
	if not frappe.db.table_exists(PRODUCT_REGISTRATION):
		return {}
	rows = frappe.db.sql(
		f"select name, registration_number_normalized from `tab{PRODUCT_REGISTRATION}`", as_dict=True
	)
	return {row["registration_number_normalized"]: row["name"] for row in rows}


def read_existing_items(item_codes: set[str]) -> set[str]:
	if not item_codes:
		return set()
	rows = frappe.db.sql(
		"select name from `tabItem` where name in %(names)s", {"names": tuple(item_codes)}, as_dict=True
	)
	return {row["name"] for row in rows}


def read_existing_lines(item_codes: set[str]) -> list[dict]:
	"""Active product lines of in-force RA registrations for the planned Items (for overlap detection)."""
	tables = (PRODUCT_REGISTRATION, "Regulatory Registered Product")
	if not item_codes or not all(frappe.db.table_exists(table) for table in tables):
		return []
	return (
		frappe.db.sql(
			f"""select line.parent, line.item_code, line.effective_from, line.effective_to,
				registration.official_status, registration.issue_date, registration.expiry_basis, registration.expiry_date
			from `tabRegulatory Registered Product` line
			inner join `tab{PRODUCT_REGISTRATION}` registration on registration.name = line.parent
			where line.parenttype = %(parenttype)s and line.line_status = 'Active'
				and line.item_code in %(items)s and registration.official_status in %(statuses)s""",
			{
				"parenttype": PRODUCT_REGISTRATION,
				"items": tuple(item_codes),
				"statuses": tuple(IN_FORCE_STATUSES),
			},
			as_dict=True,
		)
		or []
	)


def run(dry_run: bool = False) -> dict:
	"""Merge the legacy data; with `dry_run=True` only print and return the plan (nothing is written)."""
	if not legacy_tables_exist():
		return {"plan": [], "created": [], "errors": [], "message": "No legacy Thai FDA tables."}

	rows = read_legacy_rows()
	children = read_children()
	existing_items = read_existing_items({clean(row.get("item")) for row in rows if clean(row.get("item"))})
	plan = plan_groups(
		rows,
		children,
		read_merged_names(),
		read_existing_numbers(),
		existing_items,
		on_date=today(),
		existing_lines=read_existing_lines(existing_items),
	)
	summary = format_plan(plan)
	print(summary)
	result = {
		"plan": plan,
		"created": [],
		"errors": [],
		"skipped_users": [],
		"summary": summary,
		"dry_run": bool(dry_run),
	}
	if dry_run:
		return result

	# Custom Fields must exist before Items are written (RA's own after_migrate runs later).
	from erpnext.regulatory_affairs.install import ensure_configuration

	ensure_configuration()
	# Keep the seeded settings and rules: a later per-group rollback must not undo them.
	frappe.db.commit()

	failed = 0
	for entry in plan:
		if entry["action"] != "create":
			continue
		try:
			insert_group(entry)
			frappe.db.commit()
			result["created"].append(entry["registration"]["registration_number"])
		except Exception:
			failed += 1
			frappe.db.rollback()
			message = f"{entry['legacy_names']}: {frappe.get_traceback(with_context=False)}"
			result["errors"].append(message)
			entry["conflicts"].append(f"not merged, error: {message.splitlines()[-1]}")

	failed_numbers = {
		entry["registration"]["registration_number"]
		for entry in plan
		if entry["action"] == "create"
		and entry["registration"]["registration_number"] not in result["created"]
	}
	result["warnings"] = overlap_warnings(plan, failed_numbers)
	for warning in result["warnings"]:
		print(warning)
	plan.extend(
		{"action": "warning", "legacy_names": [], "conflicts": [warning], "key": "warning"}
		for warning in result["warnings"]
	)

	# Recomputed on every run from the merged registrations, so a missed copy is repaired by a re-run.
	try:
		apply_item_categories(choose_item_categories(read_merged_lines()))
	except Exception:
		frappe.db.rollback()
		message = f"Item category copy failed: {frappe.get_traceback(with_context=False)}"
		result["errors"].append(message)
		frappe.log_error(title="Thai FDA to RA item category copy failed", message=message)
	frappe.db.commit()

	try:
		result["skipped_users"] = merge_settings() or []
		if result["skipped_users"]:
			print(
				"Legacy alert recipients skipped (user no longer exists): "
				+ ", ".join(result["skipped_users"])
			)
			entry_message = "Legacy alert recipients skipped (user no longer exists): " + ", ".join(
				result["skipped_users"]
			)
			plan.append(
				{"action": "settings", "legacy_names": [], "conflicts": [entry_message], "key": "settings"}
			)
	except Exception:
		frappe.db.rollback()
		message = f"Settings merge failed: {frappe.get_traceback(with_context=False)}"
		result["errors"].append(message)
		frappe.log_error(title="Thai FDA to RA settings merge failed", message=message)

	messages = [message for entry in plan for message in entry["conflicts"]] + result["errors"]
	if messages:
		frappe.log_error(title=CONFLICT_TITLE, message="\n".join(messages))
	frappe.db.commit()
	if failed:
		site = getattr(frappe.local, "site", None) or "<site>"
		print(
			"\n"
			+ "!" * 78
			+ f"\n{failed} legacy group(s) were NOT merged. Details: Error Log entries titled "
			f"'{CONFLICT_TITLE}' (and the output above). Legacy data is untouched. Fix the cause, then "
			f"re-run (already merged groups are skipped):\n"
			f"  bench --site {site} execute {EXECUTE_PATH}\n" + "!" * 78
		)
	result["failed"] = failed
	return result


def insert_group(entry: dict) -> None:
	registration = frappe._dict(entry["registration"])
	stamps = {key: registration.pop(key) for key in ("creation", "modified", "modified_by", "owner")}
	doc = frappe.get_doc(dict(registration))
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	doc.flags.allow_status_change = True
	doc.insert(ignore_permissions=True)
	restamp(PRODUCT_REGISTRATION, doc.name, stamps)

	for document in entry["documents"]:
		document = dict(document)
		stamps = {key: document.pop(key) for key in ("creation", "modified", "owner")}
		document["registration"] = doc.name
		child = frappe.get_doc(document)
		child.flags.ignore_validate = True
		child.flags.ignore_mandatory = True
		child.flags.ignore_links = True
		child.insert(ignore_permissions=True)
		restamp(REGULATORY_DOCUMENT, child.name, stamps)


def restamp(doctype: str, name: str, stamps: dict) -> None:
	"""Keep the legacy creation/modified/owner of a migrated record."""
	values = {key: value for key, value in stamps.items() if value}
	if values:
		frappe.db.set_value(doctype, name, values, update_modified=False)


def read_merged_lines() -> list[dict]:
	"""Item lines of the merged registrations (those listing legacy records) with their category."""
	line_table = "Regulatory Registered Product"
	if not all(frappe.db.table_exists(table) for table in (PRODUCT_REGISTRATION, line_table)):
		return []
	return (
		frappe.db.sql(
			f"""select registration.name, registration.product_category, registration.other_category,
				registration.official_status, registration.modified, line.item_code, line.line_status
			from `tab{PRODUCT_REGISTRATION}` registration
			inner join `tab{line_table}` line
				on line.parent = registration.name and line.parenttype = %(parenttype)s
			where ifnull(registration.legacy_thai_fda_records, '') != ''
				and ifnull(line.item_code, '') != ''""",
			{"parenttype": PRODUCT_REGISTRATION},
			as_dict=True,
		)
		or []
	)


def apply_item_categories(categories: dict[str, tuple[str, str | None]]) -> None:
	"""Copy the category to Items that have none, then refresh the stored coverage of every mapped Item."""
	if not categories:
		return
	for item, (category, other_category) in categories.items():
		frappe.db.sql(
			"update `tabItem` set regulatory_product_category = %s, regulatory_other_category = %s"
			" where name = %s and ifnull(regulatory_product_category, '') = ''",
			(category, other_category, item),
		)
	from erpnext.regulatory_affairs.coverage import refresh_item_coverage_for_items

	refresh_item_coverage_for_items(set(categories))


def merge_settings() -> list[str]:
	"""Copy the legacy Single settings, reminder days and recipients (only if the legacy Single exists)."""
	rows = frappe.db.sql(
		"select field, value from `tabSingles` where doctype = %s", (LEGACY_SETTINGS,), as_dict=True
	)
	if not rows:
		return []
	legacy = {row["field"]: row["value"] for row in rows}
	settings = frappe.get_single(SETTINGS)

	if not frappe.db.get_default(SETTINGS_MARKER):
		settings.update(map_settings(legacy))
		frappe.db.set_default(SETTINGS_MARKER, "1")

	existing_days = {cint(rule.days_before_expiry) for rule in settings.get("expiry_alert_rules") or []}
	for rule in plan_alert_rules(legacy.get("reminder_days"), existing_days):
		settings.append("expiry_alert_rules", rule)

	recipient_rows = []
	if frappe.db.table_exists(LEGACY_RECIPIENT):
		recipient_rows = frappe.db.sql(
			f"select `user`, enabled from `tab{LEGACY_RECIPIENT}` where parenttype = %s order by idx",
			(LEGACY_SETTINGS,),
			as_dict=True,
		)
	existing_users = {row.user for row in settings.get("recipients") or []}
	recipients, skipped = plan_recipients(
		recipient_rows, existing_users, lambda user: frappe.db.exists("User", user)
	)
	for recipient in recipients:
		settings.append("recipients", recipient)

	settings.flags.ignore_validate = True
	settings.save(ignore_permissions=True)
	return skipped


def execute():
	run(dry_run=False)
