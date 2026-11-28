"""Link repair requests to stock issues and reverse returned parts at their issued value."""

import frappe
from frappe import _

REPAIR_TYPES = ("Asset Repair",)


def get_serial_numbers(row):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
	from erpnext.stock.serial_batch_bundle import get_serial_nos as bundle_serials

	return (
		bundle_serials(row.serial_and_batch_bundle)
		if row.get("serial_and_batch_bundle")
		else get_serial_nos(row.get("serial_no") or "")
	)


def get_return_allocations(rows):
	result = []
	for row in rows:
		batch = row.get("batch_no") or ""
		if row.get("serial_and_batch_bundle"):
			from erpnext.stock.doctype.batch.batch import get_batch_no

			batches = get_batch_no(row.serial_and_batch_bundle)
			if len(batches) > 1:
				frappe.throw(_("Use a separate repair return row for each batch."))
			batch = next(iter(batches), batch)
		result.append(
			{
				"issue_row": f"{row.get('repair_return_issue_row') or ''}|{batch}",
				"item_code": row.get("item_code"),
				"batch_no": batch,
				"qty": row.get("transfer_qty")
				or frappe.utils.flt(row.get("qty")) * frappe.utils.flt(row.get("conversion_factor") or 1),
				"serial_numbers": get_serial_numbers(row),
			}
		)
	return result


