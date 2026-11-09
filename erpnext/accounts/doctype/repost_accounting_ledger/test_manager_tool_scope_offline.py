"""Accounting tools keep source and company permissions when made available to accounting managers."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.opening_invoice_creation_tool import opening_invoice_creation_tool as opening
from erpnext.accounts.doctype.repost_accounting_ledger import repost_accounting_ledger as repost


class TestManagerRepostScope(TestCase):
	def setUp(self):
		self.enterContext(patch.object(repost, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
		self.enterContext(
			patch.object(
				repost, "get_allowed_types_from_settings", return_value={"Purchase Receipt", "Journal Entry"}
			)
		)
		self.enterContext(patch.object(frappe, "flags", frappe._dict()))
		self.enterContext(patch.object(frappe, "session", frappe._dict(user="accounting@example.com")))
		self.source = frappe._dict(
			doctype="Purchase Receipt",
			name="PR-1",
			company="Company A",
			docstatus=1,
			posting_date="2026-11-01",
			check_permission=Mock(),
		)
		self.company = frappe._dict(check_permission=Mock())
		self.sources = {("Purchase Receipt", "PR-1"): self.source, ("Company", "Company A"): self.company}
		self.enterContext(patch.object(frappe, "get_doc", side_effect=self.get_doc))
		self.doc = object.__new__(repost.RepostAccountingLedger)
		self.doc.__dict__.update(
			doctype="Repost Accounting Ledger",
			name="REPOST-1",
			owner="another@example.com",
			company="Company A",
			docstatus=1,
			vouchers=[frappe._dict(voucher_type="Purchase Receipt", voucher_no="PR-1")],
			scheduled_job=None,
			reload=Mock(),
			check_permission=Mock(),
			db_set=Mock(),
		)
		self.sources[(self.doc.doctype, self.doc.name)] = self.doc

	def get_doc(self, doctype, name):
		if (doctype, name) not in self.sources:
			raise frappe.DoesNotExistError(name)
		return self.sources[(doctype, name)]

	def test_read_only_receipt_access_can_rebuild_without_warehouse_write(self):
		def source_permission(permission):
			if permission != "read":
				raise frappe.PermissionError("Warehouse writes are not allowed")

		self.source.check_permission.side_effect = source_permission
		self.doc.validate_voucher_access()
		self.source.check_permission.assert_called_once_with("read")
		self.company.check_permission.assert_called_once_with("read")

	def test_source_payroll_and_company_scope_are_enforced(self):
		self.source.check_permission.side_effect = frappe.PermissionError("Private payroll source")
		with self.assertRaises(frappe.PermissionError):
			self.doc.validate_voucher_access()
		self.source.check_permission.side_effect = None
		self.source.company = "Foreign Company"
		with self.assertRaises(frappe.ValidationError):
			self.doc.validate_voucher_access()
		self.doc.company = None
		foreign = frappe._dict(check_permission=Mock(side_effect=frappe.PermissionError))
		self.sources[("Company", "Foreign Company")] = foreign
		with self.assertRaises(frappe.PermissionError):
			self.doc.validate_voucher_access()

	def test_missing_source_is_not_treated_as_a_submitted_voucher(self):
		self.doc.vouchers[0].voucher_no = "MISSING"
		with self.assertRaises(frappe.DoesNotExistError):
			self.doc.validate_vouchers_are_submitted()

	def test_preview_and_start_recheck_sources_before_reading_gl_or_enqueuing(self):
		self.source.check_permission.side_effect = frappe.PermissionError
		with patch.object(self.doc, "get_existing_ledger_entries") as read_gl:
			with self.assertRaises(frappe.PermissionError):
				self.doc.generate_preview_data()
		read_gl.assert_not_called()
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value="Queued"))),
			patch.object(repost, "_enqueue_repost") as enqueue,
		):
			with self.assertRaises(frappe.PermissionError):
				self.doc.start_repost()
		enqueue.assert_not_called()
		self.doc.db_set.assert_not_called()

	def test_background_job_rechecks_current_permission_before_any_ledger_write(self):
		self.doc.check_permission.side_effect = frappe.PermissionError
		with (
			patch.object(repost, "_lock_vouchers") as lock,
			patch.object(repost, "_record_repost_failure"),
		):
			with self.assertRaises(frappe.PermissionError):
				repost.repost(self.doc.name, commit=False)
		lock.assert_not_called()
		self.doc.check_permission.assert_called_once_with("write")
		self.doc.db_set.assert_not_called()

	def test_native_source_update_repost_runs_for_editor_without_manual_tool_rights(self):
		from erpnext.controllers.accounts_controller import AccountsController

		self.doc.owner = frappe.session.user
		self.doc.flags = frappe._dict()
		self.doc.insert = Mock()
		self.doc.check_permission.side_effect = lambda permission: (
			None if self.doc.flags.ignore_permissions else self.deny_manual_tool(permission)
		)
		self.doc.submit = Mock(side_effect=self.doc.start_repost)
		self.doc.delete_cancelled_entries = 0
		self.doc.vouchers = []
		self.doc.append = lambda field, values: self.doc.vouchers.append(frappe._dict(values))
		self.source.unlock = Mock()
		with (
			patch.object(frappe, "new_doc", return_value=self.doc),
			patch.object(
				frappe, "db", Mock(get_value=Mock(return_value="Queued"), get_all=Mock(return_value=[]))
			),
			patch.object(frappe, "msgprint"),
			patch.object(frappe, "publish_progress"),
			patch.object(repost, "is_scheduler_inactive", return_value=False),
			patch.object(repost, "create_job_id", side_effect=lambda name: name),
			patch.object(repost, "_enqueue_repost") as enqueue,
			patch.object(self.doc, "validate_for_deferred_accounting"),
			patch.object(repost, "_lock_vouchers", return_value={("Purchase Receipt", "PR-1"): self.source}),
			patch.object(repost, "_repost_vouchers") as rebuild,
		):
			AccountsController.repost_accounting_entries(self.source)
			enqueue.assert_called_once_with(self.doc.name)
			self.doc.insert.assert_called_once_with()
			self.doc.submit.assert_called_once_with()
			# A worker loads a fresh document: the bypass from the native hook is gone.
			self.doc.flags = frappe._dict()
			row = self.doc.vouchers[0]
			row.db_set = lambda values: row.update(values)
			repost.repost(self.doc.name, commit=False)
			rebuild.assert_called_once_with(self.source, 0)
			self.assertEqual(row.status, "Reposted")
		self.source.check_permission.assert_any_call("write")
		self.company.check_permission.assert_called_with("read")

	@staticmethod
	def deny_manual_tool(permission):
		raise frappe.PermissionError("No manual repost tool access")

	def test_automatic_worker_fallback_is_limited_to_owned_single_source_with_current_write(self):
		self.doc.check_permission.side_effect = self.deny_manual_tool
		for case in ("other_owner", "multiple_sources", "draft", "revoked_read", "revoked_write"):
			with self.subTest(case=case):
				self.doc.owner = frappe.session.user
				self.doc.docstatus = 1
				self.doc.vouchers = [frappe._dict(voucher_type="Purchase Receipt", voucher_no="PR-1")]
				self.source.check_permission.side_effect = None
				if case == "other_owner":
					self.doc.owner = "another@example.com"
				elif case == "multiple_sources":
					self.doc.vouchers *= 2
				elif case == "draft":
					self.doc.docstatus = 0
				else:
					denied = case.removeprefix("revoked_")
					self.source.check_permission.side_effect = lambda permission: (
						self.deny_manual_tool(permission) if permission == denied else None
					)
				with (
					patch.object(repost, "_lock_vouchers") as lock,
					patch.object(repost, "_record_repost_failure"),
				):
					with self.assertRaises(frappe.PermissionError):
						repost.repost(self.doc.name, commit=False)
				lock.assert_not_called()

	def test_source_editor_cannot_manually_start_repost_even_when_owning_single_source(self):
		self.doc.owner = frappe.session.user
		self.doc.check_permission.side_effect = self.deny_manual_tool
		with patch.object(repost, "_enqueue_repost") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				self.doc.start_repost()
		enqueue.assert_not_called()
		self.source.check_permission.assert_not_called()

	def test_queued_repost_rechecks_revoked_source_permission(self):
		self.source.check_permission.side_effect = frappe.PermissionError
		with (
			patch.object(repost, "_lock_vouchers") as lock,
			patch.object(repost, "_record_repost_failure"),
		):
			with self.assertRaises(frappe.PermissionError):
				repost.repost(self.doc.name, commit=False)
		lock.assert_not_called()
		self.doc.db_set.assert_not_called()

	def test_forged_request_fields_cannot_start_a_stored_foreign_repost(self):
		self.doc.reload.side_effect = lambda: setattr(self.doc, "company", "Foreign Company")

		def check_permission(ptype):
			if self.doc.company != "Company A":
				raise frappe.PermissionError()

		self.doc.check_permission.side_effect = check_permission
		with (
			patch.object(frappe, "db", Mock()) as db,
			patch.object(repost, "_enqueue_repost") as enqueue,
		):
			with self.assertRaises(frappe.PermissionError):
				self.doc.start_repost()
		self.doc.reload.assert_called_once_with()
		db.get_value.assert_not_called()
		enqueue.assert_not_called()
		self.doc.db_set.assert_not_called()

	def test_any_closed_voucher_is_refused_even_when_another_voucher_is_newer(self):
		old = frappe._dict(self.source, name="PR-OLD", posting_date="2026-01-01")
		self.sources[("Purchase Receipt", old.name)] = old
		with patch.object(frappe, "db", Mock(get_all=Mock(return_value=["2026-06-30"]))):
			for names in ([old.name, self.source.name], [self.source.name, old.name]):
				self.doc.vouchers = [
					frappe._dict(voucher_type="Purchase Receipt", voucher_no=name) for name in names
				]
				with self.subTest(order=names), self.assertRaises(frappe.ValidationError):
					self.doc.validate_for_closed_fiscal_year()

	def test_blank_company_still_checks_each_sources_actual_company_closing(self):
		self.doc.company = None
		other = frappe._dict(self.source, name="PR-OTHER", company="Company B", posting_date="2026-05-01")
		self.sources[("Purchase Receipt", other.name)] = other
		self.sources[("Company", "Company B")] = self.company
		self.doc.vouchers.append(frappe._dict(voucher_type="Purchase Receipt", voucher_no=other.name))

		def closing(_dt, *, filters, **kwargs):
			return ["2026-06-30"] if filters["company"] == "Company B" else []

		with patch.object(frappe, "db", Mock(get_all=Mock(side_effect=closing))):
			self.doc.validate_voucher_access()
			with self.assertRaises(frappe.ValidationError):
				self.doc.validate_for_closed_fiscal_year()

	def test_cancelled_source_remains_skippable_by_queued_repost(self):
		self.source.docstatus = 2
		with patch.object(frappe, "db", Mock()) as db:
			self.doc.validate_for_closed_fiscal_year()
		db.get_all.assert_not_called()


class TestManagerOpeningInvoiceScope(TestCase):
	def tool(self, **values):
		doc = object.__new__(opening.OpeningInvoiceCreationTool)
		doc.__dict__.update(
			doctype="Opening Invoice Creation Tool", company="Company A", invoice_type="Sales"
		)
		doc.__dict__.update(values)
		return doc

	def test_summary_uses_permission_aware_company_and_invoice_queries(self):
		def get_list(doctype, **kwargs):
			self.assertEqual(kwargs["limit_page_length"], 0)
			if doctype == "Company":
				return [frappe._dict(company="Company A", currency="THB")]
			self.assertEqual(kwargs["filters"]["company"], ["in", ["Company A"]])
			return [frappe._dict(company="Company A", total_invoices=1, outstanding_amount=100)]

		with (
			patch.object(frappe, "get_list", side_effect=get_list) as query,
			patch.object(frappe, "get_all", side_effect=AssertionError("Permission bypass")),
		):
			summary, _ = self.tool().get_opening_invoice_summary()
		self.assertEqual(set(summary), {"Company A"})
		self.assertEqual(
			[call.args[0] for call in query.call_args_list], ["Company", "Sales Invoice", "Purchase Invoice"]
		)

	def test_denied_company_stops_creation_before_invoice_rows_are_loaded(self):
		company = frappe._dict(check_permission=Mock(side_effect=frappe.PermissionError))
		doc = self.tool(check_permission=Mock(), get_invoices=Mock())
		with (
			patch.object(frappe, "get_doc", return_value=company),
			patch.object(frappe, "has_permission", return_value=True),
		):
			with self.assertRaises(frappe.PermissionError):
				doc.make_invoices()
		company.check_permission.assert_called_once_with("read")
		doc.get_invoices.assert_not_called()

	def test_singleton_does_not_show_temporary_account_of_a_denied_selected_company(self):
		doc = self.tool(set_onload=Mock(), get_opening_invoice_summary=Mock(return_value=({}, {})))
		with (
			patch.object(frappe, "has_permission", return_value=False),
			patch.object(opening, "get_temporary_opening_account") as temporary,
		):
			doc.onload()
		temporary.assert_not_called()
		doc.set_onload.assert_any_call("temporary_opening_account", None)

	def test_temporary_account_endpoint_checks_company_and_account_visibility(self):
		company = frappe._dict(check_permission=Mock(side_effect=frappe.PermissionError))
		with (
			patch.object(frappe, "get_doc", return_value=company),
			patch.object(frappe, "get_list", return_value=[frappe._dict(name="Temporary")]) as query,
		):
			with self.assertRaises(frappe.PermissionError):
				opening.get_temporary_opening_account("Foreign Company")
			query.assert_not_called()
			company.check_permission.side_effect = None
			self.assertEqual(opening.get_temporary_opening_account("Company A"), "Temporary")
			query.assert_called_once_with(
				"Account", filters={"company": "Company A", "account_type": "Temporary"}, limit_page_length=1
			)
