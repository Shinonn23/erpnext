# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, flt, get_datetime, get_link_to_form

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
		requires_shipment: DF.Check
		shipment: DF.Link | None
		shipment_transit_leg: DF.Check
		shipment_source_movement: DF.Link | None
		customer: DF.Link | None
		customer_contact: DF.Link | None
		customer_address: DF.Link | None
		customer_address_display: DF.TextEditor | None
		customer_contact_display: DF.TextEditor | None
		customer_contact_email: DF.Data | None
		is_system_generated: DF.Check
		is_custody_loan_transaction: DF.Check
		custody_loan: DF.Link | None
		custody_loan_source_doctype: DF.Link | None
		custody_loan_source_name: DF.DynamicLink | None
		company: DF.Link
		expected_return_date: DF.Date | None
		purpose: DF.Literal["", "Issue", "Receipt", "Transfer", "Transfer and Issue"]
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		transaction_date: DF.Datetime
	# end: auto-generated types

	@property
	def material_request_name(self):
		if self.reference_doctype == "Material Request":
			return self.reference_name
		return None

	def validate(self):
		self.prepare_shipment_transfer()
		self.validate_material_request()
		for d in self.assets:
			self.validate_asset(d)
			self.validate_movement(d)
			self.validate_transaction_date(d)

	def prepare_shipment_transfer(self):
		if not self.get("requires_shipment"):
			return
		if self.purpose not in ("Transfer", "Receipt"):
			frappe.throw(_("Shipment is available for Asset Movement with Purpose Transfer or Receipt only."))
		if self._action != "submit":
			for row in self.assets:
				if not row.shipment_destination_location:
					row.shipment_destination_location = row.target_location
			return
		if not self.shipment:
			frappe.throw(_("Create a Shipment before submitting this Asset Movement."))
		if frappe.db.get_value("Shipment", self.shipment, "docstatus") != 1:
			frappe.throw(_("The linked Shipment must be submitted before this Asset Movement."))
		if not frappe.db.exists(
			"Shipment Document",
			{"parent": self.shipment, "reference_doctype": self.doctype, "reference_name": self.name},
		):
			frappe.throw(_("This Asset Movement is not linked to the selected Shipment."))
		transit = frappe.db.get_value("Company", self.company, "default_in_transit_location")
		if not transit:
			locations = frappe.get_all(
				"Location", filters={"is_in_transit_location": 1, "is_group": 0}, pluck="name"
			)
			if len(locations) == 1:
				transit = locations[0]
		if not transit:
			frappe.throw(_("Set a Default In-Transit Location on Company before using Shipment."))
		is_transit, is_group = frappe.db.get_value(
			"Location", transit, ["is_in_transit_location", "is_group"]
		)
		if not is_transit or is_group:
			frappe.throw(_("The Default In-Transit Location must be marked as In-Transit and be a leaf Location."))
		for row in self.assets:
			if not row.shipment_destination_location:
				row.shipment_destination_location = row.target_location
			if not row.shipment_destination_location:
				frappe.throw(_("Select the final Target Location before creating a Shipment."))
			if row.shipment_destination_location == transit:
				frappe.throw(_("The final Target Location cannot be the In-Transit Location."))
			row.target_location = transit
		self.shipment_transit_leg = 1

	def validate_asset(self, d):
		status, company = frappe.db.get_value("Asset", d.asset, ["status", "company"])
		if self.purpose == "Transfer" and status in ("Draft", "Scrapped", "Sold", "Capitalized"):
			frappe.throw(_("{0} asset cannot be transferred").format(status))

		if company != self.company:
			frappe.throw(_("Asset {0} does not belong to company {1}").format(d.asset, self.company))

	def validate_movement(self, d):
		if self.get("is_custody_loan_transaction"):
			if self.purpose in ("Transfer", "Receipt"):
				self.validate_location(d)
			else:
				self.validate_employee(d)
			return
		if self.customer and self.material_request_name:
			if self.purpose in ("Transfer", "Receipt"):
				if not d.target_location:
					frappe.throw(_("Target Location is required for Customer Asset Movements"))
				if self.purpose == "Transfer" and not self.get("shipment_transit_leg"):
					request = frappe.get_cached_doc("Material Request", self.material_request_name)
					if d.target_location != request.customer_location:
						frappe.throw(_("Transfer Location must match the Customer Location on the linked Request"))
				self.validate_location(d)
				return
		if self.purpose == "Transfer and Issue":
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

	def on_submit(self):
		self.set_latest_location_and_custodian_in_asset()
		self.update_demo_loan_status()
		self.update_request_progress()

	def on_cancel(self):
		self.set_latest_location_and_custodian_in_asset()
		self.update_demo_loan_status()
		self.update_request_progress()

	def before_cancel(self):
		if self.get("shipment") and frappe.db.get_value("Shipment", self.shipment, "docstatus") == 1:
			frappe.throw(_("Cancel the Shipment before cancelling its Asset Movement."))

	def update_request_progress(self):
		if self.material_request_name and not self.get("shipment_transit_leg"):
			request = frappe.get_doc("Material Request", self.material_request_name)
			request.update_progress()
			if request.material_request_type in ("Customer Demo", "Customer Borrow / Loan"):
				request.db_set("request_progress_status", request.request_progress_status)
			elif request.material_request_type == "Asset Transfer":
				request.db_set("asset_transfer_status", request.asset_transfer_status)

	def validate_material_request(self):
		if not self.material_request_name:
			if self.reference_doctype == "Material Request" or any(row.request_item for row in self.assets):
				frappe.throw(_("Select a Material Request in Reference Document Type and Reference Document Name"))
			return
		request = frappe.get_doc("Material Request", self.material_request_name)
		if request.docstatus != 1 or request.company != self.company:
			frappe.throw(_("Asset Movement must reference a submitted Material Request for the same Company"))
		from erpnext.assets.doctype.material_request.material_request import (
			get_customer_delivery_snapshot,
			get_in_transit_asset_movement_qty,
			is_asset_transfer_request,
			is_customer_request,
		)

		if request.material_request_type == "Customer Borrow / Loan":
			if not self.get("is_custody_loan_transaction") or not self.get("custody_loan"):
				frappe.throw(_("Create Customer Borrow / Loan Asset Movements from the Custody Loan"))
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
		if not is_customer_request(request) or request.customer != self.customer:
			frappe.throw(_("Customer and submitted Customer Demo / Loan Request must match"))
		snapshot = get_customer_delivery_snapshot(
			request.customer,
			request.customer_address,
			request.customer_address_display,
			request.contact_person,
			request.customer_contact_display,
			request.customer_contact_email,
		)
		self.customer_address = snapshot.customer_address
		self.customer_address_display = snapshot.customer_address_display
		self.customer_contact = snapshot.customer_contact
		self.customer_contact_display = snapshot.customer_contact_display
		self.customer_contact_email = snapshot.customer_contact_email
		if request.contact_person:
			self.customer_contact = request.contact_person
		if self.purpose not in ("Transfer", "Receipt"):
			frappe.throw(_("Customer Demo / Loan Asset Movements must use Transfer or Receipt"))
		if not self.expected_return_date:
			frappe.throw(_("Expected Return Date is required"))
		request.update_progress()
		qty_by_request_item = {}
		assets_seen = set()
		for row in self.assets:
			if not row.request_item:
				frappe.throw(_("Select the requested Item for each Asset"))
			request_row = frappe.get_doc("Material Request Item", row.request_item)
			asset_item = frappe.db.get_value("Asset", row.asset, "item_code")
			if row.asset in assets_seen:
				frappe.throw(_("Asset {0} is listed more than once in this Movement").format(row.asset))
			assets_seen.add(row.asset)
			if request.material_request_type == "Customer Demo" and not frappe.db.get_value("Asset", row.asset, "is_demo_asset"):
				frappe.throw(_("Asset {0} is not marked as a Demo Asset").format(row.asset))
			if self.purpose == "Transfer" and frappe.db.get_value("Asset", row.asset, "custodian"):
				frappe.throw(_("Asset {0} is currently assigned to an Employee").format(row.asset))
			valid_item_codes = {request_row.item_code}
			if request_row.item_type == "Stock Item":
				valid_item_codes.add(frappe.db.get_value("Item", request_row.item_code, "demo_asset_item_code"))
			if request_row.parent != request.name or asset_item not in valid_item_codes:
				frappe.throw(_("Asset {0} does not match the linked Request Item").format(row.asset))
			if (
				self.purpose == "Transfer"
				and not self.get("shipment_source_movement")
				and frappe.db.get_value("Asset", row.asset, "demo_loan_status") in ("With Customer", "In Transit")
			):
				frappe.throw(_("Asset {0} is already with a Customer").format(row.asset))
			if self.purpose == "Receipt" and not self.get("shipment_source_movement"):
				latest = frappe.db.sql(
					"""select am.purpose, am.reference_doctype, am.reference_name, ami.request_item
					from `tabAsset Movement Item` ami join `tabAsset Movement` am on am.name=ami.parent
					where ami.asset=%s and am.docstatus=1 order by am.transaction_date desc, am.modified desc limit 1""",
					row.asset,
					as_dict=True,
				)
				if (
					not latest
					or latest[0].purpose != "Transfer"
					or latest[0].reference_doctype != "Material Request"
					or latest[0].reference_name != request.name
				):
					frappe.throw(_("Asset {0} is not currently issued against this request").format(row.asset))
			request_item = next((item for item in request.items if item.name == row.request_item), None)
			if not request_item:
				frappe.throw(_("Request Item does not belong to the linked Request"))
			qty_by_request_item[row.request_item] = qty_by_request_item.get(row.request_item, 0) + 1
		for request_item_name, qty in qty_by_request_item.items():
			request_item = next(item for item in request.items if item.name == request_item_name)
			if self.purpose == "Transfer" and self.get("shipment_source_movement"):
				continue
			elif self.purpose == "Transfer":
				in_transit_qty = get_in_transit_asset_movement_qty(request_item_name, "Transfer")
				available_qty = (
					flt(request_item.qty) - flt(request_item.fulfilled_qty) - flt(in_transit_qty)
				)
			else:
				in_transit_qty = get_in_transit_asset_movement_qty(request_item_name, "Receipt")
				available_qty = flt(request_item.outstanding_qty) - flt(in_transit_qty)
			if qty > available_qty:
				frappe.throw(
					_("Request Item {0}: Movement Qty exceeds available Qty of {1}").format(
						request_item_name, available_qty
					)
				)

	def validate_asset_transfer_request(self, request):
		if self.customer or self.expected_return_date:
			frappe.throw(_("Internal Asset Transfers cannot have a Customer or Expected Return Date"))
		if self.purpose != "Transfer":
			frappe.throw(_("Asset Transfer requests must use a Transfer Asset Movement"))
		if not self.assets:
			frappe.throw(_("Add the requested Assets to the Asset Movement"))

		request_items = {row.name: row for row in request.items}
		assets_seen = set()
		request_qty = {}
		source_movement = None
		if self.shipment_source_movement:
			source_movement = frappe.get_doc("Asset Movement", self.shipment_source_movement)
			if (
				source_movement.docstatus != 1
				or not source_movement.shipment_transit_leg
				or source_movement.shipment != self.shipment
				or source_movement.reference_doctype != "Material Request"
				or source_movement.reference_name != request.name
			):
				frappe.throw(_("The Shipment receipt must match the submitted Asset Transfer Movement"))
			source_rows = {
				(row.asset, row.request_item): row for row in source_movement.assets
			}
		else:
			source_rows = {}
		request.update_progress()
		for row in self.assets:
			request_item = request_items.get(row.request_item)
			if not request_item or request_item.parent != request.name:
				frappe.throw(_("Select an Asset Transfer Item from the linked Material Request"))
			if row.asset in assets_seen:
				frappe.throw(_("Asset {0} is listed more than once in this Movement").format(row.asset))
			assets_seen.add(row.asset)
			if row.asset != request_item.asset_transfer_asset:
				frappe.throw(_("Asset {0} does not match the linked Material Request Item").format(row.asset))
			target_location = (
				row.shipment_destination_location
				if self.get("requires_shipment")
				else row.target_location
			)
			if target_location != request_item.asset_transfer_target_location:
				frappe.throw(_("Target Location for Asset {0} must match the Material Request").format(row.asset))

			current_location = frappe.db.get_value("Asset", row.asset, "location")
			if source_movement:
				source_row = source_rows.get((row.asset, row.request_item))
				if (
					not source_row
					or row.source_location != source_row.target_location
					or row.target_location != source_row.shipment_destination_location
				):
					frappe.throw(_("Asset {0} does not match the linked Shipment movement").format(row.asset))
				expected_source_location = source_row.target_location
			else:
				expected_source_location = request_item.asset_transfer_source_location
			if current_location != expected_source_location:
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
		if self.get("shipment_transit_leg"):
			for row in self.assets:
				frappe.db.set_value("Asset", row.asset, "demo_loan_status", "In Transit")
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
			ORDER BY asm.transaction_date DESC
			LIMIT 1
			""",
			args,
		)

		if latest_movement_entry:
			current_location = latest_movement_entry[0][0]
			current_employee = latest_movement_entry[0][1]

		return current_location, current_employee

	def update_asset_location_and_custodian(self, asset_id, location, employee):
		asset = frappe.get_doc("Asset", asset_id)

		if cstr(employee) != asset.custodian:
			frappe.db.set_value("Asset", asset_id, "custodian", cstr(employee))
		if location and location != asset.location:
			frappe.db.set_value("Asset", asset_id, "location", location)

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
