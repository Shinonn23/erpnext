"""Hold a returned borrowing asset until its required receipt inspection is complete."""

import frappe
from frappe import _
from frappe.utils import cint

AWAITING = "awaiting_inspection"
REFERENCE = "inspection_hold_reference"


def has_fields():
	return frappe.get_meta("Asset").has_field(AWAITING)


def is_held(asset):
	"""Whether the Asset (a document, or a name) waits for the inspection of its return. A document that has no such
	field (a site without them) is not held."""
	if isinstance(asset, str):
		return has_fields() and bool(cint(frappe.db.get_value("Asset", asset, AWAITING)))
	return bool(cint(asset.get(AWAITING)))


def hold_reference(asset):
	"""The return Asset Movement that holds the Asset (a document, or a name), else None."""
	if isinstance(asset, str):
		return frappe.db.get_value("Asset", asset, REFERENCE) if has_fields() else None
	return asset.get(REFERENCE)


def require_not_held(asset, label=None):
	"""Refuse to lend, reserve or convert an asset held for the inspection of its return, naming the return."""
	if not is_held(asset):
		return
	name = asset if isinstance(asset, str) else asset.name
	reference = hold_reference(asset)
	where = _(" (return {0})").format(reference) if reference else ""
	frappe.throw(
		_(
			"{0} is waiting for the inspection of its return{1}. The Quality Inspection team inspects it first."
		).format(label or name, where),
		title=_("Awaiting Inspection"),
	)


def _is_return_movement(doc):
	return bool(
		doc.get("doctype") == "Asset Movement"
		and doc.get("borrowing")
		and doc.get("borrowing_source_doctype") == "Borrowing"
		and doc.get("purpose") == "Receipt"
	)


def hold_received_assets(doc, method=None):
	"""Asset Movement on_submit: a submitted return movement of a borrowing starts the inspection hold."""
	if not _is_return_movement(doc) or doc.docstatus != 1:
		return
	hold_returned_assets(doc)
	for notifier in frappe.get_hooks("asset_return_inspection_notifiers"):
		frappe.get_attr(notifier)(doc)


def release_received_asset_holds(doc, method=None):
	"""Asset Movement on_cancel: nothing waits for the inspection of a return movement that is cancelled."""
	if not _is_return_movement(doc):
		return
	for row in doc.get("assets") or []:
		if row.get("asset"):
			release_hold(row.asset, doc.name)


def validate_receipt_cancellation(doc, method=None):
	"""An inspection belongs to one physical receipt; close it before reversing that receipt."""
	if not _is_return_movement(doc):
		return
	frappe.db.sql("select name from `tabAsset Movement` where name=%s for update", doc.name)
	inspection = frappe.db.get_value(
		"Quality Inspection",
		{"reference_type": "Asset Movement", "reference_name": doc.name, "docstatus": ["!=", 2]},
		"name",
		for_update=True,
	)
	if inspection:
		frappe.throw(
			_(
				"Cancel submitted Quality Inspection {0} or delete its draft before cancelling this receipt."
			).format(inspection)
		)


def _inspection_teams_enabled():
	guards = frappe.get_hooks("asset_return_inspection_enabled")
	if guards:
		return all(frappe.get_attr(guard)() for guard in guards)
	return frappe.get_meta("Item").has_field("incoming_qi_team")


def _incoming_team(item_code):
	providers = frappe.get_hooks("asset_return_inspection_team")
	if providers:
		teams = {frappe.get_attr(provider)(item_code, "Incoming") for provider in providers}
		return next(iter(teams)) if len(teams) == 1 else None
	return frappe.get_cached_value("Item", item_code, "incoming_qi_team")


def hold_returned_assets(doc, method=None):
	"""A physically received Asset is held until the required inspection of its return movement row."""
	if not _is_return_movement(doc) or doc.docstatus != 1 or not has_fields():
		return
	if not _inspection_teams_enabled():
		return
	for row in doc.get("assets") or []:
		item_code = row.get("item_code") or row.get("asset_item_code")
		if not row.get("asset") or not cint(
			frappe.get_cached_value("Item", item_code, "inspection_required_before_purchase")
		):
			continue
		inspected = row.get("quality_inspection") and frappe.db.get_value(
			"Quality Inspection", row.quality_inspection, ["docstatus", "status"], as_dict=True
		)
		if inspected and cint(inspected.docstatus) == 1:
			# already inspected: nothing is left to wait for
			if inspected.status == "Rejected":
				from erpnext.borrowing.loan_inspection import flag_rejected_asset_return

				flag_rejected_asset_return(doc, row, row.quality_inspection)
			continue
		if not _incoming_team(item_code):
			# no team can inspect it: holding it would hold it for ever (the Item form asks for the team)
			continue
		frappe.db.set_value("Asset", row.asset, {AWAITING: 1, REFERENCE: doc.name}, update_modified=False)


def release_hold(asset, return_name=None):
	"""End the hold (Accepted or Rejected): the Quality Inspection of the return row is submitted. A hold set by another
	return is left."""
	if not has_fields() or not is_held(asset):
		return
	if return_name and hold_reference(asset) not in (None, "", return_name):
		return
	frappe.db.set_value("Asset", asset, {AWAITING: 0, REFERENCE: None}, update_modified=False)


def hold_again(asset, return_name):
	"""A cancelled inspection of a returned asset: it waits for its inspection again."""
	if has_fields() and not is_held(asset):
		frappe.db.set_value("Asset", asset, {AWAITING: 1, REFERENCE: return_name}, update_modified=False)
