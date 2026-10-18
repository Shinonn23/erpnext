"""Policy-driven regulatory validation and per-row regulatory snapshots.

Guarded purchase, stock and sales documents are checked row by row against the registration that covers
each Item on the document's coverage date. Rule evaluation is kept in small functions that only take
plain values so it can be tested without a database; the hook entry points at the top do the I/O.
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
	OUTBOUND_DOCTYPES,
	REGULATORY_OVERRIDE,
	REVOKED,
	SETTINGS,
	SUPERSEDED,
	SUSPENDED,
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
OVERRIDE_FIELDS = ("status", "transaction_doctype", "transaction_name", "item_code")

# Used when a setting is blank, e.g. before the Single has ever been saved.
POLICY_DEFAULTS = {
	"validate_on_purchase_order": 1,
	"validate_on_purchase_receipt": 1,
	"validate_on_stock_entry": 1,
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
	"""Report regulatory gaps on a Purchase Order or Sales Order without ever blocking the save."""
	_validate(doc, warn_only=True)


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

	override = get_override(doc, strict=False)
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

		override_name = override.name if override_covers_row(override, row) else None
		country = _get_manufacturer_country(mapping, countries)
		row.update(build_snapshot(mapping, coverage_date, override_name, country))


def _validate(doc, *, warn_only):
	# Consolidated invoices are built by the POS closing job from POS Invoices, which are not guarded.
	if not is_guarded_document(doc) or doc.get("is_return") or doc.get("is_consolidated"):
		return

	settings = get_settings()
	if not validation_enabled(doc.doctype, settings):
		return

	reported = []
	# A bad override is an error even when every row happens to be compliant. Order entry only warns.
	override, problem = load_override(doc)
	if problem:
		if not warn_only:
			frappe.throw(problem)
		reported.append(build_message(problem, WARNING))

	rows = [
		row
		for row in doc.get("items") or []
		if row.get("item_code") and not is_delivery_linked(doc.doctype, row)
	]
	in_scope = get_items_in_scope({row.get("item_code") for row in rows})
	if not in_scope:
		if reported:
			report_messages(reported, [], override, warn_only=True)
		return

	mappings = {}
	exempted = []
	for row in rows:
		item_code = row.get("item_code")
		if item_code not in in_scope:
			continue

		coverage_date = get_coverage_date(doc, row)
		key = (item_code, coverage_date)
		if key not in mappings:
			mappings[key] = _lookup_mapping(item_code, coverage_date, warn_only, reported)
		if mappings[key] is AMBIGUOUS:
			continue

		messages = evaluate_row(doc.doctype, row, mappings[key], coverage_date, settings)
		target = exempted if override_covers_row(override, row) else reported
		target.extend(messages)

	report_messages(reported, exempted, override, warn_only=warn_only)


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


def report_messages(reported, exempted, override, *, warn_only):
	"""Show exempted and warning messages; raise when a blocking message is not exempted."""
	if exempted:
		frappe.msgprint(
			_("Regulatory policy was overridden by {0}:").format(frappe.bold(override.name))
			+ "<br>"
			+ "<br>".join(exempted),
			title=_("Regulatory override applied"),
			indicator="orange",
		)

	if not reported:
		return

	body = "<br>".join(reported)
	if not warn_only and has_blocking_message(reported):
		frappe.throw(body, title=_("Regulatory requirement not met"))
	frappe.msgprint(body, title=_("Regulatory warning"), indicator="orange")


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
			filters={
				"name": ("in", item_codes),
				ITEM_CATEGORY_FIELD: ("is", "set"),
			},
			pluck="name",
		)
	)


def get_coverage_date(doc, row):
	"""The date a row has to be covered on: its expected date on orders, the posting date otherwise."""
	if doc.doctype == "Purchase Order":
		candidates = (row.get("schedule_date"), doc.get("schedule_date"), doc.get("transaction_date"))
	elif doc.doctype == "Sales Order":
		candidates = (row.get("delivery_date"), doc.get("delivery_date"), doc.get("transaction_date"))
	else:
		candidates = (doc.get("posting_date"), doc.get("transaction_date"))
	return getdate(next((value for value in candidates if value), None) or today())


# Override


def load_override(doc):
	"""(override, problem) for the override linked on a document; both None when none is linked.

	The override is read fresh from the database. A missing override, one that is not Approved or one
	bound to another document gives a problem text and no override.
	"""
	name = doc.get("regulatory_override_ref")
	if not name:
		return None, None

	override = frappe.db.get_value(REGULATORY_OVERRIDE, name, list(OVERRIDE_FIELDS), as_dict=True)
	problem = get_override_problem(name, override, doc.doctype, doc.name)
	if problem:
		return None, problem

	override.name = name
	return override, None


def get_override(doc, *, strict=True):
	"""The Approved override bound to this document, or None when none is set.

	With `strict` an unusable override raises; otherwise it is ignored.
	"""
	override, problem = load_override(doc)
	if problem and strict:
		frappe.throw(problem)
	return override


def get_override_problem(name, override, doctype, docname):
	"""Why an override cannot be used for a document, or None when it can."""
	if not override:
		return _("Regulatory Override {0} does not exist.").format(frappe.bold(name))
	if override.get("status") != "Approved":
		return _("Regulatory Override {0} is not approved.").format(frappe.bold(name))
	if override.get("transaction_doctype") != doctype or override.get("transaction_name") != docname:
		return _("Regulatory Override {0} does not belong to this transaction.").format(frappe.bold(name))
	return None


def override_covers_row(override, row):
	"""An override without an Item exempts every row; with an Item only that Item's rows."""
	if not override:
		return False
	return not override.get("item_code") or override.get("item_code") == row.get("item_code")


# Row evaluation


def evaluate_row(doctype, row, mapping, coverage_date, settings):
	"""Policy messages for one row, each prefixed `Block:` or `Warning:`; ignored checks add nothing."""
	item_code = row.get("item_code") or ""
	coverage_date = getdate(coverage_date)
	prefix = _("Row {0} ({1})").format(row.get("idx") or "?", frappe.bold(escape_html(item_code)))

	if not mapping:
		detail = _("{0} has no registration mapping effective on {1}.").format(prefix, coverage_date)
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
		detail = _("{0} has manufacturer {1}, but registration {2} lists {3}.").format(
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
		detail = _("{0} is mapped to suspended registration {1}.").format(prefix, registration)
		return detail, settings["suspended_registration_action"]
	if status == REVOKED:
		detail = _("{0} is mapped to revoked registration {1}.").format(prefix, registration)
		return detail, settings["revoked_registration_action"]
	if status == EXPIRED or (status in (ACTIVE, SUPERSEDED) and expiry_date and expiry_date < coverage_date):
		if expiry_date:
			detail = _("{0} is mapped to registration {1}, which expired on {2}.").format(
				prefix, registration, expiry_date
			)
		else:
			detail = _("{0} is mapped to expired registration {1}.").format(prefix, registration)
		return detail, get_expired_action(doctype, settings)

	detail = _("{0} is mapped to registration {1} with status {2}, which does not cover {3}.").format(
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


def build_snapshot(mapping, coverage_date, override_name=None, manufacturer_country=None):
	"""Values for the nine snapshot fields; only the coverage date and override survive a missing mapping."""
	snapshot = dict.fromkeys(SNAPSHOT_FIELDNAMES)
	snapshot["regulatory_coverage_date"] = coverage_date
	snapshot["regulatory_override"] = override_name or None
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
