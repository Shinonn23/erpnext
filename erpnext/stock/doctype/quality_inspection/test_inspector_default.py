"""Offline regression for the inspector identity on native server-created inspections."""

import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.model.create_new import get_static_default_value


class TestInspectorDefault(TestCase):
	def test_native_new_document_uses_each_authenticated_inspector(self):
		metadata = json.loads(Path(__file__).with_name("quality_inspection.json").read_text())
		field = frappe._dict(next(row for row in metadata["fields"] if row["fieldname"] == "inspected_by"))
		for user in ("incoming@example.test", "outgoing@example.test"):
			with self.subTest(user=user), patch.object(frappe, "session", frappe._dict(user=user)):
				self.assertEqual(get_static_default_value(field, {}, []), user)
