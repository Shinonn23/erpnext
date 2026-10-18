import unittest
from contextlib import ExitStack, contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import frappe

from erpnext.regulatory_affairs import transactions
from erpnext.regulatory_affairs.custom_fields import SNAPSHOT_FIELDNAMES

COVERAGE_DATE = date(2026, 8, 9)


def make_settings(**values):
	"""Settings as the shipped defaults, so a test only states what it changes."""
	return transactions.normalize_settings(values)


def make_mapping(**values):
	mapping = frappe._dict(
		registration="REG-1",
		newcode="NEW-1",
		identifier="ID-1",
		physical_manufacturer="Maker A",
		manufacturer_country=None,
		official_status="Active",
		expiry_basis="Expiry Date Specified",
		expiry_date=date(2027, 1, 1),
		in_force=True,
	)
	mapping.update(values)
	return mapping


def make_row(item_code="DEVICE-1", **values):
	row = frappe._dict(idx=1, item_code=item_code)
	row.update(values)
	return row


def make_doc(doctype="Purchase Receipt", rows=None, **values):
	doc = frappe._dict(doctype=doctype, name="DOC-1", posting_date="2026-08-09", items=rows or [make_row()])
	doc.update(values)
	return doc


def make_override(**values):
	override = frappe._dict(
		status="Approved",
		transaction_doctype="Purchase Receipt",
		transaction_name="DOC-1",
		item_code=None,
	)
	override.update(values)
	return override


def evaluate(doctype, mapping, settings=None, **row_values):
	return transactions.evaluate_row(
		doctype, make_row(**row_values), mapping, COVERAGE_DATE, settings or make_settings()
	)


class TestRowEvaluation(unittest.TestCase):
	def assert_single(self, messages, prefix):
		self.assertEqual(len(messages), 1, messages)
		self.assertTrue(messages[0].startswith(prefix), messages[0])

	def test_covering_mapping_has_no_messages(self):
		self.assertEqual(evaluate("Purchase Receipt", make_mapping()), [])

	def test_missing_mapping_follows_policy(self):
		for action, expected in (("Block", "Block:"), ("Warning", "Warning:")):
			settings = make_settings(missing_registration_action=action)
			self.assert_single(evaluate("Purchase Receipt", None, settings), expected)
		self.assertEqual(
			evaluate("Purchase Receipt", None, make_settings(missing_registration_action="Ignore")), []
		)

	def test_identifier_only_line_passes_the_newcode_rule(self):
		# A Food registration (or a migrated legacy line) has an Identifier but no NEWCODE.
		mapping = make_mapping(newcode=None, identifier="FOOD-ID-1")
		self.assertEqual(evaluate("Purchase Receipt", mapping, make_settings()), [])
		self.assertEqual(evaluate("Delivery Note", make_mapping(newcode="N", identifier=None)), [])

	def test_missing_newcode_follows_policy(self):
		mapping = make_mapping(newcode=None, identifier=None)
		for action, expected in (("Block", "Block:"), ("Warning", "Warning:")):
			settings = make_settings(missing_newcode_action=action)
			self.assert_single(evaluate("Purchase Receipt", mapping, settings), expected)
		self.assertEqual(
			evaluate("Purchase Receipt", mapping, make_settings(missing_newcode_action="Ignore")), []
		)

	def test_suspended_and_revoked_use_their_own_actions(self):
		settings = make_settings(suspended_registration_action="Warning", revoked_registration_action="Block")
		suspended = make_mapping(official_status="Suspended", in_force=False)
		revoked = make_mapping(official_status="Revoked", in_force=False)
		self.assert_single(evaluate("Purchase Receipt", suspended, settings), "Warning:")
		self.assert_single(evaluate("Purchase Receipt", revoked, settings), "Block:")

		ignored = make_settings(suspended_registration_action="Ignore", revoked_registration_action="Ignore")
		self.assertEqual(evaluate("Purchase Receipt", suspended, ignored), [])
		self.assertEqual(evaluate("Purchase Receipt", revoked, ignored), [])

	def test_other_statuses_count_as_missing(self):
		settings = make_settings(missing_registration_action="Warning", expired_registration_action="Block")
		for status in ("Cancelled", "Draft"):
			mapping = make_mapping(official_status=status, in_force=False)
			self.assert_single(evaluate("Purchase Receipt", mapping, settings), "Warning:")

	def test_expired_inbound_uses_expired_registration_action(self):
		expired = make_mapping(official_status="Expired", expiry_date=date(2026, 8, 1), in_force=False)
		lapsed_active = make_mapping(expiry_date=date(2026, 8, 1), in_force=False)
		for mapping in (expired, lapsed_active):
			for doctype in ("Purchase Receipt", "Stock Entry", "Purchase Order", "Sales Order"):
				for action, expected in (("Block", "Block:"), ("Warning", "Warning:")):
					other = "Block" if action == "Warning" else "Warning"
					settings = make_settings(
						expired_registration_action=action, expired_registration_existing_stock_policy=other
					)
					self.assert_single(evaluate(doctype, mapping, settings), expected)
			ignored = make_settings(expired_registration_action="Ignore")
			self.assertEqual(evaluate("Purchase Receipt", mapping, ignored), [])

	def test_expired_outbound_uses_existing_stock_policy(self):
		mapping = make_mapping(official_status="Expired", expiry_date=date(2026, 8, 1), in_force=False)
		for doctype in ("Delivery Note", "Sales Invoice"):
			for policy, expected in (("Block", "Block:"), ("Warning", "Warning:")):
				settings = make_settings(
					expired_registration_action="Ignore", expired_registration_existing_stock_policy=policy
				)
				self.assert_single(evaluate(doctype, mapping, settings), expected)

			allowed = make_settings(
				expired_registration_action="Block",
				expired_registration_existing_stock_policy="Allow Existing Compliant Stock",
			)
			self.assertEqual(evaluate(doctype, mapping, allowed), [])

	def test_allow_existing_stock_does_not_hide_other_problems(self):
		mapping = make_mapping(
			official_status="Expired",
			expiry_date=date(2026, 8, 1),
			in_force=False,
			newcode=None,
			identifier=None,
		)
		settings = make_settings(expired_registration_existing_stock_policy="Allow Existing Compliant Stock")
		messages = evaluate("Delivery Note", mapping, settings)
		self.assert_single(messages, "Block:")
		self.assertIn("NEWCODE or identifier", messages[0])

	def test_expiry_is_reported_once(self):
		settings = make_settings()
		for status in ("Expired", "Active", "Superseded"):
			mapping = make_mapping(official_status=status, expiry_date=date(2026, 8, 1), in_force=False)
			messages = evaluate("Purchase Receipt", mapping, settings)
			self.assertEqual(len(messages), 1, (status, messages))
			self.assertIn("2026-08-01", messages[0])

	def test_expired_status_without_a_stated_expiry_is_still_expired(self):
		mapping = make_mapping(official_status="Expired", expiry_date=None, in_force=False)
		self.assert_single(evaluate("Purchase Receipt", mapping), "Block:")

	def test_backdated_document_inside_the_validity_window_is_covered(self):
		# The daily job already moved the registration to Expired, but the document predates its expiry.
		mapping = make_mapping(official_status="Expired", expiry_date=date(2026, 12, 31), in_force=True)
		self.assertEqual(evaluate("Purchase Receipt", mapping), [])

		superseded = make_mapping(official_status="Superseded", in_force=True)
		self.assertEqual(evaluate("Delivery Note", superseded), [])

	def test_expiry_is_ignored_when_the_basis_states_none(self):
		mapping = make_mapping(expiry_basis="No Expiry Date Specified", expiry_date=date(2020, 1, 1))
		self.assertEqual(evaluate("Purchase Receipt", mapping), [])

	def test_mapping_without_in_force_flag_is_derived(self):
		active = {"registration": "REG-1", "newcode": "NEW-1", "official_status": "Active"}
		self.assertEqual(evaluate("Purchase Receipt", active), [])

		lapsed = dict(active, expiry_date="2026-08-01")
		self.assert_single(evaluate("Purchase Receipt", lapsed), "Block:")

		expired = dict(active, official_status="Expired")
		self.assert_single(evaluate("Purchase Receipt", expired), "Block:")

	def test_manufacturer_mismatch_follows_policy(self):
		mapping = make_mapping()
		for action, expected in (("Block", "Block:"), ("Warning", "Warning:")):
			settings = make_settings(manufacturer_mismatch_action=action)
			messages = evaluate("Purchase Order", mapping, settings, manufacturer="Maker B")
			self.assert_single(messages, expected)
		ignored = make_settings(manufacturer_mismatch_action="Ignore")
		self.assertEqual(evaluate("Purchase Order", mapping, ignored, manufacturer="Maker B"), [])

	def test_manufacturer_is_only_compared_when_both_sides_state_one(self):
		self.assertEqual(evaluate("Purchase Receipt", make_mapping(), manufacturer="Maker A"), [])
		self.assertEqual(evaluate("Purchase Receipt", make_mapping()), [])
		self.assertEqual(
			evaluate("Purchase Receipt", make_mapping(physical_manufacturer=None), manufacturer="Maker B"), []
		)

	def test_independent_problems_are_all_reported(self):
		mapping = make_mapping(newcode=None, identifier=None)
		messages = evaluate("Purchase Receipt", mapping, manufacturer="Maker B")
		self.assertEqual([message.split(" ")[0] for message in messages], ["Block:", "Warning:"])

	def test_item_code_is_escaped(self):
		message = evaluate("Purchase Receipt", None, item_code="A<b>")[0]
		self.assertIn("A&lt;b&gt;", message)
		self.assertNotIn("<b>", message)

	def test_message_names_row_and_item(self):
		message = evaluate("Purchase Receipt", None, item_code="DEVICE-1", idx=3)[0]
		self.assertIn("3", message)
		self.assertIn("<strong>DEVICE-1</strong>", message)

	def test_unknown_action_fails_safe_to_block(self):
		self.assertEqual(transactions.build_message("x", "Strict"), "Block: x")
		self.assertIsNone(transactions.build_message("x", "Ignore"))
		self.assertIsNone(transactions.build_message(None, "Block"))


