"""Read-only list of a Customer's/Supplier's branches, derived from the branch code on linked Addresses.

The tax ID (13 digits) stays on the party; each Address carries its own 5-digit branch code
(00000 = head office, 0000X = branch X). Nothing is stored; the table is built on form load.
"""

import re
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import escape_html

PARTY_TYPES = ("Customer", "Supplier")
HEAD_OFFICE = "00000"
NO_CODE = "—"


def _t(text):
	"""Translate, but stay usable (returns the source text) when no site is initialised."""
	try:
		return _(text)
	except Exception:
		return text


def normalise_branch_code(raw):
	"""Return (code, state). state is "ok", "blank" or "invalid".

	Non-digits are dropped and short codes are left-padded to 5 digits ("1" -> "00001").
	More than 5 digits, or text without any digit, is "invalid" (code is the digits found, if any).
	"""
	text = str(raw or "").strip()
	if not text:
		return "", "blank"
	digits = re.sub(r"\D", "", text)
	if not digits or len(digits) > 5:
		return digits, "invalid"
	return digits.zfill(5), "ok"


def branch_label(code):
	if code == HEAD_OFFICE:
		return "สำนักงานใหญ่"
	return "สาขาที่ " + code


@frappe.whitelist()
def get_party_branches(party_type, party):
	"""Rows for every Address linked to the party (permission-aware)."""
	if party_type not in PARTY_TYPES:
		frappe.throw(_("Party Type must be Customer or Supplier"))
	if not party or not frappe.has_permission(party_type, "read", doc=party):
		return []

	names = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": party_type, "link_name": party},
		pluck="parent",
	)
	if not names:
		return []
	addresses = frappe.get_list(
		"Address",
		filters={"name": ("in", list(set(names)))},
		fields=[
			"name",
			"address_title",
			"address_type",
			"branch_code",
			"is_primary_address",
			"city",
			"disabled",
		],
		order_by="is_primary_address desc, name asc",
	)
	return [build_row(a) for a in addresses]


def build_row(address):
	code, state = normalise_branch_code(address.get("branch_code"))
	primary = bool(address.get("is_primary_address"))
	if state == "blank" and primary:
		code, state = HEAD_OFFICE, "ok"
	return {
		"name": address.get("name"),
		"address_title": address.get("address_title") or address.get("name"),
		"address_type": address.get("address_type") or "",
		"branch_code": code,
		"branch_state": state,
		"is_primary_address": primary,
		"city": address.get("city") or "",
		"disabled": bool(address.get("disabled")),
	}


def get_branch_warnings(rows, party_tax_id=None):
	"""Plain-text warnings about the enabled branch rows."""
	warnings = []
	enabled = [r for r in rows if not r.get("disabled")]

	seen = {}
	for r in enabled:
		if r["branch_state"] == "ok":
			seen.setdefault(r["branch_code"], []).append(r["address_title"])
	for code, titles in sorted(seen.items()):
		if len(titles) > 1:
			warnings.append(
				_t("Branch code {0} is used by more than one address: {1}").format(code, ", ".join(titles))
			)

	for r in enabled:
		if r["branch_state"] == "invalid":
			warnings.append(
				_t("Address {0}: branch code must be 5 digits (got '{1}')").format(
					r["address_title"], r["branch_code"]
				)
			)
		elif r["branch_state"] == "blank":
			warnings.append(_t("Address {0}: branch code is empty").format(r["address_title"]))

	if party_tax_id and str(party_tax_id).strip() and HEAD_OFFICE not in seen:
		warnings.append(_t("Tax ID is set but no address has branch code 00000 (head office)"))
	return warnings


def render_branches_html(rows, party_tax_id=None):
	"""Small HTML table of the branches plus warnings. Every dynamic value is escaped."""
	if not rows:
		return '<p class="text-muted small">{}</p>'.format(escape_html(_t("No linked addresses")))

	head = "".join(
		f"<th>{escape_html(_t(h))}</th>"
		for h in ("Address", "Type", "Branch Code", "Branch", "City", "Status")
	)
	body = []
	for r in rows:
		code = r["branch_code"] if r["branch_state"] != "blank" else NO_CODE
		label = branch_label(code) if r["branch_state"] == "ok" else NO_CODE
		status = []
		if r.get("is_primary_address"):
			status.append(_t("Primary"))
		if r.get("disabled"):
			status.append(_t("Disabled"))
		href = "/app/address/" + quote(str(r["name"]), safe="")
		style = ' class="text-muted"' if r.get("disabled") else ""
		body.append(
			'<tr{style}><td><a href="{href}">{title}</a></td><td>{type}</td><td>{code}</td>'
			"<td>{label}</td><td>{city}</td><td>{status}</td></tr>".format(
				style=style,
				href=escape_html(href),
				title=escape_html(r["address_title"]),
				type=escape_html(r["address_type"]),
				code=escape_html(code),
				label=escape_html(label),
				city=escape_html(r["city"]),
				status=escape_html(", ".join(status)),
			)
		)

	html = (
		'<table class="table table-bordered table-sm"><thead><tr>{}</tr></thead><tbody>{}</tbody></table>'
	).format(head, "".join(body))
	warnings = get_branch_warnings(rows, party_tax_id)
	if warnings:
		html += '<div class="text-danger small">{}</div>'.format(
			"".join(f"<div>{escape_html(w)}</div>" for w in warnings)
		)
	return html


def onload(doc, method=None):
	"""Customer/Supplier onload hook: put the rendered branches into __onload."""
	if doc.doctype not in PARTY_TYPES or doc.is_new():
		return
	rows = get_party_branches(doc.doctype, doc.name)
	doc.set_onload("branches_html", render_branches_html(rows, doc.get("tax_id")))
