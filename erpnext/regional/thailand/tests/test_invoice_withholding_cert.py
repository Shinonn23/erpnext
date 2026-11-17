"""A Purchase Invoice of an agent makes a Withholding Tax Cert for each third-party recipient it withheld from
(17 Nov 2026): form of the recipient, dated wht_paid_on, the invoice's supplier as the agent; cancel cancels."""

import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext import hooks
from erpnext.regional.thailand.custom import withholding_cert as certificates


class Certificate(frappe._dict):
	def append(self, field, value):
		self.setdefault(field, []).append(frappe._dict(value))


def invoice(**values):
	doc = frappe._dict(
		doctype="Purchase Invoice",
		name="PINV-1",
		company="Company",
		supplier="iDo Shipping",
		posting_date="2026-11-10",
		taxes=[
			frappe._dict(
				add_deduct_tax="Deduct",
				wht_recipient="WFS-PG Cargo",
				wht_paid_on="2026-11-05",
				is_tax_withholding_account=1,
				account_head="2132-02",
				rate=3,
				base_tax_amount=58.71,
				base_net_amount=99999,
				wht_base_amount=1957,
			),
			frappe._dict(
				add_deduct_tax="Deduct",
				wht_recipient="Mr Thanakit",
				wht_paid_on=None,
				is_tax_withholding_account=0,
				account_head="2132-02",
				rate=1,
				base_tax_amount=15,
				base_net_amount=0,
			),
			frappe._dict(add_deduct_tax="Add", account_head="1154-00", base_tax_amount=136.99),
			frappe._dict(add_deduct_tax="Deduct", account_head="2132-02", base_tax_amount=63),
		],
	)
	doc.update(values)
	return doc


SUPPLIERS = {
	"WFS-PG Cargo": frappe._dict(supplier_type="Company", tax_withholding_category=None),
	"Mr Thanakit": frappe._dict(supplier_type="Individual", tax_withholding_category=None),
}


class TestInvoiceRecipients(unittest.TestCase):
	def test_only_deduct_rows_naming_a_recipient_on_a_withholding_account(self):
		with patch.object(certificates, "withholding_accounts", return_value={"2132-02"}):
			rows = certificates.invoice_recipient_rows(invoice())
		self.assertEqual([row.wht_recipient for row in rows], ["WFS-PG Cargo", "Mr Thanakit"])
		with patch.object(certificates, "withholding_accounts", return_value=set()):
			rows = certificates.invoice_recipient_rows(invoice())
		self.assertEqual([row.wht_recipient for row in rows], ["WFS-PG Cargo"])

	def certificate(self, row):
		cert = Certificate(flags=frappe._dict(), withholding_tax_items=[])

		def get_value(doctype, name, fields=None, as_dict=False):
			if doctype == "Supplier" and isinstance(fields, list) and "supplier_type" in fields:
				return SUPPLIERS[name]
			if doctype == "Supplier" and fields == "supplier_name":
				return "iDo Shipping Co."
			return frappe._dict(supplier_name=name, supplier_primary_address=f"{name}-Billing")

		with (
			patch.object(frappe, "new_doc", return_value=cert),
			patch.object(frappe, "db", Mock(get_value=Mock(side_effect=get_value))),
			patch.object(certificates, "company_address_of", return_value="Company Address"),
		):
			return certificates.invoice_certificate(invoice(), row if isinstance(row, list) else [row])

	def test_company_recipient_is_pnd53_dated_paid_on_with_the_agent(self):
		cert, lines = self.certificate(invoice().taxes[0])
		self.assertEqual(cert.income_tax_form, "PND53")
		self.assertEqual(cert.date, "2026-11-05")
		self.assertEqual((cert.is_agent, cert.withholding_agent_for), (1, "iDo Shipping"))
		self.assertEqual(cert.supplier, "WFS-PG Cargo")
		self.assertEqual((cert.voucher_type, cert.voucher_no), ("Purchase Invoice", "PINV-1"))
		item = cert.withholding_tax_items[0]
		self.assertEqual((item.tax_base, item.tax_amount, item.tax_rate), (1957, 58.71, 3))

	def test_individual_recipient_is_pnd3_and_falls_back_to_the_posting_date(self):
		cert, _lines = self.certificate(invoice().taxes[1])
		self.assertEqual(cert.income_tax_form, "PND3")
		self.assertEqual(cert.date, "2026-11-10")
		self.assertAlmostEqual(cert.withholding_tax_items[0].tax_base, 1500)

	def test_base_is_never_the_invoice_net_total(self):
		row = invoice().taxes[0]
		row.wht_base_amount = None
		row.rate = 0
		_cert, lines = self.certificate(row)
		line = next(iter(lines.values()))
		self.assertEqual(line["tax_base"], 0)
		self.assertTrue(line["no_base"])

	def test_rows_of_one_recipient_and_date_share_a_certificate_other_dates_get_their_own(self):
		doc = invoice()
		second = frappe._dict(doc.taxes[0], base_tax_amount=30, wht_base_amount=1000)
		later = frappe._dict(doc.taxes[0], wht_paid_on="2026-11-07")
		doc.taxes += [second, later]
		with patch.object(certificates, "withholding_accounts", return_value={"2132-02"}):
			groups = certificates.recipient_groups(doc)
		self.assertEqual(len(groups[("WFS-PG Cargo", "2026-11-05")]), 2)
		self.assertEqual(len(groups[("WFS-PG Cargo", "2026-11-07")]), 1)
		_cert, lines = self.certificate(groups[("WFS-PG Cargo", "2026-11-05")])
		line = lines[("", 3)]
		self.assertEqual((line["tax_base"], round(line["tax_amount"], 2)), (2957, 88.71))

	def test_purchase_invoice_is_a_voucher_and_hooks_make_and_cancel(self):
		self.assertIn("Purchase Invoice", certificates.VOUCHER_TYPES)
		events = hooks._thailand_doc_events["Purchase Invoice"]
		self.assertEqual(
			events["on_submit"],
			"erpnext.regional.thailand.custom.withholding_cert.create_invoice_withholding_certificates",
		)
		self.assertIn(
			"erpnext.regional.thailand.custom.withholding_cert.cancel_withholding_certificates",
			events["on_cancel"],
		)


if __name__ == "__main__":
	unittest.main()
