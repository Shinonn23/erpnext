from frappe import _


# Todo: non_standard_fieldnames is to be decided
def get_data():
	return {
		"fieldname": "stock_entry",
		"non_standard_fieldnames": {
			"Stock Reservation Entry": "from_voucher_no",
			"Quality Inspection": "reference_name",
		},
		"dynamic_links": {"reference_name": ["Stock Entry", "reference_type"]},
		"transactions": [
			{"label": _("Stock Reservation"), "items": ["Stock Reservation Entry"]},
			{"label": _("Reference"), "items": ["Quality Inspection"]},
		],
	}