class TestPolicyHelpers(unittest.TestCase):
	def test_normalize_settings_fills_blanks_and_keeps_explicit_zero(self):
		settings = transactions.normalize_settings(
			frappe._dict(
				validate_on_stock_entry=0, missing_registration_action="", revoked_registration_action=None
			)
		)
		self.assertEqual(settings.validate_on_stock_entry, 0)
		self.assertEqual(settings.missing_registration_action, "Block")
		self.assertEqual(settings.revoked_registration_action, "Block")
		self.assertEqual(settings.manufacturer_mismatch_action, "Warning")
		self.assertEqual(settings.expired_registration_existing_stock_policy, "Warning")

	def test_validation_switch_uses_the_scrubbed_doctype(self):
		settings = make_settings(validate_on_purchase_receipt=0, validate_on_sales_invoice=0)
		self.assertFalse(transactions.validation_enabled("Purchase Receipt", settings))
		self.assertFalse(transactions.validation_enabled("Sales Invoice", settings))
		self.assertTrue(transactions.validation_enabled("Delivery Note", settings))
		self.assertTrue(transactions.validation_enabled("Stock Entry", settings))

	def test_only_material_receipts_are_guarded_stock_entries(self):
		self.assertTrue(transactions.is_guarded_document(make_doc("Stock Entry", purpose="Material Receipt")))
		for purpose in ("Material Transfer", "Material Issue", "Manufacture", "Repack", None):
			doc = make_doc("Stock Entry", purpose=purpose)
			self.assertFalse(transactions.is_guarded_document(doc), purpose)
		self.assertTrue(transactions.is_guarded_document(make_doc("Delivery Note")))

	def test_delivery_linked_invoice_rows(self):
		self.assertTrue(transactions.is_delivery_linked("Sales Invoice", make_row(dn_detail="DNI-1")))
		self.assertTrue(transactions.is_delivery_linked("Sales Invoice", make_row(delivery_note="DN-1")))
		self.assertFalse(transactions.is_delivery_linked("Sales Invoice", make_row()))
		self.assertFalse(transactions.is_delivery_linked("Delivery Note", make_row(dn_detail="DNI-1")))

	def test_expired_action_resolution(self):
		settings = make_settings(
			expired_registration_action="Warning", expired_registration_existing_stock_policy="Block"
		)
		self.assertEqual(transactions.get_expired_action("Purchase Receipt", settings), "Warning")
		self.assertEqual(transactions.get_expired_action("Delivery Note", settings), "Block")
		settings.expired_registration_existing_stock_policy = "Allow Existing Compliant Stock"
		self.assertEqual(transactions.get_expired_action("Sales Invoice", settings), "Ignore")
		self.assertEqual(transactions.get_expired_action("Stock Entry", settings), "Warning")

	def test_items_in_scope_come_from_one_query(self):
		with patch.object(frappe, "get_all", return_value=["B", "A"]) as get_all:
			scope = transactions.get_items_in_scope(["B", "A", "A", None, ""])
		self.assertEqual(scope, {"A", "B"})
		get_all.assert_called_once_with(
			"Item",
			filters={
				"name": ("in", ["A", "B"]),
				"custom_thai_fda_applicability": "Registration Required",
			},
			pluck="name",
		)

	def test_items_in_scope_skips_the_query_without_items(self):
		with patch.object(frappe, "get_all") as get_all:
			self.assertEqual(transactions.get_items_in_scope([None, ""]), set())
		get_all.assert_not_called()


