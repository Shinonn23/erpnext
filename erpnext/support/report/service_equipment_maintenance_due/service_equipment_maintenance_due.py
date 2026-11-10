# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import date_diff, getdate, nowdate


def execute(filters=None):
	"""The open maintenance logs of Service Equipment (planned or overdue), due soonest first."""
	filters = frappe._dict(filters or {})
	conditions = {
		"service_equipment": ["is", "set"],
		"docstatus": 0,
		"maintenance_status": ["in", ["Planned", "Overdue"]],
	}
	for key in ("service_equipment", "maintenance_type"):
		if filters.get(key):
			conditions[key] = filters.get(key)
	if filters.to_date:
		conditions["due_date"] = ["<=", filters.to_date]
	rows = frappe.get_list(
		"Asset Maintenance Log",
		filters=conditions,
		fields=[
			"service_equipment",
			"name as log",
			"task_name",
			"maintenance_type",
			"periodicity",
			"due_date",
			"maintenance_status",
			"assign_to_name",
		],
		order_by="due_date asc",
		limit_page_length=0,
	)
	today = getdate(nowdate())
	for row in rows:
		row.days = date_diff(row.due_date, today) if row.due_date else None
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
		{
			"label": _("Log"),
			"fieldname": "log",
			"fieldtype": "Link",
			"options": "Asset Maintenance Log",
			"width": 150,
		},
		{"label": _("Task"), "fieldname": "task_name", "fieldtype": "Data", "width": 180},
		{"label": _("Type"), "fieldname": "maintenance_type", "fieldtype": "Data", "width": 160},
		{"label": _("Periodicity"), "fieldname": "periodicity", "fieldtype": "Data", "width": 100},
		{"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 100},
		{"label": _("Days"), "fieldname": "days", "fieldtype": "Int", "width": 70},
		{"label": _("Status"), "fieldname": "maintenance_status", "fieldtype": "Data", "width": 100},
		{"label": _("Assigned To"), "fieldname": "assign_to_name", "fieldtype": "Data", "width": 150},
	]
