from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.selling.page.point_of_sale.point_of_sale import get_past_order_list


class TestPastOrderPermissions(TestCase):
	def test_sales_invoices_only_queried_with_select_permission(self):
		for search_term in ("", "customer"):
			for allowed in (False, True):
				with self.subTest(search_term=search_term, allowed=allowed):
					with (
						patch.object(frappe, "has_permission", return_value=allowed),
						patch.object(frappe, "db", Mock()) as db,
					):
						db.get_list.return_value = []
						self.assertEqual(get_past_order_list(search_term, "Paid"), [])
						doctypes = [call.args[0] for call in db.get_list.call_args_list]
						self.assertIn("POS Invoice", doctypes)
						self.assertEqual("Sales Invoice" in doctypes, allowed)