class TestCoverageDate(unittest.TestCase):
	def test_purchase_order_uses_the_row_schedule_date(self):
		doc = make_doc("Purchase Order", transaction_date="2026-01-01", schedule_date="2026-02-02")
		row = make_row(schedule_date="2026-03-03")
		self.assertEqual(transactions.get_coverage_date(doc, row), date(2026, 3, 3))

	def test_purchase_order_falls_back_to_the_required_by_then_transaction_date(self):
		doc = make_doc("Purchase Order", transaction_date="2026-01-01", schedule_date="2026-02-02")
		self.assertEqual(transactions.get_coverage_date(doc, make_row()), date(2026, 2, 2))
		doc.schedule_date = None
		self.assertEqual(transactions.get_coverage_date(doc, make_row()), date(2026, 1, 1))

	def test_sales_order_uses_the_row_delivery_date(self):
		doc = make_doc("Sales Order", transaction_date="2026-01-01", delivery_date="2026-02-02")
		row = make_row(delivery_date="2026-04-04", schedule_date="2026-05-05")
		self.assertEqual(transactions.get_coverage_date(doc, row), date(2026, 4, 4))
		self.assertEqual(transactions.get_coverage_date(doc, make_row()), date(2026, 2, 2))
		doc.delivery_date = None
		self.assertEqual(transactions.get_coverage_date(doc, make_row()), date(2026, 1, 1))

	def test_other_documents_use_the_posting_date(self):
		row = make_row(schedule_date="2026-05-05", delivery_date="2026-06-06")
		for doctype in ("Purchase Receipt", "Stock Entry", "Delivery Note", "Sales Invoice"):
			doc = make_doc(doctype, posting_date="2026-07-07", transaction_date="2026-01-01")
			self.assertEqual(transactions.get_coverage_date(doc, row), date(2026, 7, 7))

	def test_posting_date_falls_back_to_transaction_date_then_today(self):
		doc = make_doc("Delivery Note", posting_date=None, transaction_date="2026-01-01")
		self.assertEqual(transactions.get_coverage_date(doc, make_row()), date(2026, 1, 1))

		doc = make_doc("Delivery Note", posting_date=None)
		with patch.object(transactions, "today", return_value="2026-09-09"):
			self.assertEqual(transactions.get_coverage_date(doc, make_row()), date(2026, 9, 9))


class TestOverride(unittest.TestCase):
	def get_override(self, stored, doc=None, **kwargs):
		db = MagicMock()
		db.get_value.return_value = stored
		doc = doc or make_doc(regulatory_override_ref="OVR-1")
		with patch.object(frappe, "db", new=db):
			return transactions.get_override(doc, **kwargs)

	def test_no_override_is_none_and_not_looked_up(self):
		db = MagicMock()
		with patch.object(frappe, "db", new=db):
			self.assertIsNone(transactions.get_override(make_doc()))
		db.get_value.assert_not_called()

	def test_override_is_read_fresh_from_the_database(self):
		db = MagicMock()
		db.get_value.return_value = make_override()
		with patch.object(frappe, "db", new=db), patch.object(frappe, "get_cached_doc") as cached:
			override = transactions.get_override(make_doc(regulatory_override_ref="OVR-1"))
		cached.assert_not_called()
		db.get_value.assert_called_once_with(
			"Regulatory Override",
			"OVR-1",
			["status", "transaction_doctype", "transaction_name", "item_code"],
			as_dict=True,
		)
		self.assertEqual(override.name, "OVR-1")

	def test_approved_bound_override_is_accepted(self):
		self.assertEqual(self.get_override(make_override()).name, "OVR-1")

	def test_unusable_overrides_raise(self):
		cases = {
			"missing": None,
			"unapproved": make_override(status="Requested"),
			"rejected": make_override(status="Rejected"),
			"other document": make_override(transaction_name="DOC-2"),
			"other doctype": make_override(transaction_doctype="Delivery Note"),
		}
		for label, stored in cases.items():
			with (
				self.subTest(label),
				patch.object(frappe, "throw", side_effect=frappe.ValidationError) as throw,
				self.assertRaises(frappe.ValidationError),
			):
				self.get_override(stored)
			throw.assert_called_once()

	def test_non_strict_lookup_ignores_unusable_overrides(self):
		with patch.object(frappe, "throw") as throw:
			self.assertIsNone(self.get_override(make_override(status="Requested"), strict=False))
			self.assertIsNone(self.get_override(None, strict=False))
		throw.assert_not_called()

	def test_item_scope(self):
		row = make_row("DEVICE-1")
		self.assertFalse(transactions.override_covers_row(None, row))
		self.assertTrue(transactions.override_covers_row(make_override(), row))
		self.assertTrue(transactions.override_covers_row(make_override(item_code="DEVICE-1"), row))
		self.assertFalse(transactions.override_covers_row(make_override(item_code="DEVICE-2"), row))


