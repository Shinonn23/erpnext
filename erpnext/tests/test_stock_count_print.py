"""Render worksheet branches without a site, database writes, or stock fixtures."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import frappe
from bs4 import BeautifulSoup
from jinja2 import FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment


class PrintDocument(frappe._dict):
	def get_formatted(self, fieldname):
		value = self.get(fieldname)
		return "" if value is None else str(value)

	def get_countable_dimensions(self):
		return self.get("countable_dimensions", [])


class TestStockCountPrint(unittest.TestCase):
	def setUp(self):
		self.fullname = Mock(side_effect=lambda user: f"Full name of {user}")
		self.env = SandboxedEnvironment(loader=FileSystemLoader(Path(frappe.__file__).parent))
		self.env.globals.update(
			_=lambda value: value or "",
			frappe=SimpleNamespace(
				utils=SimpleNamespace(get_url=lambda: "https://example.test"),
				get_fullname=lambda user: self.fullname(user),
			),
		)
		self.template = self.env.from_string(
			(
				Path(__file__).resolve().parents[1]
				/ "stock/print_format/stock_count_audit/stock_count_audit.html"
			).read_text()
		)
		self.doc = PrintDocument(
			doctype="Stock Count",
			name="STOCK-COUNT-TEST",
			docstatus=0,
			meta=frappe._dict(is_submittable=True),
			company="บริษัท ทดสอบ",
			warehouse="Warehouse A",
			count_type="Annual",
			snapshot_datetime="2026-10-15 10:00:00",
			owner="document-owner",
			modified_by="last-editor",
			items=[
				PrintDocument(
					idx=1,
					item_code="ITEM-1",
					item_name="สินค้า",
					batch_no="BATCH-1",
					expiry_date="2027-01-02",
					storage_location="SHELF-A",
					serial_no="",
					system_qty=5,
					counted_qty=0,
					difference_qty=-5,
					is_counted=0,
				)
			],
		)

	def render(self, **options):
		args = dict(
			doc=self.doc,
			letter_head="<div>Company letterhead</div>",
			no_letterhead=False,
			footer="Company footer",
			print_settings=frappe._dict(repeat_header_footer=1, add_draft_heading=1),
		)
		args.update(options)
		return BeautifulSoup(self.template.render(**args), "html.parser")

	def test_uncounted_cells_are_blank_but_a_counted_zero_is_printed(self):
		soup = self.render()
		cells = [cell.get_text(strip=True) for cell in soup.select(".count-items tbody td")]
		self.assertEqual(cells[-3:], ["5", "", ""])
		self.doc.get("items")[0].is_counted = 1
		cells = [cell.get_text(strip=True) for cell in self.render().select(".count-items tbody td")]
		self.assertEqual(cells[-3:], ["5", "0", "-5"])

	def test_saved_expiry_and_all_count_identities_are_printed(self):
		self.doc.countable_dimensions = [
			frappe._dict(source_fieldname="storage_location", dimension_name="Storage Location"),
			frappe._dict(source_fieldname="project", dimension_name="Project"),
		]
		row = self.doc.get("items")[0]
		row.update(serial_no="SERIAL-1", project="PROJECT-A", item_name="<script>unsafe</script>")
		soup = self.render()
		cells = [cell.get_text(" ", strip=True) for cell in soup.select(".count-items tbody td")]
		self.assertEqual(cells[2:5], ["BATCH-1", "2027-01-02", "SERIAL-1"])
		self.assertIn("SHELF-A", cells[5])
		self.assertIn("Project: PROJECT-A", cells[5])
		self.assertIn("<script>unsafe</script>", cells[1])
		self.assertIsNone(soup.select_one(".count-items script"))

	def test_annual_records_each_actual_actor_and_monthly_only_stock_approval(self):
		self.doc.docstatus = 1
		for prefix, actor in (
			("counted", "counter"),
			("stock_approved", "stock"),
			("accounts_approved", "accounts"),
			("final_approved", "final"),
		):
			self.doc[f"{prefix}_by"] = actor
			self.doc[f"{prefix}_on"] = "2026-10-15 11:22:33"
		soup = self.render()
		text = soup.get_text(" ", strip=True)
		for actor in ("counter", "stock", "accounts", "final"):
			self.assertIn(f"Full name of {actor}", text)
		self.assertEqual(text.count("2026-10-15 11:22:33"), 4)
		self.assertIn("APPROVED / SUBMITTED", text)
		self.assertNotIn("Pending", text)
		self.assertNotIn("Historical approval details not recorded", text)
		self.doc.count_type = "Monthly"
		text = self.render().get_text(" ", strip=True)
		self.assertIn("Full name of stock", text)
		self.assertNotIn("Accounts Approval", text)
		self.assertNotIn("Full name of accounts", text)
		self.assertNotIn("Final Approval", text)
		self.assertNotIn("Full name of final", text)

	def test_legacy_submission_never_infers_actors_from_owner_or_editor(self):
		self.doc.docstatus = 1
		soup = self.render()
		self.assertEqual(soup.get_text().count("Historical approval details not recorded"), 3)
		self.assertEqual(soup.select_one(".approval-details td").get_text(strip=True), "")
		self.fullname.assert_not_called()
		self.assertNotIn("document-owner", soup.get_text())
		self.assertNotIn("last-editor", soup.get_text())

	def test_draft_status_pending_approvals_native_header_footer_and_auditor_lines(self):
		self.doc.workflow_state = "Stock Count Pending Accounts Approval"
		soup = self.render()
		text = soup.get_text(" ", strip=True)
		self.assertIn("DRAFT / UNAPPROVED", text)
		self.assertIn(self.doc.workflow_state, text)
		self.assertEqual(text.count("Pending"), 4)  # Three actors plus workflow state.
		self.assertIn("STOCK-COUNT-TEST", soup.select_one(".print-heading").get_text())
		self.assertIn("Company letterhead", text)
		self.assertIn("Company footer", soup.select_one("#footer-html").get_text())
		self.assertIsNone(soup.select_one("#header-html"))
		self.assertIsNotNone(soup.select_one(".count-items thead"))
		self.assertIsNotNone(soup.select_one("#footer-html .page"))
		self.assertIsNotNone(soup.select_one("#footer-html .topage"))
		auditor = soup.select_one(".auditor-signature").get_text(" ", strip=True)
		for label in ("Auditor", "Name:", "Signature:", "Date:"):
			self.assertIn(label, auditor)
		self.assertNotIn("Full name of", auditor)
		self.doc.docstatus = 2
		soup = self.render(no_letterhead=True)
		self.assertEqual(soup.select_one(".count-status").get_text(strip=True), "CANCELLED")
		self.assertNotIn("Company letterhead", soup.get_text())
		self.assertNotIn("Company footer", soup.get_text())
