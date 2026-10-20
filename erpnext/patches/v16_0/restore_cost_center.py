# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Make the restored Cost Center usable on sites where it was purged.

The retired patches `remove_cost_center_doctypes` and `purge_cost_center_data` deleted every
Cost Center (and its allocations) and dropped every `cost_center` column. The restored DocTypes
bring the tables and columns back empty. This patch:

- makes sure every Cost Center link has its database column again,
- gives every company the standard root cost center and one "Main" leaf (as a new company gets),
  and sets the company's default, round off and depreciation cost centers to it,
- fills empty cost center links on ledger entries, transactions and the POS Profile write off
  cost center with that default.

The back-fill is needed because GL Entry requires a cost center on every "Profit and Loss" row,
also on the reverse entries made on cancellation and on the entries rebuilt by a repost: without
it, cancelling, amending or reposting a document posted while Cost Center was gone would fail.
Every step only fills what is empty, so the patch can run again safely.
"""

import frappe
from frappe.query_builder.functions import Coalesce

LEDGER_DOCTYPES = ("GL Entry", "Payment Ledger Entry", "Account Closing Balance")

# Cost center links that select what a record applies to; an empty value has its own meaning.
SKIP_FIELDS = {
	("Budget", "cost_center"),
	("Cost Center", "parent_cost_center"),
	("Cost Center", "old_parent"),
	("Cost Center Allocation", "main_cost_center"),
	("Cost Center Allocation Percentage", "cost_center"),
	("PSOA Cost Center", "cost_center_name"),
	("Process Payment Reconciliation", "cost_center"),
}


def execute():
	fields = get_cost_center_fields()
	sync_missing_columns(fields)

	default_cost_centers = {}
	for company in frappe.get_all("Company", fields=["name", "abbr"]):
		if cost_center := ensure_default_cost_center(company):
			default_cost_centers[company.name] = cost_center

	if default_cost_centers:
		backfill(fields, default_cost_centers)

	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import sync_budget_dimensions

	sync_budget_dimensions()
	# erpnext.get_default_cost_center memoizes per request; the rest of the migrate needs the new defaults
	frappe.flags.company_cost_center = {}
	frappe.clear_cache()


def get_cost_center_fields():
	"""{doctype: [fieldname, ...]} of every Link to Cost Center, standard or custom."""
	fields = {}
	for row in frappe.get_all(
		"DocField",
		filters={"fieldtype": "Link", "options": "Cost Center", "parenttype": "DocType"},
		fields=["parent", "fieldname"],
	):
		fields.setdefault(row.parent, set()).add(row.fieldname)

	for row in frappe.get_all(
		"Custom Field", filters={"fieldtype": "Link", "options": "Cost Center"}, fields=["dt", "fieldname"]
	):
		fields.setdefault(row.dt, set()).add(row.fieldname)

	return fields


def is_table_doctype(doctype):
	meta = frappe.get_meta(doctype)
	return not (meta.issingle or meta.is_virtual)


def sync_missing_columns(fields):
	for doctype, fieldnames in fields.items():
		if not frappe.db.exists("DocType", doctype) or not is_table_doctype(doctype):
			continue

		if not frappe.db.table_exists(doctype, cached=False):
			continue

		columns = set(frappe.db.get_table_columns(doctype))
		if fieldnames - columns:
			frappe.db.updatedb(doctype)


def ensure_default_cost_center(company):
	"""Root cost center and one leaf for the company, set as its defaults. Returns the default."""
	root = frappe.db.get_value(
		"Cost Center",
		{"company": company.name, "is_group": 1, "parent_cost_center": ["is", "not set"]},
		order_by="lft asc",
	)
	if not root:
		root = insert_cost_center(company.name, company.name, is_group=1)

	leaf = {"company": company.name, "is_group": 0, "disabled": 0}
	default = frappe.db.get_value("Company", company.name, "cost_center")
	if not default or not frappe.db.exists("Cost Center", {"name": default, **leaf}):
		default = frappe.db.get_value("Cost Center", leaf, order_by="lft asc")
	if not default:
		default = insert_cost_center("Main", company.name, parent_cost_center=root)

	for fieldname in ("cost_center", "round_off_cost_center", "depreciation_cost_center"):
		if not frappe.db.get_value("Company", company.name, fieldname):
			frappe.db.set_value("Company", company.name, fieldname, default, update_modified=False)

	return default


def insert_cost_center(cost_center_name, company, is_group=0, parent_cost_center=None):
	doc = frappe.get_doc(
		{
			"doctype": "Cost Center",
			"cost_center_name": cost_center_name,
			"company": company,
			"is_group": is_group,
			"parent_cost_center": parent_cost_center,
		}
	)
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = not parent_cost_center
	doc.insert()
	return doc.name


def backfill(fields, default_cost_centers):
	for doctype, fieldnames in fields.items():
		if not frappe.db.exists("DocType", doctype) or not is_table_doctype(doctype):
			continue

		meta = frappe.get_meta(doctype)
		fieldnames = {f for f in fieldnames if (doctype, f) not in SKIP_FIELDS}

		if doctype in LEDGER_DOCTYPES or (meta.is_submittable and meta.has_field("company")):
			backfill_parent(doctype, fieldnames, default_cost_centers)
		elif meta.istable:
			for parent in get_submittable_parents(doctype):
				backfill_child(doctype, parent, fieldnames, default_cost_centers)
		elif doctype == "POS Profile":
			backfill_parent(doctype, {"write_off_cost_center"}, default_cost_centers)


def get_submittable_parents(child_doctype):
	parents = set(
		frappe.get_all(
			"DocField",
			filters={"fieldtype": ["in", ["Table", "Table MultiSelect"]], "options": child_doctype},
			pluck="parent",
		)
	) | set(
		frappe.get_all(
			"Custom Field",
			filters={"fieldtype": ["in", ["Table", "Table MultiSelect"]], "options": child_doctype},
			pluck="dt",
		)
	)

	return [
		parent
		for parent in parents
		if frappe.db.exists("DocType", parent)
		and is_table_doctype(parent)
		and frappe.get_meta(parent).is_submittable
		and frappe.get_meta(parent).has_field("company")
	]


def backfill_parent(doctype, fieldnames, default_cost_centers):
	table = frappe.qb.DocType(doctype)
	columns = set(frappe.db.get_table_columns(doctype))
	for fieldname in sorted(fieldnames & columns):
		for company, cost_center in default_cost_centers.items():
			(
				frappe.qb.update(table)
				.set(table[fieldname], cost_center)
				.where(table.company == company)
				.where(Coalesce(table[fieldname], "") == "")
			).run()


def backfill_child(doctype, parent_doctype, fieldnames, default_cost_centers):
	child = frappe.qb.DocType(doctype)
	parent = frappe.qb.DocType(parent_doctype)
	columns = set(frappe.db.get_table_columns(doctype))
	for fieldname in sorted(fieldnames & columns):
		for company, cost_center in default_cost_centers.items():
			parents_of_company = frappe.qb.from_(parent).select(parent.name).where(parent.company == company)
			(
				frappe.qb.update(child)
				.set(child[fieldname], cost_center)
				.where(child.parenttype == parent_doctype)
				.where(child.parent.isin(parents_of_company))
				.where(Coalesce(child[fieldname], "") == "")
			).run()
