# License: GNU General Public License v3. See license.txt

"""Claimed landed cost of a vendor invoice (Landed Cost Voucher). Offline: the claim arithmetic and the
checks of validate_vendor_invoices run on stand-ins, no database."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.landed_cost_voucher import landed_cost_voucher as lcv_module
from erpnext.stock.doctype.landed_cost_voucher.landed_cost_voucher import (
	LandedCostVoucher,
	get_new_claimed_amount,
)


class FakeRow(SimpleNamespace):
	def precision(self, fieldname):
		return 2


def make_voucher(rows, taxes, docstatus=0, unclaimed=None):
	voucher = SimpleNamespace(
		vendor_invoices=[
			FakeRow(idx=i + 1, vendor_invoice=name, amount=amount) for i, (name, amount) in enumerate(rows)
		],
		total_taxes_and_charges=taxes,
		total_vendor_invoices_cost=sum(amount for _name, amount in rows),
		docstatus=docstatus,
		precision=lambda fieldname: 2,
		get_vendor_invoice_amount=lambda name: {"amount": (unclaimed or {}).get(name, 0.0)},
	)
	voucher.get = lambda key: getattr(voucher, key, None)
	voucher.get_claims_by_vendor_invoice = lambda: LandedCostVoucher.get_claims_by_vendor_invoice(voucher)
	return voucher


def plain_flt(value, precision=None):
	# frappe.utils.flt reads the System Settings rounding method, which needs a site
	return round(float(value or 0), precision if precision is not None else 9)


class TestLandedCostClaims(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(lcv_module, "flt", plain_flt)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_two_vouchers_add_up_and_a_cancel_takes_only_its_own(self):
		claimed = get_new_claimed_amount(0, 3000, 1)  # first voucher
		claimed = get_new_claimed_amount(claimed, 2000, 1)  # second voucher
		self.assertEqual(claimed, 5000)
		self.assertEqual(get_new_claimed_amount(claimed, 3000, 2), 2000)  # first is cancelled

	def test_a_cancel_never_goes_below_zero(self):
		self.assertEqual(get_new_claimed_amount(1000, 3000, 2), 0)
		self.assertEqual(get_new_claimed_amount(None, 3000, 2), 0)

	def test_claims_are_summed_for_each_invoice(self):
		voucher = make_voucher([("PI-1", 100.0), ("PI-2", 50.0)], 150.0)
		self.assertEqual(
			LandedCostVoucher.get_claims_by_vendor_invoice(voucher), {"PI-1": 100.0, "PI-2": 50.0}
		)

	def validate(self, voucher):
		def throw(message, *args, **kwargs):
			raise frappe.ValidationError(message)

		with (
			patch.object(lcv_module, "_", lambda text: text),
			patch.object(lcv_module.frappe, "throw", throw),
			patch.object(lcv_module.frappe, "bold", lambda text: text),
		):
			LandedCostVoucher.validate_vendor_invoices(voucher)

	def test_charges_within_the_invoices_pass(self):
		self.validate(make_voucher([("PI-1", 100.0)], 100.0, unclaimed={"PI-1": 100.0}))

	def test_no_vendor_invoice_is_not_checked(self):
		self.validate(make_voucher([], 500.0))

	def test_charges_above_the_invoices_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.validate(make_voucher([("PI-1", 100.0)], 120.0, unclaimed={"PI-1": 100.0}))

	def test_amount_above_the_unclaimed_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.validate(make_voucher([("PI-1", 100.0)], 100.0, unclaimed={"PI-1": 60.0}))

	def test_the_same_invoice_twice_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.validate(make_voucher([("PI-1", 50.0), ("PI-1", 50.0)], 100.0, unclaimed={"PI-1": 100.0}))
