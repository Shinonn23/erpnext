# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from collections import defaultdict
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum
from frappe.utils import flt, get_datetime, get_time, getdate, now_datetime

from erpnext.stock.doctype.inventory_dimension.inventory_dimension import get_inventory_dimensions
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	get_serial_nos_based_on_posting_date,
)
from erpnext.stock.doctype.storage_location.storage_location import validate_storage_location_warehouse


class StockCount(Document):
	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.stock.doctype.stock_count_dimension.stock_count_dimension import StockCountDimension
		from erpnext.stock.doctype.stock_count_item.stock_count_item import StockCountItem
		from erpnext.stock.doctype.stock_count_selected_item.stock_count_selected_item import (
			StockCountSelectedItem,
		)

		company: DF.Link
		count_template: DF.Link | None
		count_type: DF.Literal["", "Monthly", "Annual"]
		dimensions: DF.Table[StockCountDimension]
		item_count: DF.Int | None
		item_selection: DF.Literal["Random Sample", "Selected Items"]
		items: DF.Table[StockCountItem]
		naming_series: DF.Literal["STOCK-COUNT-.YYYY.-"]
		quarantine_storage_location: DF.Link | None
		quarantine_warehouse: DF.Link | None
		scan_barcode: DF.Data | None
		scan_storage_location: DF.Link | None
		selected_items: DF.Table[StockCountSelectedItem]
		snapshot_datetime: DF.Datetime | None
		status: DF.Literal["", "Draft", "In Progress", "Completed"]
		stock_entry: DF.Link | None
		stock_reconciliation: DF.Link | None
		warehouse: DF.Link

	def validate(self):
		validate_storage_location_warehouse(self.get("scan_storage_location"), self.warehouse)
		self.validate_generated_links()
		if self.warehouse:
			warehouse = frappe.db.get_value(
				"Warehouse", self.warehouse, ["company", "is_group", "disabled"], as_dict=True
			)
			if not warehouse:
				frappe.throw(_("Warehouse {0} does not exist.").format(self.warehouse))
			if warehouse.company != self.company:
				frappe.throw(_("Warehouse must belong to Company {0}.").format(self.company))
			if warehouse.is_group or warehouse.disabled:
				frappe.throw(_("Select an enabled, non-group Warehouse."))
			self.validate_quarantine_storage_location()
			self.validate_dimensions()

		if self.docstatus == 0 and not self.snapshot_datetime and self.items:
			frappe.throw(
				_(
					"Stock Count items are generated from the stock snapshot. Do not add items manually. "
					"To choose the items, use Items to Count (Monthly count, Selected Items)."
				)
			)

		self.validate_item_selection()
		if self.count_type == "Monthly":
			if not self.selects_items() and flt(self.item_count) <= 0:
				frappe.throw(_("Number of Item Codes to Sample must be greater than zero."))
			self.validate_quarantine_warehouse()

		if self.docstatus == 0 and self.status == "In Progress" and not self.snapshot_datetime:
			frappe.throw(_("Prepare the count to create its stock snapshot."))
		if self.docstatus == 0 and self.snapshot_datetime:
			self.keep_snapshot_rows()

		if getattr(self, "_action", None) == "submit":
			if self.status != "In Progress" or not self.snapshot_datetime:
				frappe.throw(_("Prepare this Stock Count before submitting it."))
			self.validate_count_lines()

	def selects_items(self):
		"""True for a Monthly count whose items are chosen; every other count samples or counts all."""
		return self.count_type == "Monthly" and self.item_selection == "Selected Items"

	def validate_item_selection(self):
		before = self.get_doc_before_save()
		prepared = bool(before and before.get("snapshot_datetime"))
		if prepared and selection_signature(self) != selection_signature(before):
			frappe.throw(_("The item selection cannot be changed after the count is prepared."))
		if prepared or self.snapshot_datetime:
			return
		if self.count_type != "Monthly":
			# An Annual count always counts every item with stock; nothing chosen can stay on it.
			self.item_selection = "Random Sample"
			self.count_template = None
			self.set("selected_items", [])
			return
		if not self.item_selection:
			self.item_selection = "Random Sample"
		if self.item_selection != "Selected Items":
			self.count_template = None
			self.set("selected_items", [])
			return
		if self.count_template:
			self.validate_count_template()
		if not self.selected_items:
			frappe.throw(_("Add at least one item to Items to Count, or choose a Count Template."))
		validate_countable_item_rows(self.selected_items)

	def validate_count_template(self):
		template = frappe.db.get_value(
			"Stock Count Template", self.count_template, ["company", "warehouse", "disabled"], as_dict=True
		)
		if not template:
			frappe.throw(_("Count Template {0} does not exist.").format(self.count_template))
		if template.disabled:
			frappe.throw(_("Count Template {0} is disabled.").format(self.count_template))
		if template.company != self.company:
			frappe.throw(_("Count Template {0} belongs to another Company.").format(self.count_template))
		if template.warehouse and template.warehouse != self.warehouse:
			frappe.throw(
				_("Count Template {0} is for Warehouse {1}.").format(self.count_template, template.warehouse)
			)

	def copy_template_items(self):
		"""Rows chosen by hand stay; a template fills an empty list, so a later template edit never alters a prepared count."""
		if not self.selects_items() or not self.count_template or self.selected_items:
			return
		self.validate_count_template()
		for item_code in frappe.get_all(
			"Stock Count Template Item",
			filters={"parent": self.count_template, "parenttype": "Stock Count Template"},
			order_by="idx",
			pluck="item_code",
		):
			self.append("selected_items", {"item_code": item_code})

	def validate_quarantine_warehouse(self):
		if not self.quarantine_warehouse:
			frappe.throw(_("Select a Quarantine Warehouse for a monthly count."))
		if self.warehouse == self.quarantine_warehouse:
			frappe.throw(_("Quarantine Warehouse must be different from the counted Warehouse."))

		warehouse = frappe.db.get_value(
			"Warehouse", self.quarantine_warehouse, ["company", "is_group", "disabled"], as_dict=True
		)
		if not warehouse or warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Select an enabled, non-group Quarantine Warehouse in the same Company."))
		self.validate_quarantine_storage_location()

	def validate_quarantine_storage_location(self):
		location = self.quarantine_storage_location
		if not location:
			return
		if not frappe.db.exists("DocType", "Storage Location"):
			frappe.throw(_("Storage Location is not configured on this site."))
		location_data = frappe.db.get_value(
			"Storage Location", location, ["warehouse", "disabled"], as_dict=True
		)
		if (
			not location_data
			or location_data.disabled
			or location_data.warehouse != self.quarantine_warehouse
		):
			frappe.throw(_("Quarantine Storage Location must belong to the Quarantine Warehouse."))

	def get_countable_dimensions(self):
		child_meta = frappe.get_meta("Stock Count Item")
		reconciliation_meta = frappe.get_meta("Stock Reconciliation Item")
		dimensions = []
		for dimension in get_inventory_dimensions():
			if (
				dimension.get("fieldname")
				and dimension.get("source_fieldname")
				and frappe.db.has_column("Stock Ledger Entry", dimension.fieldname)
				and child_meta.has_field(dimension.source_fieldname)
				and reconciliation_meta.has_field(dimension.source_fieldname)
			):
				dimensions.append(dimension)
		return dimensions

	def get_dimension_filters(self):
		dimensions = self.get_countable_dimensions()
		by_name = {dimension.dimension_name: dimension for dimension in dimensions}
		filters = {}
		seen = set()
		for row in self.dimensions:
			if row.inventory_dimension in seen:
				frappe.throw(
					_("Inventory Dimension {0} is selected more than once.").format(row.inventory_dimension)
				)
			seen.add(row.inventory_dimension)
			dimension = by_name.get(row.inventory_dimension)
			if not dimension:
				frappe.throw(
					_(
						"Inventory Dimension {0} is not available on Stock Count and Stock Reconciliation."
					).format(row.inventory_dimension)
				)
			filters[dimension.fieldname] = row.dimension_value
		return dimensions, filters

	def validate_dimensions(self):
		if self.warehouse:
			dimensions, _filters = self.get_dimension_filters()
			storage_location_dimension = next(
				(dimension for dimension in dimensions if dimension.dimension_name == "Storage Location"),
				None,
			)
			if not storage_location_dimension:
				return
			selected_location = next(
				(
					row.dimension_value
					for row in self.dimensions
					if row.inventory_dimension == storage_location_dimension.dimension_name
				),
				None,
			)
			if selected_location:
				location = frappe.db.get_value(
					"Storage Location", selected_location, ["warehouse", "disabled"], as_dict=True
				)
				if (
					not location
					or location.disabled
					or location.warehouse != self.warehouse
				):
					frappe.throw(_("Storage Location must be active and belong to the selected Warehouse."))

	def keep_snapshot_rows(self):
		"""The snapshot owns the rows and their System Qty; an edited or uploaded Items table changes only counts."""
		before = self.get_doc_before_save()
		if not before or not before.get("snapshot_datetime") or not before.get("items"):
			return
		fields = sorted({dimension.source_fieldname for dimension in self.get_countable_dimensions()})

		def key(row):
			return (
				row.item_code,
				row.batch_no or "",
				row.serial_no or "",
				tuple(row.get(fieldname) or "" for fieldname in fields),
			)

		stored = {key(row): flt(row.system_qty) for row in before.get("items")}
		present = set()
		for row in self.get("items"):
			row_key = key(row)
			if row_key not in stored:
				frappe.throw(
					_(
						"Row {0}: Stock Count items are generated from the stock snapshot. Do not add items manually."
					).format(row.idx)
				)
			row.system_qty = stored[row_key]
			present.add(row_key)
		missing = [row_key for row_key in stored if row_key not in present]
		if missing:
			frappe.throw(
				_(
					"Item {0} from the stock snapshot is missing. Keep every snapshot row and count zero when nothing is found."
				).format(missing[0][0])
			)

	def validate_count_lines(self):
		seen = set()
		seen_serials = set()
		dimension_fields = sorted(
			{dimension.source_fieldname for dimension in self.get_countable_dimensions()}
		)
		for row in self.items:
			if not row.item_code:
				frappe.throw(_("Row {0}: Item Code is required.").format(row.idx))
			item = frappe.get_cached_doc("Item", row.item_code)
			if not item.is_stock_item:
				frappe.throw(_("Row {0}: Item must be a stock item.").format(row.idx))
			if row.storage_location:
				location_warehouse = frappe.db.get_value(
					"Storage Location", row.storage_location, "warehouse"
				)
				if location_warehouse != self.warehouse:
					frappe.throw(
						_("Row {0}: Storage Location must belong to the counted Warehouse.").format(row.idx)
					)
			if item.has_batch_no and not row.batch_no:
				frappe.throw(_("Row {0}: Batch No is required for this item.").format(row.idx))
			if item.has_serial_no and not row.serial_no:
				frappe.throw(_("Row {0}: Serial No is required for this item.").format(row.idx))
			if row.serial_no and row.serial_no in seen_serials:
				frappe.throw(
					_("Row {0}: Serial No {1} is listed more than once.").format(row.idx, row.serial_no)
				)
			if not row.is_counted:
				frappe.throw(
					_("Row {0}: mark the count as complete, including a zero count.").format(row.idx)
				)
			if row.counted_qty is None or flt(row.counted_qty) < 0:
				frappe.throw(_("Row {0}: Counted Qty must be zero or greater.").format(row.idx))
			if row.serial_no and flt(row.counted_qty) not in (0, 1):
				frappe.throw(_("Row {0}: Serial No count must be zero or one.").format(row.idx))
			row.difference_qty = flt(row.counted_qty) - flt(row.system_qty)

			key = (
				row.item_code,
				row.batch_no or "",
				row.serial_no or "",
				tuple(row.get(fieldname) or "" for fieldname in dimension_fields),
			)
			if key in seen:
				frappe.throw(
					_("Row {0}: duplicate Item, Batch, Serial, and Inventory Dimensions combination.").format(
						row.idx
					)
				)
			seen.add(key)
			if row.serial_no:
				seen_serials.add(row.serial_no)

	@frappe.whitelist()
	def prepare_count(self):
		self.check_permission("write")
		if self.docstatus != 0:
			frappe.throw(_("Only a draft Stock Count can be prepared."))
		if self.status != "Draft" or self.items:
			frappe.throw(_("This Stock Count has already been prepared."))
		if not self.company or not self.warehouse or not self.count_type:
			frappe.throw(_("Company, Count Type, and Warehouse are required."))
		if self.count_type == "Monthly":
			self.validate_quarantine_warehouse()
			self.copy_template_items()
			if not self.selects_items() and flt(self.item_count) <= 0:
				frappe.throw(_("Number of Item Codes to Sample must be greater than zero."))

		self.validate()
		snapshot = now_datetime()
		self.snapshot_datetime = snapshot
		item_codes = self.get_item_codes_to_count(snapshot)
		for item_code in item_codes:
			self.append_snapshot_rows(item_code, snapshot)

		skipped = self.flags.get("not_counted_items") or []
		if not self.items:
			if skipped:
				frappe.throw(
					_("None of the selected items has stock in Warehouse {0}: {1}").format(
						self.warehouse, ", ".join(skipped)
					)
				)
			frappe.throw(_("No stock was found in Warehouse {0}.").format(self.warehouse))
		if skipped:
			frappe.msgprint(
				_("Not counted, no stock in {0}: {1}").format(self.warehouse, ", ".join(skipped)),
				indicator="orange",
			)

		self.status = "In Progress"
		self.save()
		return len(self.items)

	def get_stock_ledger_groups(self, snapshot, item_code=None):
		dimensions, dimension_filters = self.get_dimension_filters()
		sle = DocType("Stock Ledger Entry")
		group_fields = [sle.item_code, sle.batch_no, sle.serial_and_batch_bundle]
		select_fields = [
			sle.item_code,
			sle.batch_no,
			sle.serial_and_batch_bundle,
			Sum(sle.actual_qty).as_("qty"),
		]
		for dimension in dimensions:
			group_fields.append(sle[dimension.fieldname])
			select_fields.append(sle[dimension.fieldname].as_(dimension.fieldname))

		query = (
			frappe.qb.from_(sle)
			.select(*select_fields)
			.where(
				(sle.warehouse == self.warehouse)
				& (sle.company == self.company)
				& (sle.is_cancelled == 0)
				& (sle.posting_datetime <= snapshot)
			)
		)
		if item_code:
			query = query.where(sle.item_code == item_code)
		for fieldname, value in dimension_filters.items():
			query = query.where(sle[fieldname] == value)
		ledger_groups = query.groupby(*group_fields).having(Sum(sle.actual_qty) != 0).run(as_dict=True)
		bundle_names = {row.serial_and_batch_bundle for row in ledger_groups if row.serial_and_batch_bundle}
		batch_entries = []
		if bundle_names:
			bundle_entry = DocType("Serial and Batch Entry")
			batch_entries = (
				frappe.qb.from_(bundle_entry)
				.select(bundle_entry.parent, bundle_entry.batch_no, Sum(bundle_entry.qty).as_("qty"))
				.where(bundle_entry.parent.isin(bundle_names))
				.where(bundle_entry.batch_no.isnotnull())
				.groupby(bundle_entry.parent, bundle_entry.batch_no)
				.run(as_dict=True)
			)
		batches_by_bundle = defaultdict(list)
		for entry in batch_entries:
			batches_by_bundle[entry.parent].append(entry)

		grouped = {}
		for row in ledger_groups:
			if row.batch_no:
				batch_quantities = [(row.batch_no, row.qty)]
			elif (
				row.serial_and_batch_bundle
				and frappe.get_cached_value("Item", row.item_code, "has_batch_no")
			):
				batch_quantities = [
					(entry.batch_no, entry.qty)
					for entry in batches_by_bundle.get(row.serial_and_batch_bundle, [])
				]
			else:
				batch_quantities = [(None, row.qty)]

			for batch_no, qty in batch_quantities:
				dimension_values = tuple(
					(row.get(dimension.fieldname) or None) for dimension in dimensions
				)
				key = (row.item_code, batch_no, dimension_values)
				group = grouped.setdefault(
					key,
					{
						"item_code": row.item_code,
						"batch_no": batch_no,
						"qty": 0.0,
						**{
							dimension.fieldname: row.get(dimension.fieldname)
							for dimension in dimensions
						},
					},
				)
				group["qty"] += flt(qty)

		return [frappe._dict(group) for group in grouped.values() if flt(group["qty"]) > 0]

	def get_item_codes_to_count(self, snapshot):
		self.flags.not_counted_items = []
		item_codes = sorted({row.item_code for row in self.get_stock_ledger_groups(snapshot)})
		if not item_codes:
			self.flags.not_counted_items = self.get_selected_item_codes()
			return []
		item_codes = frappe.get_all(
			"Item",
			filters={
				"name": ["in", item_codes],
				"is_stock_item": 1,
				"disabled": 0,
			},
			pluck="name",
		)
		item_codes.sort()

		if self.selects_items():
			# Only the chosen items that still have stock are counted: a batch item without a positive batch
			# balance has no row, so no zero row without a batch can appear.
			chosen = self.get_selected_item_codes()
			self.flags.not_counted_items = [code for code in chosen if code not in item_codes]
			return [code for code in item_codes if code in chosen]

		if self.count_type == "Monthly":
			import random

			random.shuffle(item_codes)
			item_codes = item_codes[: int(self.item_count)]

		return item_codes

	def get_selected_item_codes(self):
		return list(dict.fromkeys(row.item_code for row in self.get("selected_items") or [] if row.item_code))

	def append_snapshot_rows(self, item_code, snapshot):
		item = frappe.get_cached_doc("Item", item_code)
		posting_datetime = get_datetime(snapshot)
		dimensions, _filters = self.get_dimension_filters()
		groups = [
			row
			for row in self.get_stock_ledger_groups(posting_datetime, item_code)
			if row.item_code == item_code
		]
		for group in groups:
			dimension_values = {
				dimension.fieldname: group.get(dimension.fieldname) for dimension in dimensions
			}
			row_values = {
				dimension.source_fieldname: group.get(dimension.fieldname)
				for dimension in dimensions
				if group.get(dimension.fieldname) is not None
			}
			base_row = {
				"item_code": item_code,
				"batch_no": group.batch_no,
				"expiry_date": frappe.get_cached_value("Batch", group.batch_no, "expiry_date")
				if group.batch_no
				else None,
				"system_qty": flt(group.qty),
				"counted_qty": 0,
				**row_values,
			}
			if item.has_serial_no:
				serials = get_serial_nos_based_on_posting_date(
					frappe._dict(
						{
							"item_code": item_code,
							"warehouse": self.warehouse,
							"posting_datetime": posting_datetime,
							"check_serial_nos": True,
							"batch_no": group.batch_no,
							"inventory_dimensions_dict": dimension_values,
						}
					),
					[],
				)
				for serial in serials:
					self.append("items", {**base_row, "serial_no": serial, "system_qty": 1})
			else:
				self.append("items", base_row)

	def posts_variance(self):
		"""A Monthly count of a warehouse with a Count Variance Account posts its differences as a Stock
		Reconciliation (the test cost of a laboratory); others move the counted stock to quarantine."""
		return self.count_type == "Monthly" and bool(get_count_variance_account(self.warehouse))

	def on_submit(self):
		if self.count_type == "Monthly" and not self.posts_variance():
			stock_entry = self.make_quarantine_stock_entry()
			if stock_entry:
				self.db_set("stock_entry", stock_entry.name)
		else:
			stock_reconciliation = self.make_stock_reconciliation()
			if stock_reconciliation:
				self.db_set("stock_reconciliation", stock_reconciliation.name)
		self.db_set("status", "Completed")

	def on_cancel(self):
		for doctype, name in (
			("Stock Entry", self.stock_entry),
			("Stock Reconciliation", self.stock_reconciliation),
		):
			if not name:
				continue
			doc = frappe.get_doc(doctype, name)
			if doc.get("stock_count") != self.name:
				frappe.throw(_("Linked {0} {1} was not generated by this Stock Count.").format(doctype, name))
			if doc.docstatus == 1:
				frappe.throw(
					_("Cancel linked {0} {1} before cancelling this Stock Count.").format(doctype, name)
				)
			if doc.docstatus == 0:
				frappe.delete_doc(doctype, name, ignore_permissions=True)

	def before_update_after_submit(self):
		self.validate_generated_links()

	def validate_generated_links(self):
		previous = self.get_doc_before_save()
		for fieldname in ("stock_entry", "stock_reconciliation"):
			if (self.get(fieldname) or "") != ((previous.get(fieldname) if previous else None) or ""):
				frappe.throw(_("Generated document links can only be set by Stock Count submission."))

	def has_differences(self):
		return {
			row.item_code for row in self.items if abs(flt(row.system_qty) - flt(row.counted_qty)) > 0.000001
		}

	def get_row_dimension_values(self, row):
		return {
			dimension.source_fieldname: row.get(dimension.source_fieldname)
			for dimension in self.get_countable_dimensions()
		}

	def get_current_stock_qty(self, row):
		dimensions = self.get_countable_dimensions()
		return sum(
			flt(group.qty)
			for group in self.get_stock_ledger_groups(now_datetime(), row.item_code)
			if (group.batch_no or "") == (row.batch_no or "")
			and all(
				(group.get(dimension.fieldname) or "") == (row.get(dimension.source_fieldname) or "")
				for dimension in dimensions
			)
		)

	def make_quarantine_stock_entry(self):
		different_items = self.has_differences()
		if not different_items:
			return

		transfer_rows = defaultdict(lambda: {"qty": 0.0, "serial_nos": []})
		for row in self.items:
			if row.item_code not in different_items or not flt(row.counted_qty):
				continue

			dimension_values = self.get_row_dimension_values(row)
			dimension_key = tuple(sorted(dimension_values.items()))
			key = (row.item_code, row.batch_no or "", dimension_key)
			if row.serial_no:
				serial_warehouse = frappe.db.get_value("Serial No", row.serial_no, "warehouse")
				if (
					flt(row.system_qty)
					and serial_warehouse == self.warehouse
					and self.get_current_stock_qty(row) > 0
				):
					transfer_rows[key]["qty"] += 1
					transfer_rows[key]["serial_nos"].append(row.serial_no)
			else:
				available_qty = self.get_current_stock_qty(row)
				transfer_qty = min(flt(row.system_qty), flt(row.counted_qty), flt(available_qty))
				if transfer_qty > 0:
					transfer_rows[key]["qty"] += transfer_qty

		transfer_rows = {key: values for key, values in transfer_rows.items() if values["qty"] > 0}
		if not transfer_rows:
			return

		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = self.company
		stock_entry.purpose = "Material Transfer"
		stock_entry.stock_entry_type = frappe.db.get_value(
			"Stock Entry Type", {"purpose": "Material Transfer", "is_standard": 1}, "name"
		)
		if not stock_entry.stock_entry_type:
			frappe.throw(_("Create a standard Material Transfer Stock Entry Type before closing this count."))
		stock_entry.from_warehouse = self.warehouse
		stock_entry.to_warehouse = self.quarantine_warehouse
		stock_entry.remarks = _("Created from Stock Count {0}.").format(self.name)
		stock_entry.stock_count = self.name

		stock_entry_item_meta = frappe.get_meta("Stock Entry Detail")
		for (item_code, batch_no, dimension_key), values in transfer_rows.items():
			entry_row = {
				"item_code": item_code,
				"qty": values["qty"],
				"s_warehouse": self.warehouse,
				"t_warehouse": self.quarantine_warehouse,
				"batch_no": batch_no,
				"serial_no": "\n".join(values["serial_nos"]),
			}
			for fieldname, value in dimension_key:
				if stock_entry_item_meta.has_field(fieldname):
					entry_row[fieldname] = value
				transfer_field = f"to_{fieldname}"
				if stock_entry_item_meta.has_field(transfer_field):
					entry_row[transfer_field] = (
						self.quarantine_storage_location if fieldname == "storage_location" else value
					)
			stock_entry.append("items", entry_row)

		stock_entry.insert(ignore_permissions=True)
		return stock_entry

	def make_stock_reconciliation(self):
		reconciliation_rows = defaultdict(lambda: {"system_qty": 0.0, "counted_qty": 0.0, "serial_nos": []})
		for row in self.items:
			dimension_values = self.get_row_dimension_values(row)
			key = (row.item_code, row.batch_no or "", tuple(sorted(dimension_values.items())))
			values = reconciliation_rows[key]
			values["system_qty"] += flt(row.system_qty)
			values["counted_qty"] += flt(row.counted_qty)
			if row.serial_no and flt(row.system_qty):
				values.setdefault("system_serial_nos", []).append(row.serial_no)
			if row.serial_no and flt(row.counted_qty):
				values["serial_nos"].append(row.serial_no)

		different_rows = {
			key: values
			for key, values in reconciliation_rows.items()
			if abs(values["system_qty"] - values["counted_qty"]) > 0.000001
			or set(values.get("system_serial_nos", [])) != set(values["serial_nos"])
		}
		if not different_rows:
			return

		reconciliation = frappe.new_doc("Stock Reconciliation")
		reconciliation.company = self.company
		reconciliation.purpose = "Stock Reconciliation"
		reconciliation.posting_date = getdate(self.snapshot_datetime)
		reconciliation.posting_time = get_time(self.snapshot_datetime)
		reconciliation.set_posting_time = 1
		reconciliation.set_warehouse = self.warehouse
		reconciliation.stock_count = self.name
		if reconciliation.meta.has_field("stock_adjustment_reason"):
			reconciliation.stock_adjustment_reason = _("Physical count differences from Stock Count {0}.").format(
				self.name
			)

		reconciliation_item_meta = frappe.get_meta("Stock Reconciliation Item")
		for (item_code, batch_no, dimension_key), values in different_rows.items():
			item = frappe.get_cached_doc("Item", item_code)
			row = {
				"item_code": item_code,
				"warehouse": self.warehouse,
				"qty": values["counted_qty"],
				"use_serial_batch_fields": 1,
				"batch_no": batch_no,
			}
			if item.has_serial_no:
				row["serial_no"] = "\n".join(values["serial_nos"])
			for fieldname, value in dimension_key:
				if reconciliation_item_meta.has_field(fieldname):
					row[fieldname] = value
			reconciliation.append("items", row)

		reconciliation.insert(ignore_permissions=True)
		return reconciliation


