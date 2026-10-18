"""No site/test-suite imports: native immediate journal uses server-only identity trust."""
import unittest
from unittest.mock import Mock,patch
import frappe
from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry,is_trusted_system_generation
from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion

class TestImmediateOrigin(unittest.TestCase):
 def fixture(self):
  asset=Mock(name='Asset',net_purchase_amount=36000,company='CD',stock_to_asset_conversion='Conversion');asset.name='Asset';asset.precision.return_value=None
  journal=Mock(accounts=[frappe._dict(),frappe._dict()],flags=frappe._dict());journal.mark_as_system_generated.side_effect=lambda:JournalEntry.mark_as_system_generated(journal);return asset,journal
 def generate(self,status=1,company='CD'):
  asset,journal=self.fixture()
  def insert():
   self.assertTrue(is_trusted_system_generation(journal));self.assertTrue(journal.journal_entry_reason);self.assertEqual(journal.accounts[0].debit,journal.accounts[1].credit)
  journal.insert.side_effect=insert
  with patch('erpnext.assets.doctype.asset.asset.make_journal_entry',return_value=journal),patch.object(conversion.frappe,'get_doc',return_value=frappe._dict(name='Conversion',company=company,docstatus=status)),patch.object(conversion,'_',side_effect=lambda x:x),patch.object(conversion.frappe,'throw',side_effect=frappe.ValidationError):
   result=conversion.create_immediate_depreciation_entry(asset,'2026-10-16')
  return result
 def test_native_generator_trusted_before_insert_and_submits_balanced_reasoned_journal(self):
  journal=self.generate();self.assertTrue(is_trusted_system_generation(journal));self.assertEqual(journal.accounts[0].debit,36000);self.assertIn('Conversion',journal.journal_entry_reason);journal.insert.assert_called_once_with();journal.submit.assert_called_once_with()
 def test_draft_and_foreign_company_source_still_rejected(self):
  for status,company in [(0,'CD'),(1,'UB')]:
   with self.subTest(status=status,company=company),self.assertRaises(frappe.ValidationError):self.generate(status,company)
 def test_client_system_flag_and_forged_token_do_not_create_trust(self):
  journal=Mock(flags=frappe._dict(system_generation_token='forged'),is_system_generated=1);self.assertFalse(is_trusted_system_generation(journal));journal.flags.system_generation_token=None;self.assertFalse(is_trusted_system_generation(journal))
if __name__=='__main__':unittest.main()
