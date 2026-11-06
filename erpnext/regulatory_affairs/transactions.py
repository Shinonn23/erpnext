"""Policy-driven regulatory validation and per-row regulatory snapshots.

Guarded purchase, stock and sales documents are checked row by row against the registration that covers
each Item on the document's coverage date. Rule evaluation is kept in small functions that only take
plain values so it can be tested without a database; the hook entry points at the top do the I/O.

There is no way around a block (Siwat, 5 Nov 2026): Regulatory Affairs registers or renews the product first. The
block message says so in plain words and offers "Notify Regulatory Affairs" (`notify_regulatory_affairs`).
"""

import frappe
from frappe import _
from frappe.utils import escape_html, getdate, today

from erpnext.regulatory_affairs.constants import (
	ACTIVE,
	EXPIRED,
	EXPIRY_SPECIFIED,
	IN_FORCE_STATUSES,
	ITEM_CATEGORY_FIELD,
	NOT_REGULATED,
	OUTBOUND_DOCTYPES,
	REVOKED,
	SALE_DOCTYPES,
	SETTINGS,
	SUPERSEDED,
	SUSPENDED,
	TRANSACTION_DOCTYPES,
	TRANSACTION_ITEM_DOCTYPES,
)
from erpnext.regulatory_affairs.coverage import get_mapping_at_date
from erpnext.regulatory_affairs.custom_fields import SNAPSHOT_FIELDNAMES

BLOCK = "Block"
WARNING = "Warning"
IGNORE = "Ignore"
ALLOW_EXISTING_STOCK = "Allow Existing Compliant Stock"

BLOCK_PREFIX = "Block:"
WARNING_PREFIX = "Warning:"

STOCK_ENTRY_RECEIPT = "Material Receipt"
AMBIGUOUS = object()  # a mapping lookup that raised: master data is invalid for that Item and date
# Orders that warn on save and are checked with the full policy on submit (validate_transaction, before_submit).
SUBMIT_CHECKED_ORDERS = frozenset({"Quotation", "Sales Order"})
# Set on `doc.flags` by a caller that already ran `validate_transaction` on this save (an app blocking a Quotation
# when it is sent for approval), so the save-time warning does not report the same rows again.
POLICY_CHECKED_FLAG = "regulatory_policy_checked"

# Used when a setting is blank, e.g. before the Single has ever been saved.
POLICY_DEFAULTS = {
	"validate_on_purchase_order": 1,
	"validate_on_purchase_receipt": 1,
	"validate_on_stock_entry": 1,
	"validate_on_quotation": 1,
	"validate_on_sales_order": 1,
	"validate_on_delivery_note": 1,
	"validate_on_sales_invoice": 1,
	"missing_registration_action": BLOCK,
	"expired_registration_action": BLOCK,
	"suspended_registration_action": BLOCK,
	"revoked_registration_action": BLOCK,
	"missing_newcode_action": BLOCK,
	"manufacturer_mismatch_action": WARNING,
	"expired_registration_existing_stock_policy": WARNING,
}


def validate_warning_transaction(doc, method=None):
	"""Report regulatory gaps on a Quotation, Purchase Order or Sales Order without ever blocking the save.

	A Quotation or Sales Order being submitted is skipped here: its `before_submit` runs `validate_transaction`,
	which reports the same rows (and blocks), so the user does not see every message twice. So is a save on which
	the caller already ran `validate_transaction` (`POLICY_CHECKED_FLAG`).
	"""
	if doc.doctype in SUBMIT_CHECKED_ORDERS and doc.get("docstatus") == 1:
		return
	if (getattr(doc, "flags", None) or {}).get(POLICY_CHECKED_FLAG):
		return
	if _rows_unchanged_since_last_save(doc):
		return
	_validate(doc, warn_only=True)


def rows_signature(doc):
	"""What decides the regulatory result of a draft: each row's Item and coverage date."""
	return sorted(
		(row.get("item_code"), str(get_coverage_date(doc, row)))
		for row in doc.get("items") or []
		if row.get("item_code")
	)


