# Copyright (c) 2024, Ecosoft and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ThaiBillingSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		create_payment_receipt_from_sales_billing: DF.Check
	# end: auto-generated types

	pass
