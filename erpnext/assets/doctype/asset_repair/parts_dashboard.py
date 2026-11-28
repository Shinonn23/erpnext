"""Native document connections for Asset Repair and Stock Entry."""

from frappe import _


def _add(data, label, items, fields=None, internal=None, dynamic=None):
	if not data.get("fieldname") and fields:
		data["fieldname"] = next(iter(fields.values()))
	data.setdefault("transactions", []).append({"label": _(label), "items": items})
	data.setdefault("non_standard_fieldnames", {}).update(fields or {})
	data.setdefault("internal_links", {}).update(internal or {})
	data.setdefault("dynamic_links", {}).update(dynamic or {})
	return data


def asset_repair(data):
	for group in data.get("transactions") or []:
		if not group.get("label") and group.get("items") == ["Stock Entry"]:
			group["label"] = _("Stock Movements")
	_add(
		data,
		"Inspection and Borrowing",
		["Quality Inspection", "Asset Movement"],
		{"Material Request": "repair_source", "Quality Inspection": "reference_name"},
		{"Asset Movement": "return_movement"},
		{
			"repair_source": ["Asset Repair", "repair_source_type"],
			"reference_name": ["Asset Repair", "reference_type"],
		},
	)
	return _add(data, "Spare Parts", ["Material Request"])


def stock_entry(data):
	_add(data, "Parts Returns", ["Stock Entry"], {"Stock Entry": "repair_return_issue"})
	return data
