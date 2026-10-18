"""Item validation hook for the Thai FDA assessment status (the regulatory classification).

The status field ``custom_thai_fda_applicability`` decides whether an Item needs an FDA registration:
``Registration Required`` Items are validated against registrations on transactions, ``Not Required / Exempt``
Items carry the reason in the note field, ``Not Assessed`` Items are the classification worklist.

Gate ("tighten freely, loosen only via Approver"): anybody may leave the classification unchanged or set
an Item to Registration Required; every other change needs a Regulatory Approver.
"""

from typing import Any

import frappe
from frappe import _

from erpnext.regulatory_affairs import coverage
from erpnext.regulatory_affairs.constants import (
	ASSESSMENT_EXEMPT,
	ASSESSMENT_FIELD,
	ASSESSMENT_NOT_ASSESSED,
	ASSESSMENT_NOTE_FIELD,
	ASSESSMENT_REQUIRED,
	ASSESSMENT_STATUSES,
	ITEM_COVERAGE_FIELDS,
)
from erpnext.regulatory_affairs.permissions import approver_only_message, is_approver


def assessment_status(item: Any) -> str:
	"""The Item's assessment status; blank means Not Assessed."""
	return (item.get(ASSESSMENT_FIELD) or "").strip() or ASSESSMENT_NOT_ASSESSED


def assessment_note(item: Any) -> str:
	return (item.get(ASSESSMENT_NOTE_FIELD) or "").strip()


def normalize_classification(item: Any, previous: Any | None = None, lenient: bool = False) -> str:
	"""Make the classification coherent and return the (normalised) status.

	Blank status becomes Not Assessed, an exempt Item needs a note, the note is kept only while exempt and
	the stored coverage is cleared unless the Item requires a registration.

	An Item that was already exempt with the same blank note (legacy data) stays savable, and ``lenient``
	(system, import or patch saves) skips the note requirement; new transitions into Exempt still need one.
	"""
	status = assessment_status(item)
	if status not in ASSESSMENT_STATUSES:
		frappe.throw(_("Thai FDA Assessment Status {0} is not valid.").format(frappe.bold(status)))
	item.update({ASSESSMENT_FIELD: status})
	if status == ASSESSMENT_EXEMPT:
		legacy_blank = (
			previous is not None
			and assessment_status(previous) == ASSESSMENT_EXEMPT
			and not assessment_note(previous)
		)
		if not assessment_note(item) and not (lenient or legacy_blank):
			frappe.throw(_("Enter the reason why this Item does not require an FDA registration."))
	else:
		item.update({ASSESSMENT_NOTE_FIELD: None})
	if status != ASSESSMENT_REQUIRED:
		item.update(dict.fromkeys(ITEM_COVERAGE_FIELDS))
	return status


def classification_changed(doc: Any, previous: Any | None) -> bool:
	"""Whether the classification differs from the stored one in a way that needs a Regulatory Approver.

	A new Item compares against Not Assessed. Setting Registration Required is always allowed, leaving the
	status or (while exempt) the note unchanged is allowed, everything else needs an approver.
	"""
	new_status = assessment_status(doc)
	old_status = assessment_status(previous) if previous else ASSESSMENT_NOT_ASSESSED
	if new_status != old_status:
		return new_status != ASSESSMENT_REQUIRED
	if new_status == ASSESSMENT_EXEMPT:
		return assessment_note(doc) != (assessment_note(previous) if previous else "")
	return False


def validate(doc, method=None):
	"""Item `validate` hook: guard and normalise the classification and refresh derived coverage."""
	system_change = frappe.flags.in_import or frappe.flags.in_patch or frappe.flags.in_install
	previous = doc.get_doc_before_save()
	if not system_change and classification_changed(doc, previous) and not is_approver():
		frappe.throw(
			approver_only_message(
				_("set an Item to Not Assessed or Not Required / Exempt, or change its exemption reason")
			),
			frappe.PermissionError,
		)
	if normalize_classification(doc, previous, system_change) == ASSESSMENT_REQUIRED:
		coverage.refresh_item_coverage(doc)
