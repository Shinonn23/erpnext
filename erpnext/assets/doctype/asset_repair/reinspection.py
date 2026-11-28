"""Validate the source of a repair reinspection after a rejected borrowing return."""

import frappe
from frappe import _
from frappe.utils import cint


def return_repair_row(movement, row_name, *, required=True):
	if (
		cint(movement.docstatus) != 1
		or movement.doctype != "Asset Movement"
		or not movement.get("borrowing")
		or movement.get("borrowing_source_doctype") != "Borrowing"
		or movement.get("purpose") != "Receipt"
	):
		frappe.throw(_("Create repairs only from a submitted return Asset Movement of a borrowing."))
	if not row_name:
		frappe.throw(_("Select one asset row to open its document."))
	row = next((row for row in movement.get("assets") or [] if row.name == row_name), None)
	if row is None:
		frappe.throw(_("The selected row does not belong to this borrowing document."))
	if not row.get("item_code"):
		row.item_code = row.get("asset_item_code")
	inspection = (
		frappe.db.get_value(
			"Quality Inspection",
			row.quality_inspection,
			[
				"docstatus",
				"status",
				"reference_type",
				"reference_name",
				"child_row_reference",
				"item_code",
				"inspection_type",
				"company",
			],
			as_dict=True,
		)
		if row.get("quality_inspection")
		else None
	)
	if (
		not row.get("asset")
		or not inspection
		or cint(inspection.docstatus) != 1
		or inspection.status != "Rejected"
		or inspection.reference_type != movement.doctype
		or inspection.reference_name != movement.name
		or inspection.child_row_reference != row.name
		or inspection.item_code != row.item_code
		or inspection.inspection_type != "Incoming"
		or inspection.company != movement.company
	):
		if not required:
			return None
		frappe.throw(
			_("Row {0}: the rejected inspection must belong to this returned asset.").format(row.idx)
		)
	return row


def get_reinspection_source(repair_name, *, for_update=False):
	"""Read a completed repair and its matching rejected receipt row, locking the repair for writes."""
	repair = frappe.get_doc("Asset Repair", repair_name, for_update=for_update)
	repair.check_permission("read")
	if (
		cint(repair.docstatus) != 1
		or repair.get("repair_status") != "Completed"
		or not repair.get("asset")
		or not repair.get("return_movement")
	):
		frappe.throw(_("Reinspection requires a submitted completed repair of a returned asset."))

	# Access to the repair governs this lookup, which exposes only its matching row's Item.
	movement = frappe.get_doc("Asset Movement", repair.return_movement)
	row = return_repair_row(movement, repair.return_movement_item)
	if row.asset != repair.asset or movement.company != repair.company:
		frappe.throw(_("The repair must match its returned asset and company."))
	return repair, row


def hold_repaired_asset(repair, method=None):
	if not repair.get("asset") or not repair.get("return_movement"):
		return
	if repair.get("repair_status") != "Completed":
		return
	from erpnext.assets.inspection_hold import hold_again

	hold_again(repair.asset, repair.return_movement)


def _repair(name):
	return get_reinspection_source(name, for_update=True)


def validate_inspection(qi):
	repair, row = _repair(qi.reference_name)
	from erpnext.assets.inspection_hold import hold_reference, is_held

	if not is_held(repair.asset) or hold_reference(repair.asset) != repair.return_movement:
		frappe.throw(_("The asset is not awaiting inspection for this repair."))
	if (
		qi.item_code != row.item_code
		or qi.inspection_type != "Incoming"
		or qi.get("child_row_reference") != repair.name
		or (qi.get("company") and qi.company != repair.company)
	):
		frappe.throw(_("Reinspection must match the repaired asset, item and company."))
	if cint(qi.get("docstatus")) == 1 and not qi.get("readings"):
		frappe.throw(_("Add at least one inspection reading before submitting a repair reinspection."))
	if cint(qi.get("docstatus")) == 1 and qi.get("status") not in ("Accepted", "Rejected"):
		frappe.throw(_("Choose an accepted or rejected inspection result."))
	if (
		cint(qi.get("docstatus")) == 1
		and qi.get("status") == "Accepted"
		and any(reading.get("status") == "Rejected" for reading in qi.get("readings") or [])
	):
		frappe.throw(_("An accepted repair reinspection cannot contain a rejected reading."))
	previous = qi.get_doc_before_save()
	if previous and any(
		(qi.get(field) or "") != (previous.get(field) or "")
		for field in (
			"reference_type",
			"reference_name",
			"child_row_reference",
			"item_code",
			"inspection_type",
			"company",
		)
	):
		frappe.throw(_("The reinspection source cannot be changed."))
	other = frappe.db.sql(
		"""select name from `tabQuality Inspection`
		where reference_type='Asset Repair' and reference_name=%s and name!=%s
		and (docstatus=0 or (docstatus=1 and status!='Rejected')) limit 1 for update""",
		(repair.name, qi.get("name") or ""),
	)
	if other:
		frappe.throw(_("This repair already has reinspection {0}.").format(other[0][0]))
	qi.company = repair.company


