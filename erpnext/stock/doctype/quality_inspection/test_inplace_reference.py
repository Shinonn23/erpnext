"""In-place repair inspections refer to real repair evidence, not stock movements."""
import unittest
from unittest.mock import Mock, patch

import frappe

from erpnext.stock.doctype.quality_inspection.quality_inspection import QualityInspection


class TestInPlaceReference(unittest.TestCase):
	def setUp(self):
		self.qi = frappe._dict(
			reference_type="Asset Repair",
			reference_name="REPAIR",
			inspection_type="Incoming",
			child_row_reference="",
			item_code="ITEM",
			company="COMPANY",
			docstatus=0,
			name="QI",
		)

	def test_reinspection_does_not_update_or_guess_a_native_stock_row(self):
		with patch.object(frappe, "qb", Mock()) as query:
			QualityInspection.set_child_row_reference(self.qi)
			QualityInspection.update_qc_reference(self.qi)
		query.assert_not_called()