def selection_signature(doc):
	return (
		doc.get("item_selection") or "Random Sample",
		doc.get("count_template") or "",
		sorted(row.item_code for row in doc.get("selected_items") or []),
	)


def validate_countable_item_rows(rows):
	"""Rows of a template or a selection: each item once, an enabled stock item."""
	seen = set()
	for row in rows:
		if not row.item_code:
			frappe.throw(_("Row {0}: Item Code is required.").format(row.idx))
		if row.item_code in seen:
			frappe.throw(_("Row {0}: Item {1} is listed more than once.").format(row.idx, row.item_code))
		seen.add(row.item_code)
		item = frappe.db.get_value("Item", row.item_code, ["is_stock_item", "disabled"], as_dict=True)
		if not item:
			frappe.throw(_("Row {0}: Item {1} does not exist.").format(row.idx, row.item_code))
		if not item.is_stock_item or item.disabled:
			frappe.throw(_("Row {0}: Item {1} must be an enabled stock item.").format(row.idx, row.item_code))


def get_count_variance_account(warehouse):
	"""The Count Variance Account of a warehouse, or None. The field is added by the company policy layer, so
	without it (or without a value) the count behaves as before."""
	if not warehouse or not frappe.db.has_column("Warehouse", "stock_adjustment_account"):
		return None
	return frappe.db.get_value("Warehouse", warehouse, "stock_adjustment_account") or None


