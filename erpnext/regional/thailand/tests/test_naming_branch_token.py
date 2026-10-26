"""Offline tests of the BRANCH naming series token."""

import unittest
from contextlib import ExitStack
from unittest import mock

import frappe

from erpnext.regional.thailand.custom import company_branch, custom_api, naming, naming_branch


def address(name, code="", address_type="Office"):
	return frappe._dict(
		name=name, address_type=address_type, branch_code=code, is_primary_address=0, disabled=0
	)


def fake_frappe(addresses, docs=None):
	"""A stand-in for frappe: `addresses` are the company's, `docs` maps (doctype, name) to field values."""
	fake = mock.MagicMock()
	by_name = {a.name: a for a in addresses}
	docs = docs or {}

	def get_all(doctype, filters=None, order_by=None, **kwargs):
		return sorted(addresses, key=lambda a: a.name)

	def get_value(doctype, name, fieldname=None, as_dict=False):
		if doctype == "Address":
			row = by_name.get(name)
			return row.branch_code if row else None
		if (doctype, name) in docs:
			return docs[(doctype, name)].get(fieldname)
		return None

	fake.db.get_all.side_effect = get_all
	fake.db.get_value.side_effect = get_value
	return fake


def patched(addresses, docs=None, default=None):
	"""Patch frappe in the modules involved; `default` is the company's default (primary) address."""
	stack = ExitStack()
	fake = fake_frappe(list(addresses), docs)
	for module in (company_branch, custom_api, naming_branch):
		stack.enter_context(mock.patch.object(module, "frappe", fake))
	stack.enter_context(mock.patch.object(naming_branch, "get_default_address", lambda *a, **kw: default))
	return stack


def token(doc, addresses=(), docs=None, default=None):
	with patched(addresses, docs, default):
		return naming.parse_naming_series_variable(doc, "BRANCH")


HEAD = address("CD-Head", "00000", "Billing")
B3 = address("CD-B3", "00003")
B7 = address("CD-B7", "7")


