"""Clear Undue Tax on a Payment Entry: the pair of tax rows that moves undue VAT to due VAT is included in the paid
amount, so it posts no bank (paid account) lines and bank reconciliation sees only the money that moved.
Offline: frappe.db, the Thai Tax Settings and the document are stand-ins."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from erpnext.regional.thailand.custom import custom_api


def _receipt():
	doc = MagicMock()
	doc.company = "CD"
	doc.payment_type = "Receive"
	doc.has_purchase_tax_invoice = 0
	doc.total_allocated_amount = 53500
	doc.references = [
		SimpleNamespace(
			reference_doctype="Sales Invoice",
			reference_name="SINV-1",
			allocated_amount=53500,
			total_amount=53500,
		)
	]
	doc.get_doc_before_save.return_value = None
	rows = []
	doc.append.side_effect = lambda table, row: rows.append(row)
	return doc, rows


class TestClearUndueTaxRows(unittest.TestCase):
	def test_the_pair_is_included_in_the_paid_amount(self):
		doc, rows = _receipt()
		settings = SimpleNamespace(sales_tax_account_undue="2135-01", sales_tax_account="2135-00")
		with (
			patch.object(custom_api.frappe, "flags", custom_api.frappe._dict()),
			patch.object(
				custom_api.frappe, "db", MagicMock(get_all=Mock(return_value=[{"account": "2135-01"}]))
			),
			patch.object(custom_api, "get_thai_tax_settings", return_value=settings),
			patch.object(custom_api, "get_undue_tax", return_value=(3500, 50000, "2135-01", "2135-00")),
		):
			custom_api.clear_invoice_undue_tax(doc, None)
		self.assertEqual([r["description"] for r in rows], ["Clear Undue Tax", "Clear Undue Tax"])
		self.assertEqual([r["account_head"] for r in rows], ["2135-01", "2135-00"])
		self.assertEqual([r["tax_amount"] for r in rows], [-3500, 3500])
		self.assertTrue(all(r.get("included_in_paid_amount") == 1 for r in rows))
		doc.save.assert_called_once()


if __name__ == "__main__":
	unittest.main()
