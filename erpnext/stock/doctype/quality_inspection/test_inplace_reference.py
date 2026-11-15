"""In-place repair inspections refer to real confirmation evidence, not stock movements."""
import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.quality_inspection.quality_inspection import QualityInspection


class TestInPlaceReference(unittest.TestCase):
	def setUp(self):
		self.allocation = frappe._dict(name="A1", serial_no="SN1", batch_no="B1", quantity=1)
		self.step = frappe._dict(name="STEP", docstatus=1, stage="Confirm Current Location", item_code="ITEM",
			company="COMPANY", treatment_case="CASE", allocations=[self.allocation], check_permission=Mock())
		self.qi = frappe._dict(reference_type="Quality Treatment Step", reference_name="STEP", inspection_type="Incoming",
			child_row_reference="A1", item_code="ITEM", item_serial_no="SN1", batch_no="B1",
			purchase_unit_qty=1, quality_treatment_case="CASE", company="COMPANY",
			get_doc_before_save=lambda: None)

	def test_valid_confirmation_is_read_checked_and_used_as_authoritative_company(self):
		self.qi.company = None
		with patch.object(frappe, "get_doc", return_value=self.step):
			QualityInspection.validate_treatment_step_reference(self.qi)
		self.step.check_permission.assert_called_once_with("read")
		self.assertEqual(self.qi.company, "COMPANY")

	def test_altered_serial_or_company_is_rejected(self):
		for field, value in (("item_serial_no", "OTHER"), ("company", "OTHER"), ("inspection_type", "Outgoing")):
			with self.subTest(field=field):
				original = self.qi[field]
				self.qi[field] = value
				with (patch.object(frappe, "get_doc", return_value=self.step),
					patch.object(frappe, "throw", side_effect=frappe.ValidationError),
					self.assertRaises(frappe.ValidationError)):
					QualityInspection.validate_treatment_step_reference(self.qi)
				self.qi[field] = original

	def test_reinspection_does_not_update_or_guess_a_native_stock_row(self):
		with patch.object(frappe, "qb", Mock()) as query:
			QualityInspection.set_child_row_reference(self.qi)
			QualityInspection.update_qc_reference(self.qi)
		query.assert_not_called()
