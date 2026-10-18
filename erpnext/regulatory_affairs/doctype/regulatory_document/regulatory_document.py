# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today

from erpnext.regulatory_affairs.constants import ACTIVE, PRODUCT_REGISTRATION, REGULATORY_DOCUMENT
from erpnext.regulatory_affairs.permissions import approver_only_message, is_approver

REVIEWED_STATUSES = ("Verified", "Rejected")
# What an Approver verified; changing any of it voids the verification.
VERIFIED_CONTENT = (
	"registration",
	"document_type",
	"document_number",
	"version",
	"issuer",
	"issue_date",
	"expiry_date",
	"applicable_newcode",
	"file",
)


class RegulatoryDocument(Document):
	"""Supporting evidence; its expiry is independent from the registration's expiry."""

	def validate(self):
		self._validate_dates()
		self._validate_verification()

	def on_trash(self):
		"""Do not delete the only valid certificate of an Active registration."""
		if self.document_type != "Certificate" or self.verification_status != "Verified":
			return
		if frappe.db.get_value(PRODUCT_REGISTRATION, self.registration, "official_status") != ACTIVE:
			return
		others = frappe.get_all(
			REGULATORY_DOCUMENT,
			filters={
				"registration": self.registration,
				"document_type": "Certificate",
				"verification_status": "Verified",
				"name": ("!=", self.name),
			},
			fields=["expiry_date"],
		)
		if not any(not row.expiry_date or getdate(row.expiry_date) >= getdate(today()) for row in others):
			frappe.throw(
				_("This is the only valid Certificate of an Active registration and cannot be deleted.")
			)

	def _validate_dates(self):
		if self.issue_date and self.expiry_date and getdate(self.expiry_date) < getdate(self.issue_date):
			frappe.throw(_("Document Expiry Date must be on or after Issue Date."))

	def _validate_verification(self):
		previous = self.get_doc_before_save()
		previous_status = previous.verification_status if previous else "Pending"
		if (
			previous
			and previous_status == "Verified"
			and self.verification_status == "Verified"
			and any(
				str(previous.get(field) or "") != str(self.get(field) or "") for field in VERIFIED_CONTENT
			)
		):
			self.verification_status = "Pending"
			self.verified_by = None
			self.verified_on = None
			frappe.msgprint(
				_("The verified document was changed, so it is Pending again and must be re-verified."),
				indicator="orange",
				alert=True,
			)
			return
		status = self.verification_status
		if status == previous_status:
			return
		if status in REVIEWED_STATUSES and not is_approver():
			frappe.throw(
				approver_only_message(_("verify or reject a regulatory document")), frappe.PermissionError
			)
		if status == "Verified":
			if self.expiry_date and getdate(self.expiry_date) < getdate(today()):
				frappe.throw(_("An expired document cannot be verified."))
			self.verified_by = frappe.session.user
			self.verified_on = today()
		elif status != "Expired":
			self.verified_by = None
			self.verified_on = None
