"""Attachment consumers use File storage without requiring a site or cloud credentials."""

import csv
import gzip
import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
import openpyxl

from erpnext.accounts.doctype.bank_statement_import import bank_statement_import as bank
from erpnext.accounts.doctype.chart_of_accounts_importer import chart_of_accounts_importer as coa
from erpnext.regional.doctype.import_supplier_invoice import import_supplier_invoice as supplier
from erpnext.regional.italy import utils as italy
from erpnext.setup.doctype.transaction_deletion_record import transaction_deletion_record as deletion
from erpnext.stock import stock_ledger
from erpnext.support import service_equipment


class TestAttachmentStorage(unittest.TestCase):
	def test_bank_updates_cloud_csv_without_disk(self):
		file_doc = Mock()
		file_doc.get_extension.return_value = ("statement", ".csv")
		data = [["Description", "Bank Account"], ["Café\ntransfer", "Bank-1"]]
		with (
			patch.object(bank.gcs_storage, "enabled", return_value=True),
			patch("builtins.open", side_effect=AssertionError("disk access")),
		):
			bank.write_files(SimpleNamespace(file_doc=file_doc), data)
		content = file_doc.save_file.call_args.kwargs["content"]
		self.assertEqual(list(csv.reader(io.StringIO(content))), data)
		self.assertTrue(file_doc.save_file.call_args.kwargs["overwrite"])
		self.assertTrue(file_doc.save_file.call_args.kwargs["ignore_existing_file_check"])
		file_doc.db_update.assert_called_once()
		file_doc.get_full_path.assert_not_called()

	def test_bank_updates_cloud_excel_without_temporary_files(self):
		file_doc = Mock()
		file_doc.get_extension.return_value = ("statement", ".xlsx")
		with (
			patch.object(bank.gcs_storage, "enabled", return_value=True),
			patch("builtins.open", side_effect=AssertionError("disk access")),
			patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("temporary file")),
		):
			bank.write_files(SimpleNamespace(file_doc=file_doc), [["Bank Account"], ["Bank-1"]])
		wb = openpyxl.load_workbook(io.BytesIO(file_doc.save_file.call_args.kwargs["content"]))
		self.assertEqual(wb.sheetnames, ["trans"])
		self.assertEqual(list(wb.active.values), [("Bank Account",), ("Bank-1",)])

	def test_coa_csv_accepts_text_and_bytes(self):
		for content in (
			"Account Name,Parent Account\nCafé,Assets\n",
			b"Account Name,Parent Account\nCaf\xc3\xa9,Assets\n",
		):
			with self.subTest(content=type(content)):
				file_doc = Mock()
				file_doc.get_content.return_value = content
				with patch("builtins.open", side_effect=AssertionError("disk access")):
					self.assertEqual(
						coa.generate_data_from_csv(file_doc, as_dict=True),
						[{"account_name": "Café", "parent_account": "Assets"}],
					)
				file_doc.get_full_path.assert_not_called()

	def test_coa_excel_response_stays_in_memory(self):
		response = {}
		with (
			patch.object(frappe, "response", response, create=True),
			patch("builtins.open", side_effect=AssertionError("disk access")),
			patch("tempfile.mkstemp", side_effect=AssertionError("temporary file")),
		):
			coa.build_response_as_excel(io.StringIO("Account Name\nCafé\n"))
		wb = openpyxl.load_workbook(io.BytesIO(response["filecontent"]))
		self.assertEqual(list(wb.active.values), [("Account Name",), ("Café",)])

	def test_italy_zip_preserves_binary_and_text_content(self):
		for content in (b"\xff\xfe\x00binary", "Café"):
			with self.subTest(content=type(content)):
				file_doc = Mock()
				file_doc.get_content.return_value = content
				response = SimpleNamespace()
				with (
					patch.object(frappe, "get_doc", return_value=file_doc),
					patch.object(frappe, "local", SimpleNamespace(response=response)),
					patch("builtins.open", side_effect=AssertionError("disk access")),
				):
					italy.download_zip([SimpleNamespace(name="file-1", file_name="invoice.xml")], "invoices.zip")
				with zipfile.ZipFile(io.BytesIO(response.filecontent)) as archive:
					self.assertEqual(archive.read("invoice.xml"), content.encode() if isinstance(content, str) else content)
				file_doc.get_content.assert_called_once_with(encodings=[])

	def test_supplier_zip_reads_binary_storage(self):
		archive = io.BytesIO()
		with zipfile.ZipFile(archive, "w") as zf:
			zf.writestr("invoice.xml", "<invoice>Café</invoice>")
		file_doc = Mock()
		file_doc.get_content.return_value = archive.getvalue()
		doc = SimpleNamespace(
			zip_file="/private/files/invoices.zip",
			doctype="Import Supplier Invoice",
			name="import-1",
			publish=Mock(),
			prepare_data_for_import=Mock(),
			save=Mock(),
		)
		with (
			patch.object(frappe, "get_doc", return_value=file_doc),
			patch.object(frappe, "db", Mock()),
			patch.object(supplier, "_", side_effect=lambda value: value),
		):
			supplier.ImportSupplierInvoice.import_xml_data(doc)
		self.assertEqual(doc.prepare_data_for_import.call_args.args[1:], ("invoice.xml", "<invoice>Café</invoice>"))
		file_doc.get_content.assert_called_once_with(encodings=[])
		file_doc.get_full_path.assert_not_called()

	def test_private_evidence_keeps_ownership_and_existence_checks(self):
		doc = SimpleNamespace(doctype="Asset Movement", name="movement-1")
		file_doc = Mock()
		file_doc.exists_on_disk.return_value = True
		with patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "get_doc", return_value=file_doc):
			db.get_value.return_value = "file-1"
			service_equipment.check_private_file(doc, "service_evidence", "/private/files/evidence.pdf")
			self.assertEqual(
				db.get_value.call_args.args[1],
				{
					"file_url": "/private/files/evidence.pdf",
					"is_private": 1,
					"attached_to_doctype": doc.doctype,
					"attached_to_name": doc.name,
				},
			)
			file_doc.exists_on_disk.assert_called_once()
			file_doc.get_full_path.assert_not_called()

	def test_deletion_csv_decodes_bytes_and_checks_permissions(self):
		for content in ("doctype,name\nSales Invoice,INV-1", b"doctype,name\nSales Invoice,INV-1"):
			with self.subTest(content=type(content)):
				doc = Mock()
				file_doc = Mock(
					attached_to_doctype="Transaction Deletion Record",
					attached_to_name="deletion-1",
					file_name="import.csv",
				)
				file_doc.get_content.return_value = content
				with patch.object(frappe, "get_doc", side_effect=[doc, file_doc]):
					deletion.process_import_template("deletion-1", "/private/files/import.csv")
				doc.check_permission.assert_called_once_with("write")
				doc.import_to_delete_template_method.assert_called_once_with("doctype,name\nSales Invoice,INV-1")
				file_doc.get_full_path.assert_not_called()

	def test_deletion_rejects_files_owned_by_another_document(self):
		doc = Mock()
		file_doc = Mock(attached_to_doctype="Transaction Deletion Record", attached_to_name="other")
		with (
			patch.object(frappe, "get_doc", side_effect=[doc, file_doc]),
			patch.object(deletion, "_", side_effect=lambda value: value),
			patch.object(frappe, "throw", side_effect=ValueError("wrong owner")),
			self.assertRaisesRegex(ValueError, "wrong owner"),
		):
			deletion.process_import_template("deletion-1", "/private/files/import.csv")
		file_doc.get_content.assert_not_called()
		doc.import_to_delete_template_method.assert_not_called()

	def test_private_evidence_rejects_missing_objects(self):
		doc = SimpleNamespace(doctype="Asset Movement", name="movement-1")
		file_doc = Mock()
		file_doc.exists_on_disk.return_value = False
		with (
			patch.object(frappe, "db", Mock()),
			patch.object(frappe, "get_doc", return_value=file_doc),
			patch.object(service_equipment, "_", side_effect=lambda value: value),
			patch.object(frappe, "throw", side_effect=ValueError("missing attachment")),
			self.assertRaisesRegex(ValueError, "missing attachment"),
		):
			service_equipment.check_private_file(doc, "service_evidence", "/private/files/evidence.pdf")

	def test_reposting_overwrites_cloud_file_without_disk(self):
		file_doc = Mock(file_url="/private/files/reposting.json.gz")
		doc = SimpleNamespace(reposting_data_file=file_doc.file_url)
		with (
			patch.object(frappe, "get_doc", return_value=file_doc),
			patch.object(stock_ledger.gcs_storage, "enabled", return_value=True),
			patch("builtins.open", side_effect=AssertionError("disk access")),
		):
			self.assertEqual(stock_ledger.create_json_gz_file({"index": 2}, doc, "file-1"), doc.reposting_data_file)
		self.assertEqual(json.loads(gzip.decompress(file_doc.save_file.call_args.kwargs["content"])), {"index": 2})
		file_doc.db_update.assert_called_once()
		file_doc.delete.assert_not_called()
		file_doc.get_full_path.assert_not_called()


if __name__ == "__main__":
	unittest.main()
