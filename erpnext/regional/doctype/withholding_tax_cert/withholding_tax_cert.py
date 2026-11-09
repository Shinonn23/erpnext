# Copyright (c) 2023, Kitti U. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext.regional.thailand.custom.withholding_cert import employee_recipient, withholding_recipient


class WithholdingTaxCert(Document):
	def validate(self):
		if bool(self.get("supplier")) == bool(self.get("employee")):
			frappe.throw(_("Select either Supplier or Employee for the certificate."))
		if not self.get("employee"):
			return
		self.supplier_name = employee_recipient(self.employee, self.company).employee_name
		if self.get("supplier_address"):
			frappe.throw(_("Use Employee Address for an Employee certificate."))
		if self.get("voucher_no"):
			if self.get("voucher_type") != "Payment Entry":
				frappe.throw(_("An Employee certificate must reference a Payment Entry."))
			voucher = frappe.get_doc("Payment Entry", self.voucher_no)
			voucher.check_permission("read")
			if (
				voucher.company != self.company
				or withholding_recipient(voucher) != ("Employee", self.employee)
			):
				frappe.throw(_(
					"The certificate Employee and Company must match the stored employee payment."
				))

	def before_submit(self):
		if self.get("employee") and not (self.get("supplier_tax_id") and self.get("employee_address")):
			frappe.throw(_("Enter Recipient Tax ID and Employee Address before submitting the certificate."))
		if self.get("employee") and self.get("voucher_no"):
			if frappe.db.get_value("Payment Entry", self.voucher_no, "docstatus") != 1:
				frappe.throw(_("Submit the Employee payment before submitting its withholding certificate."))