class ValidationHarness:
	"""Runs the entry points with the database, the policy settings and the messages replaced."""

	def __init__(self, mappings, settings=None, in_scope=None, override=None, override_problem=None):
		self.mappings = mappings
		self.settings = settings or make_settings()
		self.in_scope = in_scope
		self.override = override
		self.override_problem = override_problem
		self.mapping_error = None

	def _mapping_for(self, item_code, on_date):
		if self.mapping_error:
			raise self.mapping_error
		return self.mappings.get(item_code)

	@contextmanager
	def patched(self):
		with ExitStack() as stack:
			self.get_settings = stack.enter_context(
				patch.object(transactions, "get_settings", return_value=self.settings)
			)
			self.get_scope = stack.enter_context(
				patch.object(
					transactions,
					"get_items_in_scope",
					side_effect=lambda codes: set(codes) if self.in_scope is None else set(self.in_scope),
				)
			)
			self.get_mapping = stack.enter_context(
				patch.object(
					transactions,
					"get_mapping_at_date",
					side_effect=self._mapping_for,
				)
			)
			self.get_override = stack.enter_context(
				patch.object(
					transactions, "load_override", return_value=(self.override, self.override_problem)
				)
			)
			self.msgprint = stack.enter_context(patch.object(frappe, "msgprint"))
			self.throw = stack.enter_context(
				patch.object(frappe, "throw", side_effect=frappe.ValidationError)
			)
			yield self

	def run(self, doc, entry_point=transactions.validate_transaction):
		with self.patched():
			try:
				entry_point(doc)
			except frappe.ValidationError:
				return True
		return False

	def run_with(self, doc, entry_point=transactions.validate_transaction):
		"""Run and keep the mocks inspectable afterwards."""
		with self.patched():
			try:
				entry_point(doc)
				self.raised = False
			except frappe.ValidationError:
				self.raised = True
			self.throw_calls = list(self.throw.call_args_list)
			self.msgprint_calls = list(self.msgprint.call_args_list)
			self.mapping_calls = list(self.get_mapping.call_args_list)
			self.scope_calls = list(self.get_scope.call_args_list)
			self.settings_calls = list(self.get_settings.call_args_list)
		return self


