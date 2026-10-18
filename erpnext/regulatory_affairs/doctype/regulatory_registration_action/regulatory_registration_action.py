# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, now_datetime

from erpnext.regulatory_affairs.constants import (
	ACTIVE,
	CANCELLED,
	CLOSED_STAGES,
	DRAFT,
	EXPIRED,
	EXPIRY_SPECIFIED,
	PRODUCT_REGISTRATION,
	REGISTRATION_ACTION,
	SETTINGS,
	SUPERSEDED,
	SUSPENDED,
)
from erpnext.regulatory_affairs.doctype.regulatory_product_registration.regulatory_product_registration import (
	change_status,
)
from erpnext.regulatory_affairs.permissions import approver_only_message, is_approver

STAGE_RENEWAL_STATUS = {
	"Preparing": "Preparing",
	"Internal Review": "Internal Review",
	"Submitted": "Submitted",
	"FDA Query": "FDA Query",
}
IN_PROGRESS_RENEWAL = tuple(STAGE_RENEWAL_STATUS.values())
STAGES_REQUIRING_SUBMISSION = ("Submitted", "FDA Query", "Approved")
LIVE_REGISTRATION_STATUSES = (ACTIVE, SUSPENDED, EXPIRED)
OUTCOME_FIELDS = ("current_stage", "new_expiry_date", "resulting_registration", "approval_date")


