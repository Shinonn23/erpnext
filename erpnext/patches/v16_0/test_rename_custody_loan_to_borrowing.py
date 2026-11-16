import unittest
from unittest.mock import MagicMock, patch

from erpnext.patches.v16_0 import rename_custody_loan_to_borrowing as patch_module


class TestRenameCustodyLoanToBorrowing(unittest.TestCase):
	def test_request_type_follows_the_borrower(self):
		request_type = patch_module.request_type
		self.assertEqual(request_type("Employee", "Customer", "C-1"), "Borrow")
		self.assertEqual(request_type("Customer", None, None), "Customer Borrow")
		self.assertEqual(request_type(None, "Customer", None), "Customer Borrow")
		self.assertEqual(request_type(None, "Employee", "C-1"), "Borrow")
		self.assertEqual(request_type(None, None, "C-1"), "Customer Borrow")
		self.assertEqual(request_type(None, None, None), "Borrow")
		self.assertEqual(request_type("Supplier", None, None), "Borrow")
		self.assertEqual(request_type("Contact", None, "C-1"), "Borrow")

	def test_doctypes_already_renamed_are_skipped(self):
		database = MagicMock()
		database.exists.side_effect = lambda doctype, name=None: name in patch_module.DOCTYPES.values()
		with (
			patch.object(patch_module.frappe, "db", database, create=True),
			patch.object(patch_module.frappe, "rename_doc", create=True) as rename_doc,
			patch.object(patch_module.frappe, "reload_doc", create=True) as reload_doc,
		):
			patch_module.rename_doctypes()
		rename_doc.assert_not_called()
		self.assertEqual(reload_doc.call_count, len(patch_module.DOCTYPES))

	def test_reports_already_renamed_are_skipped(self):
		database = MagicMock()
		database.exists.side_effect = lambda doctype, name=None: name in patch_module.REPORTS.values()
		with (
			patch.object(patch_module.frappe, "db", database, create=True),
			patch.object(patch_module.frappe, "rename_doc", create=True) as rename_doc,
		):
			patch_module.rename_reports()
		rename_doc.assert_not_called()

	def test_no_old_request_left_means_no_write(self):
		database = MagicMock()
		database.has_column.return_value = True
		database.sql.return_value = []
		with patch.object(patch_module.frappe, "db", database, create=True):
			patch_module.split_request_types()
		database.set_value.assert_not_called()