class TestValidateTransaction(unittest.TestCase):
	def test_compliant_document_passes_silently(self):
		harness = ValidationHarness({"DEVICE-1": make_mapping()}).run_with(make_doc())
		self.assertFalse(harness.raised)
		self.assertEqual(harness.msgprint_calls, [])

	def test_blocking_policy_raises_with_the_message(self):
		harness = ValidationHarness({}).run_with(make_doc())
		self.assertTrue(harness.raised)
		args, kwargs = harness.throw_calls[0]
		self.assertTrue(args[0].startswith("Block:"), args[0])
		self.assertEqual(kwargs["title"], "Regulatory requirement not met")

	def test_warning_policy_shows_an_orange_message_and_does_not_raise(self):
		settings = make_settings(missing_registration_action="Warning")
		harness = ValidationHarness({}, settings).run_with(make_doc())
		self.assertFalse(harness.raised)
		args, kwargs = harness.msgprint_calls[0]
		self.assertTrue(args[0].startswith("Warning:"), args[0])
		self.assertEqual(kwargs["indicator"], "orange")

	def test_ignore_policy_is_silent(self):
		settings = make_settings(missing_registration_action="Ignore")
		harness = ValidationHarness({}, settings).run_with(make_doc())
		self.assertFalse(harness.raised)
		self.assertEqual(harness.msgprint_calls, [])

	def test_blocks_and_warnings_are_reported_together(self):
		rows = [make_row("DEVICE-1", idx=1), make_row("DEVICE-2", idx=2, manufacturer="Maker B")]
		mappings = {"DEVICE-1": None, "DEVICE-2": make_mapping()}
		harness = ValidationHarness(mappings).run_with(make_doc(rows=rows))
		self.assertTrue(harness.raised)
		message = harness.throw_calls[0][0][0]
		self.assertIn("Block:", message)
		self.assertIn("Warning:", message)

	def test_warn_only_entry_point_never_raises(self):
		doc = make_doc("Purchase Order", transaction_date="2026-08-09")
		harness = ValidationHarness({}).run_with(doc, transactions.validate_warning_transaction)
		self.assertFalse(harness.raised)
		self.assertEqual(harness.throw_calls, [])
		self.assertEqual(len(harness.msgprint_calls), 1)
		self.assertIn("Block:", harness.msgprint_calls[0][0][0])

	def test_warn_only_entry_point_is_silent_when_compliant(self):
		harness = ValidationHarness({"DEVICE-1": make_mapping()}).run_with(
			make_doc("Sales Order", transaction_date="2026-08-09"), transactions.validate_warning_transaction
		)
		self.assertFalse(harness.raised)
		self.assertEqual(harness.msgprint_calls, [])

	def test_hook_method_argument_is_accepted(self):
		with ValidationHarness({"DEVICE-1": make_mapping()}).patched():
			transactions.validate_transaction(make_doc(), "before_submit")
			transactions.validate_warning_transaction(
				make_doc("Sales Order", transaction_date="2026-08-09"), method="validate"
			)

	def test_returns_are_not_validated(self):
		harness = ValidationHarness({}).run_with(make_doc("Delivery Note", is_return=1))
		self.assertFalse(harness.raised)
		self.assertEqual(harness.mapping_calls, [])

	def test_disabled_document_type_is_not_validated(self):
		settings = make_settings(validate_on_purchase_receipt=0)
		harness = ValidationHarness({}, settings).run_with(make_doc("Purchase Receipt"))
		self.assertFalse(harness.raised)
		self.assertEqual(harness.mapping_calls, [])
		# Other document types keep being validated.
		self.assertTrue(ValidationHarness({}, settings).run(make_doc("Delivery Note")))

	def test_stock_entry_is_validated_only_for_material_receipts(self):
		for purpose in ("Material Transfer", "Material Issue", "Repack"):
			harness = ValidationHarness({}).run_with(make_doc("Stock Entry", purpose=purpose))
			self.assertFalse(harness.raised, purpose)
			self.assertEqual(harness.mapping_calls, [])
		self.assertTrue(ValidationHarness({}).run(make_doc("Stock Entry", purpose="Material Receipt")))

	def test_invoice_rows_linked_to_a_delivery_note_are_not_validated(self):
		for linked in ({"dn_detail": "DNI-1"}, {"delivery_note": "DN-1"}):
			harness = ValidationHarness({}).run_with(make_doc("Sales Invoice", rows=[make_row(**linked)]))
			self.assertFalse(harness.raised, linked)
			self.assertEqual(harness.mapping_calls, [])
			self.assertEqual(harness.scope_calls[0][0][0], set())

		self.assertTrue(ValidationHarness({}).run(make_doc("Sales Invoice", rows=[make_row()])))

	def test_unlinked_invoice_rows_are_validated_beside_linked_ones(self):
		rows = [make_row("DEVICE-1", dn_detail="DNI-1"), make_row("DEVICE-2", idx=2)]
		harness = ValidationHarness({}).run_with(make_doc("Sales Invoice", rows=rows))
		self.assertTrue(harness.raised)
		self.assertEqual([call[0][0] for call in harness.mapping_calls], ["DEVICE-2"])

	def test_scope_is_resolved_once_for_the_whole_document(self):
		rows = [make_row("DEVICE-1", idx=1), make_row("PLAIN-1", idx=2), make_row("DEVICE-2", idx=3)]
		mappings = {"DEVICE-1": make_mapping(), "DEVICE-2": make_mapping()}
		harness = ValidationHarness(mappings, in_scope={"DEVICE-1", "DEVICE-2"}).run_with(make_doc(rows=rows))
		self.assertFalse(harness.raised)
		self.assertEqual(len(harness.scope_calls), 1)
		self.assertEqual(harness.scope_calls[0][0][0], {"DEVICE-1", "PLAIN-1", "DEVICE-2"})
		self.assertEqual([call[0][0] for call in harness.mapping_calls], ["DEVICE-1", "DEVICE-2"])

	def test_rows_without_an_item_or_out_of_scope_are_skipped(self):
		rows = [make_row(None), make_row("PLAIN-1", idx=2)]
		harness = ValidationHarness({}, in_scope=set()).run_with(make_doc(rows=rows))
		self.assertFalse(harness.raised)
		self.assertEqual(harness.mapping_calls, [])

	def test_the_mapping_of_a_repeated_item_is_resolved_once_per_date(self):
		rows = [make_row("DEVICE-1", idx=1), make_row("DEVICE-1", idx=2)]
		harness = ValidationHarness({"DEVICE-1": make_mapping()}).run_with(make_doc(rows=rows))
		self.assertEqual(len(harness.mapping_calls), 1)

	def test_the_mapping_is_resolved_on_the_row_coverage_date(self):
		rows = [
			make_row("DEVICE-1", schedule_date="2026-09-01"),
			make_row("DEVICE-1", idx=2, schedule_date="2026-10-01"),
		]
		doc = make_doc("Purchase Order", rows=rows, transaction_date="2026-08-09")
		harness = ValidationHarness({"DEVICE-1": make_mapping()}).run_with(
			doc, transactions.validate_warning_transaction
		)
		self.assertEqual(
			[call[0][1] for call in harness.mapping_calls], [date(2026, 9, 1), date(2026, 10, 1)]
		)

	def test_an_ambiguous_mapping_stops_the_document(self):
		with ValidationHarness({}).patched() as harness:
			harness.get_mapping.side_effect = frappe.ValidationError("Ambiguous regulatory mapping")
			with self.assertRaises(frappe.ValidationError):
				transactions.validate_transaction(make_doc())
			harness.throw.assert_not_called()


class TestValidateWithOverride(unittest.TestCase):
	def override_message(self, harness):
		title = "Regulatory override applied"
		return [call for call in harness.msgprint_calls if call[1].get("title") == title]

	def test_approved_override_exempts_blocking_messages(self):
		override = make_override(name="OVR-1")
		harness = ValidationHarness({}, override=override).run_with(make_doc())
		self.assertFalse(harness.raised)
		self.assertEqual(len(harness.msgprint_calls), 1)
		args, kwargs = self.override_message(harness)[0]
		self.assertIn("OVR-1", args[0])
		self.assertIn("Block:", args[0])
		self.assertEqual(kwargs["indicator"], "orange")

	def test_override_message_is_not_shown_when_nothing_needed_exempting(self):
		override = make_override(name="OVR-1")
		harness = ValidationHarness({"DEVICE-1": make_mapping()}, override=override).run_with(make_doc())
		self.assertFalse(harness.raised)
		self.assertEqual(harness.msgprint_calls, [])

	def test_item_scoped_override_exempts_only_that_item(self):
		rows = [make_row("DEVICE-1", idx=1), make_row("DEVICE-2", idx=2)]
		override = make_override(name="OVR-1", item_code="DEVICE-1")
		harness = ValidationHarness({}, override=override).run_with(make_doc(rows=rows))
		self.assertTrue(harness.raised)
		message = harness.throw_calls[0][0][0]
		self.assertIn("DEVICE-2", message)
		self.assertNotIn("DEVICE-1", message)
		exempted = self.override_message(harness)[0][0][0]
		self.assertIn("DEVICE-1", exempted)
		self.assertNotIn("DEVICE-2", exempted)

	def test_item_scoped_override_for_another_item_exempts_nothing(self):
		override = make_override(name="OVR-1", item_code="DEVICE-9")
		harness = ValidationHarness({}, override=override).run_with(make_doc())
		self.assertTrue(harness.raised)
		self.assertEqual(self.override_message(harness), [])

	def test_item_scoped_override_still_warns_about_other_rows(self):
		rows = [make_row("DEVICE-1", idx=1), make_row("DEVICE-2", idx=2, manufacturer="Maker B")]
		mappings = {"DEVICE-1": None, "DEVICE-2": make_mapping()}
		override = make_override(name="OVR-1", item_code="DEVICE-1")
		harness = ValidationHarness(mappings, override=override).run_with(make_doc(rows=rows))
		self.assertFalse(harness.raised)
		titles = [call[1]["title"] for call in harness.msgprint_calls]
		self.assertEqual(titles, ["Regulatory override applied", "Regulatory warning"])

	def test_unusable_override_stops_validation_even_for_a_compliant_document(self):
		doc = make_doc(regulatory_override_ref="OVR-1")
		for stored in (None, make_override(status="Requested"), make_override(transaction_name="OTHER")):
			db = MagicMock()
			db.get_value.return_value = stored
			with (
				patch.object(frappe, "db", new=db),
				patch.object(transactions, "get_settings", return_value=make_settings()),
				patch.object(transactions, "get_items_in_scope", return_value={"DEVICE-1"}),
				patch.object(transactions, "get_mapping_at_date", return_value=make_mapping()),
				patch.object(frappe, "throw", side_effect=frappe.ValidationError),
				self.assertRaises(frappe.ValidationError),
			):
				transactions.validate_transaction(doc)

	def test_override_is_not_looked_up_when_validation_does_not_apply(self):
		doc = make_doc("Stock Entry", purpose="Material Transfer", regulatory_override_ref="OVR-1")
		db = MagicMock()
		with patch.object(frappe, "db", new=db), patch.object(transactions, "get_settings"):
			transactions.validate_transaction(doc)
		db.get_value.assert_not_called()


