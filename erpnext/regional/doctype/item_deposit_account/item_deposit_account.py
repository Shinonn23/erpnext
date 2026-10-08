# Copyright (c) 2025, Ecosoft and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ItemDepositAccount(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		purchase_deposit_account: DF.Link
		sales_deposit_account: DF.Link
	# end: auto-generated types

	pass
