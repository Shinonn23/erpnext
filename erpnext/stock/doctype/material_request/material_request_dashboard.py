from frappe import _


def get_data():
	return {
		"fieldname": "material_request",
		"non_standard_fieldnames": {"Asset Movement": "reference_name"},
		"dynamic_links": {"reference_name": ["Material Request", "reference_doctype"]},
		"internal_links": {
			"Sales Order": ["items", "sales_order"],
			"Project": ["items", "project"],
		},
		"transactions": [
			{
				"label": _("Reference"),
				"items": [
					"Sales Order",
					"Request for Quotation",
					"Supplier Quotation",
					"Purchase Order",
					"Asset Movement",
				],
			},
			{"label": _("Stock"), "items": ["Stock Entry", "Purchase Receipt", "Pick List"]},
			{"label": _("Manufacturing"), "items": ["Work Order"]},
			{"label": _("Internal Transfer"), "items": ["Sales Order"]},
			{"label": _("Accounting Dimensions"), "items": ["Project"]},
		],
	}
