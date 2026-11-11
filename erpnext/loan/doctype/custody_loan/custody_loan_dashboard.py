from frappe import _


def get_data():
	return {
		"fieldname": "custody_loan",
		"non_standard_fieldnames": {"Quality Inspection": "reference_name"},
		"dynamic_links": {"reference_name": ["Custody Loan", "reference_type"]},
		"transactions": [{"label": _("Quality"), "items": ["Quality Inspection"]}],
	}
