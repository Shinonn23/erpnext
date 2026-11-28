# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""Selling Price vs Standard: who sells at what price against the Standard.

One row for each submitted, non-free item line of a Sales Order, Quotation or Sales Invoice: the Standard (the row's
Price List rate), the rate sold (the net rate, as the discount approval compares it), the difference and the amount, all
in company currency, with the sales user, her Sales Person and the Territory. "Summary by Sales" groups them by sales user
with the weighted average percent above Standard. Parents are read with ``frappe.get_list`` so User Permissions and
permission hooks apply at the source. Currency columns use the Selling and Sold labels for report visibility rules.

A Sales Invoice is credited to the owner of the Sales Order its row names, else to the owner of the invoice (accounts
receivable issues the invoice, the seller made the order). Returns, debit notes, opening invoices and free rows are left out.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, nowdate

DEFAULT_DOCTYPE = "Sales Order"
# document type: (item table, date field)
DOCTYPES = {
	"Sales Order": ("Sales Order Item", "transaction_date"),
	"Quotation": ("Quotation Item", "transaction_date"),
	"Sales Invoice": ("Sales Invoice Item", "posting_date"),
}
CHUNK = 500


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Select a Company."))
	filters.document_type = filters.document_type or DEFAULT_DOCTYPE
	if filters.document_type not in DOCTYPES:
		frappe.throw(_("Choose Sales Order, Quotation or Sales Invoice."))
	if not frappe.has_permission(filters.document_type, "read"):
		frappe.throw(
			_("You cannot read {0}s. Choose Quotation or Sales Order.").format(filters.document_type),
			frappe.PermissionError,
		)
	filters.to_date = filters.to_date or nowdate()
	filters.from_date = filters.from_date or add_months(filters.to_date, -1)
	rows = get_rows(filters)
	if filters.summary_by_sales:
		owners = sales_people_of(sorted({row["sales_user"] for row in rows if row["sales_user"]}))
		return summary_columns(), summarize(rows, owners)
	return line_columns(filters.document_type), rows


def _parent_filters(filters):
	doctype = filters.document_type
	date_field = DOCTYPES[doctype][1]
	found = {
		"docstatus": 1,
		"company": filters.company,
		date_field: ["between", [getdate(filters.from_date), getdate(filters.to_date)]],
	}
	if filters.territory:
		found["territory"] = ["in", territory_tree(filters.territory)]
	if filters.customer:
		if doctype == "Quotation":
			found.update({"quotation_to": "Customer", "party_name": filters.customer})
		else:
			found["customer"] = filters.customer
	if doctype == "Sales Invoice":
		found["is_return"] = 0
		found["is_opening"] = ["!=", "Yes"]
		if frappe.get_meta(doctype).has_field("is_debit_note"):
			found["is_debit_note"] = 0
	return found


def tree_of(doctype, name):
	"""``name`` and every node below it (the doctype is a tree)."""
	bounds = frappe.db.get_value(doctype, name, ["lft", "rgt"])
	if not bounds:
		return [name]
	return frappe.get_all(doctype, filters={"lft": [">=", bounds[0]], "rgt": ["<=", bounds[1]]}, pluck="name")


def territory_tree(name):
	return tree_of("Territory", name)


def parent_fields(doctype):
	date_field = DOCTYPES[doctype][1]
	fields = ["name", "owner", date_field, "territory", "currency", "status"]
	if doctype == "Quotation":
		fields += ["quotation_to", "party_name", "customer_name"]
	else:
		fields += ["customer", "customer_name"]
	return fields


def sales_people_of(users):
	"""{user: Sales Person} through Sales Person.employee -> Employee.user_id (the first enabled one by name)."""
	if not users:
		return {}
	employees = {
		row.name: row.user_id
		for row in frappe.get_all(
			"Employee", filters={"user_id": ["in", list(users)]}, fields=["name", "user_id"]
		)
	}
	if not employees:
		return {}
	found = {}
	for row in frappe.get_all(
		"Sales Person",
		filters={"employee": ["in", list(employees)], "enabled": 1},
		fields=["name", "employee"],
		order_by="name asc",
	):
		found.setdefault(employees[row.employee], row.name)
	return found


def users_of_sales_person(name):
	"""The users of the Sales Person and of every Sales Person below her."""
	people = tree_of("Sales Person", name)
	employees = frappe.get_all("Sales Person", filters={"name": ["in", people]}, pluck="employee")
	employees = [employee for employee in employees if employee]
	if not employees:
		return set()
	return set(frappe.get_all("Employee", filters={"name": ["in", employees]}, pluck="user_id")) - {None, ""}


