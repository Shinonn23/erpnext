# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	"""Every submitted movement of each Service Equipment: where it went, from where, and who handed it over."""
	filters = frappe._dict(filters or {})
	conditions = [
		["Asset Movement", "docstatus", "=", 1],
		["Asset Movement Item", "service_equipment", "is", "set"],
	]
	if filters.service_equipment:
		conditions.append(["Asset Movement Item", "service_equipment", "=", filters.service_equipment])
	if filters.customer:
		conditions.append(["Asset Movement", "service_target_customer", "=", filters.customer])
	if filters.from_date:
		conditions.append(["Asset Movement", "transaction_date", ">=", filters.from_date])
	if filters.to_date:
		conditions.append(["Asset Movement", "transaction_date", "<=", f"{filters.to_date} 23:59:59"])
	rows = frappe.get_list(
		"Asset Movement",
		filters=conditions,
		fields=[
			"`tabAsset Movement Item`.service_equipment",
			"`tabAsset Movement Item`.equipment_name",
			"transaction_date",
			"name as movement",
			"service_movement_type",
			"`tabAsset Movement Item`.source_location",
			"`tabAsset Movement Item`.service_previous_customer as from_customer",
			"`tabAsset Movement Item`.target_location",
			"service_target_customer as to_customer",
			"`tabAsset Movement Item`.to_employee",
			"service_sender",
			"service_receiver",
		],
		order_by="transaction_date desc",
		limit_page_length=0,
	)
	return columns(), rows


def columns():
	return [
		{
			"label": _("Equipment"),
			"fieldname": "service_equipment",
			"fieldtype": "Link",
			"options": "Service Equipment",
			"width": 150,
		},
		{"label": _("Equipment Name"), "fieldname": "equipment_name", "fieldtype": "Data", "width": 180},
		{"label": _("Date"), "fieldname": "transaction_date", "fieldtype": "Datetime", "width": 150},
		{
			"label": _("Movement"),
			"fieldname": "movement",
			"fieldtype": "Link",
			"options": "Asset Movement",
			"width": 150,
		},
		{
			"label": _("Handover Type"),
			"fieldname": "service_movement_type",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": _("From Location"),
			"fieldname": "source_location",
			"fieldtype": "Link",
			"options": "Location",
			"width": 150,
		},
		{
			"label": _("From Customer"),
			"fieldname": "from_customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 160,
		},
		{
			"label": _("To Location"),
			"fieldname": "target_location",
			"fieldtype": "Link",
			"options": "Location",
			"width": 150,
		},
		{
			"label": _("To Customer"),
			"fieldname": "to_customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 160,
		},
		{
			"label": _("Custodian"),
			"fieldname": "to_employee",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 120,
		},
		{"label": _("Sender"), "fieldname": "service_sender", "fieldtype": "Data", "width": 120},
		{"label": _("Receiver"), "fieldname": "service_receiver", "fieldtype": "Data", "width": 120},
	]
