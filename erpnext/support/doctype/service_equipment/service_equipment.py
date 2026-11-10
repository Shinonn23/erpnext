# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext.support.service_equipment import PLACE_FIELDS, add_activity, validate_placement


class ServiceEquipment(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		address: DF.Link | None
		brand: DF.Data | None
		company: DF.Link
		contract_reference: DF.Data | None
		coverage_end: DF.Date | None
		custodian: DF.Link | None
		customer: DF.Link | None
		equipment_id: DF.Data
		equipment_name: DF.Data | None
		item: DF.Link | None
		last_movement: DF.Link | None
		legacy_asset: DF.Data | None
		location: DF.Link | None
		maintenance_team: DF.Link | None
		model: DF.Data | None
		next_calibration: DF.Date | None
		next_pm: DF.Date | None
		owner_supplier: DF.Link
		placed_on: DF.Datetime | None
		received_date: DF.Date | None
		serial_no: DF.Data | None
		serial_no_link: DF.Link | None
		source_hash: DF.Data | None
		source_key: DF.Data | None
		source_note: DF.SmallText | None
		status: DF.Literal[
			"Received",
			"Installed",
			"In Service",
			"Under Repair",
			"Out of Order",
			"Returned to Supplier",
			"Retired",
		]
		technician: DF.Link | None
		warranty_end: DF.Date | None
		warranty_source: DF.Data | None
	# end: auto-generated types

	def before_validate(self):
		for fieldname in ("equipment_id", "serial_no"):
			if self.get(fieldname):
				setattr(self, fieldname, self.get(fieldname).strip())
		if not self.serial_no and self.serial_no_link:
			self.serial_no = self.serial_no_link
		if not self.equipment_name:
			self.equipment_name = self.equipment_id
		if not self.owner_supplier:
			self.owner_supplier = frappe.db.get_single_value("Service Equipment Settings", "supplier")

	def validate(self):
		self.validate_place()
		if self.technician:
			company = frappe.db.get_value("Employee", self.technician, "company")
			if company and company != self.company:
				frappe.throw(_("The Responsible Technician must be an Employee of the responsible Company."))

	def validate_place(self):
		"""The place is set when the equipment is first recorded, then only an Asset Movement changes it."""
		if self.is_new() or self.flags.from_movement:
			if self.location or self.customer:
				validate_placement(self.company, self.location, self.custodian, self.customer, self.address)
			return
		previous = self.get_doc_before_save()
		if previous and any(str(previous.get(f) or "") != str(self.get(f) or "") for f in PLACE_FIELDS):
			frappe.throw(_("Use an Asset Movement to change where the equipment is or who holds it."))

	def after_insert(self):
		add_activity(self.name, _("Service Equipment recorded"))
