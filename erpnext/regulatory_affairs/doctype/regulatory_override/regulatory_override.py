# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

from erpnext.regulatory_affairs.constants import TRANSACTION_DOCTYPES
from erpnext.regulatory_affairs.permissions import approver_only_message, is_approver

TRANSITIONS = {
	"Requested": ("Approved", "Rejected", "Cancelled"),
	"Approved": ("Cancelled",),
	"Rejected": (),
	"Cancelled": (),
}
FROZEN_WHEN_APPROVED = ("transaction_doctype", "transaction_name", "item_code", "reason")


class RegulatoryOverride(Document):
	"""A document-bound exception; approving it never rewrites the transaction's policy result."""

	def validate(self):
		if self.transaction_doctype not in TRANSACTION_DOCTYPES:
			frappe.throw(
				_("A regulatory override must reference one of: {0}.").format(", ".join(TRANSACTION_DOCTYPES))
			)
		self._validate_transaction()
		previous = self.get_doc_before_save()
		self._protect_audit_stamps(previous)
		if self.is_new():
			self.requested_by = frappe.session.user
			self.requested_on = today()
		self._validate_status(previous)

	def _protect_audit_stamps(self, previous):
		"""Requester and approver stamps are system-owned: undo any client attempt to change them."""
		if previous:
			self.requested_by = previous.requested_by
			self.requested_on = previous.requested_on
		if not previous or self.status != "Approved" or previous.status == "Approved":
			self.approved_by = previous.approved_by if previous else None
			self.approved_on = previous.approved_on if previous else None

	def _validate_transaction(self):
		if not self.is_new():
			return
		docstatus = frappe.db.get_value(self.transaction_doctype, self.transaction_name, "docstatus")
		if docstatus is None:
			frappe.throw(
				_("{0} {1} does not exist.").format(
					_(self.transaction_doctype), frappe.bold(self.transaction_name)
				)
			)
		if docstatus != 0:
			frappe.throw(_("An override can only be requested for a draft document, before it is submitted."))

	def _validate_status(self, previous):
		status = self.status
		previous_status = previous.status if previous else None
		if not previous:
			if status != "Requested" and not is_approver():
				frappe.throw(_("A new override must start as Requested."))
			if status == "Approved":
				self._approve()
			return
		if previous_status == "Approved" and any(
			str(previous.get(field) or "") != str(self.get(field) or "") for field in FROZEN_WHEN_APPROVED
		):
			frappe.throw(_("An approved override cannot be edited. Cancel it and request a new one."))
		if status == previous_status:
			return
		if status not in TRANSITIONS.get(previous_status, ()):
			frappe.throw(_("An override cannot move from {0} to {1}.").format(_(previous_status), _(status)))
		if status == "Approved":
			self._approve()
		elif status == "Rejected" and not is_approver():
			frappe.throw(approver_only_message(_("reject an override")), frappe.PermissionError)
		elif status == "Cancelled" and not (is_approver() or frappe.session.user == self.requested_by):
			frappe.throw(
				_("Only the requester or a Regulatory Approver can cancel an override."),
				frappe.PermissionError,
			)

	def _approve(self):
		if not is_approver():
			frappe.throw(approver_only_message(_("approve a regulatory override")), frappe.PermissionError)
		if self.requested_by == frappe.session.user and frappe.session.user != "Administrator":
			frappe.throw(
				_("The requester cannot approve the same regulatory override."), frappe.PermissionError
			)
		self.approved_by = frappe.session.user
		self.approved_on = today()