def _rows_unchanged_since_last_save(doc):
	"""The user saw the warning for these rows on an earlier save: it is said once per change (Siwat, 5 Nov 2026)."""
	getter = getattr(doc, "get_doc_before_save", None)
	previous = getter() if getter else None
	if not previous or doc.get("__islocal"):
		return False
	return rows_signature(previous) == rows_signature(doc)


def validate_transaction(doc, method=None):
	"""Apply the configured block/warning policy before a stock-affecting document is submitted."""
	_validate(doc, warn_only=False)


def capture_transaction_snapshot(doc, method=None):
	"""Stamp each regulated row with the registration facts that applied on its coverage date."""
	if not is_guarded_document(doc):
		return

	rows = [row for row in doc.get("items") or [] if row.get("item_code")]
	in_scope = get_items_in_scope({row.get("item_code") for row in rows})
	if not in_scope:
		return

	mappings = {}
	countries = {}
	for row in rows:
		item_code = row.get("item_code")
		if item_code not in in_scope or inherit_delivery_note_snapshot(doc, row):
			continue

		coverage_date = get_coverage_date(doc, row)
		key = (item_code, coverage_date)
		if key not in mappings:
			mappings[key] = _resolve_mapping_for_snapshot(doc, item_code, coverage_date)
		mapping = mappings[key]

		country = _get_manufacturer_country(mapping, countries)
		row.update(build_snapshot(mapping, coverage_date, country))


def _validate(doc, *, warn_only):
	# Consolidated invoices are built by the POS closing job from POS Invoices, which are not guarded.
	if not is_guarded_document(doc) or doc.get("is_return") or doc.get("is_consolidated"):
		return

	settings = get_settings()
	if not validation_enabled(doc.doctype, settings):
		return

	reported = []
	problem_items = set()

	rows = [
		row
		for row in doc.get("items") or []
		if row.get("item_code") and not is_delivery_linked(doc.doctype, row)
	]
	codes = {row.get("item_code") for row in rows}
	unclassified = get_unclassified_items(codes) if doc.doctype in SALE_DOCTYPES else set()
	in_scope = get_items_in_scope(codes)
	if not in_scope and not unclassified:
		return

	mappings = {}
	for row in rows:
		item_code = row.get("item_code")
		if item_code in unclassified:
			reported.append(build_message(unclassified_text(row), BLOCK))
			problem_items.add(item_code)
			continue
		if item_code not in in_scope:
			continue

		coverage_date = get_coverage_date(doc, row)
		key = (item_code, coverage_date)
		if key not in mappings:
			mappings[key] = _lookup_mapping(item_code, coverage_date, warn_only, reported)
		if mappings[key] is AMBIGUOUS:
			continue

		messages = evaluate_row(doc.doctype, row, mappings[key], coverage_date, settings)
		if not messages:
			continue
		reported.extend(messages)
		problem_items.add(item_code)

	report_messages(reported, warn_only=warn_only, doc=doc, item_codes=sorted(problem_items))


def unclassified_text(row):
	item_code = frappe.bold(escape_html(row.get("item_code") or ""))
	return _(
		"Item {0} (row {1}) has not been classified by Regulatory Affairs yet, so it cannot be sold."
	).format(item_code, row.get("idx") or "?")


def _lookup_mapping(item_code, coverage_date, warn_only, reported):
	"""The mapping for a row. Submitting stops on an ambiguous mapping; order entry only reports it."""
	try:
		return get_mapping_at_date(item_code, coverage_date)
	except frappe.ValidationError as error:
		if not warn_only:
			raise
		frappe.clear_last_message()
		reported.append(build_message(str(error), WARNING))
		return AMBIGUOUS


def _is_administrator():
	"""Administrator does everything (Siwat, 6 Nov 2026, for his testing)."""
	try:
		return frappe.session.user == "Administrator"
	except RuntimeError:  # no session bound (an offline test)
		return False


def report_messages(reported, *, warn_only, doc=None, item_codes=()):
	"""Show warning messages; raise when a message blocks. The text is plain English: which Item, why, what to do."""
	if not reported:
		return

	body = "<br>".join(display_message(message) for message in reported)
	# Administrator does everything (Siwat, 6 Nov 2026, for his testing): he is told, never stopped.
	blocking = not warn_only and has_blocking_message(reported) and not _is_administrator()
	advice = what_to_do(doc, blocking=blocking, warn_only=warn_only)
	if advice:
		body += "<br><br>" + advice
	if blocking:
		frappe.throw(
			body,
			title=_("This cannot go on yet: an Item is not registered"),
			primary_action=notify_action(doc, item_codes),
		)
	frappe.msgprint(body, title=_("Regulatory warning"), indicator="orange")


