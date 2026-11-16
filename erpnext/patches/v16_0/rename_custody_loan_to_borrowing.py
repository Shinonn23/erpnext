"""Custody Loan becomes Borrowing (Siwat, 16 Nov 2026; pre-go-live, renamed outright).

Runs at the end of [pre_model_sync]: the DocTypes, reports and the module Loan are renamed before the
model sync imports the new JSON, the link fields are renamed in place and every stored Material Request of
the old type "Customer Borrow / Loan" becomes "Borrow" (an Employee borrows) or "Customer Borrow" (a
Customer borrows). Each step is guarded, so the patch can run again.
"""

import frappe
from frappe import scrub
from frappe.model.utils.rename_field import rename_field

OLD_REQUEST_TYPE = "Customer Borrow / Loan"

DOCTYPES = {
	"Custody Loan": "Borrowing",
	"Custody Loan Item": "Borrowing Item",
	"Custody Loan Return": "Borrowing Return",
	"Custody Loan Return Item": "Borrowing Return Item",
	"Custody Loan Adjustment": "Borrowing Adjustment",
	"Custody Loan Adjustment Item": "Borrowing Adjustment Item",
	"Custody Loan Serial Batch": "Borrowing Serial Batch",
}

REPORTS = {
	"Custody Loan Register": "Borrowing Register",
	"Custody Loan Stock Balance": "Borrowing Stock Balance",
	"Custody Loan Asset Balance": "Borrowing Asset Balance",
	"Custody Loan Movement History": "Borrowing Movement History",
	"Overdue Custody Loans": "Overdue Borrowings",
}

TRANSACTION_FIELDS = {
	"custody_loan": "borrowing",
	"is_custody_loan_transaction": "is_borrowing_transaction",
	"custody_loan_source_doctype": "borrowing_source_doctype",
	"custody_loan_source_name": "borrowing_source_name",
}

# (module, DocType): {old fieldname: new fieldname}
FIELDS = {
	("accounts", "Sales Invoice"): TRANSACTION_FIELDS,
	("assets", "Asset Movement"): TRANSACTION_FIELDS,
	("stock", "Stock Entry"): TRANSACTION_FIELDS,
	("stock", "Delivery Note"): {**TRANSACTION_FIELDS, "custody_loan_shipment": "borrowing_shipment"},
	("stock", "Shipment"): {"custody_loan": "borrowing", "custody_loan_direction": "borrowing_direction"},
	("stock", "Pick List"): {"custody_loan": "borrowing"},
	("assets", "Stock to Asset Conversion"): {
		"custody_loan": "borrowing",
		"custody_loan_item": "borrowing_item",
	},
	("stock", "Material Request Item"): {"custody_loan": "borrowing"},
	("accounts", "Sales Invoice Item"): {"custody_loan_item": "borrowing_item"},
	("assets", "Asset Movement Item"): {"custody_loan_item": "borrowing_item"},
	("stock", "Delivery Note Item"): {"custody_loan_item": "borrowing_item"},
	("stock", "Pick List Item"): {"custody_loan_item": "borrowing_item"},
	("stock", "Stock Entry Detail"): {"custody_loan_item": "borrowing_item"},
	("borrowing", "Borrowing Return"): {"custody_loan": "borrowing"},
	("borrowing", "Borrowing Adjustment"): {"custody_loan": "borrowing"},
	("borrowing", "Borrowing Return Item"): {"custody_loan_item": "borrowing_item"},
	("borrowing", "Borrowing Adjustment Item"): {"custody_loan_item": "borrowing_item"},
	("borrowing", "Borrowing Serial Batch"): {"custody_loan_item": "borrowing_item"},
}

OLD_MODULE, NEW_MODULE = "Loan", "Borrowing"
# The Workspace, Sidebar, Workspace Sidebar and Desktop Icon are "Borrowings": a workspace named
# "Borrowing" would take the route /desk/borrowing of the DocType Borrowing (the router asks the
# workspaces first). "Borrowing" is listed too, for a site that ran an earlier build of this patch.
OLD_NAVIGATION, NEW_NAVIGATION = ("Loan", "Borrowing"), "Borrowings"