class RegulatoryRegistrationAction(Document):
	def validate(self):
		self._set_default_assignee()
		self._validate_registration_state()
		self._validate_closed_action_is_final()
		self._validate_stage()
		self._validate_outcome()
		self._validate_single_open_renewal()

	def on_update(self):
		self._sync_registration()

	def _previous_stage(self):
		previous = self.get_doc_before_save()
		return previous.current_stage if previous else None

	def _set_default_assignee(self):
		if self.assigned_to:
			return
		self.assigned_to = frappe.db.get_value(
			PRODUCT_REGISTRATION, self.registration, "responsible_user"
		) or frappe.db.get_single_value(SETTINGS, "default_regulatory_owner")

	def _validate_registration_state(self):
		status = frappe.db.get_value(PRODUCT_REGISTRATION, self.registration, "official_status")
		if self.action_type == "New Registration":
			allowed = (DRAFT, ACTIVE)
		else:
			allowed = LIVE_REGISTRATION_STATUSES
		if self.is_new() and status not in allowed:
			frappe.throw(
				_("A {0} action cannot be raised for a registration whose status is {1}.").format(
					_(self.action_type), _(status)
				)
			)

	def _validate_closed_action_is_final(self):
		previous = self.get_doc_before_save()
		if not previous or previous.current_stage not in CLOSED_STAGES:
			return
		if any(str(previous.get(field) or "") != str(self.get(field) or "") for field in OUTCOME_FIELDS):
			frappe.throw(_("A closed action cannot be reopened or have its outcome changed."))

	def _validate_stage(self):
		stage = self.current_stage
		if stage in STAGES_REQUIRING_SUBMISSION and not self.submitted_date:
			frappe.throw(_("Enter the Submitted Date for the {0} stage.").format(_(stage)))
		if stage == "FDA Query" and not self.response_due_date:
			frappe.throw(_("FDA Query stage requires a Response Due Date."))
		if (
			self.fda_query_date
			and self.response_due_date
			and getdate(self.response_due_date) < getdate(self.fda_query_date)
		):
			frappe.throw(_("Response Due Date cannot be before the FDA Query Date."))
		if self.submitted_date and getdate(self.submitted_date) > getdate():
			frappe.throw(_("Submitted Date cannot be in the future."))
		if stage == "Approved":
			if not self.approval_date:
				frappe.throw(_("Approved stage requires an Approval Date."))
			if self._previous_stage() != "Approved" and not is_approver():
				frappe.throw(
					approver_only_message(_("approve a registration action")), frappe.PermissionError
				)

	def _validate_outcome(self):
		if self.current_stage != "Approved" or self.action_type != "Renewal":
			return
		if bool(self.new_expiry_date) == bool(self.resulting_registration):
			frappe.throw(
				_(
					"An approved renewal needs either a New Expiry Date (same registration number) or a Resulting Registration (new number), not both."
				)
			)
		if self.resulting_registration:
			self._validate_successor()
		else:
			self._validate_new_expiry()

	def _validate_new_expiry(self):
		if getdate(self.new_expiry_date) <= getdate(self.approval_date):
			frappe.throw(_("New Expiry Date must be after the Approval Date."))
		registration = frappe.db.get_value(
			PRODUCT_REGISTRATION, self.registration, ["expiry_basis", "expiry_date"], as_dict=True
		)
		if (
			registration.expiry_basis == EXPIRY_SPECIFIED
			and registration.expiry_date
			and getdate(self.new_expiry_date) <= getdate(registration.expiry_date)
		):
			frappe.throw(
				_("New Expiry Date must be after the current expiry date {0}.").format(
					registration.expiry_date
				)
			)

	def _validate_successor(self):
		if self.resulting_registration == self.registration:
			frappe.throw(_("The Resulting Registration must differ from the registration being renewed."))
		successor = frappe.db.get_value(
			PRODUCT_REGISTRATION,
			self.resulting_registration,
			["official_status", "issue_date", "previous_registration"],
			as_dict=True,
		)
		if successor.official_status != ACTIVE:
			frappe.throw(
				_(
					"Activate the Resulting Registration before approving this renewal, so coverage has no gap."
				)
			)
		if not successor.issue_date:
			frappe.throw(_("The Resulting Registration needs an Issue Date."))
		if successor.previous_registration and successor.previous_registration != self.registration:
			frappe.throw(
				_("The Resulting Registration is already linked to a different previous registration.")
			)

	def _validate_single_open_renewal(self):
		if self.action_type != "Renewal" or self.current_stage in CLOSED_STAGES:
			return
		other = frappe.db.get_value(
			REGISTRATION_ACTION,
			{
				"registration": self.registration,
				"action_type": "Renewal",
				"current_stage": ("not in", CLOSED_STAGES),
				"name": ("!=", self.name),
			},
		)
		if other:
			frappe.throw(
				_("Renewal action {0} is already open for this registration.").format(frappe.bold(other))
			)

	def _sync_registration(self):
		if self.action_type != "Renewal" and self.current_stage != "Approved":
			return
		stage = self.current_stage
		if stage == "Approved":
			self._apply_approval()
		elif self.action_type == "Renewal" and stage in STAGE_RENEWAL_STATUS:
			# A decision not to renew is the registration owner's; editing an open action must not undo it.
			if (
				frappe.db.get_value(PRODUCT_REGISTRATION, self.registration, "renewal_status")
				!= "Not Renewing"
			):
				frappe.db.set_value(
					PRODUCT_REGISTRATION,
					self.registration,
					"renewal_status",
					STAGE_RENEWAL_STATUS[stage],
					update_modified=False,
				)
		elif self.action_type == "Renewal" and stage in ("Rejected", "Cancelled"):
			self._release_registration()

	def _release_registration(self):
		"""A rejected or cancelled renewal returns the registration to Not Started unless another is open."""
		current = frappe.db.get_value(PRODUCT_REGISTRATION, self.registration, "renewal_status")
		still_open = frappe.db.exists(
			REGISTRATION_ACTION,
			{
				"registration": self.registration,
				"action_type": "Renewal",
				"current_stage": ("not in", CLOSED_STAGES),
				"name": ("!=", self.name),
			},
		)
		if current in IN_PROGRESS_RENEWAL and not still_open:
			frappe.db.set_value(
				PRODUCT_REGISTRATION,
				self.registration,
				"renewal_status",
				"Not Started",
				update_modified=False,
			)

	def _apply_approval(self):
		"""Push an approved outcome onto the registration exactly once."""
		if self.applied_on:
			return
		registration = frappe.get_doc(PRODUCT_REGISTRATION, self.registration)
		previous_expiry = registration.expiry_date
		if self.action_type == "Renewal":
			if self.resulting_registration:
				self._apply_successor(registration)
			else:
				self._apply_new_expiry(registration)
		elif self.action_type == "Not Renew":
			frappe.db.set_value(
				PRODUCT_REGISTRATION,
				registration.name,
				"renewal_status",
				"Not Renewing",
				update_modified=False,
			)
		elif self.action_type == "Cancel":
			change_status(registration, CANCELLED, reason=_("Cancelled by action {0}").format(self.name))
		applied_on = now_datetime()
		frappe.db.set_value(
			REGISTRATION_ACTION,
			self.name,
			{"applied_on": applied_on, "previous_expiry_date": previous_expiry},
			update_modified=False,
		)
		self.applied_on = applied_on
		self.previous_expiry_date = previous_expiry

	def _apply_new_expiry(self, registration):
		status = ACTIVE if registration.official_status == EXPIRED else None
		change_status(
			registration,
			status,
			reason=_("Renewed by action {0}").format(self.name),
			changes={
				"expiry_basis": EXPIRY_SPECIFIED,
				"expiry_date": self.new_expiry_date,
				"renewal_status": "Approved",
			},
		)

	def _apply_successor(self, registration):
		successor = frappe.get_doc(PRODUCT_REGISTRATION, self.resulting_registration)
		frappe.db.set_value(
			PRODUCT_REGISTRATION,
			successor.name,
			"previous_registration",
			registration.name,
			update_modified=False,
		)
		change_status(
			registration,
			SUPERSEDED,
			reason=_("Superseded by {0} through action {1}").format(successor.name, self.name),
			changes={"superseded_by": successor.name, "renewal_status": "Approved"},
			close_lines_after=add_days(successor.issue_date, -1),
		)
