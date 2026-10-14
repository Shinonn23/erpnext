"""Source-document authorization for detailed ledger results."""

from contextlib import contextmanager
from contextvars import ContextVar

import frappe

_ledger_query_context = ContextVar("ledger_query_permission_context", default=False)


@contextmanager
def _ledger_query_permission_context():
	"""Construct a complete ledger query whose consumer enforces source permissions."""
	token = _ledger_query_context.set(True)
	try:
		yield
	finally:
		_ledger_query_context.reset(token)


def in_ledger_query_permission_context():
	return _ledger_query_context.get()


def check_ledger_voucher_permissions(rows):
	"""Reject the whole result if any referenced Journal Entry cannot be read."""
	vouchers = set()
	for row in rows:
		for type_field, name_fields in (
			("voucher_type", ("voucher_no",)),
			("against_voucher_type", ("against_voucher_no", "against_voucher")),
		):
			if row.get(type_field) == "Journal Entry":
				name = next((row.get(field) for field in name_fields if row.get(field)), None)
				if not name:
					frappe.throw(
						"Journal Entry source is required for ledger permission checks.",
						frappe.PermissionError,
					)
				vouchers.add(name)
	for name in sorted(vouchers):
		frappe.has_permission("Journal Entry", doc=name, ptype="read", throw=True)
