import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	filters.setdefault("from_date", frappe.utils.add_months(frappe.utils.today(), -12))
	filters.setdefault("to_date", frappe.utils.today())
	conditions = ["s.docstatus=1", "s.purpose='Material Issue'", "a.demo_consumption=1", "a.docstatus=1", "a.adjustment_date between %(from_date)s and %(to_date)s"]
	if filters.get("requester"):
		conditions.append("a.demo_confirmed_by=%(requester)s")
	if filters.get("company"):
		conditions.append("s.company=%(company)s")
	if not filters.get("company"):
		frappe.throw(_("Select a Company."))
	frappe.has_permission("Company", "read", doc=filters.company, throw=True)
	rows = frappe.db.sql("""select s.company, a.demo_confirmed_by requester, u.full_name requester_name, a.demo_customer customer,
		count(distinct s.name) issue_count, count(distinct a.borrowing) request_count, sum(d.amount) cost
		from `tabStock Entry` s join `tabStock Entry Detail` d on d.parent=s.name
		join `tabBorrowing Adjustment` a on a.name=s.borrowing_source_name and s.borrowing_source_doctype='Borrowing Adjustment'
		left join `tabUser` u on u.name=a.demo_confirmed_by where """ + " and ".join(conditions) + " group by s.company, a.demo_confirmed_by, u.full_name, a.demo_customer order by cost desc", filters, as_dict=True)
	columns = [
		{"fieldname":"company", "label":_("Company"), "fieldtype":"Link", "options":"Company", "width":180},
		{"fieldname":"requester", "label":_("Requester"), "fieldtype":"Link", "options":"User", "width":180},
		{"fieldname":"requester_name", "label":_("Requester Name"), "fieldtype":"Data", "width":180},
		{"fieldname":"customer", "label":_("Customer"), "fieldtype":"Link", "options":"Customer", "width":180},
		{"fieldname":"issue_count", "label":_("Stock Entries"), "fieldtype":"Int", "width":120},
		{"fieldname":"request_count", "label":_("Borrowings"), "fieldtype":"Int", "width":140},
		{"fieldname":"cost", "label":_("Total Cost"), "fieldtype":"Currency", "width":140},
	]
	return columns, rows
