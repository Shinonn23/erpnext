import frappe
from frappe import _

LEGACY_LINK_FIELDS = (
	("Asset", "customer_demo_loan_request"),
	("Asset Movement", "customer_demo_loan_request"),
	("Stock Entry", "customer_demo_loan_request"),
	("Stock Entry Detail", "customer_demo_loan_request_item"),
	("Pick List", "customer_demo_loan_request"),
	("Pick List Item", "customer_demo_loan_request_item"),
	("Stock to Asset Conversion", "customer_demo_loan_request"),
	("Stock to Asset Conversion Item", "customer_demo_loan_request_item"),
)

LEGACY_REPORTS = (
	"Customer Use Request Register",
	"Customer Use Item Fulfillment",
	"Customer Use Movement History",
	"Customer Asset Custody",
	"Customer Stock Loan Balance",
	"Overdue Customer Returns",
)


def assert_no_legacy_data():
	legacy_records = []
	for doctype in ("Customer Use Request", "Customer Use Request Item"):
		if frappe.db.table_exists(doctype):
			table_name = quote_identifier("tab" + doctype)
			rows = frappe.db.sql(
				f"select name from {table_name} limit 1", pluck=True
			)
			if rows:
				legacy_records.append(doctype)

	for doctype, fieldname in LEGACY_LINK_FIELDS:
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, fieldname):
			continue
		table_name = quote_identifier("tab" + doctype)
		column_name = quote_identifier(fieldname)
		rows = frappe.db.sql(
			f"select name from {table_name} where coalesce({column_name}, '') != '' limit 5",
			pluck=True,
		)
		if rows:
			legacy_records.extend(f"{doctype} {name}" for name in rows)

	if frappe.db.table_exists("Asset Movement") and frappe.db.has_column("Asset Movement", "reference_doctype"):
		rows = frappe.db.get_all(
			"Asset Movement",
			filters={"reference_doctype": "Customer Use Request"},
			pluck="name",
			limit_page_length=5,
		)
		if rows:
			legacy_records.extend(f"Asset Movement {name}" for name in rows)

	if legacy_records:
		frappe.throw(
			_("Cannot remove Customer Use Request because legacy records still exist: {0}").format(
				", ".join(legacy_records)
			)
		)


def execute():
	assert_no_legacy_data()
	if frappe.db.table_exists("Pick List") and frappe.db.has_column("Pick List", "pick_list_type"):
		frappe.db.sql(
			f"""update {quote_identifier('tabPick List')}
			set {quote_identifier('pick_list_type')}=%s
			where {quote_identifier('pick_list_type')}=%s""",
			("Customer Demo - Asset Conversion", "Customer Use - Asset Conversion"),
		)
	for doctype in ("Customer Use Request Item", "Customer Use Request"):
		frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)

	for report in LEGACY_REPORTS:
		frappe.delete_doc_if_exists("Report", report, force=True)

	for doctype, name in (
		("Workspace Sidebar", "Customer Use"),
		("Workspace", "Customer Use"),
		("Desktop Icon", "Customer Use"),
	):
		frappe.delete_doc_if_exists(doctype, name, force=True)


def quote_identifier(identifier):
	if frappe.db.db_type == "postgres":
		return f'"{identifier.replace(chr(34), chr(34) * 2)}"'

	return f"`{identifier.replace('`', '``')}`"