def _require_roles(company, roles, user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return
	actual = set(frappe.get_roles(user))
	if "System Manager" in actual:
		return
	if (
		not actual.intersection(roles)
		or not frappe.db.get_value("User", user, "enabled")
		or not frappe.db.exists("Employee", {"user_id": user, "company": company, "status": "Active"})
	):
		frappe.throw(
			_("An active employee of this company with the responsible operational role is required."),
			frappe.PermissionError,
		)


@frappe.whitelist(methods=["GET"])
def is_repair_issue(issue_name):
	if not frappe.db.has_column("Material Request", "repair_source"):
		return False
	issue = frappe.get_doc("Stock Entry", issue_name)
	issue.check_permission("read")
	if issue.docstatus != 1 or issue.purpose != "Material Issue" or not issue.items:
		return False
	for row in issue.items:
		request = (
			frappe.db.get_value(
				"Material Request",
				row.get("material_request"),
				["repair_source_type", "repair_source"],
				as_dict=True,
			)
			if row.get("material_request")
			else None
		)
		if not request or request.repair_source_type not in REPAIR_TYPES or not request.repair_source:
			return False
	return True


def _repair(doctype, name, lock=False, check_access=True):
	if doctype not in REPAIR_TYPES or not name:
		frappe.throw(_("Choose an Asset Repair."))
	doc = frappe.get_doc(doctype, name, for_update=lock)
	if check_access:
		doc.check_permission("read")
	if (
		doc.get("docstatus") == 2
		or doc.get("status") == "Completed"
		or doc.get("repair_status") in ("Completed", "Cancelled")
	):
		frappe.throw(_("The repair must still be open."))
	if doctype == "Asset Repair" and doc.get("docstatus") == 1 and doc.get("stock_items"):
		frappe.throw(_("This repair has already issued stock directly; do not request the same parts again."))
	return doc


@frappe.whitelist(methods=["POST"])
def make_parts_request(repair_type, repair_name):
	repair = _repair(repair_type, repair_name)
	frappe.has_permission("Material Request", "create", throw=True)
	roles = frappe.get_hooks("repair_parts_request_roles") or ["System Manager"]
	_require_roles(repair.company, roles)
	doc = frappe.new_doc("Material Request")
	doc.update(
		{
			"material_request_type": "Material Issue",
			"company": repair.company,
			"repair_source_type": repair_type,
			"repair_source": repair_name,
		}
	)
	return doc.as_dict()


def validate_parts_request(doc, method=None):
	previous = doc.get_doc_before_save()
	if (
		previous
		and previous.get("repair_source")
		and (
			previous.get("repair_source") != doc.get("repair_source")
			or previous.get("repair_source_type") != doc.get("repair_source_type")
		)
	):
		frappe.throw(_("The originating repair cannot be changed."))
	if not doc.get("repair_source") and not doc.get("repair_source_type"):
		return
	repair = _repair(
		doc.get("repair_source_type"),
		doc.get("repair_source"),
		lock=True,
		check_access=not bool(previous and previous.get("repair_source")),
	)
	if doc.get("material_request_type") != "Material Issue" or doc.get("company") != repair.company:
		frappe.throw(_("Repair parts require a Material Issue request in the repair company."))


def prevent_duplicate_asset_consumption(doc, method=None):
	if not doc.get("stock_items") or not doc.get("name"):
		return
	if not frappe.db.has_column("Material Request", "repair_source"):
		return
	frappe.db.get_value("Asset Repair", doc.name, "name", for_update=True)
	if frappe.db.exists(
		"Material Request",
		{"repair_source_type": "Asset Repair", "repair_source": doc.name, "docstatus": ["<", 2]},
	):
		frappe.throw(
			_(
				"This repair has a parts request. Issue its parts through that request; remove the direct Asset Repair stock items to avoid a duplicate stock issue."
			)
		)


def validate_return_allocations(issued_rows, returned_rows, requested_rows):
	"""Validate exact issued rows and identities; caller locks the source issue."""
	from frappe.utils import flt

	issued = {row["name"]: row for row in issued_rows}
	used = {}
	used_serials = set()
	for row in [*returned_rows, *requested_rows]:
		key = row.get("issue_row")
		original = issued.get(key)
		qty = flt(row.get("qty"))
		if not original or qty <= 0 or row.get("item_code") != original.get("item_code"):
			frappe.throw(
				_("Each returned item must match its original issued row and have a positive quantity.")
			)
		if (row.get("batch_no") or "") != (original.get("batch_no") or ""):
			frappe.throw(_("A returned batch must match its original issue."))
		original_serials = set(original.get("serial_numbers") or [])
		serials = row.get("serial_numbers") or []
		if original_serials:
			if (
				len(serials) != qty
				or len(set(serials)) != len(serials)
				or not set(serials) <= original_serials
			):
				frappe.throw(_("Returned serial numbers must match the issued units and quantity."))
			if used_serials.intersection(serials):
				frappe.throw(_("A serial number cannot be returned twice."))
			used_serials.update(serials)
		elif serials:
			frappe.throw(_("The issued row has no matching serial numbers."))
		used[key] = used.get(key, 0) + qty
		if used[key] > flt(original.get("qty")) + 1e-9:
			frappe.throw(_("Returned quantity cannot exceed the original issued quantity."))


def _issued_allocations(issue):
	originals = []
	for row in issue.items:
		if row.get("serial_and_batch_bundle"):
			entries = frappe.get_all(
				"Serial and Batch Entry",
				filters={"parent": row.serial_and_batch_bundle},
				fields=["batch_no", "serial_no", "qty"],
			)
			groups = {}
			for entry in entries:
				group = groups.setdefault(entry.batch_no or "", {"qty": 0, "serial_numbers": []})
				group["qty"] += abs(frappe.utils.flt(entry.qty))
				if entry.serial_no:
					group["serial_numbers"].append(entry.serial_no)
			for batch, group in groups.items():
				originals.append(
					{"name": f"{row.name}|{batch}", "item_code": row.item_code, "batch_no": batch, **group}
				)
		else:
			originals.append(
				{
					"name": f"{row.name}|{row.get('batch_no') or ''}",
					"item_code": row.item_code,
					"batch_no": row.get("batch_no") or "",
					"qty": row.get("transfer_qty") or row.qty,
					"serial_numbers": get_serial_numbers(row),
				}
			)
	return originals


def _issue_unit_rate(row, batch, serial_numbers):
	"""Use the actual value posted for the selected issue batch and serials."""
	from frappe.utils import flt

	if not row.get("serial_and_batch_bundle"):
		return flt(row.get("basic_rate"))
	entries = frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": row.serial_and_batch_bundle},
		fields=["qty", "batch_no", "serial_no", "stock_value_difference"],
	)
	selected = set(serial_numbers or [])
	matching = [
		entry
		for entry in entries
		if (entry.batch_no or "") == (batch or "") and (not selected or entry.serial_no in selected)
	]
	quantity = sum(abs(flt(entry.qty)) for entry in matching)
	if quantity <= 0 or (selected and {entry.serial_no for entry in matching} != selected):
		frappe.throw(_("The original issue has no matching posted valuation for these parts."))
	if (
		selected
		and len(
			{
				round(abs(flt(entry.stock_value_difference) / flt(entry.qty)), 6)
				for entry in matching
				if flt(entry.qty)
			}
		)
		> 1
	):
		frappe.throw(_("Return serial numbers with different costs on separate rows."))
	return sum(abs(flt(entry.stock_value_difference)) for entry in matching) / quantity


