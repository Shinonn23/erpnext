# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from datetime import date
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, date_diff, getdate, now_datetime, today

from erpnext.regulatory_affairs import coverage
from erpnext.regulatory_affairs.constants import (
	ACTIVE,
	CANCELLED,
	DRAFT,
	EXPIRED,
	EXPIRY_SPECIFIED,
	IN_FORCE_STATUSES,
	ITEM_CATEGORY_FIELD,
	MEDICAL_DEVICE,
	OTHER_CATEGORY,
	PRODUCT_CATEGORIES,
	PRODUCT_REGISTRATION,
	REGISTERED_PRODUCT,
	REGISTRATION_ACTION,
	REGULATORY_DOCUMENT,
	REVOKED,
	SUPERSEDED,
	SUSPENDED,
)
from erpnext.regulatory_affairs.permissions import approver_only_message, is_approver

DEVICE_ONLY_FIELDS = ("device_category", "risk_class", "grouping_type", "device_description", "intended_use")
STATUS_TRANSITIONS = {
	DRAFT: (ACTIVE,),
	ACTIVE: (SUSPENDED, REVOKED, CANCELLED, SUPERSEDED, EXPIRED),
	SUSPENDED: (ACTIVE, REVOKED, CANCELLED, SUPERSEDED),
	EXPIRED: (ACTIVE, CANCELLED, SUPERSEDED),
	REVOKED: (),
	CANCELLED: (),
	SUPERSEDED: (),
}
REASON_REQUIRED = (SUSPENDED, REVOKED, CANCELLED)
SYSTEM_ONLY_STATUSES = (EXPIRED, SUPERSEDED)