@frappe.whitelist()
def make_count_template(name, title):
	"""Save the items chosen on a Stock Count as a reusable Stock Count Template."""
	count = frappe.get_doc("Stock Count", name)
	count.check_permission("read")
	frappe.has_permission("Stock Count Template", "create", throw=True)
	if count.docstatus != 0:
		frappe.throw(_("Only a draft Stock Count can be saved as a template."))
	if not count.selects_items() or not count.selected_items:
		frappe.throw(_("Choose the items to count before saving a template."))
	template = frappe.get_doc(
		{
			"doctype": "Stock Count Template",
			"title": title,
			"company": count.company,
			"warehouse": count.warehouse,
			"items": [{"item_code": row.item_code} for row in count.selected_items],
		}
	)
	template.insert()
	return template.name


def allow_generated_stock_count_cancellation(doc):
	if not doc.get("stock_count"):
		return
	link_field = {"Stock Entry": "stock_entry", "Stock Reconciliation": "stock_reconciliation"}.get(doc.doctype)
	if (
		link_field
		and frappe.db.get_value("Stock Count", doc.stock_count, link_field) == doc.name
		and not frappe.db.exists(
			"Stock Count", {link_field: doc.name, "docstatus": 1, "name": ["!=", doc.stock_count]}
		)
	):
		# Cancel the generated voucher first; Stock Count still enforces this order on cancellation.
		doc.ignore_linked_doctypes = tuple(set(doc.get("ignore_linked_doctypes") or ()) | {"Stock Count"})