def validate_parts_return(doc, method=None):
	issue_name = doc.get("repair_return_issue")
	previous_doc = doc.get_doc_before_save()
	if (
		previous_doc
		and previous_doc.get("repair_return_issue")
		and previous_doc.get("repair_return_issue") != issue_name
	):
		frappe.throw(_("The original repair parts issue cannot be changed."))
	if not issue_name:
		return
	frappe.db.get_value("Stock Entry", issue_name, "name", for_update=True)
	issue = frappe.get_doc("Stock Entry", issue_name, for_update=True)
	issue.check_permission("read")
	if issue.docstatus != 1 or issue.purpose != "Material Issue" or issue.company != doc.company:
		frappe.throw(_("Return parts against a submitted Material Issue in the same company."))
	if doc.purpose != "Material Receipt":
		frappe.throw(_("Repair parts returns must be a Material Receipt."))
	if doc.get("additional_costs"):
		frappe.throw(_("Do not add costs to a repair parts return; it reverses the original issue."))
	for row in issue.items:
		request = (
			frappe.db.get_value(
				"Material Request",
				row.get("material_request"),
				["repair_source_type", "repair_source"],
				as_dict=True,
			)
			if row.get("material_request")
			else None
		)
		if not request or request.repair_source_type not in REPAIR_TYPES or not request.repair_source:
			frappe.throw(_("Every issue row must come from an approved repair parts request."))
	originals = _issued_allocations(issue)
	previous = []
	for name in frappe.get_all(
		"Stock Entry",
		filters={"repair_return_issue": issue_name, "docstatus": 1, "name": ["!=", doc.name or ""]},
		pluck="name",
	):
		previous.extend(get_return_allocations(frappe.get_doc("Stock Entry", name).items))
	validate_return_allocations(originals, previous, get_return_allocations(doc.items))
	issue_rows = {row.name: row for row in issue.items}
	for row, allocation in zip(doc.items, get_return_allocations(doc.items), strict=True):
		original = issue_rows[row.repair_return_issue_row]
		rate = _issue_unit_rate(original, allocation["batch_no"], allocation["serial_numbers"])
		row.basic_rate = rate
		row.set_basic_rate_manually = 1
		row.allow_zero_valuation_rate = int(rate == 0)
		row.expense_account = original.get("expense_account")
		row.cost_center = original.get("cost_center")


def validate_issue_cancel(doc, method=None):
	if not frappe.db.has_column("Stock Entry", "repair_return_issue"):
		return
	if frappe.db.exists("Stock Entry", {"repair_return_issue": doc.name, "docstatus": ["<", 2]}):
		frappe.throw(_("Cancel linked repair parts returns before cancelling the original issue."))


@frappe.whitelist(methods=["POST"])
def make_parts_return(issue_name):
	"""Open an unsaved receipt for the remaining issued repair parts."""
	from frappe.utils import flt

	frappe.has_permission("Stock Entry", "create", throw=True)
	issue = frappe.get_doc("Stock Entry", issue_name)
	issue.check_permission("read")
	if issue.docstatus != 1 or issue.purpose != "Material Issue":
		frappe.throw(_("Choose a submitted repair parts Material Issue."))
	for row in issue.items:
		request = (
			frappe.db.get_value(
				"Material Request",
				row.get("material_request"),
				["repair_source_type", "repair_source"],
				as_dict=True,
			)
			if row.get("material_request")
			else None
		)
		if not request or request.repair_source_type not in REPAIR_TYPES or not request.repair_source:
			frappe.throw(_("Every issue row must come from an approved repair parts request."))
	originals = _issued_allocations(issue)
	returned = []
	for name in frappe.get_all(
		"Stock Entry", filters={"repair_return_issue": issue.name, "docstatus": 1}, pluck="name"
	):
		returned.extend(get_return_allocations(frappe.get_doc("Stock Entry", name).items))
	validate_return_allocations(originals, returned, [])
	by_name = {row.name: row for row in issue.items}
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"company": issue.company,
			"purpose": "Material Receipt",
			"repair_return_issue": issue.name,
			"operation_receipt_reference": issue.name,
			"stock_entry_type": frappe.db.get_value(
				"Stock Entry Type", {"purpose": "Material Receipt", "is_standard": 1}, "name"
			),
		}
	)
	for original in originals:
		issue_row_name, batch = original["name"].split("|", 1)
		row = by_name[issue_row_name]
		used = [part for part in returned if part["issue_row"] == original["name"]]
		remaining = flt(original["qty"]) - sum(flt(part["qty"]) for part in used)
		if remaining <= 1e-9:
			continue
		returned_serials = {serial for part in used for serial in part["serial_numbers"]}
		serials = [serial for serial in original["serial_numbers"] if serial not in returned_serials]
		for selected, quantity in [([serial], 1) for serial in serials] if serials else [([], remaining)]:
			rate = _issue_unit_rate(row, batch, selected)
			entry.append(
				"items",
				{
					"item_code": row.item_code,
					"qty": quantity,
					"uom": row.stock_uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": 1,
					"t_warehouse": row.s_warehouse,
					"to_storage_location": row.get("storage_location"),
					"batch_no": batch or None,
					"serial_no": "\n".join(selected),
					"use_serial_batch_fields": 1,
					"basic_rate": rate,
					"set_basic_rate_manually": 1,
					"allow_zero_valuation_rate": int(not rate),
					"expense_account": row.get("expense_account"),
					"cost_center": row.get("cost_center"),
					"repair_return_issue_row": row.name,
				},
			)
	if not entry.items:
		frappe.throw(_("All parts issued on this document have already been returned."))
	return entry.as_dict()
