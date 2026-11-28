from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.borrowing import policy


class TestBorrowingPolicy(TestCase):
	def test_site_actor_denial_does_not_fall_back_to_document_write(self):
		doc = frappe._dict(doctype="Material Request")
		provider = Mock(return_value=False)
		with (
			patch.object(frappe, "get_hooks", return_value=[provider]),
			patch.object(frappe, "has_permission", return_value=True) as has_permission,
		):
			self.assertFalse(policy.actor_allowed("prepare_release", doc, "user@example.test"))
		provider.assert_called_once_with(action="prepare_release", doc=doc, user="user@example.test")
		has_permission.assert_not_called()

	def test_workflow_uses_its_configured_field_and_distinct_rejection_states(self):
		workflow = frappe._dict(
			workflow_state_field="review_stage",
			states=[
				frappe._dict(state=name, doc_status=status)
				for name, status in (("Entered", 0), ("Review", 0), ("Rework", 0), ("Posted", 1))
			],
			transitions=[
				frappe._dict(action=action, next_state=state)
				for action, state in (("Review", "Review"), ("Reject", "Rework"), ("Reject", "Entered"))
			],
		)
		with (
			patch("frappe.model.workflow.get_workflow_name", return_value="Loan workflow"),
			patch.object(frappe, "get_cached_doc", return_value=workflow),
			patch.object(frappe, "get_hooks", return_value=[]),
		):
			self.assertEqual(
				policy.workflow_state_rules("Stock to Asset Conversion", "release_conversion_reservation"),
				("review_stage", {"Rework"}),
			)
			self.assertTrue(policy.may_edit_demo_acquisition(frappe._dict(review_stage="Entered")))
			self.assertTrue(policy.may_edit_demo_acquisition(frappe._dict(review_stage="Rework")))
			self.assertFalse(policy.may_edit_demo_acquisition(frappe._dict(review_stage="Review")))