def request_type(borrower_type=None, recipient_type=None, customer=None):
	"""The new type of a stored "Customer Borrow / Loan" request: its Borrowing's borrower, else the
	request's recipient type, else Customer when the request names a customer; anything else is Borrow."""
	kind = borrower_type or recipient_type or ("Customer" if customer else None)
	return "Customer Borrow" if kind == "Customer" else "Borrow"


def execute():
	rename_doctypes()
	split_request_types()
	split_borrowing_request_types()
	log_odd_borrowings()
	rename_fields()
	rename_reports()
	rename_module()
	rename_blocked_modules()
	rename_navigation()


def rename_doctypes():
	for old, new in DOCTYPES.items():
		if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
			frappe.rename_doc("DocType", old, new, force=True)
	for new in DOCTYPES.values():
		frappe.reload_doc("borrowing", "doctype", scrub(new))


def split_request_types():
	"""Employee borrower -> Borrow, Customer borrower -> Customer Borrow. Supplier and Contact borrowers
	(no longer allowed) become Borrow and are logged for the Administrator."""
	if not frappe.db.has_column("Material Request", "material_request_type"):
		return
	requests = frappe.db.sql(
		"""select name, loan_recipient_type, customer from `tabMaterial Request`
		where material_request_type = %s""",
		OLD_REQUEST_TYPE,
		as_dict=True,
	)
	if not requests:
		return
	borrowers = {}
	if frappe.db.table_exists("Borrowing"):
		for row in frappe.db.sql(
			"""select material_request, borrower_type from `tabBorrowing`
			where docstatus < 2 order by docstatus desc, creation asc""",
			as_dict=True,
		):
			borrowers.setdefault(row.material_request, row.borrower_type)
	odd, mismatched = [], []
	for request in requests:
		borrower = borrowers.get(request.name)
		new = request_type(borrower, request.loan_recipient_type, request.customer)
		if (borrower or request.loan_recipient_type) not in (None, "", "Employee", "Customer"):
			odd.append(f"{request.name} ({borrower or request.loan_recipient_type})")
		elif request.loan_recipient_type in ("Employee", "Customer") and request.loan_recipient_type != (
			"Customer" if new == "Customer Borrow" else "Employee"
		):
			# the Borrowing's borrower decided the type; the request's recipient (a name of the other
			# DocType) is left as it is for the Administrator, never rewritten to a wrong link
			mismatched.append(f"{request.name} ({new}, recipient {request.loan_recipient_type})")
		values = {"material_request_type": new}
		if not request.loan_recipient_type or request.loan_recipient_type not in ("Employee", "Customer"):
			values["loan_recipient_type"] = "Customer" if new == "Customer Borrow" else "Employee"
		frappe.db.set_value("Material Request", request.name, values, update_modified=False)
	print(f"Material Requests of type {OLD_REQUEST_TYPE} converted: {len(requests)}")
	if odd:
		message = "Supplier or Contact borrowers moved to Borrow (Employee); fix by hand:\n" + "\n".join(odd)
		print(message)
		frappe.log_error(title="Borrowing rename: borrowers to review", message=message)
	if mismatched:
		message = "Requests whose recipient type differs from their Borrowing's borrower; fix by hand:\n"
		message += "\n".join(mismatched)
		print(message)
		frappe.log_error(title="Borrowing rename: recipients to review", message=message)