class RegulatoryProductRegistration(Document):
	def validate(self):
		self._normalize_number()
		self._validate_category()
		self._validate_status_guard()
		self._validate_validity_edit()
		self._validate_dates()
		self._validate_successor_links()
		self._validate_registered_product_rows()
		self._validate_effective_mapping_overlaps()

	def on_update(self):
		"""Refresh stored Item coverage after a mapping or status change."""
		previous = self.get_doc_before_save()
		rows = [
			*self.get("registered_products", []),
			*(previous.get("registered_products", []) if previous else []),
		]
		coverage.refresh_item_coverage_for_items({row.item_code for row in rows if row.item_code})

	def on_trash(self):
		"""Regulatory facts are history: only an unused Draft can be deleted."""
		if self.official_status != DRAFT:
			frappe.throw(
				_("Only a Draft registration can be deleted. Cancel it instead to keep the history.")
			)
		for doctype in (REGISTRATION_ACTION, REGULATORY_DOCUMENT):
			if frappe.db.exists(doctype, {"registration": self.name}):
				frappe.throw(_("Delete the {0} records of this registration first.").format(_(doctype)))

	def _normalize_number(self):
		registration_number = (self.registration_number or "").strip()
		if not registration_number:
			frappe.throw(_("Registration Number is required."))
		self.registration_number = registration_number
		self.registration_number_normalized = coverage.normalize_registration_number(registration_number)
		duplicate = frappe.db.get_value(
			PRODUCT_REGISTRATION,
			{
				"registration_number_normalized": self.registration_number_normalized,
				"name": ("!=", self.name),
			},
		)
		if duplicate:
			frappe.throw(
				_("Registration Number {0} duplicates {1} once spaces and punctuation are ignored.").format(
					frappe.bold(registration_number), frappe.bold(duplicate)
				)
			)

	def _validate_category(self):
		"""Every product category is registered here; only Medical Device carries the device fields."""
		self.product_category = self.product_category or MEDICAL_DEVICE
		if self.product_category not in PRODUCT_CATEGORIES:
			frappe.throw(_("Product Category {0} is not valid.").format(frappe.bold(self.product_category)))
		if self.product_category == OTHER_CATEGORY:
			self.other_category = (self.other_category or "").strip()
			if not self.other_category:
				frappe.throw(_("Enter the Other Category when the Product Category is Other."))
		else:
			self.other_category = None
		if self.product_category != MEDICAL_DEVICE:
			for fieldname in DEVICE_ONLY_FIELDS:
				self.set(fieldname, None)

	def _validate_status_guard(self):
		"""Official status changes only through `set_official_status` or an approved Registration Action."""
		allowed = self.flags.allow_status_change or frappe.flags.in_import
		previous = self.get_doc_before_save()
		if not previous:
			if self.official_status != DRAFT and not allowed:
				frappe.throw(_("A new registration starts as Draft."))
		elif previous.official_status != self.official_status and not allowed:
			frappe.throw(
				_("Use the status buttons to change the Official Status of a registration."),
				title=_("Status is controlled"),
			)

	def _validate_validity_edit(self):
		"""Issue and expiry decide coverage: once a registration is live only an Approver may edit them."""
		previous = self.get_doc_before_save()
		if not previous or previous.official_status == DRAFT:
			return
		if self.flags.allow_status_change or frappe.flags.in_import or is_approver():
			return
		for fieldname in ("issue_date", "expiry_date", "expiry_basis"):
			if str(previous.get(fieldname) or "") != str(self.get(fieldname) or ""):
				frappe.throw(
					approver_only_message(
						_("change the issue date, expiry date or expiry basis of a live registration")
					),
					frappe.PermissionError,
				)

	def after_rename(self, old_name, new_name, merge=False):
		"""Keep the stored identity fields in step with a renamed registration number."""
		frappe.db.set_value(
			PRODUCT_REGISTRATION,
			new_name,
			{
				"registration_number": new_name,
				"registration_number_normalized": coverage.normalize_registration_number(new_name),
			},
			update_modified=False,
		)

	def _validate_dates(self):
		if self.expiry_basis == EXPIRY_SPECIFIED:
			if not self.expiry_date:
				frappe.throw(
					_("Expiry Date is required when the expiry basis is {0}.").format(_(EXPIRY_SPECIFIED))
				)
			if self.issue_date and getdate(self.expiry_date) < getdate(self.issue_date):
				frappe.throw(_("Expiry Date must be on or after Issue Date."))
			self.days_to_expiry = date_diff(self.expiry_date, today())
			if self.official_status == ACTIVE and getdate(self.expiry_date) < getdate(today()):
				self.official_status = EXPIRED
		else:
			self.expiry_date = None
			self.days_to_expiry = None

	def _validate_successor_links(self):
		for fieldname in ("previous_registration", "superseded_by"):
			if self.get(fieldname) and self.get(fieldname) == self.name:
				frappe.throw(
					_("A registration cannot reference itself as {0}.").format(
						_(self.meta.get_label(fieldname))
					)
				)

	def _validate_registered_product_rows(self):
		rows = self.get("registered_products", [])
		item_names = {}
		item_codes = {row.item_code for row in rows if row.item_code}
		if item_codes:
			items = frappe.get_all(
				"Item",
				filters={"name": ("in", list(item_codes))},
				fields=["name", "item_name", ITEM_CATEGORY_FIELD],
			)
			item_names = {item.name: item for item in items}

		seen = set()
		uncategorized = set()
		for row in rows:
			row.line_no = row.idx
			key = (row.item_code, row.effective_from, row.effective_to)
			if row.item_code and key in seen:
				frappe.throw(
					_("Row {0}: duplicate Item mapping for the same effective dates.").format(row.idx)
				)
			seen.add(key)
			if row.line_status == "Active" and not (row.newcode or row.identifier):
				frappe.throw(
					_("Row {0}: an Active registered product needs a NEWCODE or an Identifier.").format(
						row.idx
					)
				)
			if (
				row.effective_from
				and row.effective_to
				and getdate(row.effective_to) < getdate(row.effective_from)
			):
				frappe.throw(_("Row {0}: Effective To must be on or after Effective From.").format(row.idx))
			if row.item_code:
				item = item_names.get(row.item_code)
				row.item_name_at_registration = item.item_name if item else ""
				if item and not item.get(ITEM_CATEGORY_FIELD):
					uncategorized.add(row.item_code)

		active_rows = [row for row in rows if row.item_code and row.line_status == "Active"]
		for index, row in enumerate(active_rows):
			for other in active_rows[index + 1 :]:
				if row.item_code == other.item_code and coverage.lines_overlap(row, other):
					frappe.throw(
						_("Rows {0} and {1}: Active mappings of Item {2} overlap.").format(
							row.idx, other.idx, frappe.bold(row.item_code)
						)
					)
		if uncategorized:
			frappe.msgprint(
				_(
					"Items {0} have no Product Category. Set it on the Item's Regulatory tab so transactions are validated against this registration."
				).format(", ".join(frappe.bold(item) for item in sorted(uncategorized))),
				indicator="orange",
				alert=True,
			)

	def _validate_effective_mapping_overlaps(self):
		if self.official_status not in IN_FORCE_STATUSES:
			return
		own = [
			row
			for row in self.get("registered_products", [])
			if row.item_code and row.line_status == "Active"
		]
		if not own:
			return
		conflict = find_mapping_conflict(
			own, self, get_other_lines({row.item_code for row in own}, self.name)
		)
		if conflict:
			item_code, registration = conflict
			frappe.throw(
				_(
					"Item {0} is already covered by registration {1} over overlapping dates. To replace that registration, set Previous Registration on this one before activating it."
				).format(frappe.bold(item_code), frappe.bold(registration)),
				title=_("Overlapping mapping"),
			)

	@frappe.whitelist(methods=["POST"])
	def set_official_status(self, status: str, reason: str | None = None):
		"""Move the registration through its lifecycle (Regulatory Approver only)."""
		self.check_permission("write")
		if status in SYSTEM_ONLY_STATUSES:
			frappe.throw(
				_(
					"{0} is set by the system: on expiry, or when a renewal under a new number is approved."
				).format(_(status))
			)
		if not is_approver():
			frappe.throw(
				approver_only_message(_("change the official status of a registration")),
				frappe.PermissionError,
			)
		if self.is_new():
			frappe.throw(_("Save the registration first."))
		change_status(self, status, reason=reason, enforce_activation=True)
		return self.official_status

	@frappe.whitelist()
	def get_activation_problems(self) -> list[str]:
		"""What still blocks activation; shown on a Draft registration."""
		self.check_permission("read")
		return get_activation_problems(self)