def may_inspect_repair(qi):
	guards = frappe.get_hooks("repair_reinspection_grants")
	if guards:
		return any(frappe.get_attr(guard)(doc=qi) for guard in guards)
	from erpnext.stock.quarantine.quarantine_inspection import _member
	from erpnext.stock.quarantine.quarantine_release import item_team

	return _member(item_team(qi.item_code), qi)


@frappe.whitelist(methods=["POST"])
def make_inspection(repair_name):
	repair, row = _repair(repair_name)
	frappe.has_permission("Quality Inspection", "create", throw=True)
	qi = frappe.new_doc("Quality Inspection")
	qi.update(
		{
			"reference_type": "Asset Repair",
			"reference_name": repair.name,
			"child_row_reference": repair.name,
			"inspection_type": "Incoming",
			"item_code": row.item_code,
			"company": repair.company,
			"sample_size": 1,
			"inspected_by": frappe.session.user,
		}
	)
	if not may_inspect_repair(qi):
		frappe.throw(_("Only the assigned inspection team can inspect this repair."), frappe.PermissionError)
	validate_inspection(qi)
	return qi.as_dict()


def settle_inspection(qi):
	if qi.get("reference_type") != "Asset Repair":
		return
	repair, _ = _repair(qi.reference_name)
	from erpnext.assets.inspection_hold import hold_again, release_hold

	if qi.status == "Accepted":
		release_hold(repair.asset, repair.return_movement)
		frappe.get_doc("Asset", repair.asset).set_status()
		from erpnext.borrowing.demo_reservations import _notify_ready_asset

		_notify_ready_asset(repair.asset)
	else:
		hold_again(repair.asset, repair.return_movement)
		frappe.get_doc("Asset", repair.asset).set_status("Out of Order")


def cancel_inspection(qi):
	if qi.get("reference_type") != "Asset Repair" or qi.get("status") != "Accepted":
		return
	repair, _ = _repair(qi.reference_name)
	from erpnext.borrowing.doctype.borrowing.borrowing_core import asset_has_unreturned_borrowing

	if asset_has_unreturned_borrowing(repair.asset):
		frappe.throw(
			_("The asset is on borrowing; receive it back before cancelling its accepted inspection.")
		)
	from erpnext.assets.inspection_hold import hold_again

	hold_again(repair.asset, repair.return_movement)


def validate_repair_cancel(repair, method=None):
	if not repair.get("return_movement"):
		return
	inspection = frappe.db.get_value(
		"Quality Inspection",
		{
			"reference_type": "Asset Repair",
			"reference_name": repair.name,
			"docstatus": ["!=", 2],
		},
		"name",
		for_update=True,
	)
	if inspection:
		frappe.throw(_("Cancel or delete reinspection {0} before cancelling this repair.").format(inspection))


class RepairInspectionMixin:
	def validate_inspection_required(self):
		if self.get("reference_type") == "Asset Repair":
			return validate_inspection(self)
		return super().validate_inspection_required()

	def on_submit(self):
		result = super().on_submit()
		settle_inspection(self)
		return result

	def on_cancel(self):
		result = super().on_cancel()
		cancel_inspection(self)
		return result