class TestBranchToken(unittest.TestCase):
	def test_sales_invoice_company_address(self):
		doc = frappe._dict(doctype="Sales Invoice", company="CD", company_address="CD-B3")
		self.assertEqual(token(doc, [HEAD, B3]), "00003")

	def test_short_code_is_padded(self):
		doc = frappe._dict(doctype="Sales Order", company="CD", company_address="CD-B7")
		self.assertEqual(token(doc, [HEAD, B7]), "00007")

	def test_blank_single_address(self):
		doc = frappe._dict(doctype="Sales Invoice", company="CD", company_address="")
		self.assertEqual(token(doc, [B3]), "00003")

	def test_blank_several_addresses_uses_head_office(self):
		doc = frappe._dict(doctype="Sales Invoice", company="CD")
		self.assertEqual(token(doc, [B3, HEAD, B7]), "00000")

	def test_purchase_order_uses_billing_address(self):
		doc = frappe._dict(
			doctype="Purchase Order", company="CD", billing_address="CD-B3", company_address="CD-Head"
		)
		self.assertEqual(token(doc, [HEAD, B3]), "00003")

	def test_payment_entry_via_references(self):
		doc = frappe._dict(
			doctype="Payment Entry",
			company="CD",
			references=[frappe._dict(reference_doctype="Sales Invoice", reference_name="SINV-1")],
		)
		docs = {("Sales Invoice", "SINV-1"): {"company_address": "CD-B3"}}
		self.assertEqual(token(doc, [HEAD, B3], docs), "00003")

	def test_payment_entry_without_references_uses_head_office(self):
		doc = frappe._dict(doctype="Payment Entry", company="CD", references=[])
		self.assertEqual(token(doc, [HEAD, B3]), "00000")

	def test_resolve_returns_address_name(self):
		doc = frappe._dict(doctype="Journal Entry", company="CD")
		with patched([HEAD, B3]):
			self.assertEqual(naming_branch.resolve_branch_address(doc), "CD-Head")

	def test_blank_address_follows_the_default_address_validate_uses(self):
		doc = frappe._dict(doctype="Sales Invoice", company="CD")
		self.assertEqual(token(doc, [HEAD, B3], default="CD-B3"), "00003")
		doc = frappe._dict(doctype="Purchase Order", company="CD")
		self.assertEqual(token(doc, [HEAD, B3], default="CD-B3"), "00003")
		with patched([HEAD, B3], default="CD-B3"):
			self.assertEqual(naming_branch.resolve_branch_address(doc), "CD-B3")

	def test_payment_entry_single_reference_beats_its_field(self):
		doc = frappe._dict(
			doctype="Payment Entry",
			company="CD",
			company_tax_address="CD-B7",
			references=[frappe._dict(reference_doctype="Sales Invoice", reference_name="SINV-1")],
		)
		docs = {("Sales Invoice", "SINV-1"): {"company_address": "CD-B3"}}
		self.assertEqual(token(doc, [HEAD, B3, B7], docs), "00003")

	def test_payment_entry_field_without_references(self):
		doc = frappe._dict(doctype="Payment Entry", company="CD", company_tax_address="CD-B7", references=[])
		self.assertEqual(token(doc, [HEAD, B3, B7]), "00007")

	def test_certificate_company_address(self):
		doc = frappe._dict(doctype="Withholding Tax Cert", company="CD", company_address="CD-B3")
		self.assertEqual(token(doc, [HEAD, B3]), "00003")

	def test_certificate_blank_address_uses_its_voucher_branch(self):
		doc = frappe._dict(
			doctype="Withholding Tax Cert",
			company="CD",
			voucher_type="Payment Entry",
			voucher_no="PE-1",
		)
		docs = {("Payment Entry", "PE-1"): {"company_tax_address": "CD-B7"}}
		self.assertEqual(token(doc, [HEAD, B3, B7], docs), "00007")

	def test_certificate_blank_without_voucher_uses_head_office(self):
		doc = frappe._dict(doctype="Withholding Tax Cert", company="CD")
		self.assertEqual(token(doc, [B3, HEAD]), "00000")

	def test_real_naming_series_parser(self):
		"""ABBR.BRANCH.-.SI.YY-BE.MM-BE.### through frappe's own parser; the counter is per branch and month."""
		from frappe.model.naming import parse_naming_series

		hooks = {
			"BRANCH": "erpnext.regional.thailand.custom.naming.parse_naming_series_variable",
			"ABBR": "x",
			"YY-BE": "x",
			"MM-BE": "x",
		}
		parsers = {
			"ABBR": lambda doc, e: "CD",
			"YY-BE": lambda doc, e: str(doc.posting_date.year + 543)[-2:],
			"MM-BE": lambda doc, e: "%02d" % doc.posting_date.month,
		}
		keys = []

		def counter(key, digits):
			keys.append(key)
			return "1".zfill(digits)

		def get_attr(method):
			return lambda doc, e: (
				naming.parse_naming_series_variable(doc, e) if e == "BRANCH" else parsers[e](doc, e)
			)

		import datetime

		doc = frappe._dict(
			doctype="Sales Invoice",
			company="CD",
			company_address="CD-B3",
			posting_date=datetime.date(2026, 10, 5),
		)
		with (
			patched([HEAD, B3]),
			mock.patch("frappe.model.naming.has_custom_parser", lambda e: hooks.get(e)),
			mock.patch("frappe.model.naming.now_datetime", datetime.datetime.now),
			mock.patch("frappe.model.naming.frappe.get_attr", get_attr),
		):
			series = "ABBR.BRANCH.-.SI.YY-BE.MM-BE.###"
			name = parse_naming_series(series, doc=doc, number_generator=counter)
			doc.company_address = "CD-Head"
			other = parse_naming_series(series, doc=doc, number_generator=counter)
		self.assertEqual(name, "CD00003-SI6910001")
		self.assertEqual(keys, ["CD00003-SI6910", "CD00000-SI6910"])
		self.assertEqual(other, "CD00000-SI6910001")

	def test_no_doc_or_unknown_doctype(self):
		self.assertEqual(token(None), "00000")
		self.assertEqual(token(frappe._dict()), "00000")
		self.assertEqual(token(frappe._dict(doctype="ToDo", company="CD"), [B3]), "00000")

	def test_invalid_code(self):
		bad = address("CD-Bad", "ABC")
		doc = frappe._dict(doctype="Sales Invoice", company="CD", company_address="CD-Bad")
		self.assertEqual(token(doc, [HEAD, bad]), "00000")

	def test_never_raises(self):
		doc = frappe._dict(doctype="Sales Invoice", company="CD", company_address="CD-B3")
		boom = mock.MagicMock()
		boom.db.get_value.side_effect = RuntimeError("db down")
		boom.db.get_all.side_effect = RuntimeError("db down")
		with ExitStack() as stack:
			for module in (company_branch, custom_api, naming_branch):
				stack.enter_context(mock.patch.object(module, "frappe", boom))
			self.assertEqual(naming.parse_naming_series_variable(doc, "BRANCH"), "00000")
			self.assertEqual(naming_branch.resolve_branch_address(doc), "CD-B3")
			boom.log_error.assert_called_with(title="Branch naming token")
