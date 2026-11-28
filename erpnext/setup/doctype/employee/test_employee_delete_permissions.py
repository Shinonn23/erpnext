import unittest
from unittest.mock import Mock, call, patch

from erpnext.setup.doctype.employee import employee


class TestEmployeeDeletePermissions(unittest.TestCase):
	def test_only_permissions_for_the_deleted_employee_are_removed(self):
		doc = Mock(doctype="Employee")
		doc.name = "EMP-1"
		with patch.object(employee, "frappe") as frappe, patch.object(employee, "delete_events"):
			frappe.db.get_values.return_value = ["employee-permission", "payroll-permission"]
			frappe.db.has_column.return_value = False
			employee.Employee.on_trash(doc)

		frappe.db.get_values.assert_called_once_with(
			"User Permission", {"allow": "Employee", "for_value": "EMP-1"}, "name", pluck=True, for_update=True
		)
		self.assertEqual(
			frappe.delete_doc.call_args_list,
			[
				call("User Permission", "employee-permission", ignore_permissions=True),
				call("User Permission", "payroll-permission", ignore_permissions=True),
			],
		)

	def test_only_assignments_of_the_deleted_manager_are_cleared(self):
		for department in ("Lab", None):
			with (
				self.subTest(department=department),
				patch.object(employee, "frappe") as frappe,
				patch.object(employee, "delete_events"),
			):
				doc = Mock(doctype="Employee")
				doc.name = "EMP-1"
				frappe.db.get_values.return_value = []
				frappe.db.has_column.return_value = True
				frappe.db.get_value.return_value = department
				employee.Employee.on_trash(doc)
				filters = {"department_manager": "EMP-1"}
				frappe.db.get_value.assert_called_once_with("Department", filters, "name", for_update=True)
				if department:
					frappe.db.set_value.assert_called_once_with("Department", filters, "department_manager", None)
					doc.run_method.assert_called_once_with("on_department_manager_unassigned")
				else:
					frappe.db.set_value.assert_not_called()
					doc.run_method.assert_not_called()
