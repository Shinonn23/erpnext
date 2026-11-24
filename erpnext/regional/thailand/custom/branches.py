"""Read linked Address branch codes for a Customer or Supplier."""

import re

import frappe
from frappe import _

PARTY_TYPES = ("Customer", "Supplier")
HEAD_OFFICE = "00000"


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


def onload(doc, method=None):
	"""Allow cached hooks from before the branch panel was removed to run."""
