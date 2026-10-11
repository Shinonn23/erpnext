# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.query_builder import Order
from frappe.query_builder.functions import Sum
from frappe.utils import get_link_to_form, nowdate, nowtime

from erpnext.stock.doctype.storage_location.storage_location import validate_storage_location_warehouse
from erpnext.stock.utils import get_combine_datetime


class PlantFloor(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link | None
		floor_name: DF.Data | None
		warehouse: DF.Link | None
		storage_location: DF.Link | None
	# end: auto-generated types

	def validate(self):
		validate_storage_location_warehouse(self.storage_location, self.warehouse)

	@frappe.whitelist()
	def make_stock_entry(self, kwargs):
		if isinstance(kwargs, str):
			kwargs = frappe.parse_json(kwargs)

		if isinstance(kwargs, dict):
			kwargs = frappe._dict(kwargs)

		items = self.get_item_details(kwargs)
		validate_storage_location_warehouse(items[0].get("storage_location"), kwargs.from_warehouse)
		validate_storage_location_warehouse(items[0].get("to_storage_location"), kwargs.to_warehouse)

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.update(
			{
				"company": kwargs.company,
				"from_warehouse": kwargs.from_warehouse,
				"to_warehouse": kwargs.to_warehouse,
				"purpose": kwargs.purpose,
				"stock_entry_type": kwargs.purpose,
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"items": items,
			}
		)

		stock_entry.set_missing_values()

		return stock_entry

	def get_item_details(self, kwargs) -> list[dict]:
		item_details = frappe.db.get_value(
			"Item", kwargs.item_code, ["item_name", "stock_uom", "item_group", "description"], as_dict=True
		)
		item_details.update(
			{
				"qty": kwargs.qty,
				"uom": item_details.stock_uom,
				"item_code": kwargs.item_code,
				"conversion_factor": 1,
				"s_warehouse": kwargs.from_warehouse,
				"t_warehouse": kwargs.to_warehouse,
				"storage_location": kwargs.from_storage_location
				or (self.storage_location if kwargs.from_warehouse == self.warehouse else None),
				"to_storage_location": kwargs.to_storage_location
				or (self.storage_location if kwargs.to_warehouse == self.warehouse else None),
			}
		)

		return [item_details]


@frappe.whitelist()
def get_stock_summary(warehouse, start=0, item_code=None, item_group=None, storage_location=None):
	frappe.has_permission("Warehouse", doc=warehouse, throw=True)
	if storage_location:
		frappe.has_permission("Storage Location", doc=storage_location, throw=True)
		validate_storage_location_warehouse(storage_location, warehouse)

	if item_code:
		frappe.has_permission("Item", doc=item_code, throw=True)

	if item_group:
		frappe.has_permission("Item Group", doc=item_group, throw=True)

	if storage_location:
		stock_details = get_stock_details_for_storage_location(
			warehouse, storage_location, start=start, item_code=item_code, item_group=item_group
		)
	else:
		stock_details = get_stock_details(warehouse, start=start, item_code=item_code, item_group=item_group)

	max_count = 0.0
	for d in stock_details:
		if storage_location:
			# Projected and reserved Bin quantities are warehouse-wide, so show on-hand stock for
			# this location without presenting warehouse totals as if they belonged to the location.
			d.projected_qty = d.actual_qty
			d.reserved_qty = 0
			d.reserved_qty_for_production = 0
			d.reserved_qty_for_sub_contract = 0
			d.reserved_qty_for_production_plan = 0
			d.reserved_stock = 0

		d.actual_or_pending = (
			d.projected_qty + d.reserved_qty + d.reserved_qty_for_production + d.reserved_qty_for_sub_contract
		)
		d.pending_qty = 0
		d.total_reserved = d.reserved_qty + d.reserved_qty_for_production + d.reserved_qty_for_sub_contract
		if d.actual_or_pending > d.actual_qty:
			d.pending_qty = d.actual_or_pending - d.actual_qty

		d.max_count = max(d.actual_or_pending, d.actual_qty, d.total_reserved, max_count)
		max_count = d.max_count
		d.item_link = get_link_to_form("Item", d.item_code)

	return stock_details


def get_stock_details_for_storage_location(warehouse, storage_location, start=0, item_code=None, item_group=None):
	item_table = frappe.qb.DocType("Item")
	sle = frappe.qb.DocType("Stock Ledger Entry")
	actual_qty = Sum(sle.actual_qty)
	posting_datetime = get_combine_datetime(nowdate(), nowtime())

	query = (
		frappe.qb.from_(sle)
		.inner_join(item_table)
		.on(sle.item_code == item_table.name)
		.select(
			sle.item_code,
			actual_qty.as_("actual_qty"),
			item_table.item_name,
			item_table.item_group,
			item_table.image,
		)
		.where(
			(sle.warehouse == warehouse)
			& (sle.storage_location == storage_location)
			& (sle.is_cancelled == 0)
			& (sle.posting_datetime <= posting_datetime)
		)
		.groupby(sle.item_code, item_table.item_name, item_table.item_group, item_table.image)
		.having(actual_qty != 0)
		.limit(20)
		.offset(start)
		.orderby(actual_qty, order=Order.desc)
	)

	if item_code:
		query = query.where(sle.item_code == item_code)

	if item_group:
		query = query.where(item_table.item_group == item_group)

	return query.run(as_dict=True)


def get_stock_details(warehouse, start=0, item_code=None, item_group=None):
	item_table = frappe.qb.DocType("Item")
	bin_table = frappe.qb.DocType("Bin")

	query = (
		frappe.qb.from_(bin_table)
		.inner_join(item_table)
		.on(bin_table.item_code == item_table.name)
		.select(
			bin_table.item_code,
			bin_table.actual_qty,
			bin_table.projected_qty,
			bin_table.reserved_qty,
			bin_table.reserved_qty_for_production,
			bin_table.reserved_qty_for_sub_contract,
			bin_table.reserved_qty_for_production_plan,
			bin_table.reserved_stock,
			item_table.item_name,
			item_table.item_group,
			item_table.image,
		)
		.where(bin_table.warehouse == warehouse)
		.limit(20)
		.offset(start)
		.orderby(bin_table.actual_qty, order=Order.desc)
	)

	if item_code:
		query = query.where(bin_table.item_code == item_code)

	if item_group:
		query = query.where(item_table.item_group == item_group)

	return query.run(as_dict=True)
