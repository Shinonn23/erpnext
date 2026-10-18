# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe import _
from frappe.utils import getdate, today

from erpnext.regulatory_affairs import reporting


def execute(filters=None):
	"""Master-data gaps across Items, registrations, documents and establishments, most severe first."""
	filters = reporting.clean_filters(filters)
	today_date = getdate(today())
	gap_types = reporting.selected_gap_types(filters)
	data = reporting.load_gap_data(reporting.required_datasets(gap_types, filters), today_date)
	gaps = reporting.build_gaps(data, gap_types, today_date)
	if filters.get("item"):
		reporting.attach_registration_items(gaps, data.get("lines") or [], today_date)
	gaps = [gap for gap in gaps if reporting.gap_matches_filters(gap, filters)]
	return get_columns(), reporting.sort_gaps(gaps)


def get_columns():
	column = reporting.column
	return [
		column("severity", _("Severity"), "Data", 80),
		column("gap_type", _("Gap Type"), "Data", 260),
		column("subject_type", _("Subject Type"), "Data", 200),
		column("subject", _("Subject"), "Dynamic Link", 180, "subject_type"),
		column("item_name", _("Item Name"), "Data", 180),
		column("detail", _("Detail"), "Data", 380),
		column("owner", _("Owner"), "Link", 140, "User"),
	]
