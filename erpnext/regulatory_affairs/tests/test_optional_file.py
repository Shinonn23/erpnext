"""No file is required (Siwat, 5 Nov 2026): a Regulatory Document is a record whose file is optional."""

import json
from pathlib import Path
from unittest import TestCase

from erpnext.regulatory_affairs.doctype.regulatory_document import regulatory_document as module


class TestOptionalFile(TestCase):
	def test_no_attach_field_of_a_regulatory_doctype_is_mandatory(self):
		root = Path(module.__file__).parents[1]
		for path in root.glob("*/*.json"):
			for field in json.loads(path.read_text()).get("fields", []):
				if field.get("fieldtype") in ("Attach", "Attach Image"):
					self.assertFalse(field.get("reqd"), (path.name, field["fieldname"]))
					self.assertFalse(field.get("mandatory_depends_on"), (path.name, field["fieldname"]))

	def test_activation_message_does_not_ask_for_an_upload(self):
		from erpnext.regulatory_affairs.doctype.regulatory_product_registration import (
			regulatory_product_registration as registration,
		)

		self.assertNotIn("Upload", Path(registration.__file__).read_text())