def team_people(doctype, names):
	"""{document: Sales Person} of the document's own Sales Team (the largest allocation, the first row on a tie); a
	document with no Sales Team is absent and falls back to owner -> Employee -> Sales Person."""
	found = {}
	for start in range(0, len(names), CHUNK):
		rows = frappe.get_all(
			"Sales Team",
			filters={"parenttype": doctype, "parent": ["in", names[start : start + CHUNK]]},
			fields=["parent", "sales_person", "allocated_percentage", "idx"],
			order_by="parent asc, idx asc",
			limit_page_length=0,
		)
		best = {}
		for row in rows:
			if row.sales_person and (
				row.parent not in best
				or flt(row.allocated_percentage) > flt(best[row.parent].allocated_percentage)
			):
				best[row.parent] = row
		found.update({parent: row.sales_person for parent, row in best.items()})
	return found


def get_rows(filters):
	doctype = filters.document_type
	child, date_field = DOCTYPES[doctype]
	parents = frappe.get_list(
		doctype,
		filters=_parent_filters(filters),
		fields=parent_fields(doctype),
		limit_page_length=0,
	)
	by_name = {row.name: row for row in parents}
	names = list(by_name)
	child_fields = [
		"parent",
		"idx",
		"item_code",
		"item_name",
		"qty",
		"uom",
		"base_price_list_rate",
		"base_net_rate",
		"base_net_amount",
		"is_free_item",
	]
	if doctype == "Sales Invoice":
		child_fields.append("sales_order")
	lines = []
	for start in range(0, len(names), CHUNK):
		child_filters = {"parenttype": doctype, "parent": ["in", names[start : start + CHUNK]]}
		if filters.item_code:
			child_filters["item_code"] = filters.item_code
		lines.extend(frappe.get_all(child, filters=child_filters, fields=child_fields, limit_page_length=0))
	lines = [line for line in lines if not line.is_free_item]
	order_owner = {}
	if doctype == "Sales Invoice":
		orders = sorted({line.sales_order for line in lines if line.sales_order})
		for start in range(0, len(orders), CHUNK):
			for row in frappe.get_all(
				"Sales Order",
				filters={"name": ["in", orders[start : start + CHUNK]]},
				fields=["name", "owner"],
			):
				order_owner[row.name] = row.owner
	users = {(order_owner.get(line.get("sales_order")) or by_name[line.parent].owner) for line in lines}
	people = sales_people_of(users)
	teams = team_people(doctype, names)
	allowed = allowed_people = None
	if filters.sales_person:
		allowed = users_of_sales_person(filters.sales_person)
		allowed_people = set(tree_of("Sales Person", filters.sales_person))
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	rows = []
	for line in sorted(
		lines, key=lambda line: (str(by_name[line.parent].get(date_field)), line.parent, line.idx)
	):
		parent = by_name[line.parent]
		user = order_owner.get(line.get("sales_order")) or parent.owner
		if filters.sales_user and user != filters.sales_user:
			continue
		person = teams.get(parent.name) or people.get(user)
		if allowed is not None and not (
			person in allowed_people if parent.name in teams else user in allowed
		):
			continue
		standard, sold = flt(line.base_price_list_rate), flt(line.base_net_rate)
		difference = sold - standard
		rows.append(
			{
				"date": parent.get(date_field),
				"document_type": doctype,
				"document": parent.name,
				"status": parent.status,
				"sales_user": user,
				"sales_user_name": frappe.get_cached_value("User", user, "full_name") or user,
				"sales_person": person,
				"territory": parent.territory,
				"customer": parent.get("customer")
				or (parent.party_name if parent.get("quotation_to") == "Customer" else None),
				"party_name": parent.get("customer_name") or parent.get("party_name"),
				"item_code": line.item_code,
				"item_name": line.item_name,
				"qty": flt(line.qty),
				"uom": line.uom,
				"standard_selling_rate": standard,
				"sold_selling_rate": sold,
				"selling_difference": difference if standard else None,
				"difference_percent": (100 * difference / standard) if standard else None,
				"sold_selling_amount": flt(line.base_net_amount),
				"currency": currency,
			}
		)
	return rows


