# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from erpnext.regulatory_affairs.constants import ITEM_CATEGORY_FIELD


def execute(filters=None):
	"""Enabled sales Items that Regulatory Affairs has not classified yet: they cannot be sold (Siwat, 5 Nov 2026).

	Filter by Item Group, then select the Items in the Item list and use Actions > Classify as ... (or open an Item
	and press Regulatory > Classify Item). The message line gives the count."""
	filters = frappe._dict(filters or {})
	conditions = {"disabled": 0, "is_sales_item": 1, ITEM_CATEGORY_FIELD: ["in", ["", None]]}
	if filters.get("item_group"):
		conditions["item_group"] = filters.item_group
	rows = frappe.get_all(
		"Item",
		filters=conditions,
		fields=["name as item", "item_name", "item_group", "is_stock_item"],
		order_by="item_group, name",
		limit_page_length=0,
	)
	columns = [
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 200},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 300},
		{
			"fieldname": "item_group",
			"label": _("Item Group"),
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 180,
		},
		{"fieldname": "is_stock_item", "label": _("Stock Item"), "fieldtype": "Check", "width": 90},
	]
	message = _("{0} sales Items are not classified yet and cannot be sold.").format(len(rows))
	return columns, rows, message