def snapshot_values(row):
	return {fieldname: row.get(fieldname) for fieldname in SNAPSHOT_FIELDNAMES}


class SnapshotHarness:
	def __init__(self, mappings, in_scope=None, override=None, countries=None, delivery_rows=None):
		self.mappings = mappings
		self.in_scope = in_scope
		self.override = override
		self.countries = countries or {}
		self.delivery_rows = delivery_rows or {}

	def lookup(self, doctype, name, fields=None, **kwargs):
		if doctype == "Manufacturer":
			return self.countries.get(name)
		if doctype == "Delivery Note Item":
			return self.delivery_rows.get(name)
		raise AssertionError(f"Unexpected lookup of {doctype}")

	def run(self, doc):
		db = MagicMock()
		db.get_value.side_effect = self.lookup
		with ExitStack() as stack:
			stack.enter_context(patch.object(frappe, "db", new=db))
			self.get_scope = stack.enter_context(
				patch.object(
					transactions,
					"get_items_in_scope",
					side_effect=lambda codes: set(codes) if self.in_scope is None else set(self.in_scope),
				)
			)
			self.get_mapping = stack.enter_context(
				patch.object(
					transactions,
					"get_mapping_at_date",
					side_effect=lambda item_code, on_date: self.mappings.get(item_code),
				)
			)
			stack.enter_context(patch.object(transactions, "get_override", return_value=self.override))
			self.log_error = stack.enter_context(patch.object(frappe, "log_error"))
			self.throw = stack.enter_context(
				patch.object(frappe, "throw", side_effect=frappe.ValidationError)
			)
			transactions.capture_transaction_snapshot(doc)
		self.db = db
		return doc


