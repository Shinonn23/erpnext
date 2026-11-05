"""A Shipment is optional for a custody loan (Siwat, 5 Nov 2026): a loan that waited for one is released.

Loans made with "Ship via Shipment" kept their Delivery Note (out) or return Delivery Note (back) as a draft until the
Shipment was delivered. The field is gone and nothing waits for a Shipment, so each such draft is delivered or received
as for any loan. A failure is logged and leaves the draft for the Stock team (the loan's comment says why).
"""

import frappe


def execute():
	if not frappe.db.table_exists("Custody Loan") or not frappe.db.has_column(
		"Custody Loan", "requires_shipment"
	):
		return
	loans = frappe.db.sql_list(
		"select name from `tabCustody Loan` where docstatus = 1 and loan_type = 'Stock' and requires_shipment = 1"
	)
	for name in loans:
		savepoint = "release_loan_waiting_for_shipment"
		frappe.db.savepoint(savepoint)
		try:
			loan = frappe.get_doc("Custody Loan", name)
			if loan.pick_list and frappe.db.get_value("Pick List", loan.pick_list, "docstatus") == 1:
				if (
					not loan.delivery_note
					or frappe.db.get_value("Delivery Note", loan.delivery_note, "docstatus") == 0
				):
					loan.deliver_after_pick()
			for return_name in frappe.get_all(
				"Custody Loan Return", filters={"custody_loan": name, "docstatus": 1}, pluck="name"
			):
				return_doc = frappe.get_doc("Custody Loan Return", return_name)
				note = return_doc.delivery_note
				if note and frappe.db.get_value("Delivery Note", note, "docstatus") == 0:
					return_doc.inspect_or_receive(frappe.get_doc("Delivery Note", note))
				return_doc.update_status()
			frappe.get_doc("Custody Loan", name).update_status()
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(title=f"Loan {name} could not be released from its Shipment")
