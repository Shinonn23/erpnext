"""Pure checks for source-master imports; no site fixtures or database writes."""
import logging
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

with patch.object(frappe, "logger", return_value=logging.getLogger(__name__)):
	from erpnext.setup.doctype.company.company import Company


class TestCompanyDepartmentDefaults(TestCase):
	def check_records(self, root_only):
		doc = Mock()
		doc.name = "source-company-unit-check"
		doc.flags = frappe._dict(only_create_department_root=root_only)
		with (
			patch.object(frappe.local, "flags", frappe._dict(), create=True),
			patch("erpnext.setup.doctype.company.company._", side_effect=lambda text: text),
			patch("erpnext.setup.doctype.company.company.make_records") as make,
			patch("erpnext.setup.doctype.company.company.rebuild_tree") as rebuild,
		):
			Company.create_default_departments(doc)
			self.assertEqual(make.call_args_list[0].args[0][0]["department_name"], "All Departments")
			self.assertNotIn("company", make.call_args_list[0].args[0][0])
			if root_only:
				self.assertEqual(make.call_count, 1)
				rebuild.assert_not_called()
			else:
				self.assertEqual(make.call_count, 2)
				self.assertGreater(len(make.call_args_list[1].args[0]), 1)
				rebuild.assert_called_once_with("Department")

	def test_source_import_creates_only_system_root(self):
		self.check_records(True)

	def test_normal_company_keeps_existing_defaults(self):
		self.check_records(False)
