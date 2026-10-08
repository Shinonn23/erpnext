# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today


class ThaiFDARegistration(Document):
	def validate(self):
		if self.expiry_basis == "มีวันหมดอายุตามเอกสาร":
			if not self.expiry_date:
				frappe.throw(_("กรุณาระบุวันหมดอายุตามเอกสาร"))
			if self.issue_date and getdate(self.expiry_date) < getdate(self.issue_date):
				frappe.throw(_("วันหมดอายุต้องไม่อยู่ก่อนวันที่ออกเอกสาร"))
		else:
			self.expiry_date = None

		if self.regulatory_category == "อื่นๆ" and not self.other_category:
			frappe.throw(_("กรุณาระบุประเภทผลิตภัณฑ์อื่นๆ"))

		self._append_approved_renewal_history()

	def _append_approved_renewal_history(self):
		previous = self.get_doc_before_save()
		if not previous or self.renewal_status != "ได้รับฉบับใหม่":
			return

		tracked_fields = ("registration_number", "issue_date", "expiry_date", "expiry_basis", "document")
		if not any(previous.get(field) != self.get(field) for field in tracked_fields):
			return

		self.append(
			"renewal_history",
			{
				"stage": "ได้รับฉบับใหม่",
				"event_date": today(),
				"previous_registration_number": previous.registration_number,
				"new_registration_number": self.registration_number,
				"previous_issue_date": previous.issue_date,
				"new_issue_date": self.issue_date,
				"previous_expiry_date": previous.expiry_date,
				"new_expiry_date": self.expiry_date,
				"previous_document": previous.document,
				"new_document": self.document,
			}
		)
