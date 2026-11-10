# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.utils import getdate, nowdate, today

from erpnext.assets.doctype.asset_maintenance.asset_maintenance import calculate_next_due_date


class AssetMaintenanceLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		actions_performed: DF.TextEditor | None
		amended_from: DF.Link | None
		asset_maintenance: DF.Link | None
		asset_name: DF.ReadOnly | None
		assign_to_name: DF.ReadOnly | None
		certificate_attachement: DF.Attach | None
		completion_date: DF.Date | None
		description: DF.ReadOnly | None
		due_date: DF.Date | None
		has_certificate: DF.Check
		item_code: DF.ReadOnly | None
		item_name: DF.ReadOnly | None
		maintenance_status: DF.Literal["Planned", "Completed", "Cancelled", "Overdue"]
		maintenance_type: DF.ReadOnly | None
		maintenance_visit: DF.Link | None
		naming_series: DF.Literal["ACC-AML-.YYYY.-"]
		periodicity: DF.Data | None
		service_equipment: DF.Link | None
		task: DF.Link | None
		task_assignee_email: DF.Data | None
		task_name: DF.Data | None
	# end: auto-generated types

	def validate(self):
		if getdate(self.due_date) < getdate(nowdate()) and self.maintenance_status not in [
			"Completed",
			"Cancelled",
		]:
			self.maintenance_status = "Overdue"

		if self.maintenance_status == "Completed" and not self.completion_date:
			frappe.throw(_("Please select Completion Date for Completed Asset Maintenance Log"))

		if self.maintenance_status != "Completed" and self.completion_date:
			frappe.throw(_("Please select Maintenance Status as Completed or remove Completion Date"))

	def on_submit(self):
		if self.maintenance_status not in ["Completed", "Cancelled"]:
			frappe.throw(_("Maintenance Status has to be Cancelled or Completed to Submit"))
		self.update_maintenance_task()
		if self.service_equipment:
			from erpnext.support.service_equipment import add_activity

			add_activity(
				self.service_equipment,
				_("{0} {1}: {2}").format(
					_(self.maintenance_type or ""), _(self.maintenance_status), self.task_name
				),
				self,
			)

	def update_maintenance_task(self):
		asset_maintenance_doc = frappe.get_doc("Asset Maintenance Task", self.task)
		if self.maintenance_status == "Completed":
			if asset_maintenance_doc.last_completion_date != self.completion_date:
				next_due_date = calculate_next_due_date(
					periodicity=self.periodicity, last_completion_date=self.completion_date
				)
				asset_maintenance_doc.last_completion_date = self.completion_date
				asset_maintenance_doc.next_due_date = next_due_date
				asset_maintenance_doc.maintenance_status = "Planned"
				asset_maintenance_doc.save()
		if self.maintenance_status == "Cancelled":
			asset_maintenance_doc.maintenance_status = "Cancelled"
			asset_maintenance_doc.save()
		asset_maintenance_doc = frappe.get_doc("Asset Maintenance", self.asset_maintenance)
		asset_maintenance_doc.save()


def complete_from_visit(visit, method=None):
	"""A submitted Maintenance Visit completes the open log of each equipment row it did (PM or Calibration, Done):
	the log of that type due soonest is submitted as Completed on the visit date."""
	kinds = {"PM": "Preventive Maintenance", "Calibration": "Calibration"}
	for row in visit.get("purposes") or []:
		kind = kinds.get(row.get("service_type"))
		if not row.get("service_equipment") or not kind or row.get("service_result") != "Done":
			continue
		name = frappe.db.get_value(
			"Asset Maintenance Log",
			{
				"service_equipment": row.service_equipment,
				"maintenance_type": kind,
				"docstatus": 0,
				"maintenance_status": ("in", ["Planned", "Overdue"]),
			},
			"name",
			order_by="due_date asc",
		)
		if not name:
			continue
		log = frappe.get_doc("Asset Maintenance Log", name)
		log.maintenance_status = "Completed"
		log.completion_date = visit.mntc_date
		log.maintenance_visit = visit.name
		log.actions_performed = row.get("work_done") or log.actions_performed
		log.flags.ignore_permissions = True
		log.submit()


def update_asset_maintenance_log_status():
	AssetMaintenanceLog = DocType("Asset Maintenance Log")
	(
		frappe.qb.update(AssetMaintenanceLog)
		.set(AssetMaintenanceLog.maintenance_status, "Overdue")
		.where(
			(AssetMaintenanceLog.maintenance_status == "Planned") & (AssetMaintenanceLog.due_date < today())
		)
	).run()


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_maintenance_tasks(doctype, txt, searchfield, start, page_len, filters):
	asset_maintenance_tasks = frappe.db.get_values(
		"Asset Maintenance Task", {"parent": filters.get("asset_maintenance")}, "maintenance_task"
	)
	return asset_maintenance_tasks
