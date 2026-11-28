"""Team members are matched to enabled Users and active Employees of the team company."""

import frappe


def inspection_teams(user=None, *, company=None):
	"""Return enabled teams backed by the user's current Active Employee, never cached child identity."""
	user = user or frappe.session.user
	if not user or user == "Guest" or not frappe.db.get_value("User", user, "enabled"):
		return []
	if not all(
		frappe.db.table_exists(doctype)
		for doctype in ("Quality Inspection Team", "Quality Inspection Team Member")
	):
		return []
	filters = {"user_id": user, "status": "Active"}
	if company:
		filters["company"] = company
	employees = frappe.get_all("Employee", filters=filters, fields=["name", "company"])
	employee_companies = {row.name: row.company for row in employees if row.company}
	if not employee_companies:
		return []
	members = frappe.get_all(
		"Quality Inspection Team Member",
		filters={
			"parenttype": "Quality Inspection Team",
			"parentfield": "members",
			"employee": ["in", list(employee_companies)],
		},
		fields=["parent", "employee"],
	)
	if not members:
		return []
	team_filters = {"disabled": 0, "name": ["in", sorted({row.parent for row in members})]}
	if company:
		team_filters["company"] = company
	teams = frappe.get_all("Quality Inspection Team", filters=team_filters, fields=["name", "company"])
	return sorted(
		{
			team.name
			for team in teams
			if any(
				member.parent == team.name and employee_companies.get(member.employee) == team.company
				for member in members
			)
		}
	)
