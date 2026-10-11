# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


from frappe import _

from erpnext.stock.report.delayed_item_report.delayed_item_report import DelayedItemReport
from erpnext.stock.report.storage_location_utils import add_location_column, location_key


def execute(filters=None):
	columns, data = [], []

	columns, data = DelayedOrderReport(filters).run()

	return columns, data


class DelayedOrderReport(DelayedItemReport):
	def run(self):
		columns = self.get_columns()
		columns.append({"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150})
		return add_location_column(columns, self.filters), self.get_data(consolidated=True) or []

	def get_data(self, consolidated=False):
		data = super().get_data(consolidated) or []

		so_list = []
		result = []
		for d in data:
			key = (d.sales_order, d.warehouse, location_key(d, self.filters))
			if key not in so_list:
				if any(existing.sales_order == d.sales_order for existing in result):
					d.grand_total = None
				so_list.append(key)
				result.append(d)

		return result

	def get_columns(self):
		based_on = self.filters.get("based_on")

		return [
			{
				"label": _(based_on),
				"fieldname": "name",
				"fieldtype": "Link",
				"options": based_on,
				"width": 100,
			},
			{
				"label": _("Customer"),
				"fieldname": "customer",
				"fieldtype": "Link",
				"options": "Customer",
				"width": 200,
			},
			{
				"label": _("Shipping Address"),
				"fieldname": "shipping_address_name",
				"fieldtype": "Link",
				"options": "Address",
				"width": 140,
			},
			{
				"label": _("Expected Delivery Date"),
				"fieldname": "delivery_date",
				"fieldtype": "Date",
				"width": 100,
			},
			{
				"label": _("Actual Delivery Date"),
				"fieldname": "posting_date",
				"fieldtype": "Date",
				"width": 100,
			},
			{"label": _("Amount"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 100},
			{"label": _("Delayed Days"), "fieldname": "delayed_days", "fieldtype": "Int", "width": 100},
			{
				"label": _("Sales Order"),
				"fieldname": "sales_order",
				"fieldtype": "Link",
				"options": "Sales Order",
				"width": 150,
			},
			{"label": _("Customer PO"), "fieldname": "po_no", "fieldtype": "Data", "width": 110},
		]