def find_mapping_conflict(
	own_lines: list[Any], registration: Any, other_lines: list[Any]
) -> tuple[str, str] | None:
	"""Return (item_code, other registration) when an own line covers the same dates as another registration's."""
	for line in own_lines:
		own_span = coverage.effective_span(line, registration)
		for other in other_lines:
			if other.item_code != line.item_code:
				continue
			if coverage.ranges_overlap(*own_span, *coverage.effective_span(other, other)):
				return line.item_code, other.parent
	return None


def get_other_lines(item_codes: set[str], exclude: str | None) -> list[frappe._dict]:
	"""Active lines of every other in-force-capable registration for the given Items, with their windows."""
	if not item_codes:
		return []
	product = frappe.qb.DocType(REGISTERED_PRODUCT)
	registration = frappe.qb.DocType(PRODUCT_REGISTRATION)
	query = (
		frappe.qb.from_(product)
		.inner_join(registration)
		.on(registration.name == product.parent)
		.select(
			product.parent,
			product.item_code,
			product.effective_from,
			product.effective_to,
			registration.issue_date,
			registration.expiry_date,
			registration.expiry_basis,
		)
		.where(product.parenttype == PRODUCT_REGISTRATION)
		.where(product.item_code.isin(list(item_codes)))
		.where(product.line_status == "Active")
		.where(registration.official_status.isin(list(IN_FORCE_STATUSES)))
	)
	if exclude:
		query = query.where(product.parent != exclude)
	return [frappe._dict(row) for row in query.run(as_dict=True)]