class TestSnapshot(unittest.TestCase):
	def test_snapshot_records_the_nine_fields(self):
		mapping = make_mapping(physical_manufacturer="Maker A", identifier="ID-9")
		doc = make_doc(rows=[make_row()])
		harness = SnapshotHarness({"DEVICE-1": mapping}, countries={"Maker A": "Japan"})
		harness.run(doc)

		self.assertEqual(
			snapshot_values(doc["items"][0]),
			{
				"regulatory_registration": "REG-1",
				"regulatory_newcode": "NEW-1",
				"regulatory_identifier": "ID-9",
				"regulatory_manufacturer": "Maker A",
				"regulatory_manufacturer_country": "Japan",
				"regulatory_registration_status": "Active",
				"regulatory_registration_expiry": date(2027, 1, 1),
				"regulatory_coverage_date": date(2026, 8, 9),
				"regulatory_override": None,
			},
		)
		harness.get_mapping.assert_called_once_with("DEVICE-1", date(2026, 8, 9))

	def test_manufacturer_country_falls_back_to_the_registration_line(self):
		doc = make_doc()
		SnapshotHarness({"DEVICE-1": make_mapping(manufacturer_country="China")}).run(doc)
		self.assertEqual(doc["items"][0].regulatory_manufacturer_country, "China")

	def test_manufacturer_country_is_looked_up_once_per_manufacturer(self):
		doc = make_doc(rows=[make_row(), make_row(idx=2)])
		harness = SnapshotHarness({"DEVICE-1": make_mapping()}, countries={"Maker A": "Japan"})
		harness.run(doc)
		lookups = [call for call in harness.db.get_value.call_args_list if call[0][0] == "Manufacturer"]
		self.assertEqual(len(lookups), 1)
		self.assertEqual(lookups[0][0], ("Manufacturer", "Maker A", "country"))

	def test_expiry_is_not_recorded_when_the_registration_states_none(self):
		mapping = make_mapping(expiry_basis="No Expiry Date Specified", expiry_date=date(2020, 1, 1))
		doc = make_doc()
		SnapshotHarness({"DEVICE-1": mapping}).run(doc)
		self.assertIsNone(doc["items"][0].regulatory_registration_expiry)

	def test_no_mapping_leaves_only_the_coverage_date(self):
		doc = make_doc()
		SnapshotHarness({}).run(doc)
		expected = dict.fromkeys(SNAPSHOT_FIELDNAMES)
		expected["regulatory_coverage_date"] = date(2026, 8, 9)
		self.assertEqual(snapshot_values(doc["items"][0]), expected)

	def test_snapshot_replaces_stale_values_when_the_mapping_is_gone(self):
		row = make_row(regulatory_registration="OLD", regulatory_newcode="OLD-1")
		SnapshotHarness({}).run(make_doc(rows=[row]))
		self.assertIsNone(row.regulatory_registration)
		self.assertIsNone(row.regulatory_newcode)

	def test_ambiguous_mapping_is_logged_and_left_blank(self):
		doc = make_doc()
		harness = SnapshotHarness({})
		harness.mappings = MagicMock()
		harness.mappings.get.side_effect = frappe.ValidationError("Ambiguous regulatory mapping")
		harness.run(doc)

		harness.log_error.assert_called_once()
		self.assertEqual(harness.log_error.call_args.kwargs["reference_doctype"], "Purchase Receipt")
		self.assertEqual(harness.log_error.call_args.kwargs["reference_name"], "DOC-1")
		harness.throw.assert_not_called()
		expected = dict.fromkeys(SNAPSHOT_FIELDNAMES)
		expected["regulatory_coverage_date"] = date(2026, 8, 9)
		self.assertEqual(snapshot_values(doc["items"][0]), expected)

	def test_ambiguity_is_logged_once_per_item_and_date(self):
		doc = make_doc(rows=[make_row(), make_row(idx=2)])
		harness = SnapshotHarness({})
		harness.mappings = MagicMock()
		harness.mappings.get.side_effect = frappe.ValidationError("Ambiguous regulatory mapping")
		harness.run(doc)
		harness.log_error.assert_called_once()

	def test_coverage_date_follows_the_document_type(self):
		mapping = {"DEVICE-1": make_mapping()}
		po = make_doc(
			"Purchase Order",
			rows=[make_row(schedule_date="2026-09-01")],
			transaction_date="2026-08-01",
			posting_date=None,
		)
		so = make_doc(
			"Sales Order",
			rows=[make_row(delivery_date="2026-10-01")],
			transaction_date="2026-08-01",
			posting_date=None,
		)
		dn = make_doc("Delivery Note", posting_date="2026-11-01")
		for doc, expected in ((po, date(2026, 9, 1)), (so, date(2026, 10, 1)), (dn, date(2026, 11, 1))):
			SnapshotHarness(mapping).run(doc)
			self.assertEqual(doc["items"][0].regulatory_coverage_date, expected, doc.doctype)

	def test_returns_are_still_snapshotted(self):
		doc = make_doc("Delivery Note", is_return=1)
		SnapshotHarness({"DEVICE-1": make_mapping()}).run(doc)
		self.assertEqual(doc["items"][0].regulatory_registration, "REG-1")

	def test_snapshot_is_not_limited_by_the_validation_switches(self):
		# The switches only govern validation; the snapshot is a record, not a check.
		doc = make_doc("Purchase Receipt")
		with patch.object(transactions, "get_settings", side_effect=AssertionError("settings read")):
			SnapshotHarness({"DEVICE-1": make_mapping()}).run(doc)
		self.assertEqual(doc["items"][0].regulatory_registration, "REG-1")

	def test_non_receipt_stock_entries_are_not_snapshotted(self):
		doc = make_doc("Stock Entry", purpose="Material Transfer")
		harness = SnapshotHarness({"DEVICE-1": make_mapping()})
		harness.run(doc)
		self.assertIsNone(doc["items"][0].get("regulatory_registration"))
		harness.get_scope.assert_not_called()

		receipt = make_doc("Stock Entry", purpose="Material Receipt")
		SnapshotHarness({"DEVICE-1": make_mapping()}).run(receipt)
		self.assertEqual(receipt["items"][0].regulatory_registration, "REG-1")

	def test_rows_out_of_scope_are_untouched(self):
		rows = [make_row("PLAIN-1"), make_row("DEVICE-1", idx=2)]
		harness = SnapshotHarness({"DEVICE-1": make_mapping()}, in_scope={"DEVICE-1"})
		doc = harness.run(make_doc(rows=rows))
		self.assertNotIn("regulatory_coverage_date", doc["items"][0])
		self.assertEqual(doc["items"][1].regulatory_registration, "REG-1")
		self.assertEqual(len(harness.get_scope.call_args_list), 1)

	def test_snapshot_without_regulated_rows_does_nothing(self):
		harness = SnapshotHarness({}, in_scope=set())
		harness.run(make_doc())
		harness.get_mapping.assert_not_called()

	def test_override_is_recorded_on_the_rows_it_covers(self):
		rows = [make_row("DEVICE-1", idx=1), make_row("DEVICE-2", idx=2)]
		mappings = {"DEVICE-1": make_mapping(), "DEVICE-2": make_mapping()}
		override = make_override(name="OVR-1", item_code="DEVICE-1")
		doc = SnapshotHarness(mappings, override=override).run(make_doc(rows=rows))
		self.assertEqual(doc["items"][0].regulatory_override, "OVR-1")
		self.assertIsNone(doc["items"][1].regulatory_override)

	def test_document_wide_override_is_recorded_on_every_regulated_row(self):
		rows = [make_row("DEVICE-1", idx=1), make_row("DEVICE-2", idx=2), make_row("PLAIN-1", idx=3)]
		override = make_override(name="OVR-1")
		harness = SnapshotHarness({}, in_scope={"DEVICE-1", "DEVICE-2"}, override=override)
		doc = harness.run(make_doc(rows=rows))
		self.assertEqual([row.get("regulatory_override") for row in doc["items"]], ["OVR-1", "OVR-1", None])

	def test_unusable_override_does_not_block_or_get_recorded(self):
		doc = make_doc(regulatory_override_ref="OVR-1", rows=[make_row()])
		db = MagicMock()
		db.get_value.return_value = make_override(status="Requested")
		with (
			patch.object(frappe, "db", new=db),
			patch.object(transactions, "get_items_in_scope", return_value={"DEVICE-1"}),
			patch.object(transactions, "get_mapping_at_date", return_value=make_mapping()),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
		):
			transactions.capture_transaction_snapshot(doc, "before_submit")
		self.assertEqual(doc["items"][0].regulatory_registration, "REG-1")
		self.assertIsNone(doc["items"][0].regulatory_override)


