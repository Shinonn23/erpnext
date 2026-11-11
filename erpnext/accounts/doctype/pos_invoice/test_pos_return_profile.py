from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext.accounts.doctype.pos_invoice import pos_invoice


class TestPOSReturnProfile(TestCase):
	def test_profile_preserves_original_return_tax_settings(self):
		profile = frappe._dict(company="Company", taxes_and_charges="Current Tax", tax_category="Current")
		for is_return in (0, 1):
			with self.subTest(is_return=is_return):
				doc = frappe._dict(
					pos_profile="POS", is_return=is_return, return_against="SALE" if is_return else None,
					payments=[{}], taxes=[{}], items=[], taxes_and_charges="Original Tax",
					tax_category="Original", account_for_change_amount="Cash",
				)
				doc.set = lambda key, value: doc.__setitem__(key, value)
				with (
					patch.object(frappe, "get_doc", return_value=profile),
					patch.object(pos_invoice, "add_return_modes"),
				):
					pos_invoice.POSInvoice.set_pos_fields(doc)
				self.assertEqual(doc.taxes_and_charges, "Original Tax" if is_return else "Current Tax")
				self.assertEqual(doc.tax_category, "Original" if is_return else "Current")
