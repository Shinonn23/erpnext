# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	"""The repairs of Service Equipment: when it failed, what was done and how long it was down."""
	filters = frappe._dict(filters or {})
	conditions = {"service_equipment": ["is", "set"], "docstatus": ["<", 2]}
	for key in ("service_equipment", "repair_status"):
		if filters.get(key):
			conditions[key] = filters.get(key)
	if filters.from_date and filters.to_date:
		conditions["failure_date"] = ["between", [filters.from_date, f"{filters.to_date} 23:59:59"]]
	rows = frappe.get_list(
		"Asset Repair",
		filters=conditions,
		fields=[
			"service_equipment",
			"equipment_name",
			"name as repair",
			"failure_date",
			"completion_date",
			"repair_status",
			"downtime",
			"issue",
			"maintenance_visit",
		],
		order_by="failure_date desc",
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
		{
			"label": _("Repair"),
			"fieldname": "repair",
			"fieldtype": "Link",
			"options": "Asset Repair",
			"width": 150,
		},
		{"label": _("Failure Date"), "fieldname": "failure_date", "fieldtype": "Datetime", "width": 150},
		{
			"label": _("Completion Date"),
			"fieldname": "completion_date",
			"fieldtype": "Datetime",
			"width": 150,
		},
		{"label": _("Status"), "fieldname": "repair_status", "fieldtype": "Data", "width": 100},
		{"label": _("Downtime"), "fieldname": "downtime", "fieldtype": "Data", "width": 100},
		{"label": _("Issue"), "fieldname": "issue", "fieldtype": "Link", "options": "Issue", "width": 130},
		{
			"label": _("Maintenance Visit"),
			"fieldname": "maintenance_visit",
			"fieldtype": "Link",
			"options": "Maintenance Visit",
			"width": 150,
		},
	]
