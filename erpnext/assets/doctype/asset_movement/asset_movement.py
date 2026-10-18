# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, get_datetime, get_link_to_form

from erpnext.assets.doctype.asset_activity.asset_activity import add_asset_activity


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
						frappe.throw(_("Transfer Location must match the Customer Location on the linked Request"))
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
		previous_movement_date = frappe.db.get_value(
			"Asset Movement",
			[["Asset Movement Item", "asset", "=", d.asset], ["docstatus", "=", 1]],
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
			frappe.throw(_("Select whether to change the Custodian, Department, or both for Asset {0}").format(d.asset))

		asset_values = frappe.db.get_value(
			"Asset", d.asset, ["custodian", "department"], as_dict=True
		)
		current_custodian = asset_values.custodian
		current_department = asset_values.department

		d.from_employee = current_custodian
		d.source_department = current_department

		if change_custodian:
			if self._action == "submit" and cstr(d.to_employee) == cstr(current_custodian):
				frappe.throw(_("Select a different To Employee or clear the Custodian change for Asset {0}").format(d.asset))
			if d.to_employee and frappe.db.get_value("Employee", d.to_employee, "company") != self.company:
				frappe.throw(
					_("Employee {0} does not belong to the company {1}").format(d.to_employee, self.company)
				)
		else:
			d.to_employee = current_custodian

		if change_department:
			if self._action == "submit" and cstr(d.target_department) == cstr(current_department):
				frappe.throw(
					_("Select a different Target Department or clear the Department change for Asset {0}").format(
						d.asset
					)
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

	def on_submit(self):
		self.set_latest_location_and_custodian_in_asset()
		self.update_demo_loan_status()
		self.update_request_progress()

	def on_cancel(self):
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
			if self.reference_doctype == "Material Request" or any(
				row.request_item for row in self.assets
			):
				frappe.throw(_("Select a Material Request in Reference Document Type and Reference Document Name"))
			return
		request = frappe.get_doc("Material Request", self.material_request_name)
		if request.docstatus != 1 or request.company != self.company:
			frappe.throw(_("Asset Movement must reference a submitted Material Request for the same Company"))
		if self._action == "submit":
			self.lock_material_request()
			request.reload()
			if request.docstatus != 1 or request.company != self.company:
				frappe.throw(_("Asset Movement must reference a submitted Material Request for the same Company"))
		from erpnext.assets.doctype.material_request.material_request import is_asset_transfer_request, is_customer_request

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
		frappe.throw(_("Asset Movements from this Material Request Purpose must be generated by its workflow"))

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
				frappe.throw(_("Asset {0} does not match the linked Material Request Asset").format(row.asset))
			if row.target_location != request_item.target_location:
				frappe.throw(_("Target Location for Asset {0} must match the Material Request").format(row.asset))

			current_location = frappe.db.get_value("Asset", row.asset, "location")
			if current_location != request_item.source_location:
				frappe.throw(_("Asset {0} has moved since the request was submitted").format(row.asset))
			if row.source_location and row.source_location != current_location:
				frappe.throw(_("Source Location for Asset {0} does not match its current Location").format(row.asset))
			row.source_location = current_location
			request_qty[request_item.name] = request_qty.get(request_item.name, 0) + 1

		for request_item_name, qty in request_qty.items():
			request_item = request_items[request_item_name]
			if qty > flt(request_item.qty) - flt(request_item.fulfilled_qty):
				frappe.throw(_("Request Item {0} has already been transferred").format(request_item_name))

	def update_demo_loan_status(self):
		if not self.material_request_name:
			return
		request_type = frappe.db.get_value("Material Request", self.material_request_name, "material_request_type")
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
			status = "With Customer" if (
				latest and latest[0].customer and latest[0].purpose == "Transfer"
				and latest[0].reference_doctype == "Material Request" and latest[0].reference_name
			) else "Available"
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
