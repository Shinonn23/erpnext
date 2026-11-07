"""Service equipment: a customer's machine kept in the service team's care, recorded as an Asset without value.

The Asset field `service_nonfinancial` is set by the Connect Diagnostics Setup app for an Asset whose Asset Owner is
the owner named in its Service Equipment Settings. Such an Asset has no cost, depreciation or accounting entries, so
the fixed asset reports leave it out. The field exists only where the app is installed: without it nothing is left out.
"""

import frappe
from frappe.query_builder.functions import IfNull

FLAG = "service_nonfinancial"


def has_flag():
	return frappe.db.has_column("Asset", FLAG)


def names():
	"""Names of the Assets that are service equipment."""
	return frappe.get_all("Asset", filters={FLAG: 1}, pluck="name") if has_flag() else []


def not_service_equipment(asset):
	"""A query condition that keeps an Asset which is not service equipment."""
	return IfNull(asset[FLAG], 0) == 0 if has_flag() else asset.name.isnotnull()
