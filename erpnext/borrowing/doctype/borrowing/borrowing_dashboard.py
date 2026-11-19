from frappe import _


def get_data():
	"""The borrowing's chain (UX WP4, Siwat 18 Nov 2026): the request it comes from, the Pick List and issue documents
	made from it, the return documents made from those, its write-offs and inspections."""
	return {
		"fieldname": "borrowing",
		"non_standard_fieldnames": {"Quality Inspection": "reference_name"},
		"dynamic_links": {"reference_name": ["Borrowing", "reference_type"]},
		"internal_links": {"Material Request": "material_request"},
		"transactions": [
			{"label": _("Request"), "items": ["Material Request"]},
			{"label": _("Fulfilment"), "items": ["Pick List", "Delivery Note", "Asset Movement"]},
			{"label": _("Write-off and Sale"), "items": ["Borrowing Adjustment", "Sales Invoice"]},
			{"label": _("Quality"), "items": ["Quality Inspection"]},
		],
	}