def display_message(message):
	"""A policy message without its internal Block:/Warning: prefix, as a bullet."""
	for prefix in (BLOCK_PREFIX, WARNING_PREFIX):
		if message.startswith(prefix):
			message = message[len(prefix) :].strip()
			break
	return "&bull; " + message


def what_to_do(doc, *, blocking, warn_only):
	"""One short next step for the reader: Regulatory Affairs has to register or renew the product."""
	doctype = doc.doctype if doc is not None else None
	if blocking:
		return _(
			"What to do: Regulatory Affairs must classify or register the Item first (or renew its registration). "
			"Press Notify Regulatory Affairs to tell them. This document goes on once the Item is classified and covered."
		)
	if warn_only and doctype in SUBMIT_CHECKED_ORDERS:
		return _(
			"You can keep this draft, but it cannot be approved or submitted until Regulatory Affairs has "
			"registered the Item. Use Regulatory > Notify Regulatory Affairs on this document."
		)
	if warn_only and doctype == "Purchase Order":
		return _("The purchase is not blocked. Regulatory Affairs must register the Item before it is sold.")
	return None


def notify_action(doc, item_codes=()):
	"""The button of the block message: it tells Regulatory Affairs about the Items (and this document when saved)."""
	if doc is None or doc.doctype not in TRANSACTION_DOCTYPES:
		return None
	saved = bool(doc.get("name")) and not doc.get("__islocal")
	return {
		"label": _("Notify Regulatory Affairs"),
		"client_action": "erpnext.regulatory.notify_regulatory_affairs",
		"args": {
			"doctype": doc.doctype,
			"name": doc.name if saved else None,
			"item_codes": list(item_codes),
		},
	}


def blocked_rows(doc):
	"""[{idx, item_code, reason}] of the rows that block this document (plain English).

	The same evaluation as `validate_transaction`, returned instead of raised, for the Notify button.
	"""
	if not is_guarded_document(doc) or doc.get("is_return") or doc.get("is_consolidated"):
		return []
	settings = get_settings()
	if not validation_enabled(doc.doctype, settings):
		return []
	rows = [
		row
		for row in doc.get("items") or []
		if row.get("item_code") and not is_delivery_linked(doc.doctype, row)
	]
	codes = {row.get("item_code") for row in rows}
	unclassified = get_unclassified_items(codes) if doc.doctype in SALE_DOCTYPES else set()
	in_scope = get_items_in_scope(codes)
	found = []
	for row in rows:
		item_code = row.get("item_code")
		if item_code in unclassified:
			reason = display_message(unclassified_text(row))[len("&bull; ") :]
			found.append({"idx": row.get("idx"), "item_code": item_code, "reason": reason})
			continue
		if item_code not in in_scope:
			continue
		coverage_date = get_coverage_date(doc, row)
		try:
			mapping = get_mapping_at_date(item_code, coverage_date)
		except frappe.ValidationError:
			frappe.clear_last_message()
			continue
		messages = evaluate_row(doc.doctype, row, mapping, coverage_date, settings)
		blocking = [m for m in messages if m.startswith(BLOCK_PREFIX)]
		if blocking:
			reason = "; ".join(display_message(m)[len("&bull; ") :] for m in blocking)
			found.append({"idx": row.get("idx"), "item_code": item_code, "reason": reason})
	return found


@frappe.whitelist()
def get_blocked_items(doctype: str, name: str) -> list[dict]:
	"""The Items that keep a saved draft from going on, for the form button (empty: show no button)."""
	if doctype not in TRANSACTION_DOCTYPES:
		return []
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	if doc.docstatus != 0:
		return []
	return blocked_rows(doc)


