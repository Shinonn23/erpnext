# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document


class WarehouseType(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
	# end: auto-generated types

	def validate(self):
		self.validate_policy_permission()

	def on_trash(self):
		self.validate_policy_permission()

	def validate_policy_permission(self):
		if frappe.session.user != "Administrator" and not set(frappe.get_roles()).intersection(
			{"Stock Manager", "Quality Manager", "System Manager"}
		):
			frappe.throw(
				_("Only a Stock Manager, Quality Manager or System Manager may change warehouse type policy."),
				frappe.PermissionError,
			)
