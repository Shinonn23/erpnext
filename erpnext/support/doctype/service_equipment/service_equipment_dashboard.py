from frappe import _


def get_data():
	return {
		"fieldname": "service_equipment",
		"transactions": [
			{
				"label": _("Maintenance"),
				"items": ["Asset Maintenance", "Asset Maintenance Log", "Asset Repair"],
			},
			{
				"label": _("Service"),
				"items": ["Issue", "Warranty Claim", "Maintenance Visit", "Maintenance Schedule"],
			},
			{"label": _("History"), "items": ["Asset Activity"]},
		],
	}