def summarize(rows, owners=None):
	"""One row for each sales user plus the total. The Sales Person of a user is the one of her own Employee
	(``owners``, {user: Sales Person}); a user with none shows the Sales Person her rows agree on, blank when they
	disagree (a Sales Team of a document may name another person)."""
	owners = owners or {}
	groups = {}
	seen = {}
	for row in rows:
		group = groups.setdefault(
			row["sales_user"],
			{
				"sales_user": row["sales_user"],
				"sales_user_name": row["sales_user_name"],
				"sales_person": row["sales_person"],
				"documents": set(),
				"lines": 0,
				"lines_above": 0,
				"lines_at": 0,
				"lines_below": 0,
				"lines_without_standard": 0,
				"standard_selling_amount": 0.0,
				"sold_selling_amount": 0.0,
				"currency": row["currency"],
			},
		)
		seen.setdefault(row["sales_user"], set()).add(row["sales_person"] or "")
		group["documents"].add(row["document"])
		group["lines"] += 1
		standard_amount = row["standard_selling_rate"] * row["qty"]
		if not row["standard_selling_rate"]:
			group["lines_without_standard"] += 1
			continue
		group["standard_selling_amount"] += standard_amount
		group["sold_selling_amount"] += row["sold_selling_amount"]
		if row["selling_difference"] > 0:
			group["lines_above"] += 1
		elif row["selling_difference"] < 0:
			group["lines_below"] += 1
		else:
			group["lines_at"] += 1
	out = []
	total = {
		"sales_user": _("Total"),
		"documents": 0,
		"lines": 0,
		"lines_above": 0,
		"lines_at": 0,
		"lines_below": 0,
		"lines_without_standard": 0,
		"standard_selling_amount": 0.0,
		"sold_selling_amount": 0.0,
		"currency": rows[0]["currency"] if rows else None,
	}
	for group in sorted(groups.values(), key=lambda group: group["sales_user"]):
		group["documents"] = len(group["documents"])
		agreed = seen.get(group["sales_user"], set())
		group["sales_person"] = owners.get(group["sales_user"]) or (
			next(iter(agreed)) if len(agreed) == 1 else ""
		)
		_finish(group)
		out.append(group)
		for key in total:
			if key not in ("sales_user", "currency", "documents"):
				total[key] += group[key]
	total["documents"] = len({row["document"] for row in rows})
	_finish(total)
	if out:
		out.append(total)
	return out


def _finish(group):
	group["selling_difference"] = group["sold_selling_amount"] - group["standard_selling_amount"]
	standard = group["standard_selling_amount"]
	group["average_percent_above_standard"] = (
		100 * group["selling_difference"] / standard if standard else None
	)


def _currency(fieldname, label, width=120):
	return {
		"fieldname": fieldname,
		"label": label,
		"fieldtype": "Currency",
		"options": "currency",
		"width": width,
	}


def line_columns(doctype):
	return [
		{"fieldname": "date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{
			"fieldname": "document",
			"label": _(doctype),
			"fieldtype": "Link",
			"options": doctype,
			"width": 150,
		},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
		{
			"fieldname": "sales_user",
			"label": _("Sales User"),
			"fieldtype": "Link",
			"options": "User",
			"width": 160,
		},
		{"fieldname": "sales_user_name", "label": _("Sales User Name"), "fieldtype": "Data", "width": 140},
		{
			"fieldname": "sales_person",
			"label": _("Sales Person"),
			"fieldtype": "Link",
			"options": "Sales Person",
			"width": 130,
		},
		{
			"fieldname": "territory",
			"label": _("Territory"),
			"fieldtype": "Link",
			"options": "Territory",
			"width": 120,
		},
		{
			"fieldname": "customer",
			"label": _("Customer"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 150,
		},
		{"fieldname": "party_name", "label": _("Party Name"), "fieldtype": "Data", "width": 160},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 140},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 80},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 70},
		_currency("standard_selling_rate", _("Standard Selling Rate")),
		_currency("sold_selling_rate", _("Sold Selling Rate")),
		_currency("selling_difference", _("Selling Difference")),
		{"fieldname": "difference_percent", "label": _("Difference %"), "fieldtype": "Percent", "width": 100},
		_currency("sold_selling_amount", _("Sold Selling Amount")),
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
			"width": 60,
		},
	]


def summary_columns():
	return [
		{"fieldname": "sales_user", "label": _("Sales User"), "fieldtype": "Data", "width": 170},
		{"fieldname": "sales_user_name", "label": _("Sales User Name"), "fieldtype": "Data", "width": 150},
		{
			"fieldname": "sales_person",
			"label": _("Sales Person"),
			"fieldtype": "Link",
			"options": "Sales Person",
			"width": 130,
		},
		{"fieldname": "documents", "label": _("Documents"), "fieldtype": "Int", "width": 90},
		{"fieldname": "lines", "label": _("Lines"), "fieldtype": "Int", "width": 70},
		{"fieldname": "lines_above", "label": _("Lines Above Standard"), "fieldtype": "Int", "width": 100},
		{"fieldname": "lines_at", "label": _("Lines At Standard"), "fieldtype": "Int", "width": 100},
		{"fieldname": "lines_below", "label": _("Lines Below Standard"), "fieldtype": "Int", "width": 100},
		{
			"fieldname": "lines_without_standard",
			"label": _("Lines Without Standard"),
			"fieldtype": "Int",
			"width": 110,
		},
		_currency("standard_selling_amount", _("Standard Selling Amount"), 150),
		_currency("sold_selling_amount", _("Sold Selling Amount"), 150),
		_currency("selling_difference", _("Selling Difference"), 130),
		{
			"fieldname": "average_percent_above_standard",
			"label": _("Average % Above Standard"),
			"fieldtype": "Percent",
			"width": 130,
		},
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
			"width": 60,
		},
	]
