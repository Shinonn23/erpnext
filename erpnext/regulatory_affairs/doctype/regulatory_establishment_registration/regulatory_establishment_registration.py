# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate, today

from erpnext.regulatory_affairs.constants import ACTIVE, EXPIRED, EXPIRY_SPECIFIED


class RegulatoryEstablishmentRegistration(Document):
	"""Company establishment licence referenced by product registrations."""

	def validate(self):
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
