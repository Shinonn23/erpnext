# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, get_datetime, get_link_to_form

from erpnext.assets.doctype.asset_activity.asset_activity import add_asset_activity
from erpnext.support import service_equipment as equipment


class AssetMovement(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.assets.doctype.asset_movement_item.asset_movement_item import AssetMovementItem

		amended_from: DF.Link | None
		assets: DF.Table[AssetMovementItem]
		company: DF.Link
		custody_loan: DF.Link | None
		custody_loan_source_doctype: DF.Link | None
		custody_loan_source_name: DF.DynamicLink | None
		customer: DF.Link | None
		customer_address: DF.Link | None
		customer_address_display: DF.TextEditor | None
		customer_contact: DF.Link | None
		customer_contact_display: DF.TextEditor | None
		customer_contact_email: DF.Data | None
		expected_return_date: DF.Date | None
		is_custody_loan_transaction: DF.Check
		is_system_generated: DF.Check
		purpose: DF.Literal["", "Issue", "Receipt", "Transfer", "Transfer and Issue", "Reassignment"]
		recipient: DF.DynamicLink | None
		recipient_type: DF.Literal["Employee", "Customer", "Supplier", "Contact"] | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		transaction_date: DF.Datetime
	# end: auto-generated types

	@property
	def material_request_name(self):
		if self.reference_doctype == "Material Request":
			return self.reference_name
		return None

	def lock_material_request(self):
		if self.material_request_name:
			frappe.db.sql(
				"select name from `tabMaterial Request` where name=%s for update", self.material_request_name
			)

	def lock_assets(self):
		for asset_name in sorted({row.asset for row in self.assets if row.asset}):
			frappe.db.sql("select name from `tabAsset` where name=%s for update", asset_name)
		for name in sorted({row.service_equipment for row in self.assets if row.get("service_equipment")}):
			frappe.db.sql("select name from `tabService Equipment` where name=%s for update", name)

	@property
	def is_equipment_movement(self):
		"""A movement of Service Equipment: every row names one, none names an Asset."""
		return any(row.get("service_equipment") for row in self.assets)

	@frappe.whitelist()
	def submit(self):
		self.lock_material_request()
		if self.get("custody_loan"):
			frappe.db.sql("select name from `tabCustody Loan` where name=%s for update", self.custody_loan)
		self.lock_assets()
		return super().submit()

	@frappe.whitelist()
	def cancel(self):
		self.lock_material_request()
		self.lock_assets()
		return super().cancel()

	def validate(self):
		if self.is_equipment_movement:
			self.validate_equipment_movement()
			return
		self.validate_material_request()
		for d in self.assets:
			self.validate_asset(d)
			self.validate_movement(d)
			self.validate_transaction_date(d)

	def validate_asset(self, d):
		status, company = frappe.db.get_value("Asset", d.asset, ["status", "company"])
		if self.purpose in ("Transfer", "Reassignment") and status in (
			"Draft",
			"Scrapped",
			"Sold",
			"Capitalized",
		):
			frappe.throw(_("{0} asset cannot be transferred").format(status))

		if company != self.company:
			frappe.throw(_("Asset {0} does not belong to company {1}").format(d.asset, self.company))

	def validate_movement(self, d):
		if self.get("is_custody_loan_transaction"):
			if self.purpose == "Reassignment":
				frappe.throw(_("Reassignment cannot be created from a Custody Loan transaction"))
			if self.purpose in ("Transfer", "Receipt"):
				self.validate_location(d)
			else:
				self.validate_employee(d)
			return
		if self.customer and self.material_request_name:
			if self.purpose in ("Transfer", "Receipt"):
				if not d.target_location:
					frappe.throw(_("Target Location is required for Customer Asset Movements"))
				if self.purpose == "Transfer":
					request = frappe.get_cached_doc("Material Request", self.material_request_name)
					if d.target_location != request.customer_location:
						frappe.throw(
							_("Transfer Location must match the Customer Location on the linked Request")
						)
				self.validate_location(d)
				return
		if self.purpose == "Reassignment":
			self.validate_reassignment(d)
		elif self.purpose == "Transfer and Issue":
			self.validate_location_and_employee(d)
		elif self.purpose in ["Receipt", "Transfer"]:
			self.validate_location(d)
		else:
			self.validate_employee(d)

	def validate_transaction_date(self, d):
		key = ("service_equipment", d.service_equipment) if d.get("service_equipment") else ("asset", d.asset)
		previous_movement_date = frappe.db.get_value(
			"Asset Movement",
			[
				["Asset Movement Item", key[0], "=", key[1]],
				["docstatus", "=", 1],
				["name", "!=", self.name or ""],
			],
			"transaction_date",
			order_by="transaction_date desc",
		)
		if previous_movement_date and get_datetime(previous_movement_date) > get_datetime(
			self.transaction_date
		):
			frappe.throw(_("Transaction date can't be earlier than previous movement date"))

	def validate_location_and_employee(self, d):
		self.validate_location(d)
		self.validate_employee(d)

	def validate_location(self, d):
		if self.purpose in ["Transfer", "Transfer and Issue"]:
			current_location = frappe.db.get_value("Asset", d.asset, "location")
			if d.source_location:
				if current_location != d.source_location:
					frappe.throw(
						_("Asset {0} does not belong to the location {1}").format(d.asset, d.source_location)
					)
			else:
				d.source_location = current_location

			if not d.target_location:
				frappe.throw(_("Target Location is required for transferring Asset {0}").format(d.asset))
			if d.source_location == d.target_location:
				frappe.throw(_("Source and Target Location cannot be same"))

		if self.purpose == "Receipt":
			if not d.target_location:
				frappe.throw(_("Target Location is required while receiving Asset {0}").format(d.asset))
			if d.to_employee and frappe.db.get_value("Employee", d.to_employee, "company") != self.company:
				frappe.throw(
					_("Employee {0} does not belong to the company {1}").format(d.to_employee, self.company)
				)

	def validate_employee(self, d):
		if self.customer and self.material_request_name and self.purpose in ("Transfer", "Receipt"):
			return
		if self.purpose == "Transfer and Issue":
			if not d.from_employee:
				frappe.throw(_("From Employee is required while issuing Asset {0}").format(d.asset))

		if d.from_employee:
			current_custodian = frappe.db.get_value("Asset", d.asset, "custodian")

			if current_custodian != d.from_employee:
				frappe.throw(
					_("Asset {0} does not belong to the custodian {1}").format(d.asset, d.from_employee)
				)

		if not d.to_employee:
			frappe.throw(_("Employee is required while issuing Asset {0}").format(d.asset))

		if d.to_employee and frappe.db.get_value("Employee", d.to_employee, "company") != self.company:
			frappe.throw(
				_("Employee {0} does not belong to the company {1}").format(d.to_employee, self.company)
			)

	def validate_reassignment(self, d):
		change_custodian = cint(d.change_custodian)
		change_department = cint(d.change_department)
		if self._action == "submit" and not change_custodian and not change_department:
			frappe.throw(
				_("Select whether to change the Custodian, Department, or both for Asset {0}").format(d.asset)
			)

		asset_values = frappe.db.get_value("Asset", d.asset, ["custodian", "department"], as_dict=True)
		current_custodian = asset_values.custodian
		current_department = asset_values.department

		d.from_employee = current_custodian
		d.source_department = current_department

		if change_custodian:
			if self._action == "submit" and cstr(d.to_employee) == cstr(current_custodian):
				frappe.throw(
					_("Select a different To Employee or clear the Custodian change for Asset {0}").format(
						d.asset
					)
				)
			if d.to_employee and frappe.db.get_value("Employee", d.to_employee, "company") != self.company:
				frappe.throw(
					_("Employee {0} does not belong to the company {1}").format(d.to_employee, self.company)
				)
		else:
			d.to_employee = current_custodian

		if change_department:
			if self._action == "submit" and cstr(d.target_department) == cstr(current_department):
				frappe.throw(
					_(
						"Select a different Target Department or clear the Department change for Asset {0}"
					).format(d.asset)
				)
			if d.target_department:
				department_company = frappe.db.get_value("Department", d.target_department, "company")
				if department_company != self.company:
					frappe.throw(
						_("Department {0} does not belong to the company {1}").format(
							d.target_department, self.company
						)
					)
		else:
			d.target_department = current_department

	# Service Equipment: the movement records a handover and writes the place onto the equipment; it has no
	# company-asset rules (status, Material Request, custody loan) and no financial effect.
	def validate_equipment_movement(self):
		if any(row.get("asset") for row in self.assets):
			frappe.throw(_("Move Assets and Service Equipment in separate Asset Movements."))
		# An Installation Note may make the Install movement of its equipment; no request or loan is fulfilled.
		referenced = self.reference_name and self.reference_doctype != "Installation Note"
		if referenced or self.get("custody_loan") or self.get("is_custody_loan_transaction"):
			frappe.throw(_("A Service Equipment movement cannot fulfil a request or a custody loan."))
		if self.purpose == "Reassignment":
			frappe.throw(_("Use Transfer, Receipt or Issue to move Service Equipment."))
		seen = set()
		for row in self.assets:
			if row.service_equipment in seen:
				frappe.throw(
					_("Service Equipment {0} is listed more than once in this Movement").format(
						row.service_equipment
					)
				)
			seen.add(row.service_equipment)
			self.validate_equipment_row(row)

	def validate_equipment_row(self, row, at_submit=False):
		current = frappe.db.get_value(
			"Service Equipment",
			row.service_equipment,
			["company", "status", "last_movement", *equipment.PLACE_FIELDS[:5]],
			as_dict=True,
			for_update=at_submit,
		)
		if not current:
			frappe.throw(_("Service Equipment {0} does not exist").format(row.service_equipment))
		if current.company != self.company:
			frappe.throw(
				_("Service Equipment {0} does not belong to company {1}").format(
					row.service_equipment, self.company
				)
			)
		if current.status == "Retired":
			frappe.throw(_("Service Equipment {0} is retired and cannot move").format(row.service_equipment))
		if self.docstatus == 0 or at_submit:
			if at_submit and (row.service_previous_movement or "") != (current.last_movement or ""):
				frappe.throw(
					_("A later handover exists for {0}. Save the draft again.").format(row.service_equipment)
				)
			row.source_location = current.location
			row.from_employee = current.custodian
			row.service_previous_customer = current.customer
			row.service_previous_address = current.address
			row.service_previous_status = current.status
			row.service_previous_movement = current.last_movement
			row.service_previous_placed_on = current.placed_on
		target_customer = self.get("service_target_customer")
		if (
			target_customer
			or row.target_location
			or self.get("service_movement_type")
			not in (
				"Return to Supplier",
				"Retire",
			)
		):
			equipment.validate_placement(
				self.company,
				row.target_location,
				row.to_employee,
				target_customer,
				self.get("service_target_address"),
			)
		self.validate_transaction_date(row)

	def before_submit(self):
		if not self.is_equipment_movement:
			return
		if not self.get("service_movement_type"):
			frappe.throw(_("Choose the Handover Type of the equipment."))
		for row in self.assets:
			self.validate_equipment_row(row, at_submit=True)
		equipment.check_private_file(self, "service_evidence")

	def before_cancel(self):
		if not self.is_equipment_movement:
			return
		for row in self.assets:
			last = frappe.db.get_value(
				"Service Equipment", row.service_equipment, "last_movement", for_update=True
			)
			if last != self.name:
				frappe.throw(
					_(
						"Cancel the later movement of {0} first: only its latest movement can be cancelled."
					).format(row.service_equipment)
				)

	def update_equipment_place(self, cancel=False):
		status = equipment.STATUS_BY_HANDOVER.get(self.get("service_movement_type"))
		for row in self.assets:
			if cancel:
				values = {
					"location": row.source_location,
					"custodian": row.from_employee,
					"customer": row.service_previous_customer,
					"address": row.service_previous_address,
					"placed_on": row.service_previous_placed_on,
					"last_movement": row.service_previous_movement,
				}
				# the status goes back only while it is still the one this movement set (a repair may have changed it)
				current = frappe.db.get_value("Service Equipment", row.service_equipment, "status")
				if status and current == status:
					values["status"] = row.service_previous_status
				subject = _("Movement cancelled, back to its previous place")
			else:
				values = {
					"location": row.target_location,
					"custodian": row.to_employee,
					"customer": self.get("service_target_customer"),
					"address": self.get("service_target_address"),
					"placed_on": self.transaction_date,
					"last_movement": self.name,
				}
				if status:
					values["status"] = status
				subject = _("{0}: moved to {1}").format(
					_(self.get("service_movement_type") or self.purpose),
					self.get("service_target_customer") or row.target_location or row.to_employee,
				)
			values = {key: value for key, value in values.items() if key != "status" or value}
			frappe.db.set_value("Service Equipment", row.service_equipment, values)
			equipment.add_activity(row.service_equipment, subject, self)

	def on_submit(self):
		if self.is_equipment_movement:
			self.update_equipment_place()
			return
		self.set_latest_location_and_custodian_in_asset()
		self.update_demo_loan_status()
		self.update_request_progress()

	def on_cancel(self):
		if self.is_equipment_movement:
			self.update_equipment_place(cancel=True)
			return
		self.set_latest_location_and_custodian_in_asset()
		self.update_demo_loan_status()
		self.update_request_progress()

	def update_request_progress(self):
		if not self.material_request_name:
			return

		request = frappe.get_doc("Material Request", self.material_request_name)
		request.update_progress()
		if request.material_request_type in ("Customer Demo", "Customer Borrow / Loan"):
			request.db_set("request_progress_status", request.request_progress_status)
		elif request.material_request_type == "Asset Transfer":
			request.db_set("asset_transfer_status", request.asset_transfer_status)

	def validate_material_request(self):
		if not self.material_request_name:
			if self.reference_doctype == "Material Request" or any(row.request_item for row in self.assets):
				frappe.throw(
					_("Select a Material Request in Reference Document Type and Reference Document Name")
				)
			return
		request = frappe.get_doc("Material Request", self.material_request_name)
		if request.docstatus != 1 or request.company != self.company:
			frappe.throw(_("Asset Movement must reference a submitted Material Request for the same Company"))
		if self._action == "submit":
			self.lock_material_request()
			request.reload()
			if request.docstatus != 1 or request.company != self.company:
				frappe.throw(
					_("Asset Movement must reference a submitted Material Request for the same Company")
				)
		from erpnext.assets.doctype.material_request.material_request import (
			is_asset_transfer_request,
			is_customer_request,
		)

		if request.material_request_type in ("Customer Borrow / Loan", "Customer Demo"):
			if not self.get("is_custody_loan_transaction") or not self.get("custody_loan"):
				frappe.throw(_("Create Customer Demo and Borrow Asset Movements from the Custody Loan"))
			from erpnext.loan.doctype.custody_loan.custody_loan_core import (
				validate_asset_movement_custody_loan,
				validate_generated_transaction,
			)

			validate_asset_movement_custody_loan(self)
			validate_generated_transaction(self)
			return

		if is_asset_transfer_request(request):
			self.validate_asset_transfer_request(request)
			return
		frappe.throw(
			_("Asset Movements from this Material Request Purpose must be generated by its workflow")
		)

	def validate_asset_transfer_request(self, request):
		# These fields are only meaningful for customer handovers. Clear values copied
		# from a previous form state instead of blocking an internal transfer.
		for fieldname in (
			"customer",
			"customer_contact",
			"customer_address",
			"customer_address_display",
			"customer_contact_display",
			"customer_contact_email",
			"recipient_type",
			"recipient",
			"expected_return_date",
		):
			self.set(fieldname, None)
		if self.purpose != "Transfer":
			frappe.throw(_("Asset Transfer requests must use a Transfer Asset Movement"))
		if not self.assets:
			frappe.throw(_("Add the requested Assets to the Asset Movement"))

		request_items = {row.name: row for row in request.items}
		assets_seen = set()
		request_qty = {}
		request.update_progress()
		for row in self.assets:
			request_item_name = row.request_item
			if not request_item_name:
				frappe.throw(_("Select an Asset Transfer row from the linked Material Request"))
			request_item = request_items.get(request_item_name)
			if not request_item or request_item.parent != request.name:
				frappe.throw(_("Select an Asset Transfer Item from the linked Material Request"))
			if row.asset in assets_seen:
				frappe.throw(_("Asset {0} is listed more than once in this Movement").format(row.asset))
			assets_seen.add(row.asset)
			if row.asset != request_item.asset:
				frappe.throw(
					_("Asset {0} does not match the linked Material Request Asset").format(row.asset)
				)
			if row.target_location != request_item.target_location:
				frappe.throw(
					_("Target Location for Asset {0} must match the Material Request").format(row.asset)
				)

			current_location = frappe.db.get_value("Asset", row.asset, "location")
			if current_location != request_item.source_location:
				frappe.throw(_("Asset {0} has moved since the request was submitted").format(row.asset))
			if row.source_location and row.source_location != current_location:
				frappe.throw(
					_("Source Location for Asset {0} does not match its current Location").format(row.asset)
				)
			row.source_location = current_location
			request_qty[request_item.name] = request_qty.get(request_item.name, 0) + 1

		for request_item_name, qty in request_qty.items():
			request_item = request_items[request_item_name]
			if qty > flt(request_item.qty) - flt(request_item.fulfilled_qty):
				frappe.throw(_("Request Item {0} has already been transferred").format(request_item_name))

	def update_demo_loan_status(self):
		if not self.material_request_name:
			return
		request_type = frappe.db.get_value(
			"Material Request", self.material_request_name, "material_request_type"
		)
		if request_type != "Customer Demo":
			return
		for row in self.assets:
			latest = frappe.db.sql(
				"""select am.purpose, am.customer, am.reference_doctype, am.reference_name from `tabAsset Movement Item` ami
				join `tabAsset Movement` am on am.name=ami.parent
				where ami.asset=%s and am.docstatus=1
				order by am.transaction_date desc, am.modified desc limit 1""",
				row.asset,
				as_dict=True,
			)
			status = (
				"With Customer"
				if (
					latest
					and latest[0].customer
					and latest[0].purpose == "Transfer"
					and latest[0].reference_doctype == "Material Request"
					and latest[0].reference_name
				)
				else "Available"
			)
			frappe.db.set_value("Asset", row.asset, "demo_loan_status", status)

	def set_latest_location_and_custodian_in_asset(self):
		for d in self.assets:
			current_location, current_employee = self.get_latest_location_and_custodian(d.asset)
			self.update_asset_location_and_custodian(d.asset, current_location, current_employee)
			if self.purpose == "Reassignment":
				self.update_asset_department(d)
				self.log_reassignment_activity(d)
			else:
				self.log_asset_activity(d.asset, current_location, current_employee)

	def get_latest_location_and_custodian(self, asset):
		current_location, current_employee = "", ""
		cond = "1=1"

		# latest entry corresponds to current document's location, employee when transaction date > previous dates
		# In case of cancellation it corresponds to previous latest document's location, employee
		args = {"asset": asset, "company": self.company}
		latest_movement_entry = frappe.db.sql(
			f"""
			SELECT asm_item.target_location, asm_item.to_employee
			FROM `tabAsset Movement Item` asm_item
			JOIN `tabAsset Movement` asm ON asm_item.parent = asm.name
			WHERE
				asm_item.asset = %(asset)s AND
				asm.company = %(company)s AND
				asm.docstatus = 1 AND {cond}
			ORDER BY asm.transaction_date DESC, asm.modified DESC, asm.name DESC, asm_item.idx DESC
			LIMIT 1
			""",
			args,
		)

		if latest_movement_entry:
			current_location = latest_movement_entry[0][0]
			current_employee = latest_movement_entry[0][1]
		elif self.purpose == "Reassignment" and (self._action == "cancel" or self.docstatus == 2):
			movement_item = next((row for row in self.assets if row.asset == asset), None)
			if movement_item:
				current_employee = movement_item.from_employee

		return current_location, current_employee

	def update_asset_location_and_custodian(self, asset_id, location, employee):
		asset = frappe.get_doc("Asset", asset_id)

		if cstr(employee) != asset.custodian:
			frappe.db.set_value("Asset", asset_id, "custodian", cstr(employee))
		if location and location != asset.location:
			frappe.db.set_value("Asset", asset_id, "location", location)

	def update_asset_department(self, movement_item):
		if not cint(movement_item.change_department):
			return

		latest_department = frappe.db.sql(
			"""SELECT ami.target_department
			FROM `tabAsset Movement Item` ami
			JOIN `tabAsset Movement` am ON am.name = ami.parent
			WHERE ami.asset = %s AND am.company = %s AND am.docstatus = 1
				AND am.purpose = 'Reassignment' AND ifnull(ami.change_department, 0) = 1
			ORDER BY am.transaction_date DESC, am.modified DESC, am.name DESC, ami.idx DESC
			LIMIT 1""",
			(movement_item.asset, self.company),
		)

		if latest_department:
			department = cstr(latest_department[0][0])
		elif self._action == "cancel" or self.docstatus == 2:
			# Legacy movements have no department snapshot. Use the cancelled row's
			# source value as the baseline when this was the first department change.
			department = cstr(movement_item.source_department)
		else:
			return

		current_department = frappe.db.get_value("Asset", movement_item.asset, "department")
		if department != cstr(current_department):
			frappe.db.set_value("Asset", movement_item.asset, "department", department)

	def log_reassignment_activity(self, movement_item):
		if self._action == "cancel" or self.docstatus == 2:
			add_asset_activity(
				movement_item.asset,
				_("Asset reassignment {0} was cancelled").format(
					get_link_to_form("Asset Movement", self.name)
				),
			)
			return

		changes = []
		if cint(movement_item.change_custodian):
			from_employee = (
				get_link_to_form("Employee", movement_item.from_employee)
				if movement_item.from_employee
				else _("Unassigned")
			)
			to_employee = (
				get_link_to_form("Employee", movement_item.to_employee)
				if movement_item.to_employee
				else _("Unassigned")
			)
			changes.append(_("Custodian changed from {0} to {1}").format(from_employee, to_employee))
		if cint(movement_item.change_department):
			from_department = (
				get_link_to_form("Department", movement_item.source_department)
				if movement_item.source_department
				else _("Unassigned")
			)
			to_department = (
				get_link_to_form("Department", movement_item.target_department)
				if movement_item.target_department
				else _("Unassigned")
			)
			changes.append(_("Department changed from {0} to {1}").format(from_department, to_department))

		if changes:
			add_asset_activity(
				movement_item.asset,
				_("Asset reassignment {0}: {1}").format(
					get_link_to_form("Asset Movement", self.name), "; ".join(changes)
				),
			)

	def log_asset_activity(self, asset_id, location, employee):
		if location and employee:
			add_asset_activity(
				asset_id,
				_("Asset received at Location {0} and issued to Employee {1}").format(
					get_link_to_form("Location", location),
					get_link_to_form("Employee", employee),
				),
			)
		elif location:
			add_asset_activity(
				asset_id,
				_("Asset transferred to Location {0}").format(get_link_to_form("Location", location)),
			)
		elif employee:
			add_asset_activity(
				asset_id,
				_("Asset issued to Employee {0}").format(get_link_to_form("Employee", employee)),
			)
