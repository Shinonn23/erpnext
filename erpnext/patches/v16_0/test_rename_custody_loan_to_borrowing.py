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

	def test_old_column_is_dropped_so_a_rerun_copies_nothing(self):
		database = MagicMock()
		database.exists.return_value = True
		columns = {("Pick List", "custody_loan")}
		database.has_column.side_effect = lambda doctype, field: (doctype, field) in columns
		fields = {("stock", "Pick List"): {"custody_loan": "borrowing"}}

		def drop(sql):
			columns.clear()

		database.sql_ddl.side_effect = drop
		with (
			patch.object(patch_module, "FIELDS", fields),
			patch.object(patch_module.frappe, "db", database, create=True),
			patch.object(patch_module.frappe, "reload_doc", create=True),
			patch.object(patch_module, "rename_field") as rename_field,
		):
			patch_module.rename_fields()
			patch_module.rename_fields()
		rename_field.assert_called_once_with("Pick List", "custody_loan", "borrowing")
		database.sql_ddl.assert_called_once()

	def test_blocked_module_rows_follow_the_module(self):
		database = MagicMock()
		database.table_exists.return_value = True
		with patch.object(patch_module.frappe, "db", database, create=True):
			patch_module.rename_blocked_modules()
			patch_module.rename_blocked_modules()
		# an update by value: a second run matches no row and changes nothing
		for call in database.sql.call_args_list:
			self.assertIn("update `tabBlock Module` set module = %s where module = %s", call.args[0])
			self.assertEqual(call.args[1], ("Borrowing", "Loan"))

	def test_stock_to_asset_conversion_link_fields_are_renamed(self):
		fields = patch_module.FIELDS[("assets", "Stock to Asset Conversion")]
		self.assertEqual(fields, {"custody_loan": "borrowing", "custody_loan_item": "borrowing_item"})

	def test_borrowing_request_type_follows_its_request_else_its_borrower(self):
		database = MagicMock()
		database.table_exists.return_value = True
		database.has_column.return_value = True
		database.sql.side_effect = [None, [frappe_dict(name="BRW-1", borrower_type="Customer")]]
		with patch.object(patch_module.frappe, "db", database, create=True):
			patch_module.split_borrowing_request_types()
		join = database.sql.call_args_list[0].args
		self.assertIn("join `tabMaterial Request` mr", join[0])
		self.assertEqual(join[1], ("Customer Borrow / Loan", "Customer Borrow / Loan"))
		database.set_value.assert_called_once_with(
			"Borrowing", "BRW-1", "material_request_type", "Customer Borrow", update_modified=False
		)

	def test_navigation_is_named_borrowings_not_the_doctype_slug(self):
		self.assertEqual(patch_module.NEW_NAVIGATION, "Borrowings")
		self.assertNotEqual(patch_module.NEW_NAVIGATION.lower(), "borrowing")
		existing = {("Workspace", "Loan"), ("Sidebar", "Loan"), ("Workspace Sidebar", "Borrowing")}
		existing |= {("Desktop Icon", "Loan")}
		database = MagicMock()
		database.table_exists.return_value = True
		database.exists.side_effect = lambda doctype, name=None: (doctype, name) in existing
		with (
			patch.object(patch_module.frappe, "db", database, create=True),
			patch.object(patch_module.frappe, "rename_doc", create=True) as rename_doc,
			patch(
				"frappe.desk.doctype.desktop_icon.desktop_icon.clear_every_users_desktop_icons", create=True
			),
		):
			patch_module.rename_navigation()
		self.assertEqual(
			sorted(call.args[:3] for call in rename_doc.call_args_list),
			[
				("Sidebar", "Loan", "Borrowings"),
				("Workspace", "Loan", "Borrowings"),
				("Workspace Sidebar", "Borrowing", "Borrowings"),
			],
		)
		database.delete.assert_called_once_with("Desktop Icon", {"name": ["in", ["Loan"]]})

	def test_navigation_json_names_do_not_collide_with_the_doctype_route(self):
		import json
		import os

		root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
		for path in (
			"borrowing/workspace/borrowings/borrowings.json",
			"borrowing/sidebar/borrowings/borrowings.json",
			"workspace_sidebar/borrowings.json",
			"desktop_icon/borrowings.json",
		):
			with open(os.path.join(root, path)) as handle:
				doc = json.load(handle)
			self.assertEqual(doc["name"], "Borrowings", path)
			for item in doc.get("items", []):
				if item.get("link_type") == "Workspace":
					self.assertEqual(item["link_to"], "Borrowings", path)


def frappe_dict(**values):
	from types import SimpleNamespace

	return SimpleNamespace(**values)
