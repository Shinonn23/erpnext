"""Optional site policies for native Borrowing and Demo transactions."""

import frappe
from frappe.utils import cint


def actor_allowed(action, doc, user=None):
	"""Evaluate a Borrowing action after the caller checks its document and transaction prerequisites."""
	user = user or frappe.session.user
	for provider in frappe.get_hooks("borrowing_actor_policies"):
		decision = frappe.call(provider, action=action, doc=doc, user=user)
		if decision is not None:
			return bool(decision)
	return bool(doc and frappe.has_permission(doc.doctype, "write", doc=doc, user=user))


def workflow_state_rules(doctype, purpose):
	"""Return the workflow field and draft states for a specific business operation."""
	from frappe.model.workflow import get_workflow_name

	name = get_workflow_name(doctype)
	workflow = frappe.get_cached_doc("Workflow", name) if name else None
	field = workflow.workflow_state_field if workflow else "workflow_state"
	states = set()
	if workflow:
		draft_states = {row.state for row in workflow.states if cint(row.doc_status) == 0}
		initial_state = workflow.states[0].state if workflow.states else None
		rejected, other = set(), {initial_state}
		for transition in workflow.transitions:
			targets = rejected if (transition.action or "").strip().casefold() == "reject" else other
			targets.add(transition.next_state)
		states = (rejected - other) & draft_states
		if purpose == "edit_demo_acquisition" and initial_state in draft_states:
			states.add(initial_state)
	for provider in frappe.get_hooks("borrowing_workflow_state_policies"):
		states.update(frappe.call(provider, doctype=doctype, purpose=purpose, field=field) or ())
	return field, states


def may_edit_demo_acquisition(doc):
	field, states = workflow_state_rules("Material Request", "edit_demo_acquisition")
	return not doc.get(field) or doc.get(field) in states


def has_administrator_override(user, hook):
	user = user or frappe.session.user
	if providers := frappe.get_hooks(hook):
		return any(frappe.call(provider, user=user) for provider in providers)
	if user != "Administrator":
		return False
	if any(
		frappe.flags.get(flag)
		for flag in ("in_migrate", "in_install", "in_patch", "in_import", "in_setup_wizard")
	):
		return False
	job = getattr(frappe.local, "job", None)
	return (
		not job
		or job.get("method") != "frappe.core.doctype.scheduled_job_type.scheduled_job_type.run_scheduled_job"
	)


def is_demo_superuser(user=None):
	return has_administrator_override(user, "borrowing_demo_superuser")


def extension_approver_roles():
	return set(frappe.get_hooks("borrowing_demo_extension_roles"))


def sync_request_tasks(doc):
	for hook in frappe.get_hooks("borrowing_demo_request_changed"):
		frappe.call(hook, doc=doc)


def asset_item_plan(stock_item, request_type):
	for hook in frappe.get_hooks("borrowing_demo_asset_item_plan"):
		result = frappe.call(hook, stock_item=stock_item, request_type=request_type)
		if result is not None:
			return result


def prepare_asset_item(request, source, asset_item_code, asset_category):
	for hook in frappe.get_hooks("borrowing_demo_prepare_asset_item"):
		result = frappe.call(
			hook,
			request=request,
			source=source,
			asset_item_code=asset_item_code,
			asset_category=asset_category,
		)
		if result is not None:
			return result


def asset_location_defaults():
	locations = {}
	for hook in frappe.get_hooks("borrowing_demo_asset_locations"):
		locations.update(frappe.call(hook) or {})
	return locations