@frappe.whitelist()
def notify_regulatory_affairs(
	doctype: str, name: str | None = None, item_codes: str | list | None = None
) -> dict:
	"""Tell the Regulatory Operators that a document waits for the classification or registration of its Items.

	One task for each Item (the app's handlers in the hook `regulatory_notify_handlers` make it, for the role, never
	for named users, and do not repeat it while it is open). A document that is not saved yet is named by its
	`item_codes`. Returns {"items": [...]} of the Items notified.
	"""
	if doctype not in TRANSACTION_DOCTYPES:
		frappe.throw(_("{0} is not checked for registration.").format(_(doctype)))
	if name:
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("read")
		items = blocked_rows(doc)
	else:
		codes = frappe.parse_json(item_codes) if isinstance(item_codes, str) else item_codes
		doc = frappe._dict(doctype=doctype, name=_("an unsaved draft"))
		items = [
			{"idx": None, "item_code": code, "reason": _("Not classified or registered yet.")}
			for code in sorted(set(codes or []))
			if frappe.db.exists("Item", code)
		]
	if not items:
		frappe.msgprint(
			_("Nothing blocks this document: every Item is classified and registered."), alert=True
		)
		return {"items": []}
	for handler in frappe.get_hooks("regulatory_notify_handlers"):
		frappe.get_attr(handler)(doc, items)
	frappe.msgprint(_("Regulatory Affairs has been told."), indicator="green", alert=True)
	return {"items": sorted({row["item_code"] for row in items})}


# Item list of the sales forms


def sellable_items_condition(on_date=None):
	"""SQL condition on `tabItem`: classified by RA and (Not Regulated or covered by a registration in force on the date).

	The registration test is the one of `coverage.get_mapping_at_date`/`is_in_force`, written as SQL for the Item link
	list. A date is a plain `YYYY-MM-DD` value (escaped here); without one it is today."""
	day = frappe.db.escape(str(getdate(on_date or today())))
	statuses = ", ".join(frappe.db.escape(status) for status in IN_FORCE_STATUSES)
	return f"""and (tabItem.{ITEM_CATEGORY_FIELD} = {frappe.db.escape(NOT_REGULATED)}
		or (ifnull(tabItem.{ITEM_CATEGORY_FIELD}, '') != '' and exists (
			select 1 from `tabRegulatory Registered Product` rp
			inner join `tabRegulatory Product Registration` reg on reg.name = rp.parent
			where rp.parenttype = 'Regulatory Product Registration'
				and rp.item_code = tabItem.name
				and rp.line_status = 'Active'
				and reg.official_status in ({statuses})
				and (rp.effective_from is null or rp.effective_from <= {day})
				and (rp.effective_to is null or rp.effective_to >= {day})
				and (reg.issue_date is null or reg.issue_date <= {day})
				and (ifnull(reg.expiry_basis, '') != 'Expiry Date Specified' or reg.expiry_date is null
					or reg.expiry_date >= {day})
		)))"""


# Settings and scope


def get_settings():
	"""The policy settings as a dict, with documented defaults for blank values."""
	return normalize_settings(frappe.get_cached_doc(SETTINGS))


def normalize_settings(source):
	"""Pick the policy fields from a settings document or dict and fill blanks with the defaults."""
	settings = frappe._dict()
	for fieldname, default in POLICY_DEFAULTS.items():
		value = source.get(fieldname)
		settings[fieldname] = default if value is None or value == "" else value
	return settings


def validation_enabled(doctype, settings):
	"""Whether the `validate_on_*` switch of a document type is on."""
	fieldname = f"validate_on_{frappe.scrub(doctype)}"
	return bool(settings.get(fieldname, POLICY_DEFAULTS.get(fieldname)))


def is_guarded_document(doc):
	"""Stock Entries are guarded (validated and snapshotted) only when they receive stock."""
	if doc.doctype == "Stock Entry":
		return doc.get("purpose") == STOCK_ENTRY_RECEIPT
	return True


def is_delivery_linked(doctype, row):
	"""Invoice rows of goods that a Delivery Note already delivered are not validated again."""
	return doctype == "Sales Invoice" and bool(row.get("dn_detail") or row.get("delivery_note"))