class TestInvoiceSnapshotInheritance(unittest.TestCase):
	DELIVERY_SNAPSHOT = frappe._dict(
		regulatory_registration="REG-DN",
		regulatory_newcode="NEW-DN",
		regulatory_identifier="ID-DN",
		regulatory_manufacturer="Maker DN",
		regulatory_manufacturer_country="Japan",
		regulatory_registration_status="Active",
		regulatory_registration_expiry=date(2026, 12, 31),
		regulatory_coverage_date=date(2026, 6, 1),
		regulatory_override="OVR-DN",
	)

	def test_invoice_row_inherits_the_delivery_note_snapshot(self):
		doc = make_doc("Sales Invoice", rows=[make_row(dn_detail="DNI-1", delivery_note="DN-1")])
		delivery_rows = {"DNI-1": self.DELIVERY_SNAPSHOT}
		harness = SnapshotHarness({"DEVICE-1": make_mapping()}, delivery_rows=delivery_rows)
		harness.run(doc)

		self.assertEqual(snapshot_values(doc["items"][0]), dict(self.DELIVERY_SNAPSHOT))
		harness.get_mapping.assert_not_called()
		harness.db.get_value.assert_called_once_with(
			"Delivery Note Item", "DNI-1", list(SNAPSHOT_FIELDNAMES), as_dict=True
		)

	def test_invoice_rows_are_resolved_when_the_delivery_was_never_snapshotted(self):
		unsnapshotted = frappe._dict(dict.fromkeys(SNAPSHOT_FIELDNAMES))
		for delivery_rows in ({"DNI-1": unsnapshotted}, {}):
			doc = make_doc("Sales Invoice", rows=[make_row(dn_detail="DNI-1")])
			harness = SnapshotHarness({"DEVICE-1": make_mapping()}, delivery_rows=delivery_rows)
			harness.run(doc)
			self.assertEqual(doc["items"][0].regulatory_registration, "REG-1")
			self.assertEqual(doc["items"][0].regulatory_coverage_date, date(2026, 8, 9))

	def test_only_delivery_note_detail_links_are_inherited(self):
		doc = make_doc("Sales Invoice", rows=[make_row(delivery_note="DN-1")])
		harness = SnapshotHarness({"DEVICE-1": make_mapping()})
		harness.run(doc)
		self.assertEqual(doc["items"][0].regulatory_registration, "REG-1")
		self.assertEqual([call[0][0] for call in harness.db.get_value.call_args_list], ["Manufacturer"])

	def test_other_documents_never_inherit(self):
		doc = make_doc("Delivery Note", rows=[make_row(dn_detail="DNI-1")])
		delivery_rows = {"DNI-1": self.DELIVERY_SNAPSHOT}
		harness = SnapshotHarness({"DEVICE-1": make_mapping()}, delivery_rows=delivery_rows)
		harness.run(doc)
		self.assertEqual(doc["items"][0].regulatory_registration, "REG-1")

	def test_out_of_scope_invoice_rows_do_not_look_up_the_delivery_note(self):
		doc = make_doc("Sales Invoice", rows=[make_row("PLAIN-1", dn_detail="DNI-1")])
		harness = SnapshotHarness({}, in_scope=set())
		harness.run(doc)
		harness.db.get_value.assert_not_called()


class TestBuildSnapshot(unittest.TestCase):
	def test_snapshot_keys_are_the_custom_field_names(self):
		self.assertEqual(
			tuple(transactions.build_snapshot(make_mapping(), COVERAGE_DATE)), tuple(SNAPSHOT_FIELDNAMES)
		)
		self.assertEqual(len(SNAPSHOT_FIELDNAMES), 9)

	def test_no_mapping_keeps_the_coverage_date_and_override(self):
		snapshot = transactions.build_snapshot(None, COVERAGE_DATE, "OVR-1", "Japan")
		blank = {k: v for k, v in snapshot.items() if v}
		self.assertEqual(blank, {"regulatory_coverage_date": COVERAGE_DATE, "regulatory_override": "OVR-1"})


class TestHardening(unittest.TestCase):
	"""Order entry never blocks; consolidated POS invoices are not guarded; submit still stops."""

	def test_consolidated_sales_invoice_is_not_validated(self):
		doc = make_doc(doctype="Sales Invoice", is_consolidated=1)
		harness = ValidationHarness({}).run_with(doc)
		self.assertFalse(harness.raised)
		self.assertEqual(harness.msgprint_calls, [])
		self.assertEqual(harness.settings_calls, [])

	def test_order_entry_reports_an_unusable_override_without_raising(self):
		problem = "Regulatory Override OVR-9 is not approved."
		harness = ValidationHarness({"DEVICE-1": make_mapping()}, override_problem=problem).run_with(
			make_doc(doctype="Sales Order", transaction_date="2026-08-09"),
			entry_point=transactions.validate_warning_transaction,
		)
		self.assertFalse(harness.raised)
		self.assertEqual(harness.throw_calls, [])
		self.assertIn("OVR-9", harness.msgprint_calls[0][0][0])
		self.assertIn("Warning:", harness.msgprint_calls[0][0][0])

	def test_submit_still_raises_for_an_unusable_override_even_with_compliant_rows(self):
		problem = "Regulatory Override OVR-9 is not approved."
		harness = ValidationHarness({"DEVICE-1": make_mapping()}, override_problem=problem).run_with(
			make_doc()
		)
		self.assertTrue(harness.raised)
		self.assertIn("OVR-9", harness.throw_calls[0][0][0])

	def test_order_entry_downgrades_an_ambiguous_mapping_to_a_warning(self):
		harness = ValidationHarness({})
		harness.mapping_error = frappe.ValidationError("Ambiguous regulatory mapping")
		with patch.object(frappe, "clear_last_message") as clear:
			harness.run_with(
				make_doc(doctype="Purchase Order", transaction_date="2026-08-09"),
				entry_point=transactions.validate_warning_transaction,
			)
		self.assertFalse(harness.raised)
		clear.assert_called_once()
		self.assertIn("Ambiguous regulatory mapping", harness.msgprint_calls[0][0][0])
		self.assertEqual(harness.throw_calls, [])

	def test_submit_propagates_an_ambiguous_mapping(self):
		harness = ValidationHarness({})
		harness.mapping_error = frappe.ValidationError("Ambiguous regulatory mapping")
		with patch.object(frappe, "clear_last_message") as clear:
			harness.run_with(make_doc())
		self.assertTrue(harness.raised)
		clear.assert_not_called()
