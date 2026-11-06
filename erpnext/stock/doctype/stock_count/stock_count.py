# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.query_builder.functions import Coalesce, Sum
from frappe.utils import flt, get_datetime, get_time, getdate, now_datetime, strip_html

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
		posts_variance_snapshot: DF.Check
		variance_account_snapshot: DF.Link | None
		items: DF.Table[StockCountItem]
		naming_series: DF.Literal["STOCK-COUNT-.YYYY.-"]
		quarantine_storage_location: DF.Link | None
		found_stock_entry: DF.Link | None
		found_transfer_entry: DF.Link | None
		found_warehouse: DF.Link | None
		quarantine_warehouse: DF.Link | None
		recount_of: DF.Link | None
		recount_stock_count: DF.Link | None
		surplus_notes: DF.SmallText | None
		scan_barcode: DF.Data | None
		scan_storage_location: DF.Link | None
		selected_items: DF.Table[StockCountSelectedItem]
		snapshot_datetime: DF.Datetime | None
		status: DF.Literal["", "Draft", "In Progress", "Completed"]
		stock_entry: DF.Link | None
		stock_reconciliation: DF.Link | None
		warehouse: DF.Link

	def validate(self):
		if self.get("scan_storage_location") and self.warehouse:
			# typed or scanned: a label may be the location's code as well as its full name
			self.scan_storage_location = resolve_storage_location(self.warehouse, self.scan_storage_location)
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
					"The rows to count come from the stock system when you press Prepare Count. "
					"To choose which items are counted, use Items to Count (Monthly count, Selected Items)."
				)
			)

		self.validate_item_selection()
		self.keep_variance_mode()
		if self.count_type == "Monthly":
			if not self.selects_items() and flt(self.item_count) <= 0:
				frappe.throw(_("Number of Item Codes to Sample must be greater than zero."))
			self.validate_quarantine_if_needed()

		if self.docstatus == 0 and self.status == "In Progress" and not self.snapshot_datetime:
			frappe.throw(_("Press Prepare Count first. It lists the stock to count."))
		if self.docstatus == 0 and self.snapshot_datetime:
			self.keep_snapshot_rows()

		if getattr(self, "_action", None) == "submit":
			if self.status != "In Progress" or not self.snapshot_datetime:
				frappe.throw(
					_("Press Prepare Count and count the stock before sending this Stock Count for approval.")
				)
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
			# Server side, so a template-only save (API, import) is complete without the form's script.
			self.validate_count_template()
			self.copy_template_items()
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
		for item_code in frappe.get_all(
			"Stock Count Template Item",
			filters={"parent": self.count_template, "parenttype": "Stock Count Template"},
			order_by="idx",
			pluck="item_code",
		):
			self.append("selected_items", {"item_code": item_code})

	def uses_variance(self):
		"""Fixed at Prepare Count; before that, what the warehouse says now."""
		if self.snapshot_datetime:
			return bool(self.posts_variance_snapshot)
		return self.count_type == "Monthly" and bool(get_count_variance_account(self.warehouse))

	def keep_variance_mode(self):
		before = self.get_doc_before_save()
		if before and before.get("snapshot_datetime"):
			self.posts_variance_snapshot = before.get("posts_variance_snapshot") or 0
			self.variance_account_snapshot = before.get("variance_account_snapshot")
		elif not self.snapshot_datetime:
			self.posts_variance_snapshot = 0
			self.variance_account_snapshot = None

	def validate_quarantine_if_needed(self):
		# The fields keep their names (quarantine_*) but hold the Lost Warehouse: a monthly count moves the quantity
		# it did not find there (Connect Diagnostics, 5 Nov 2026). A count that posts its differences as a Stock
		# Reconciliation moves nothing.
		if not self.uses_variance() and not self.quarantine_warehouse and self.company:
			self.quarantine_warehouse = get_lost_warehouse(self.company, exclude=self.warehouse)
		if self.quarantine_warehouse or not self.uses_variance():
			self.validate_quarantine_warehouse()
		# What the count finds beyond the system quantity is received into the Found Warehouse (5 Nov 2026)
		if not self.uses_variance() and not self.found_warehouse and self.company:
			self.found_warehouse = get_found_warehouse(self.company, exclude=self.warehouse)
		if self.found_warehouse:
			self.validate_found_warehouse()

	def validate_found_warehouse(self):
		if self.found_warehouse in (self.warehouse, self.quarantine_warehouse):
			frappe.throw(_("Found Warehouse must differ from the counted and the Lost Warehouse."))
		warehouse = frappe.db.get_value(
			"Warehouse", self.found_warehouse, ["company", "is_group", "disabled"], as_dict=True
		)
		if not warehouse or warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
			frappe.throw(
				_("The Found Warehouse must be an active warehouse (not a group) of the same company.")
			)

	def validate_quarantine_warehouse(self):
		if not self.quarantine_warehouse:
			frappe.throw(
				_(
					"This company has no Lost Warehouse. Ask the Stock Manager to create one (Warehouse Type Lost), then choose it here."
				)
			)
		if self.warehouse == self.quarantine_warehouse:
			frappe.throw(_("Lost Warehouse must be different from the counted Warehouse."))

		warehouse = frappe.db.get_value(
			"Warehouse", self.quarantine_warehouse, ["company", "is_group", "disabled"], as_dict=True
		)
		if not warehouse or warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
			frappe.throw(
				_("The Lost Warehouse must be an active warehouse (not a group) of the same company.")
			)
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
			frappe.throw(_("Lost Storage Location must belong to the Lost Warehouse."))

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
			# A zone or a range of locations is counted at once: Storage Location may be named many times
			# (Connect Diagnostics, 5 Nov 2026); any other dimension once.
			repeatable = row.inventory_dimension == STORAGE_LOCATION_DIMENSION
			if row.inventory_dimension in seen and not repeatable:
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
			if repeatable:
				values = filters.setdefault(dimension.fieldname, [])
				if row.dimension_value and row.dimension_value not in values:
					values.append(row.dimension_value)
			else:
				filters[dimension.fieldname] = row.dimension_value
		# one value stays a plain filter; several are matched with "in"
		return dimensions, {
			fieldname: value[0] if isinstance(value, list) and len(value) == 1 else value
			for fieldname, value in filters.items()
		}

	def validate_dimensions(self):
		if self.warehouse:
			dimensions, _filters = self.get_dimension_filters()
			storage_location_dimension = next(
				(dimension for dimension in dimensions if dimension.dimension_name == "Storage Location"),
				None,
			)
			if not storage_location_dimension:
				return
			for selected_location in [
				row.dimension_value
				for row in self.dimensions
				if row.inventory_dimension == storage_location_dimension.dimension_name
				and row.dimension_value
			]:
				location = frappe.db.get_value(
					"Storage Location", selected_location, ["warehouse", "disabled"], as_dict=True
				)
				if not location or location.disabled or location.warehouse != self.warehouse:
					frappe.throw(_("Storage Location must be active and belong to the selected Warehouse."))

	def keep_snapshot_rows(self):
		"""The snapshot owns the rows and their System Qty; an edited or uploaded Items table changes only counts.
		A row the counter adds (stock found that the snapshot does not list: a serial number, a batch, another
		location or Item) holds System Qty 0, so all of it is found stock (Found Warehouse, Connect Diagnostics,
		5 Nov 2026)."""
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
			row.system_qty = stored.get(row_key, 0)
			present.add(row_key)
		missing = [row_key for row_key in stored if row_key not in present]
		if missing:
			frappe.throw(
				_(
					"Item {0} is missing from the count. Do not delete rows: enter 0 where nothing is found."
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
						_("Row {0}: the Storage Location is not in the counted Warehouse {1}.").format(
							row.idx, self.warehouse
						)
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
					_(
						"Row {0}: enter the counted quantity (0 if nothing is there), or use Set uncounted rows to 0."
					).format(row.idx)
				)
			if row.counted_qty is None or flt(row.counted_qty) < 0:
				frappe.throw(_("Row {0}: the counted quantity cannot be negative.").format(row.idx))
			if row.serial_no and flt(row.counted_qty) not in (0, 1):
				frappe.throw(
					_("Row {0}: a serial number is counted as 1 (found) or 0 (not found).").format(row.idx)
				)
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
			frappe.throw(_("This Stock Count is no longer a draft, so it cannot be prepared."))
		if self.status != "Draft" or self.items:
			frappe.throw(_("This Stock Count is already prepared. Count the rows below."))
		if not self.company or not self.warehouse or not self.count_type:
			frappe.throw(_("Choose the Company, Count Type and Warehouse first."))
		if self.count_type == "Monthly":
			self.validate_quarantine_if_needed()
			if not self.selects_items() and flt(self.item_count) <= 0:
				frappe.throw(_("Number of Item Codes to Sample must be greater than zero."))

		self.validate()
		snapshot = now_datetime()
		self.snapshot_datetime = snapshot
		self.posts_variance_snapshot = 0
		self.variance_account_snapshot = None
		variance_account = (
			get_count_variance_account(self.warehouse) if self.count_type == "Monthly" else None
		)
		if variance_account:
			validate_variance_account(variance_account, self.company)
			self.posts_variance_snapshot = 1
			self.variance_account_snapshot = variance_account
		item_codes = self.get_item_codes_to_count(snapshot)
		for item_code in item_codes:
			self.append_snapshot_rows(item_code, snapshot)
		carried = self.add_first_count_surplus_rows()

		skipped = [code for code in self.flags.get("not_counted_items") or [] if code not in carried]
		if not self.items:
			if skipped:
				frappe.throw(
					_(
						"Nothing to count: Warehouse {0} holds none of these items in the chosen locations: {1}. "
						"Choose other items or locations."
					).format(self.warehouse, ", ".join(skipped))
				)
			frappe.throw(
				_(
					"Nothing to count: Warehouse {0} holds no stock. Check the Warehouse and the chosen locations."
				).format(self.warehouse)
			)
		if skipped:
			frappe.msgprint(
				_("Not counted, Warehouse {0} holds none of them in the chosen locations: {1}").format(
					self.warehouse, ", ".join(skipped)
				),
				indicator="orange",
			)

		self.status = "In Progress"
		self.save()
		return len(self.items)

	def add_first_count_surplus_rows(self):
		"""A recount lists what the first count found beyond the system quantity, even where the system holds nothing (the
		found-elsewhere case): the rows the snapshot lacks are added with System Qty 0, so the recount always prepares and
		the second counter counts exactly what the first reported. Returns the Item codes of the rows added."""
		if not self.recount_of:
			return set()
		pending = self.split_surplus_of(self.recount_of)
		present = {self.row_key(row) for row in self.items}
		added = set()
		for key, _qty in pending.items():
			item_code, batch_no, serial_no, dimension_key = key
			if key in present:
				continue
			values = {fieldname: value for fieldname, value in dimension_key if value}
			self.append(
				"items",
				{
					"item_code": item_code,
					"batch_no": batch_no or None,
					"serial_no": serial_no or None,
					"expiry_date": frappe.get_cached_value("Batch", batch_no, "expiry_date")
					if batch_no
					else None,
					"system_qty": 0,
					"counted_qty": 0,
					**values,
				},
			)
			added.add(item_code)
		return added

	def split_surplus_of(self, name):
		"""The pending surplus (what needs a recount) of the first count ``name``."""
		return frappe.get_doc("Stock Count", name).split_surplus()[0]

	def row_key(self, row):
		return (
			row.item_code,
			row.batch_no or "",
			row.serial_no or "",
			tuple(sorted(self.get_row_dimension_values(row).items())),
		)

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
			if isinstance(value, list):
				query = query.where(sle[fieldname].isin(value or [""]))
			else:
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
			elif row.serial_and_batch_bundle and frappe.get_cached_value(
				"Item", row.item_code, "has_batch_no"
			):
				batch_quantities = [
					(entry.batch_no, entry.qty)
					for entry in batches_by_bundle.get(row.serial_and_batch_bundle, [])
				]
			else:
				batch_quantities = [(None, row.qty)]

			for batch_no, qty in batch_quantities:
				dimension_values = tuple((row.get(dimension.fieldname) or None) for dimension in dimensions)
				key = (row.item_code, batch_no, dimension_values)
				group = grouped.setdefault(
					key,
					{
						"item_code": row.item_code,
						"batch_no": batch_no,
						"qty": 0.0,
						**{dimension.fieldname: row.get(dimension.fieldname) for dimension in dimensions},
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
		Reconciliation (the test cost of a laboratory); others move the quantity not found to the Lost Warehouse.
		Decided at Prepare Count."""
		return self.count_type == "Monthly" and bool(self.posts_variance_snapshot)

	def on_submit(self):
		"""Approving the count posts its result at once (Connect Diagnostics, 5 Nov 2026): the approval is the decision and
		the count is the evidence, so the system makes AND submits the transfer of the quantity not found to the Lost
		Warehouse, the serial numbers' transfer back and what the recount confirmed (back from Lost first, the rest into
		the Found Warehouse). Nobody reviews or approves them again. If one cannot be posted, the whole approval is
		undone with a message that says which."""
		if self.count_type == "Monthly" and not self.posts_variance():
			stock_entry = self.make_quarantine_stock_entry()
			if stock_entry:
				self.db_set("stock_entry", stock_entry.name)
			self.handle_surplus()
		else:
			stock_reconciliation = self.make_stock_reconciliation()
			if stock_reconciliation:
				self.db_set("stock_reconciliation", stock_reconciliation.name)
		self.db_set("status", "Completed")

	def before_cancel(self):
		if self.recount_of:
			# the first count links to this recount; cancelling the recount leaves it as it is
			self.ignore_linked_doctypes = tuple(
				set(self.get("ignore_linked_doctypes") or ()) | {"Stock Count"}
			)

	def on_cancel(self):
		if getattr(self, "recount_stock_count", None):
			recount = frappe.db.get_value(
				"Stock Count", self.recount_stock_count, ["docstatus", "recount_of"], as_dict=True
			)
			if recount and recount.recount_of == self.name:
				if recount.docstatus == 1:
					frappe.throw(
						_("Cancel the recount {0} before cancelling this Stock Count.").format(
							self.recount_stock_count
						)
					)
				if recount.docstatus == 0:
					# a link from this count, cancelled or not, keeps Frappe from deleting the draft it names
					name = self.recount_stock_count
					self.db_set("recount_stock_count", None)
					frappe.delete_doc("Stock Count", name, ignore_permissions=True)
		# the documents the count posted are cancelled with it, the last posted first; a draft is deleted
		for fieldname, doctype in (
			("found_stock_entry", "Stock Entry"),
			("found_transfer_entry", "Stock Entry"),
			("stock_entry", "Stock Entry"),
			("stock_reconciliation", "Stock Reconciliation"),
		):
			name = getattr(self, fieldname, None)
			if not name:
				continue
			doc = frappe.get_doc(doctype, name)
			if doc.get("stock_count") != self.name:
				frappe.throw(_("Linked {0} {1} was not generated by this Stock Count.").format(doctype, name))
			if doc.docstatus == 1:
				if doctype == "Stock Reconciliation":
					frappe.throw(
						_("Cancel linked {0} {1} before cancelling this Stock Count.").format(doctype, name)
					)
				self.cancel_posted(doc)
			elif doc.docstatus == 0:
				self.db_set(fieldname, None)
				frappe.delete_doc(doctype, name, ignore_permissions=True)

	def cancel_posted(self, entry):
		"""Cancel a Stock Entry this count posted (it is the system's, so the user's warehouse rights do not apply)."""
		start = len(frappe.local.message_log)
		try:
			with system_posting(entry):
				entry.flags.ignore_permissions = True
				entry.cancel()
		except (frappe.ValidationError, frappe.PermissionError) as error:
			frappe.throw(
				_(
					"This Stock Count cannot be cancelled: Stock Entry {0}, which it posted, cannot be cancelled. {1}"
				).format(entry.name, failure_reason(error, start))
			)

	def before_update_after_submit(self):
		self.validate_generated_links()

	def validate_generated_links(self):
		previous = self.get_doc_before_save()
		for fieldname in GENERATED_LINK_FIELDS:
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
		"""The draft Material Transfer of what the count did not find (system quantity above the counted one, a serial
		no that was not counted) from the counted warehouse to the Lost Warehouse (``quarantine_warehouse``;
		Connect Diagnostics, 5 Nov 2026: the found stock stays where it is)."""
		different_items = self.has_differences()
		if not different_items:
			return

		transfer_rows = defaultdict(lambda: {"qty": 0.0, "serial_nos": []})
		for row in self.items:
			if row.item_code not in different_items:
				continue
			missing_qty = flt(row.system_qty) - flt(row.counted_qty)
			if missing_qty <= 0:
				continue

			dimension_values = self.get_row_dimension_values(row)
			dimension_key = tuple(sorted(dimension_values.items()))
			key = (row.item_code, row.batch_no or "", dimension_key)
			if row.serial_no:
				serial_warehouse = frappe.db.get_value("Serial No", row.serial_no, "warehouse")
				if serial_warehouse == self.warehouse and self.get_current_stock_qty(row) > 0:
					transfer_rows[key]["qty"] += 1
					transfer_rows[key]["serial_nos"].append(row.serial_no)
			else:
				transfer_qty = min(missing_qty, flt(self.get_current_stock_qty(row)))
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
		stock_entry.remarks = _("Not found by Stock Count {0}: moved to the Lost Warehouse.").format(
			self.name
		)
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

		return self.post_entry(stock_entry, _("the transfer to the Lost Warehouse"))

	def post_entry(self, stock_entry, what):
		"""Insert and submit a Stock Entry the count makes. Only this document, by identity, passes the user guards
		(``is_count_posting``: the laboratory warehouse scope, the Warehouse Type's outgoing role, the approval route,
		the rate guard); a failure undoes the approval with a message that names ``what`` could not be posted."""
		start = len(frappe.local.message_log)
		try:
			with system_posting(stock_entry):
				stock_entry.insert(ignore_permissions=True)
				stock_entry.flags.ignore_permissions = True
				stock_entry.submit()
		except (frappe.ValidationError, frappe.PermissionError) as error:
			frappe.throw(
				_("This Stock Count cannot be approved yet: {0} could not be posted. {1}").format(
					what, failure_reason(error, start)
				)
			)
		return stock_entry

	def surplus(self):
		"""{(item, batch, serial, dimensions): quantity} counted beyond the system quantity."""
		found = {}
		for row in self.items:
			extra_qty = flt(row.counted_qty) - flt(row.system_qty)
			if extra_qty <= 0:
				continue
			dimension_key = tuple(sorted(self.get_row_dimension_values(row).items()))
			key = (row.item_code, row.batch_no or "", row.serial_no or "", dimension_key)
			found[key] = found.get(key, 0) + extra_qty
		return found

	def split_surplus(self):
		"""``(pending, transfers, notes)`` of the surplus: what needs a recount (a quantity, an unknown serial number), the
		serial numbers the system holds in another warehouse (to transfer back) and the lines about serial numbers that
		left stock. Reads only."""
		pending, transfers, notes = {}, defaultdict(list), []
		for key, qty in self.surplus().items():
			item_code, batch_no, serial_no, dimension_key = key
			if not serial_no:
				pending[key] = qty
				continue
			serial = frappe.db.get_value("Serial No", serial_no, ["name", "warehouse"], as_dict=True)
			if not serial:
				pending[key] = qty
			elif serial.warehouse == self.warehouse:
				continue
			elif serial.warehouse:
				transfers[(item_code, batch_no, serial.warehouse, dimension_key)].append(serial_no)
			else:
				notes.append(serial_note(serial_no))
		return pending, transfers, notes

	def handle_surplus(self):
		"""What the count found beyond the system quantity (Connect Diagnostics, 5 Nov 2026), posted on approval.

		* A serial number the system holds in another warehouse (e.g. LOST): a Material Transfer back into the counted
		  warehouse (``found_transfer_entry``); in the counted warehouse itself it is only misplaced.
		* A serial number the system shows as delivered, lent or issued: noted (``surplus_notes``), the return
		  document is made, not a receipt.
		* Any other surplus (a quantity, an unknown serial number): on a first count, a draft recount of those Items
		  and locations (``recount_stock_count``, a My Tasks "Recount" for the stock team), counted by another person;
		  on the recount, what both counts found is first moved back from the Lost Warehouse as far as it holds the same
		  Item and batch (the same transfer, no new value), and only the rest is received into the Found Warehouse
		  (``found_stock_entry``) at the Item's current valuation rate."""
		pending, transfers, notes = self.split_surplus()
		if notes:
			self.db_set("surplus_notes", "\n".join(notes))
		if pending and not self.recount_of:
			if transfers:
				self.make_found_transfer(transfers, {})
			self.db_set("recount_stock_count", self.make_recount(pending).name)
			return

		receipt_rows, back_rows = defaultdict(lambda: {"qty": 0.0, "serial_nos": []}), {}
		if pending:
			confirmed = confirmed_surplus(pending, frappe.get_doc("Stock Count", self.recount_of).surplus())
			for (item_code, batch_no, serial_no, dimension_key), qty in confirmed.items():
				values = receipt_rows[(item_code, batch_no, dimension_key)]
				values["qty"] += qty
				if serial_no:
					values["serial_nos"].append(serial_no)
			back_rows = self.take_back_from_lost(receipt_rows)
		if transfers or back_rows:
			self.make_found_transfer(transfers, back_rows)
		receipt_rows = {key: values for key, values in receipt_rows.items() if values["qty"] > 0.000001}
		if receipt_rows:
			if not self.found_warehouse:
				frappe.throw(
					_(
						"The recount found stock that the system does not hold, but this count has no Found Warehouse. "
						"Ask the Stock Manager to set one on the Stock Count and approve it again."
					)
				)
			self.db_set("found_stock_entry", self.make_found_receipt(receipt_rows).name)

	def lost_balance(self, item_code, batch_no):
		"""What the Lost Warehouse (at the Lost Storage Location, when the count names one) holds of an Item and batch."""
		if not self.quarantine_warehouse:
			return 0
		location = self.quarantine_storage_location
		field = None
		if location:
			field = next(
				(
					dimension.fieldname
					for dimension in self.get_countable_dimensions()
					if dimension.dimension_name == STORAGE_LOCATION_DIMENSION
				),
				None,
			)
		if batch_no:
			from erpnext.stock.doctype.batch.batch import get_batch_qty

			return flt(
				get_batch_qty(
					batch_no,
					self.quarantine_warehouse,
					item_code,
					inventory_dimensions_dict={field: location} if field else None,
				)
			)
		sle = DocType("Stock Ledger Entry")
		query = (
			frappe.qb.from_(sle)
			.select(Sum(sle.actual_qty))
			.where(
				(sle.item_code == item_code)
				& (sle.warehouse == self.quarantine_warehouse)
				& (sle.company == self.company)
				& (sle.is_cancelled == 0)
			)
		)
		if field:
			query = query.where(sle[field] == location)
		return flt(query.run()[0][0])

	def take_back_from_lost(self, receipt_rows):
		"""Reduce ``receipt_rows`` (what the recount confirmed beyond the system quantity) by what the Lost Warehouse holds
		of the same Item and batch, and return that as ``{(item, batch, dimensions): quantity}`` to move back. Goods an
		earlier count moved to Lost return at their old value; only the rest is new stock. Serial numbers are not netted
		(a known one is transferred back on its own, an unknown one is new)."""
		available = {}
		back = {}
		for key, values in receipt_rows.items():
			item_code, batch_no, _dimension_key = key
			if values["serial_nos"] or flt(values["qty"]) <= 0:
				continue
			if frappe.get_cached_value("Item", item_code, "has_serial_no"):
				continue
			stock_key = (item_code, batch_no or "")
			if stock_key not in available:
				available[stock_key] = self.lost_balance(item_code, batch_no)
			move = min(flt(values["qty"]), available[stock_key])
			if move <= 0.000001:
				continue
			available[stock_key] -= move
			values["qty"] -= move
			back[key] = move
		return back

	def make_recount(self, pending):
		"""A draft Monthly count of the Items and storage locations of ``pending``, linked to this one."""
		recount = frappe.new_doc("Stock Count")
		recount.company = self.company
		recount.warehouse = self.warehouse
		recount.count_type = "Monthly"
		recount.item_selection = "Selected Items"
		recount.recount_of = self.name
		recount.quarantine_warehouse = self.quarantine_warehouse
		recount.quarantine_storage_location = self.quarantine_storage_location
		recount.found_warehouse = self.found_warehouse
		for item_code in sorted({key[0] for key in pending}):
			recount.append("selected_items", {"item_code": item_code})
		locations = sorted(
			{
				value
				for key in pending
				for fieldname, value in key[3]
				if fieldname == "storage_location" and value
			}
		)
		for location in locations:
			recount.append(
				"dimensions",
				{
					"inventory_dimension": STORAGE_LOCATION_DIMENSION,
					"reference_document": "Storage Location",
					"dimension_value": location,
				},
			)
		recount.insert(ignore_permissions=True)
		recount.add_comment(
			"Info",
			_("Recount of the surplus found by {0}: another person counts it.").format(self.name),
		)
		return recount

	def new_found_entry(self, purpose, remarks):
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.company = self.company
		stock_entry.purpose = purpose
		stock_entry.stock_entry_type = frappe.db.get_value(
			"Stock Entry Type", {"purpose": purpose, "is_standard": 1}, "name"
		) or frappe.db.get_value("Stock Entry Type", {"purpose": purpose}, "name")
		if not stock_entry.stock_entry_type:
			frappe.throw(_("Create a {0} Stock Entry Type before closing this count.").format(_(purpose)))
		stock_entry.to_warehouse = self.found_warehouse
		stock_entry.remarks = remarks
		stock_entry.stock_count = self.name
		return stock_entry

	def make_found_receipt(self, rows):
		if not rows:
			return None
		stock_entry = self.new_found_entry(
			"Material Receipt", _("Found by Stock Count {0} beyond the system quantity.").format(self.name)
		)
		# the count is the receipt's supporting document (a site may require a reference and an attachment)
		if stock_entry.meta.has_field("operation_receipt_reference"):
			stock_entry.operation_receipt_reference = _("Stock Count {0}").format(self.name)
		adjustment_account = frappe.get_cached_value("Company", self.company, "stock_adjustment_account")
		for (item_code, batch_no, _dimension_key), values in rows.items():
			rate = found_valuation_rate(item_code, self.warehouse, self.company, batch_no)
			entry_row = {
				"item_code": item_code,
				"qty": values["qty"],
				"t_warehouse": self.found_warehouse,
				"basic_rate": rate,
				"set_basic_rate_manually": 1,
				"allow_zero_valuation_rate": 0 if rate else 1,
				"use_serial_batch_fields": 1 if (batch_no or values["serial_nos"]) else 0,
				"batch_no": batch_no,
				"serial_no": "\n".join(values["serial_nos"]),
			}
			if adjustment_account:
				entry_row["expense_account"] = adjustment_account
			stock_entry.append("items", entry_row)
		# The rates are the system's (found_valuation_rate), not typed by the approver: the one document passes the cost
		# guards by its identity (``is_count_posting``), as the ledger policy does for a landed cost voucher's entries.
		return self.post_entry(stock_entry, _("the receipt into the Found Warehouse"))

	def make_found_transfer(self, rows, back_rows):
		"""One Material Transfer into the counted warehouse: serial numbers the system held elsewhere (``rows``) and
		quantities taken back from the Lost Warehouse (``back_rows``)."""
		stock_entry = self.new_found_entry(
			"Material Transfer",
			_("Found by Stock Count {0}: stock the system held in another warehouse.").format(self.name),
		)
		stock_entry.to_warehouse = self.warehouse
		item_meta = frappe.get_meta("Stock Entry Detail")
		for (item_code, batch_no, held_in, dimension_key), serial_nos in rows.items():
			entry_row = {
				"item_code": item_code,
				"qty": len(serial_nos),
				"s_warehouse": held_in,
				"t_warehouse": self.warehouse,
				"use_serial_batch_fields": 1,
				"batch_no": batch_no,
				"serial_no": "\n".join(serial_nos),
			}
			for fieldname, value in dimension_key:
				if value and item_meta.has_field(f"to_{fieldname}"):
					entry_row[f"to_{fieldname}"] = value
			stock_entry.append("items", entry_row)
		for (item_code, batch_no, dimension_key), qty in back_rows.items():
			entry_row = {
				"item_code": item_code,
				"qty": qty,
				"s_warehouse": self.quarantine_warehouse,
				"t_warehouse": self.warehouse,
				"use_serial_batch_fields": 1 if batch_no else 0,
				"batch_no": batch_no,
			}
			if self.quarantine_storage_location and item_meta.has_field("storage_location"):
				entry_row["storage_location"] = self.quarantine_storage_location
			for fieldname, value in dimension_key:
				if value and item_meta.has_field(f"to_{fieldname}"):
					entry_row[f"to_{fieldname}"] = value
			stock_entry.append("items", entry_row)
		self.post_entry(stock_entry, _("the transfer back into {0}").format(self.warehouse))
		self.db_set("found_transfer_entry", stock_entry.name)
		return stock_entry

	def set_variance_account(self, reconciliation):
		# The account known at Prepare Count: a later change on the warehouse neither flips nor sticks the count.
		account = self.variance_account_snapshot
		if not account:
			frappe.throw(_("This count has no Count Variance Account recorded. Prepare it again."))
		validate_variance_account(account, self.company)
		reconciliation.expense_account = account

	def set_reconciliation_department(self, reconciliation):
		"""The reconciliation belongs to the counted Warehouse's department, never to the department of the user whose
		save made it (Siwat, 6 Nov 2026). A Warehouse with no department (CD STOCK) keeps the creator's."""
		if not reconciliation.meta.has_field("department"):
			return
		department = None
		if frappe.db.has_column("Warehouse", "department"):
			department = frappe.db.get_value("Warehouse", self.warehouse, "department")
		if department:
			reconciliation.department = department

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
		if self.posts_variance():
			self.set_variance_account(reconciliation)
		self.set_reconciliation_department(reconciliation)
		if reconciliation.meta.has_field("stock_adjustment_reason"):
			reconciliation.stock_adjustment_reason = _(
				"Physical count differences from Stock Count {0}."
			).format(self.name)

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


LOST_WAREHOUSE_TYPE = "Lost"
FOUND_WAREHOUSE_TYPE = "Found"
GENERATED_LINK_FIELDS = (
	"stock_entry",
	"found_stock_entry",
	"found_transfer_entry",
	"recount_stock_count",
	"stock_reconciliation",
)


def confirmed_surplus(recounted, first):
	"""What both counts found beyond the system quantity, key by key (the smaller quantity)."""
	return {key: min(qty, first[key]) for key, qty in recounted.items() if first.get(key, 0) > 0}


def serial_note(serial_no):
	"""A line naming the last document that moved ``serial_no`` out of stock."""
	last = frappe.db.sql(
		"""select sle.voucher_type, sle.voucher_no, sle.posting_date
		from `tabSerial and Batch Entry` entry
		join `tabStock Ledger Entry` sle on sle.serial_and_batch_bundle = entry.parent
		where entry.serial_no = %s and sle.is_cancelled = 0
		order by sle.posting_datetime desc, sle.creation desc
		limit 1""",
		serial_no,
		as_dict=True,
	)
	if not last:
		return _("{0}: in no warehouse; find how it left stock before receiving it.").format(serial_no)
	return _("{0}: left stock by {1} {2} on {3}; make its return instead of receiving it.").format(
		serial_no, _(last[0].voucher_type), last[0].voucher_no, last[0].posting_date
	)


STORAGE_LOCATION_DIMENSION = "Storage Location"


@contextmanager
def system_posting(stock_entry):
	"""While the Stock Count posts (or cancels) ``stock_entry`` the document passes the guards that stop a user's own
	Stock Entry: ``is_count_posting`` knows it by identity (and, once saved, by name), never a document a user made."""
	previous = frappe.flags.get("stock_count_posting")
	frappe.flags.stock_count_posting = stock_entry
	try:
		yield
	finally:
		frappe.flags.stock_count_posting = previous


def is_count_posting(doc):
	"""Whether ``doc`` is the Stock Entry that the Stock Count is posting right now (the guards of the warehouse scope,
	the Warehouse Type's outgoing role, the approval route and the rate rules do not apply to it). The Stock Count link
	on the document alone never qualifies."""
	try:
		posting = frappe.flags.get("stock_count_posting")
	except RuntimeError:  # no site bound (offline tests, scripts): nothing is being posted
		posting = None
	if not posting or getattr(doc, "doctype", None) != "Stock Entry" or not doc.get("stock_count"):
		return False
	return posting is doc or (bool(doc.get("name")) and doc.get("name") == posting.get("name"))


def failure_reason(error, start):
	"""The plain text of what stopped a posting; the messages it left for the user are dropped (the caller raises one)."""
	messages = []
	for entry in frappe.local.message_log[start:]:
		try:
			data = entry if isinstance(entry, dict) else json.loads(entry)
			messages.append(strip_html(data.get("message") or ""))
		except (TypeError, ValueError, AttributeError):
			messages.append(strip_html(str(entry)))
	del frappe.local.message_log[start:]
	return " ".join(filter(None, messages)) or strip_html(str(error))


@frappe.whitelist()
def get_count_storage_locations(
	warehouse: str, zone: str | None = None, from_location: str | None = None, to_location: str | None = None
):
	"""The active Storage Locations of ``warehouse`` to count: those of a zone, or those whose location code (else
	name) lies between two locations, inclusive, in code order. For the Stock Count's "Add Storage Locations"."""
	frappe.has_permission("Storage Location", "read", throw=True)
	if not warehouse:
		return []
	location = frappe.qb.DocType("Storage Location")
	code = Coalesce(location.location_code, location.name)
	query = (
		frappe.qb.from_(location)
		.select(location.name)
		.where((location.warehouse == warehouse) & (location.disabled == 0))
		.orderby(code)
		.orderby(location.name)
	)
	if zone:
		query = query.where(location.zone == zone)
	elif from_location or to_location:

		def code_of(name):
			row = frappe.db.get_value("Storage Location", name, ["location_code", "warehouse"], as_dict=True)
			if not row or row.warehouse != warehouse:
				frappe.throw(_("Storage Location {0} is not in Warehouse {1}.").format(name, warehouse))
			return row.location_code or name

		low, high = (code_of(from_location or to_location), code_of(to_location or from_location))
		if low > high:
			low, high = high, low
		query = query.where((code >= low) & (code <= high))
	else:
		frappe.throw(_("Choose a Zone, or the first and last Storage Location of the range."))
	return query.run(pluck=True)


@frappe.whitelist()
def resolve_storage_location(warehouse: str, value: str):
	"""The name of the active Storage Location of ``warehouse`` that a typed or scanned label means: its full name, else
	its location code. Throws a plain message when there is none (or it is ambiguous)."""
	frappe.has_permission("Storage Location", "read", throw=True)
	value = (value or "").strip()
	if not value:
		return None
	location = frappe.db.get_value(
		"Storage Location", {"name": value, "warehouse": warehouse, "disabled": 0}, "name"
	)
	if location:
		return location
	matches = frappe.get_all(
		"Storage Location",
		filters={"warehouse": warehouse, "disabled": 0, "location_code": value},
		pluck="name",
		limit=2,
	)
	if len(matches) == 1:
		return matches[0]
	if matches:
		frappe.throw(
			_("More than one location in {0} has the code {1}. Scan its full label.").format(warehouse, value)
		)
	frappe.throw(_("There is no active Storage Location {0} in Warehouse {1}.").format(value, warehouse))


@frappe.whitelist()
def get_storage_location_zones(warehouse: str):
	"""The zones of the active Storage Locations of ``warehouse``."""
	frappe.has_permission("Storage Location", "read", throw=True)
	return frappe.get_all(
		"Storage Location",
		filters={"warehouse": warehouse, "disabled": 0, "zone": ["is", "set"]},
		pluck="zone",
		distinct=True,
		order_by="zone",
	)


@frappe.whitelist()
def get_loss_warehouses(company, warehouse=None):
	"""The company's Lost and Found warehouses a Monthly count would name by default (the form fills them)."""
	frappe.has_permission("Stock Count", "read", throw=True)
	return {
		"lost": get_lost_warehouse(company, exclude=warehouse),
		"found": get_found_warehouse(company, exclude=warehouse),
	}


def get_lost_warehouse(company, exclude=None):
	"""The company's Lost warehouse (Warehouse Type "Lost", enabled, not a group), else None."""
	return _typed_warehouse(company, LOST_WAREHOUSE_TYPE, exclude)


def get_found_warehouse(company, exclude=None):
	"""The company's Found warehouse (Warehouse Type "Found", enabled, not a group), else None."""
	return _typed_warehouse(company, FOUND_WAREHOUSE_TYPE, exclude)


def found_valuation_rate(item_code, warehouse, company, batch_no=None):
	"""The rate found stock is received at: the Item's current valuation rate in the counted warehouse, else the
	usual fallbacks (other warehouses, last purchase rate, standard rate); 0 only when the Item has none."""
	from erpnext.stock.stock_ledger import get_valuation_rate

	rate = flt(
		get_valuation_rate(
			item_code,
			warehouse,
			"Stock Entry",
			None,
			allow_zero_rate=True,
			company=company,
			raise_error_if_no_rate=False,
			batch_no=batch_no or None,
		)
	)
	if rate > 0:
		return rate
	# a serialised Item is valued serial by serial: take the warehouse's average, else the company's
	bins = frappe.qb.DocType("Bin")
	warehouses = frappe.qb.DocType("Warehouse")
	for condition in (bins.warehouse == warehouse, warehouses.company == company):
		row = (
			frappe.qb.from_(bins)
			.join(warehouses)
			.on(warehouses.name == bins.warehouse)
			.select(Sum(bins.stock_value) / Sum(bins.actual_qty))
			.where((bins.item_code == item_code) & (bins.actual_qty > 0) & condition)
		).run()
		if row and flt(row[0][0]) > 0:
			return flt(row[0][0])
	return flt(frappe.db.get_value("Item", item_code, "last_purchase_rate"))


def _typed_warehouse(company, warehouse_type, exclude=None):
	rows = frappe.get_all(
		"Warehouse",
		filters={
			"company": company,
			"warehouse_type": warehouse_type,
			"is_group": 0,
			"disabled": 0,
			"name": ["!=", exclude or ""],
		},
		pluck="name",
		order_by="name",
		limit=1,
	)
	return rows[0] if rows else None


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


def validate_variance_account(account, company):
	data = frappe.db.get_value(
		"Account", account, ["company", "root_type", "is_group", "account_type", "disabled"], as_dict=True
	)
	if (
		not data
		or data.company != company
		or data.root_type != "Expense"
		or data.is_group
		or data.disabled
		or data.account_type == "Stock"
	):
		frappe.throw(
			_(
				"Count Variance Account {0} must be an enabled expense account of {1}, "
				"not a group or Stock account."
			).format(account, company)
		)


@frappe.whitelist()
def make_count_template(name, title):
	"""Save the items chosen on a Stock Count as a reusable Stock Count Template."""
	title = (title or "").strip()
	if not title:
		frappe.throw(_("Title is required."))
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
	link_field = next(
		(
			field
			for field in (
				("stock_entry", "found_stock_entry", "found_transfer_entry")
				if doc.doctype == "Stock Entry"
				else ("stock_reconciliation",)
				if doc.doctype == "Stock Reconciliation"
				else ()
			)
			if frappe.db.get_value("Stock Count", doc.stock_count, field) == doc.name
		),
		None,
	)
	if link_field and not frappe.db.exists(
		"Stock Count", {link_field: doc.name, "docstatus": 1, "name": ["!=", doc.stock_count]}
	):
		# Cancel the generated voucher first; Stock Count still enforces this order on cancellation.
		doc.ignore_linked_doctypes = tuple(set(doc.get("ignore_linked_doctypes") or ()) | {"Stock Count"})
