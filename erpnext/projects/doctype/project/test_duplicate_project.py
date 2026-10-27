"""Duplicate Project with Tasks. Offline: frappe.copy_doc, the database and permissions are stand-ins; the
stand-in copy drops the fields the Project and Task DocTypes mark no_copy when ignore_no_copy is False."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from erpnext.projects.doctype.project import project as project_module

NO_COPY = {"workflow_state", "status", "sales_order", "customer"}


class FakeDoc(SimpleNamespace):
	def insert(self):
		self.name = f"{self.doctype}-NEW-{len(self.__dict__)}"
		self.inserted = True
		return self

	def db_set(self, field, value):
		setattr(self, field, value)

	def set(self, field, value):
		setattr(self, field, value)


def fake_copy_doc(doc, ignore_no_copy=True):
	data = dict(doc.__dict__ if hasattr(doc, "__dict__") else doc)
	data["name"] = None
	if not ignore_no_copy:
		for field in NO_COPY:
			data.pop(field, None)
	return FakeDoc(**data)


def source_project():
	return {
		"name": "PROJ-0001",
		"doctype": "Project",
		"project_name": "Old",
		"workflow_state": "Active",
		"status": "Completed",
		"sales_order": "SO-0001",
		"project_template": "TPL",
	}


class TestCreateDuplicateProject(unittest.TestCase):
	def run_duplicate(self, tasks=()):
		created = []

		def copy_doc(doc, ignore_no_copy=True):
			new = fake_copy_doc(doc, ignore_no_copy)
			created.append(new)
			return new

		fake_frappe = MagicMock()
		fake_frappe.copy_doc.side_effect = copy_doc
		with (
			patch.object(project_module, "frappe", fake_frappe),
			patch.object(project_module, "copy_tasks") as copy_tasks,
		):
			result = project_module.create_duplicate_project(json.dumps(source_project()), "New Project")
		return result, created, copy_tasks, fake_frappe

	def test_returns_the_new_name_and_keeps_the_typed_name_as_project_name(self):
		result, created, _, _ = self.run_duplicate()
		project = created[0]
		self.assertEqual(result, project.name)
		self.assertNotEqual(result, "New Project")
		self.assertEqual(project.project_name, "New Project")

	def test_workflow_state_and_sales_order_are_not_copied(self):
		_, created, _, fake_frappe = self.run_duplicate()
		project = created[0]
		self.assertFalse(fake_frappe.copy_doc.call_args.kwargs["ignore_no_copy"])
		self.assertFalse(hasattr(project, "workflow_state"))
		self.assertIsNone(project.sales_order)
		self.assertEqual(project.status, "Open")
		self.assertEqual(project.project_template, "TPL")

	def test_same_name_is_refused(self):
		fake_frappe = MagicMock()
		fake_frappe.throw.side_effect = RuntimeError("same name")
		with patch.object(project_module, "frappe", fake_frappe):
			with self.assertRaises(RuntimeError):
				project_module.create_duplicate_project(json.dumps(source_project()), "PROJ-0001")


class TestCopyTasks(unittest.TestCase):
	def test_copied_tasks_are_open_and_lose_completion_dates(self):
		done = FakeDoc(
			doctype="Task",
			name="TASK-1",
			status="Completed",
			parent_task=None,
			depends_on=[],
			completed_on="2026-10-01",
			closing_date="2026-10-02",
			review_date="2026-10-03",
		)
		copies = []

		def copy_doc(doc, ignore_no_copy=True):
			new = fake_copy_doc(doc, ignore_no_copy)
			new.save = lambda: None
			copies.append((new, ignore_no_copy))
			return new

		fake_frappe = MagicMock()
		fake_frappe.get_all.return_value = ["TASK-1"]
		fake_frappe.get_doc.return_value = done
		fake_frappe.copy_doc.side_effect = copy_doc
		with patch.object(project_module, "frappe", fake_frappe):
			project_module.copy_tasks("PROJ-0001", "PROJ-0002")

		task, ignore_no_copy = copies[0]
		self.assertFalse(ignore_no_copy)
		self.assertEqual(task.status, "Open")
		self.assertEqual(task.project, "PROJ-0002")
		self.assertIsNone(task.completed_on)
		self.assertIsNone(task.closing_date)
		self.assertIsNone(task.review_date)
