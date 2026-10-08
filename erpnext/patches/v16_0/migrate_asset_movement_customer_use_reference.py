import frappe
from frappe import _


def execute():
	if not frappe.db.has_column("Asset Movement", "customer_demo_loan_request"):
		return

	conflicts = frappe.db.sql(
		"""SELECT name FROM `tabAsset Movement`
		WHERE IFNULL(customer_demo_loan_request, '') != ''
		AND ((IFNULL(reference_doctype, '') != '' AND reference_doctype != 'Customer Use Request')
			OR (IFNULL(reference_name, '') != '' AND reference_name != customer_demo_loan_request))""",
		pluck=True,
	)
	if conflicts:
		frappe.throw(
			_("Resolve conflicting references on Asset Movements before migrating: {0}").format(
				", ".join(conflicts)
			)
		)

	frappe.db.sql(
		"""UPDATE `tabAsset Movement`
		SET reference_doctype = 'Customer Use Request', reference_name = customer_demo_loan_request
		WHERE IFNULL(customer_demo_loan_request, '') != ''"""
	)
