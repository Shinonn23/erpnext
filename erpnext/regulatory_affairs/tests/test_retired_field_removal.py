"""Unit tests for the patch that deletes the retired Medical Device Custom Fields (mocks only, no database)."""

import unittest
from unittest.mock import MagicMock, patch

from erpnext.patches.v16_0 import remove_retired_medical_device_fields as removal
from erpnext.regulatory_affairs.custom_fields import CUSTOM_FIELDS


class TestRetiredFieldRemoval(unittest.TestCase):
	def test_retired_fields_are_not_managed_any_more(self):
		for doctype, fieldnames in removal.RETIRED_FIELDS.items():
			managed = {spec["fieldname"] for spec in CUSTOM_FIELDS.get(doctype, [])}
			for fieldname in fieldnames:
				self.assertNotIn(fieldname, managed, f"{doctype}.{fieldname}")

	def test_patch_deletes_them_and_frees_the_regulatory_owner(self):
		db = MagicMock()
		db.get_value.side_effect = lambda doctype, filters, field: (
			"Item-regulatory_owner" if field == "name" else "eval:doc.is_medical_device"
		)
		with (
			patch.object(removal.frappe, "get_all", side_effect=lambda *a, **k: [f"{k['filters']['dt']}-retired"]),
			patch.object(removal.frappe, "delete_doc") as delete_doc,
			patch.object(removal.frappe, "db", db),
			patch.object(removal.frappe, "clear_cache"),
		):
			removal.execute()
		self.assertEqual(delete_doc.call_count, len(removal.RETIRED_FIELDS))
		db.set_value.assert_called_once_with(
			"Custom Field", "Item-regulatory_owner", "depends_on", None, update_modified=False
		)