def get_items_in_scope(item_codes):
	"""Item codes that have a Product Category (regulated Items), from one query for the document."""
	item_codes = sorted({item_code for item_code in item_codes if item_code})
	if not item_codes:
		return set()
	return set(
		frappe.get_all(
			"Item",
			filters=[
				["Item", "name", "in", item_codes],
				["Item", ITEM_CATEGORY_FIELD, "is", "set"],
				["Item", ITEM_CATEGORY_FIELD, "!=", NOT_REGULATED],
			],
			pluck="name",
		)
	)


def get_unclassified_items(item_codes):
	"""Item codes RA has not classified yet (no Product Category and not Not Regulated): they are not sold."""
	item_codes = sorted({item_code for item_code in item_codes if item_code})
	if not item_codes:
		return set()
	classified = set(
		frappe.get_all(
			"Item",
			filters=[["Item", "name", "in", item_codes], ["Item", ITEM_CATEGORY_FIELD, "is", "set"]],
			pluck="name",
		)
	)
	return {code for code in item_codes if code not in classified}


def get_coverage_date(doc, row):
	"""The date a row has to be covered on: its expected date on orders, the posting date otherwise."""
	if doc.doctype == "Purchase Order":
		candidates = (row.get("schedule_date"), doc.get("schedule_date"), doc.get("transaction_date"))
	elif doc.doctype == "Sales Order":
		candidates = (row.get("delivery_date"), doc.get("delivery_date"), doc.get("transaction_date"))
	else:
		candidates = (doc.get("posting_date"), doc.get("transaction_date"))
	return getdate(next((value for value in candidates if value), None) or today())


# Row evaluation


def evaluate_row(doctype, row, mapping, coverage_date, settings):
	"""Policy messages for one row, each prefixed `Block:` or `Warning:`; ignored checks add nothing."""
	item_code = row.get("item_code") or ""
	coverage_date = getdate(coverage_date)
	prefix = _("Item {1} (row {0})").format(row.get("idx") or "?", frappe.bold(escape_html(item_code)))

	if not mapping:
		detail = _("{0} has no FDA registration in force on {1}.").format(prefix, coverage_date)
		return _compact([build_message(detail, settings["missing_registration_action"])])

	registration = frappe.bold(escape_html(mapping.get("registration") or "-"))
	messages = []

	# NEWCODE exists only for medical devices; other categories (and migrated lines) carry an Identifier.
	if not mapping.get("newcode") and not mapping.get("identifier"):
		detail = _("{0} has no NEWCODE or identifier in registration {1}.").format(prefix, registration)
		messages.append(build_message(detail, settings["missing_newcode_action"]))

	messages.append(
		build_message(*get_status_issue(doctype, mapping, coverage_date, settings, prefix, registration))
	)

	row_manufacturer = row.get("manufacturer")
	mapped_manufacturer = mapping.get("physical_manufacturer")
	if row_manufacturer and mapped_manufacturer and row_manufacturer != mapped_manufacturer:
		detail = _("{0} comes from manufacturer {1}, but registration {2} lists {3}.").format(
			prefix,
			frappe.bold(escape_html(row_manufacturer)),
			registration,
			frappe.bold(escape_html(mapped_manufacturer)),
		)
		messages.append(build_message(detail, settings["manufacturer_mismatch_action"]))

	return _compact(messages)


def get_status_issue(doctype, mapping, coverage_date, settings, prefix, registration):
	"""(detail, action) for the registration status on the coverage date, or (None, IGNORE) when covered.

	At most one issue is produced, so an expiry is never reported twice.
	"""
	status = mapping.get("official_status")
	expiry_date = get_stated_expiry(mapping)

	if is_covering(mapping, coverage_date):
		return None, IGNORE
	if status == SUSPENDED:
		detail = _("{0}: registration {1} is suspended.").format(prefix, registration)
		return detail, settings["suspended_registration_action"]
	if status == REVOKED:
		detail = _("{0}: registration {1} was revoked.").format(prefix, registration)
		return detail, settings["revoked_registration_action"]
	if status == EXPIRED or (status in (ACTIVE, SUPERSEDED) and expiry_date and expiry_date < coverage_date):
		if expiry_date:
			detail = _("{0}: registration {1} expired on {2}.").format(prefix, registration, expiry_date)
		else:
			detail = _("{0}: registration {1} has expired.").format(prefix, registration)
		return detail, get_expired_action(doctype, settings)

	detail = _("{0}: registration {1} is {2} and does not cover {3}.").format(
		prefix, registration, frappe.bold(escape_html(status or "-")), coverage_date
	)
	return detail, settings["missing_registration_action"]


