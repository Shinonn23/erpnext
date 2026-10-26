import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import add_months, getdate, nowdate

from erpnext.regional.thailand.custom.branches import set_company_branch_snapshot
from erpnext.regional.thailand.custom.custom_api import get_thai_tax_settings


def sales_tax_invoice_series(doc):
	"""Owner decision 22-10-2026: each company numbers its own tax invoices, CD-CI2600001, CD-CI2600002, ... and
	UB-CI2600001, ...: the company's abbreviation, CI and the year of the tax invoice's date (an invoice dated in
	December keeps its year), then a running number per company and year (getseries keys it by the text before
	the hashes)."""
	abbr = frappe.get_cached_value("Company", doc.company, "abbr")
	date = (
		doc.date
		or (doc.gl_entry and frappe.db.get_value("GL Entry", doc.gl_entry, "posting_date"))
		or nowdate()
	)
	return f"{abbr}-CI{getdate(date).strftime('%y')}.#####"


class SalesTaxInvoice(Document):
	def autoname(self):
		setting = get_thai_tax_settings(self.company)
		if setting.use_doc_name_for_sales_taxinv:
			self.name = self.voucher_no
		# When manual mode is enabled, use tax_invoice_number as the document name (zero tax only)
		elif (
			setting.create_sales_taxinv_on_zero_tax
			and setting.get("manual_keyin_sales_taxinv_on_zero_tax")
			and self.tax_amount == 0
		):
			voucher = frappe.get_doc(self.voucher_type, self.voucher_no)
			if voucher.tax_invoice_number:
				self.name = voucher.tax_invoice_number
		if not self.name:
			self.name = make_autoname(sales_tax_invoice_series(self), "Sales Tax Invoice", self)

	def validate(self):
		self.validate_account()
		self.compute_report_date()
		self.compute_tax_percent()
		set_company_branch_snapshot(self)

	def on_update_after_submit(self):
		if self.get_doc_before_save():  # Some change is made
			self.compute_report_date()
			self.compute_tax_percent()

	def compute_report_date(self):
		if int(self.months_delayed) == 0:
			self.db_set("report_date", self.date)
		else:
			self.db_set("report_date", add_months(self.date, int(self.months_delayed)))

	def compute_tax_percent(self):
		if self.tax_base:
			self.db_set("tax_percent", round((self.tax_amount / self.tax_base * 100), 0))
		else:
			self.db_set("tax_percent", 0)

	def validate_account(self):
		"""Get sales tax account from thai tax settings, make sure the account is correct"""
		setting = get_thai_tax_settings(self.company)
		if self.account != setting.sales_tax_account:
			frappe.throw(
				_(
					"Tax Invoice creation failed,<br/>"
					"- Invalid account is being assigned to Tax Invoice<br/>"
					"- Returning Invoice with both negative rate and quantity is incorrect"
				)
			)