def split_borrowing_request_types():
	"""`Borrowing.material_request_type` (fetched from the request) takes its request's new type; a
	Borrowing of the old type whose request is gone follows its own borrower. A rerun finds nothing."""
	if not frappe.db.table_exists("Borrowing") or not frappe.db.has_column(
		"Borrowing", "material_request_type"
	):
		return
	frappe.db.sql(
		"""update `tabBorrowing` b join `tabMaterial Request` mr on mr.name = b.material_request
		set b.material_request_type = mr.material_request_type
		where b.material_request_type = %s and mr.material_request_type != %s""",
		(OLD_REQUEST_TYPE, OLD_REQUEST_TYPE),
	)
	for row in frappe.db.sql(
		"""select name, borrower_type from `tabBorrowing` where material_request_type = %s""",
		OLD_REQUEST_TYPE,
		as_dict=True,
	):
		frappe.db.set_value(
			"Borrowing",
			row.name,
			"material_request_type",
			request_type(row.borrower_type),
			update_modified=False,
		)


def log_odd_borrowings():
	"""A Borrowing may name only an Employee or a Customer now; older Supplier / Contact ones are listed."""
	if not frappe.db.table_exists("Borrowing") or not frappe.db.has_column("Borrowing", "borrower_type"):
		return
	odd = frappe.db.sql(
		"""select name, borrower_type, borrower from `tabBorrowing`
		where docstatus < 2 and ifnull(borrower_type, '') not in ('', 'Employee', 'Customer')""",
		as_dict=True,
	)
	if odd:
		message = "Borrowings whose borrower is no longer allowed (Employee or Customer only); fix by hand:\n"
		message += "\n".join(f"{row.name} ({row.borrower_type} {row.borrower})" for row in odd)
		print(message)
		frappe.log_error(title="Borrowing rename: Borrowings to review", message=message)


def rename_fields():
	for (module, doctype), fields in FIELDS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		frappe.reload_doc(module, "doctype", scrub(doctype))
		for old, new in fields.items():
			if frappe.db.has_column(doctype, old):
				rename_field(doctype, old, new)
				# drop the old column: a second run then finds nothing to copy and never
				# overwrites the borrowing* values written since
				frappe.db.sql_ddl(f"alter table `tab{doctype}` drop column `{old}`")


def rename_reports():
	for old, new in REPORTS.items():
		if frappe.db.exists("Report", old) and not frappe.db.exists("Report", new):
			frappe.rename_doc("Report", old, new, force=True)


def rename_module():
	from frappe.model.rename_doc import get_link_fields, update_link_field_values

	if not frappe.db.exists("Module Def", OLD_MODULE):
		return
	if not frappe.db.exists("Module Def", NEW_MODULE):
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": NEW_MODULE, "app_name": "erpnext"}
		).db_insert()
	update_link_field_values(get_link_fields("Module Def"), OLD_MODULE, NEW_MODULE, "Module Def")
	# raw delete: on_trash removes the module's Sidebars and, in developer mode, edits modules.txt
	frappe.db.delete("Module Def", {"name": OLD_MODULE, "app_name": "erpnext"})


def rename_blocked_modules():
	"""`Block Module.module` is Data, so the Module Def rename does not reach it: a user or Module
	Profile that blocked Loan blocks Borrowing."""
	if frappe.db.table_exists("Block Module"):
		frappe.db.sql("update `tabBlock Module` set module = %s where module = %s", (NEW_MODULE, OLD_MODULE))


def rename_navigation():
	for doctype in ("Workspace", "Sidebar", "Workspace Sidebar"):
		if not frappe.db.table_exists(doctype):
			continue
		for old in OLD_NAVIGATION:
			if frappe.db.exists(doctype, old) and not frappe.db.exists(doctype, NEW_NAVIGATION):
				frappe.rename_doc(doctype, old, NEW_NAVIGATION, force=True)
	icons = frappe.db.table_exists("Desktop Icon") and [
		old for old in OLD_NAVIGATION if frappe.db.exists("Desktop Icon", old)
	]
	if icons:
		# raw delete: its hooks export files in developer mode; the sync imports the new icon
		frappe.db.delete("Desktop Icon", {"name": ["in", icons]})
		try:
			from frappe.desk.doctype.desktop_icon.desktop_icon import clear_every_users_desktop_icons

			clear_every_users_desktop_icons()
		except ImportError:
			pass