def close_predecessor_lines(registration: Document) -> None:
	"""End the predecessor's open product lines the day before this registration's issue date.

	Done when a successor is activated so that the two never cover an Item on the same day.
	"""
	if not (registration.previous_registration and registration.issue_date):
		return
	predecessor = frappe.get_doc(PRODUCT_REGISTRATION, registration.previous_registration)
	if predecessor.official_status in (DRAFT, CANCELLED, REVOKED):
		return
	cutoff = add_days(registration.issue_date, -1)
	if any(
		row.line_status == "Active" and (not row.effective_to or getdate(row.effective_to) > getdate(cutoff))
		for row in predecessor.get("registered_products", [])
	):
		change_status(predecessor, None, close_lines_after=cutoff)


def get_activation_problems(registration: Any) -> list[str]:
	"""Reasons a registration cannot be (re)activated yet; empty when it can."""
	problems = []
	if registration.expiry_basis == EXPIRY_SPECIFIED and registration.expiry_date:
		if getdate(registration.expiry_date) < getdate(today()):
			problems.append(_("The registration has expired; approve a renewal instead."))
	if not any(row.line_status == "Active" for row in registration.get("registered_products", [])):
		problems.append(_("Add at least one Active registered product."))
	certificates = frappe.get_all(
		REGULATORY_DOCUMENT,
		filters={
			"registration": registration.name,
			"document_type": "Certificate",
			"verification_status": "Verified",
		},
		fields=["expiry_date"],
	)
	if not any(not row.expiry_date or getdate(row.expiry_date) >= getdate(today()) for row in certificates):
		problems.append(_("Upload a Certificate document and have a Regulatory Approver verify it."))
	return problems


def change_status(
	registration: Document,
	status: str | None,
	*,
	reason: str | None = None,
	changes: dict[str, Any] | None = None,
	close_lines_after: date | str | None = None,
	enforce_activation: bool = False,
) -> None:
	"""Apply a status transition and/or field changes to a registration and save it.

	Callers are responsible for authorising the change. `close_lines_after` ends every Active product
	line at that date (used when a successor takes over).
	"""
	if status and status != registration.official_status:
		if status not in STATUS_TRANSITIONS.get(registration.official_status, ()):
			frappe.throw(
				_("A registration cannot move from {0} to {1}.").format(
					_(registration.official_status), _(status)
				)
			)
		if status in REASON_REQUIRED and not (reason or "").strip():
			frappe.throw(_("Enter a reason for setting the status to {0}.").format(_(status)))
		if status == ACTIVE and enforce_activation:
			problems = get_activation_problems(registration)
			if problems:
				frappe.throw("<br>".join(problems), title=_("Cannot activate"))
		if status == ACTIVE:
			close_predecessor_lines(registration)
		old_status = registration.official_status
		registration.official_status = status
		registration.status_reason = reason
		registration.status_changed_on = now_datetime()
		registration.status_changed_by = frappe.session.user
		registration.add_comment(
			"Info",
			_("Official Status changed from {0} to {1}. {2}").format(_(old_status), _(status), reason or ""),
		)
	for fieldname, value in (changes or {}).items():
		registration.set(fieldname, value)
	if close_lines_after:
		cutoff = getdate(close_lines_after)
		for row in registration.get("registered_products", []):
			if row.line_status == "Active" and (not row.effective_to or getdate(row.effective_to) > cutoff):
				row.effective_to = max(cutoff, getdate(row.effective_from)) if row.effective_from else cutoff
	registration.flags.allow_status_change = True
	registration.save(ignore_permissions=True)
