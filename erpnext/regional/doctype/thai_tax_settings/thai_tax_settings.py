# Copyright (c) 2024, Ecosoft and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ThaiTaxSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from erpnext.regional.doctype.thai_tax_settings_company.thai_tax_settings_company import ThaiTaxSettingsCompany
		from frappe.types import DF

		company_accounts: DF.Table[ThaiTaxSettingsCompany]
	# end: auto-generated types

	pass
