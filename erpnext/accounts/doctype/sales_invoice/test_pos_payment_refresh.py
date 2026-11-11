from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.sales_invoice.sales_invoice import update_multi_mode_option


class TestPOSPaymentRefresh(TestCase):
	def test_refresh_with_existing_payments_and_no_sales_invoice_field(self):
		doc = Mock(spec=["doctype", "payments", "company", "get", "set", "append"])
		doc.doctype = "Sales Invoice"
		doc.payments = [frappe._dict(mode_of_payment="Cash")]
		doc.company = "Company"
		doc.get.return_value = None
		profile = frappe._dict(payments=[frappe._dict(mode_of_payment="Cash", default=1)])
		mode = frappe._dict(mop="Cash", default_account="Cash Account", type="Cash")
		with (
			patch("erpnext.accounts.doctype.sales_invoice.sales_invoice.get_mode_of_payments_info", return_value={"Cash": mode}),
			patch("erpnext.accounts.doctype.sales_invoice.sales_invoice._", side_effect=lambda text: text),
			patch.object(frappe, "toast") as toast,
		):
			update_multi_mode_option(doc, profile)
		doc.set.assert_called_once_with("payments", [])
		doc.append.assert_called_once_with("payments", {})
		toast.assert_called_once()
