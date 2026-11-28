"""Confirm placement of a submitted Customer Borrowing."""

import frappe
from frappe import _

from .policy import actor_allowed, has_administrator_override


def is_superuser(user=None):
	return has_administrator_override(user, "borrowing_placement_superuser")


def validate_placement_confirmation(doc):
	engineer = (
		frappe.db.get_value(
			"Employee", doc.get("placement_confirmed_by"), ["company", "status", "user_id"], as_dict=True
		)
		if doc.get("placement_confirmed_by")
		else None
	)
	if (
		not engineer
		or engineer.company != doc.company
		or engineer.status != "Active"
		or not engineer.user_id
		or not actor_allowed("confirm_placement", doc, engineer.user_id)
	):
		frappe.throw(
			_("Placement location must be confirmed by an active Service Engineer in the same company.")
		)


def validate_placement(doc, method=None):
	"""Borrowing validate / on update after submit: a Placement Confirmation is confirmed by an active Service
	Engineer of the company (the rule the request used to carry); the confirmation file is optional."""
	if not doc.get("placement_confirmation") and not doc.get("placement_confirmed_by"):
		return
	previous = doc.get_doc_before_save()
	if previous and (previous.get("placement_confirmation"), previous.get("placement_confirmed_by")) == (
		doc.placement_confirmation,
		doc.get("placement_confirmed_by"),
	):
		return
	validate_placement_confirmation(doc)


def placement_pending(doc):
	"""A submitted Customer loan still in the customer's hands that has no Placement Confirmation yet."""
	return bool(
		doc.docstatus == 1
		and doc.borrower_type == "Customer"
		and doc.status in ("Active", "Partially Returned")
		and not doc.get("placement_confirmation")
		and not doc.get("placement_confirmed_by")
	)


def _employee_for_placement(doc, user):
	return frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active", "company": doc.company}, "name"
	)


@frappe.whitelist()
def can_confirm_placement(loan):
	if not loan:
		return False
	doc = frappe.get_doc("Borrowing", loan)
	user = frappe.session.user
	return bool(
		doc.has_permission("read")
		and placement_pending(doc)
		and actor_allowed("confirm_placement", doc, user)
		and (_employee_for_placement(doc, user) or is_superuser(user))
	)


@frappe.whitelist(methods=["POST"])
def confirm_placement(loan: str, file_url: str | None = None):
	"""The Service Engineer's Placement Confirmation, from the task or the loan's button: they are named as the one who
	confirmed it, and the file they uploaded, if any, is attached to the Borrowing (the file is optional, Siwat,
	10 Nov 2026: nothing may force an attachment). The user needs a Maintenance role and
	to be able to read the loan (the audience of the task); the loan's other fields are not touched. Returns the
	employee."""
	doc = frappe.get_doc("Borrowing", loan)
	user = frappe.session.user
	if not doc.has_permission("read") or not actor_allowed("confirm_placement", doc, user):
		frappe.throw(
			_("Only a Service Engineer who can open this borrowing confirms its placement."),
			frappe.PermissionError,
		)
	if not placement_pending(doc):
		frappe.throw(
			_(
				"This borrowing needs no Placement Confirmation: it is already confirmed or not with a customer."
			)
		)
	employee = _employee_for_placement(doc, user)
	if not employee and not is_superuser(user):  # Administrator has no Employee (Siwat, 6 Nov 2026)
		frappe.throw(_("Your user is not an active Employee of {0}.").format(doc.company))
	if file_url:
		file = frappe.db.get_value(
			"File",
			{"file_url": file_url, "owner": user},
			["name", "attached_to_doctype", "attached_to_name"],
			as_dict=True,
		)
		if not file or (
			file.attached_to_doctype
			and (file.attached_to_doctype, file.attached_to_name) != (doc.doctype, doc.name)
		):
			frappe.throw(_("The uploaded Placement Confirmation file was not found."))
		frappe.db.set_value(
			"File",
			file.name,
			{
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
				"attached_to_field": "placement_confirmation",
			},
			update_modified=False,
		)
		doc.placement_confirmation = file_url
	doc.placement_confirmed_by = employee
	# Frappe saves only the two allow-on-submit fields of a submitted loan; the user's rights on the loan are the
	# system's here (checked above), as for any step the system takes on a person's behalf
	doc.flags.ignore_permissions = True
	doc.save()
	return employee
