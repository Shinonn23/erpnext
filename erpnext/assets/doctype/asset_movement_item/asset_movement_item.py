# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


# import frappe
from frappe.model.document import Document


class AssetMovementItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		asset: DF.Link
		asset_name: DF.Data | None
		change_custodian: DF.Check
		change_department: DF.Check
		company: DF.Link | None
		from_employee: DF.Link | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		source_department: DF.Link | None
		source_location: DF.Link | None
		target_location: DF.Link | None
		target_department: DF.Link | None
		to_employee: DF.Link | None
	# end: auto-generated types

	pass
