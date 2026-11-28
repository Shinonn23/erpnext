"""QI team membership is maintained by System Manager and identifies real Employees."""

import frappe
from frappe import _
from frappe.model.document import Document


class QualityInspectionTeam(Document):
	def on_update(self):
		previous = self.get_doc_before_save()
		if previous and (
			previous.get("disabled") != self.get("disabled")
			or {row.employee for row in previous.members} != {row.employee for row in self.members}
		):
			self.run_method("on_membership_changed")

	def validate(self):
		seen = set()
		for row in self.members:
			if row.employee in seen:
				frappe.throw(_("Employee {0} appears more than once in this team.").format(row.employee))
			seen.add(row.employee)
			employee = frappe.db.get_value(
				"Employee", row.employee, ["company", "employee_name", "designation", "user_id"], as_dict=True
			)
			if not employee:
				frappe.throw(_("Employee {0} does not exist.").format(row.employee))
			if employee.company != self.company:
				frappe.throw(_("Employee {0} must belong to the team's Company.").format(row.employee))
			# Employee is authoritative; cached display fields and a supplied User never grant membership.
			row.employee_name = employee.employee_name
			row.designation = employee.designation
			row.user_id = employee.user_id
