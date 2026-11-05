"""Remove the Regulatory Override (Siwat, 5 Nov 2026: no bypass of the registration check at all).

Schema only: deletes the override link fields (`regulatory_override_ref` on the seven guarded documents,
`regulatory_override` on the six item rows), the Regulatory Override records and DocType, and closes the open
Workflow Actions, ToDos and notices that point at an override. Documents keep their values in the tables; what is
blocked stays blocked. Idempotent and safe when nothing exists. What was removed is kept in the Error Log
("Remove Regulatory Override").
"""

import json

import frappe

DOCTYPE = "Regulatory Override"
LOG_TITLE = "Remove Regulatory Override"
REF_DOCTYPES = (
	"Quotation",
	"Purchase Order",
	"Purchase Receipt",
	"Stock Entry",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
)
ROW_DOCTYPES = (
	"Purchase Order Item",
	"Purchase Receipt Item",
	"Stock Entry Detail",
	"Sales Order Item",
	"Delivery Note Item",
	"Sales Invoice Item",
)


def delete_fields(report):
	fields = {
		**{doctype: "regulatory_override_ref" for doctype in REF_DOCTYPES},
		**{doctype: "regulatory_override" for doctype in ROW_DOCTYPES},
	}
	for doctype, fieldname in fields.items():
		for name in frappe.get_all(
			"Custom Field", filters={"dt": doctype, "fieldname": fieldname}, pluck="name"
		):
			frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
			report["deleted_fields"].append(f"{doctype}.{fieldname}")
		frappe.clear_cache(doctype=doctype)


def close_references(report):
	"""Open Workflow Actions, ToDos and notices that name an override are closed or removed."""
	if frappe.db.exists("DocType", "Workflow Action"):
		frappe.db.set_value(
			"Workflow Action",
			{"reference_doctype": DOCTYPE, "status": "Open"},
			"status",
			"Completed",
			update_modified=False,
		)
	if frappe.db.exists("DocType", "ToDo"):
		meta = frappe.get_meta("ToDo")
		filters = [{"reference_type": DOCTYPE}]
		if meta.has_field("task_doctype"):
			filters.append({"task_doctype": DOCTYPE})
		for condition in filters:
			names = frappe.get_all("ToDo", filters={**condition, "status": "Open"}, pluck="name")
			for name in names:
				frappe.db.set_value("ToDo", name, "status", "Cancelled", update_modified=False)
			report["closed_todos"] += len(names)
	if frappe.db.exists("DocType", "Notification Log"):
		frappe.db.delete("Notification Log", {"document_type": DOCTYPE})


def delete_doctype(report):
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	if frappe.db.table_exists(DOCTYPE):
		report["deleted_records"] = frappe.db.count(DOCTYPE)
		frappe.db.delete(DOCTYPE)
	frappe.db.delete("Custom DocPerm", {"parent": DOCTYPE})
	frappe.delete_doc("DocType", DOCTYPE, force=True, ignore_permissions=True, ignore_missing=True)
	report["deleted_doctype"] = True


def execute():
	report = {"deleted_fields": [], "closed_todos": 0, "deleted_records": 0, "deleted_doctype": False}
	delete_fields(report)
	close_references(report)
	delete_doctype(report)
	frappe.clear_cache()
	if (
		report["deleted_fields"]
		or report["closed_todos"]
		or report["deleted_records"]
		or report["deleted_doctype"]
	):
		frappe.log_error(title=LOG_TITLE, message=json.dumps(report, indent=1))
	return report