def is_covering(mapping, coverage_date):
	"""Whether the mapped registration is in force with a status that can carry coverage."""
	if mapping.get("official_status") not in IN_FORCE_STATUSES:
		return False

	in_force = mapping.get("in_force")
	if in_force is None:
		# A hand-built mapping: derive what get_mapping_at_date would have said.
		expiry_date = get_stated_expiry(mapping)
		in_force = expiry_date >= coverage_date if expiry_date else mapping.get("official_status") != EXPIRED
	return bool(in_force)


def get_stated_expiry(mapping):
	"""The registration's expiry date, only when it states one (basis Expiry Date Specified)."""
	expiry_date = mapping.get("expiry_date") if mapping else None
	if not expiry_date or mapping.get("expiry_basis") not in (None, "", EXPIRY_SPECIFIED):
		return None
	return getdate(expiry_date)


def get_expired_action(doctype, settings):
	"""Action for an expired registration; outbound documents follow the existing-stock policy."""
	if doctype not in OUTBOUND_DOCTYPES:
		return settings["expired_registration_action"]

	policy = settings["expired_registration_existing_stock_policy"]
	return IGNORE if policy == ALLOW_EXISTING_STOCK else policy


def build_message(detail, action):
	"""Prefix a detail with the severity of its action; None for an ignored check."""
	if action == IGNORE or not detail:
		return None
	return f"{WARNING_PREFIX if action == WARNING else BLOCK_PREFIX} {detail}"


def has_blocking_message(messages):
	return any(message.startswith(BLOCK_PREFIX) for message in messages)


def _compact(messages):
	return [message for message in messages if message]


# Snapshot


def build_snapshot(mapping, coverage_date, manufacturer_country=None):
	"""Values for the eight snapshot fields; only the coverage date survives a missing mapping."""
	snapshot = dict.fromkeys(SNAPSHOT_FIELDNAMES)
	snapshot["regulatory_coverage_date"] = coverage_date
	if mapping:
		snapshot.update(
			regulatory_registration=mapping.get("registration"),
			regulatory_newcode=mapping.get("newcode"),
			regulatory_identifier=mapping.get("identifier"),
			regulatory_manufacturer=mapping.get("physical_manufacturer"),
			regulatory_manufacturer_country=manufacturer_country,
			regulatory_registration_status=mapping.get("official_status"),
			regulatory_registration_expiry=get_stated_expiry(mapping),
		)
	return snapshot


def inherit_delivery_note_snapshot(doc, row):
	"""Copy the snapshot of the Delivery Note row an invoice row was created from; True when copied."""
	if doc.doctype != "Sales Invoice" or not row.get("dn_detail"):
		return False

	values = frappe.db.get_value(
		"Delivery Note Item", row.get("dn_detail"), list(SNAPSHOT_FIELDNAMES), as_dict=True
	)
	# A delivery that was never snapshotted has nothing to inherit: resolve the invoice row itself.
	if not values or not values.get("regulatory_coverage_date"):
		return False

	row.update({fieldname: values.get(fieldname) for fieldname in SNAPSHOT_FIELDNAMES})
	return True


def _resolve_mapping_for_snapshot(doc, item_code, coverage_date):
	"""The mapping for a snapshot; an ambiguous one is logged and leaves the snapshot blank."""
	try:
		return get_mapping_at_date(item_code, coverage_date)
	except frappe.ValidationError:
		frappe.log_error(
			title=f"Ambiguous regulatory registration for {item_code}",
			message=frappe.get_traceback(),
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
		return None


def _get_manufacturer_country(mapping, cache):
	"""Country of the mapped manufacturer, falling back to the country on the registration line."""
	if not mapping:
		return None

	manufacturer = mapping.get("physical_manufacturer")
	if manufacturer and manufacturer not in cache:
		cache[manufacturer] = frappe.db.get_value("Manufacturer", manufacturer, "country")
	return (cache.get(manufacturer) if manufacturer else None) or mapping.get("manufacturer_country")
