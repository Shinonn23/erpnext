import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowtime

import erpnext
from erpnext.assets.doctype.asset_category.asset_category import get_asset_category_account
from erpnext.controllers.stock_controller import StockController, get_item_wise_inventory_account_map
from erpnext.stock.doctype.inventory_dimension.inventory_dimension import (
	get_document_wise_inventory_dimensions,
)
from erpnext.stock.utils import _get_incoming_rate, get_stock_balance

_GENERATED_ASSET_TOKEN = object()


def get_native_conversion_source(asset):
	if asset.flags.get("stock_conversion_token") is _GENERATED_ASSET_TOKEN:
		return asset.flags.get("stock_conversion_source")


class StocktoAssetConversion(StockController):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.assets.doctype.stock_to_asset_conversion_asset.stock_to_asset_conversion_asset import (
			StocktoAssetConversionAsset,
		)
		from erpnext.assets.doctype.stock_to_asset_conversion_item.stock_to_asset_conversion_item import (
			StocktoAssetConversionItem,
		)

		amended_from: DF.Link | None
		asset_location: DF.Link | None
		assets: DF.Table[StocktoAssetConversionAsset]
		company: DF.Link
		department: DF.Link | None
		material_request: DF.Link | None
		naming_series: DF.Literal["ACC-STAC-.YYYY.-"]
		pick_list: DF.Link | None
		posting_date: DF.Date
		posting_time: DF.Time
		status: DF.Literal["Draft", "Submitted", "Cancelled"]
		stock_items: DF.Table[StocktoAssetConversionItem]
		total_value: DF.Currency
	# end: auto-generated types

	ACCOUNTING_TREATMENTS = (
		"Expense",
		"Immediate Depreciation",
		"Scheduled Depreciation",
		"Hold Value",
	)

	def validate(self):
		self.set_department()
		self.validate_company_settings()
		self.sync_pick_list_selections()
		self.sync_material_request_rows()
		from erpnext.stock.doctype.storage_location.storage_location import set_default_storage_locations

		set_default_storage_locations(self)
		self.validate_storage_locations()
		self.set_missing_values()
		self.validate_stock_items()
		self.build_asset_preview()
		self.calculate_totals()
		from erpnext.stock.warehouse_policy import validate_document_policy

		validate_document_policy(self)

	def validate_company_settings(self):
		if not self.company:
			frappe.throw(_("Company is required"))
		if not self.posting_time:
			self.posting_time = nowtime()
		if self.material_request:
			request = frappe.get_doc("Material Request", self.material_request)
			if request.docstatus != 1:
				frappe.throw(_("Conversion can only be made from a submitted Material Request"))
			if request.company != self.company:
				frappe.throw(_("Request and Conversion must belong to the same Company"))
		if self.pick_list:
			pick_list = frappe.get_cached_doc("Pick List", self.pick_list)
			if pick_list.company != self.company:
				frappe.throw(_("Pick List and Conversion must belong to the same Company"))

	def set_missing_values(self):
		if not self.asset_location and self.stock_items:
			for row in self.stock_items:
				self.asset_location = frappe.db.get_value("Warehouse", row.warehouse, "asset_location")
				if self.asset_location:
					break
		for row in self.stock_items:
			row.stock_qty = flt(row.qty)
			if row.item_code:
				item = frappe.get_cached_doc("Item", row.item_code)
				row.stock_uom = item.stock_uom
				row.asset_item_code = item.demo_asset_item_code
				if row.warehouse:
					args = frappe._dict(
						item_code=row.item_code,
						warehouse=row.warehouse,
						posting_date=self.posting_date,
						posting_time=self.posting_time,
						qty=-1 * flt(row.qty),
						company=self.company,
						voucher_type=self.doctype,
						voucher_no=self.name,
						serial_and_batch_bundle=row.serial_and_batch_bundle,
						inventory_dimensions_dict=self.get_inventory_dimensions_for_row(row),
					)
					row.actual_qty = get_stock_balance(
						row.item_code,
						row.warehouse,
						self.posting_date,
						self.posting_time,
						inventory_dimensions_dict=args.inventory_dimensions_dict,
					)
					row.valuation_rate = _get_incoming_rate(args, raise_error_if_no_rate=False)
					row.amount = flt(row.qty) * flt(row.valuation_rate)

	def get_inventory_dimensions_for_row(self, row):
		return {
			dimension.target_fieldname: row.get(dimension.source_fieldname)
			for dimension in get_document_wise_inventory_dimensions(row.doctype)
		}

	def validate_stock_items(self):
		if not self.stock_items:
			frappe.throw(_("At least one Stock Item is required"))
		request_qty = {}
		converted_request_qty = {}
		for row in self.stock_items:
			if not row.warehouse:
				frappe.throw(_("Row {0}: Warehouse is required").format(row.idx))
			item = frappe.get_cached_doc("Item", row.item_code)
			if not item.is_stock_item:
				frappe.throw(_("Row {0}: {1} must be a Stock Item").format(row.idx, row.item_code))
			if not item.demo_asset_item_code:
				frappe.throw(_("Row {0}: Set Asset Item Code on {1}").format(row.idx, row.item_code))
			target = frappe.get_cached_doc("Item", item.demo_asset_item_code)
			if not target.is_fixed_asset or target.is_stock_item:
				frappe.throw(_("Row {0}: Target Item must be a non-stock Fixed Asset Item").format(row.idx))
			if not target.asset_category:
				frappe.throw(_("Row {0}: Set an Asset Category on target Item {1}").format(row.idx, target.name))
			self.validate_demo_category(row, target)
			if flt(row.qty) <= 0 or flt(row.qty) % 1:
				frappe.throw(_("Row {0}: Qty must be a positive whole number").format(row.idx))
			warehouse_company = frappe.db.get_value("Warehouse", row.warehouse, "company")
			if warehouse_company != self.company:
				frappe.throw(_("Row {0}: Warehouse must belong to Company {1}").format(row.idx, self.company))
			if flt(row.actual_qty) < flt(row.qty):
				frappe.throw(_("Row {0}: Qty exceeds available stock in {1}").format(row.idx, row.warehouse))
			if row.serial_and_batch_bundle:
				self.validate_bundle(row, item)
			if self.material_request:
				if not row.material_request_item:
					frappe.throw(_("Row {0}: Select a Material Request Item").format(row.idx))
				request = frappe.get_cached_doc("Material Request", self.material_request)
				request_row = frappe.db.get_value(
					"Material Request Item", row.material_request_item,
					["parent", "item_code", "item_type", "from_warehouse", "stock_qty", "qty", "fulfilled_qty"], as_dict=True,
				)
				if (
					not request_row
					or request_row.parent != request.name
					or request_row.item_code != row.item_code
					or request_row.item_type != "Stock Item"
					or request_row.from_warehouse != row.warehouse
				):
					frappe.throw(_("Row {0}: Stock Item is not requested on the linked Material Request").format(row.idx))
				request_qty[row.material_request_item] = (
					flt(request_row.stock_qty or request_row.qty)
					- flt(request_row.fulfilled_qty)
					- self.get_already_converted_qty(row.material_request_item)
				)
				converted_request_qty[row.material_request_item] = (
					converted_request_qty.get(row.material_request_item, 0) + flt(row.stock_qty)
				)
			elif row.material_request_item:
				frappe.throw(_("Row {0}: Material Request Item requires a linked Material Request").format(row.idx))
		for request_item, qty in converted_request_qty.items():
			if qty > request_qty[request_item]:
				frappe.throw(_("Converted quantity exceeds the remaining quantity for Material Request Item {0}").format(request_item))

	def get_already_converted_qty(self, request_item):
		return flt(
			frappe.db.sql(
				"""select sum(input.stock_qty) from `tabStock to Asset Conversion Item` input
				join `tabStock to Asset Conversion` conversion on conversion.name = input.parent
				where input.material_request_item = %s and conversion.material_request = %s
				and conversion.docstatus = 1 and conversion.name != %s""",
				(request_item, self.material_request, self.name or ""),
			)[0][0]
		)

	def validate_demo_category(self, row, target):
		"""Demo Assets are told apart by their Asset Category, so a Customer Demo request must produce one."""
		if not self.material_request:
			return
		request_type = frappe.db.get_value("Material Request", self.material_request, "material_request_type")
		if request_type == "Customer Demo" and not frappe.get_cached_value(
			"Asset Category", target.asset_category, "is_demo_category"
		):
			frappe.throw(
				_("Row {0}: Target Item {1} must be in an Asset Category marked as a Demo Category for a Customer Demo request").format(
					row.idx, target.name
				)
			)

	def validate_bundle(self, row, item):
		bundle = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
		if bundle.item_code != row.item_code or bundle.warehouse != row.warehouse:
			frappe.throw(_("Row {0}: Bundle Item and Warehouse must match the conversion row").format(row.idx))
		if self.pick_list:
			linked_to_conversion_row = (
				bundle.voucher_type == self.doctype
				and bundle.voucher_no == self.name
				and bundle.voucher_detail_no == row.name
			)
			linked_to_pick_list_row = False
			if bundle.voucher_type == "Pick List" and bundle.voucher_no == self.pick_list:
				pick_list_item = frappe.db.get_value(
					"Pick List Item",
					bundle.voucher_detail_no,
					["parent", "item_code", "warehouse"],
					as_dict=True,
				)
				linked_to_pick_list_row = bool(
					pick_list_item
					and pick_list_item.parent == self.pick_list
					and pick_list_item.item_code == row.item_code
					and pick_list_item.warehouse == row.warehouse
				)
			if not (linked_to_conversion_row or linked_to_pick_list_row):
				frappe.throw(_("Row {0}: Pick List Bundle does not match this Conversion row").format(row.idx))
		else:
			if not (
				bundle.voucher_type == self.doctype
				and bundle.voucher_no == self.name
				and bundle.voucher_detail_no == row.name
			):
				frappe.throw(_("Row {0}: Without a Pick List, the Serial and Batch Bundle must be created for this Conversion row").format(row.idx))
		if bundle.type_of_transaction != "Outward":
			frappe.throw(_("Row {0}: The Serial and Batch Bundle must be Outward").format(row.idx))
		if abs(flt(bundle.total_qty)) != flt(row.qty):
			frappe.throw(_("Row {0}: Bundle Qty must match conversion Qty").format(row.idx))

	def sync_pick_list_selections(self):
		if not self.pick_list:
			return

		pick_list = frappe.get_doc("Pick List", self.pick_list)
		if pick_list.docstatus != 1:
			frappe.throw(_("Pick List must be submitted before conversion"))
		if pick_list.company != self.company or pick_list.purpose != "Material Transfer":
			frappe.throw(_("Pick List must be a submitted Material Transfer for this Company"))
		if pick_list.material_request and self.material_request and pick_list.material_request != self.material_request:
			frappe.throw(_("Pick List must be linked to the selected Material Request"))
		if pick_list.material_request and not self.material_request:
			self.material_request = pick_list.material_request
		if self.material_request:
			request = frappe.get_doc("Material Request", self.material_request)
			if request.docstatus != 1 or request.company != self.company:
				frappe.throw(_("Pick List Material Request must be submitted and belong to this Company"))

		locations_by_conversion_row = {}
		for location in pick_list.locations:
			key = location.material_request_item or (location.item_code, location.warehouse)
			locations_by_conversion_row.setdefault(key, []).append(location)
		used_locations = set()

		for row in self.stock_items:
			if row.material_request_item:
				candidates = locations_by_conversion_row.get(row.material_request_item, [])
			else:
				candidates = [
					location
					for location in pick_list.locations
					if location.item_code == row.item_code and location.warehouse == row.warehouse
				]
			locations = [location for location in candidates if location.name not in used_locations]
			if len(locations) != 1:
				frappe.throw(
					_("Row {0}: Pick List must have exactly one matching picked row; split duplicate rows in the same way").format(row.idx)
				)
			location = locations[0]
			if (
				location.item_code != row.item_code
				or location.warehouse != row.warehouse
				or flt(location.picked_qty) != flt(row.qty)
			):
				frappe.throw(_("Row {0}: Picked Item and Warehouse must match this Conversion row").format(row.idx))
			used_locations.add(location.name)
			row.material_request_item = location.material_request_item
			row.serial_and_batch_bundle = location.serial_and_batch_bundle
			for dimension in get_document_wise_inventory_dimensions(row.doctype):
				fieldname = dimension.source_fieldname
				if row.meta.has_field(fieldname) and not row.get(fieldname) and location.get(fieldname):
					row.set(fieldname, location.get(fieldname))
			if row.meta.has_field("storage_location") and not row.storage_location and location.get("storage_location"):
				row.storage_location = location.storage_location
		if len(used_locations) != len(pick_list.locations):
			frappe.throw(_("Every selected Pick List row must be included in the Conversion"))

	def sync_material_request_rows(self):
		if not self.material_request:
			return

		request = frappe.get_doc("Material Request", self.material_request)
		rows = frappe.get_all(
			"Material Request Item",
			filters={"parent": request.name, "item_type": "Stock Item"},
			fields=["name", "item_code", "from_warehouse"],
		)
		for row in self.stock_items:
			if row.material_request_item:
				continue
			matches = [
				request_row
				for request_row in rows
				if request_row.item_code == row.item_code and request_row.from_warehouse == row.warehouse
			]
			if len(matches) == 1:
				row.material_request_item = matches[0].name
			elif self.pick_list:
				# Pick List synchronization sets the exact source row when available.
				continue
			else:
				frappe.throw(_("Row {0}: Select an unambiguous Material Request Item for this Stock Item").format(row.idx))

	def build_asset_preview(self):
		existing_treatments = {}
		existing_locations = {}
		existing_assets = {}
		previous = self.get_doc_before_save()
		previous_outputs = {
			(row.source_item_row, row.source_unit_index): row
			for row in previous.assets
		} if previous else {}
		for row in self.assets:
			key = (row.source_item_row, row.source_unit_index)
			if not all(key):
				continue
			treatment = row.accounting_treatment
			location = row.asset_location
			if row.asset and frappe.db.exists("Asset", row.asset):
				asset = frappe.get_doc("Asset", row.asset)
				if (
					asset.stock_to_asset_conversion != self.name
					or asset.stock_to_asset_conversion_item != row.source_item_row
					or asset.stock_to_asset_conversion_unit != row.source_unit_index
				):
					frappe.throw(_("Asset {0} is not linked to this Conversion unit").format(asset.name))
				existing_assets[key] = asset.name
				previous_row = previous_outputs.get(key)
				if not previous_row or treatment == previous_row.accounting_treatment:
					treatment = asset.stock_to_asset_accounting_treatment or treatment
				if not previous_row or location == previous_row.asset_location:
					location = asset.location or location
			existing_treatments[key] = normalize_accounting_treatment(treatment)
			if location:
				existing_locations[key] = location
		self.set("assets", [])
		threshold = flt(frappe.db.get_value("Company", self.company, "asset_capitalization_threshold"))
		for row in self.stock_items:
			source_unit_index = 0
			warehouse_location = frappe.db.get_value("Warehouse", row.warehouse, "asset_location")
			entries = []
			if row.serial_and_batch_bundle:
				entries = frappe.get_all(
					"Serial and Batch Entry",
					filters={"parent": row.serial_and_batch_bundle, "is_cancelled": 0},
					fields=["serial_no", "batch_no", "qty", "incoming_rate"],
					order_by="idx asc",
				)
			if not entries:
				entries = [frappe._dict(qty=row.qty, incoming_rate=row.valuation_rate)]
			for entry in entries:
				unit_count = cint(abs(flt(entry.qty)))
				if flt(entry.qty) % 1:
					frappe.throw(_("Asset conversion requires whole unit quantities"))
				for _asset_index in range(unit_count):
					source_unit_index += 1
					asset_value = flt(entry.incoming_rate or row.valuation_rate)
					asset_location = existing_locations.get(
						(row.name, source_unit_index), warehouse_location or self.asset_location
					)
					treatment = existing_treatments.get(
						(row.name, source_unit_index), get_default_accounting_treatment(asset_value, threshold)
					)
					self.append(
						"assets",
						{
							"asset": existing_assets.get((row.name, source_unit_index)),
							"source_item_row": row.name,
							"source_unit_index": source_unit_index,
							"asset_item_code": row.asset_item_code,
							"asset_serial_number": entry.serial_no,
							"source_serial_no": entry.serial_no,
							"source_batch_no": entry.batch_no,
							"asset_value": asset_value,
							"asset_location": asset_location,
							"accounting_treatment": treatment,
							"calculate_depreciation": treatment == "Scheduled Depreciation",
						},
					)

	def calculate_totals(self):
		self.total_value = sum(flt(row.amount) for row in self.stock_items)

	def before_submit(self):
		if not self.assets:
			frappe.throw(_("At least one Stock Item is required"))
		if self.material_request:
			self.lock_material_request_items()
		self.sync_draft_assets()
		if self.material_request:
			self.validate_stock_items()
		if self.pick_list and frappe.db.get_value("Pick List", self.pick_list, "docstatus") != 1:
			frappe.throw(_("Submit the linked Pick List before submitting this conversion"))
		for row in self.stock_items:
			item = frappe.get_cached_doc("Item", row.item_code)
			if (item.has_serial_no or item.has_batch_no) and not row.serial_and_batch_bundle:
				frappe.throw(_("Row {0}: Select Serial / Batch details before conversion").format(row.idx))
			if row.serial_and_batch_bundle:
				self.validate_bundle(row, item)
		for output in self.assets:
			asset = frappe.get_doc("Asset", output.asset)
			if asset.docstatus != 0 or asset.stock_to_asset_conversion != self.name:
				frappe.throw(_("Asset {0} must remain a Draft linked to this Conversion").format(asset.name))
			output.accounting_treatment = normalize_accounting_treatment(
				asset.stock_to_asset_accounting_treatment or output.accounting_treatment
			)
			output.asset_location = asset.location or output.asset_location
			output.asset_value = flt(asset.net_purchase_amount)
			if output.accounting_treatment not in self.ACCOUNTING_TREATMENTS:
				frappe.throw(_("Select a valid accounting treatment for Asset {0}").format(output.asset_item_code))
			output.calculate_depreciation = output.accounting_treatment == "Scheduled Depreciation"
			if not output.asset_location:
				frappe.throw(_("Select an Asset Location for {0}").format(output.asset_item_code))
			source_row = next(
				(row for row in self.stock_items if row.name == output.source_item_row), None
			)
			mapped_location = (
				frappe.db.get_value("Warehouse", source_row.warehouse, "asset_location")
				if source_row
				else None
			)
			if mapped_location and output.asset_location != mapped_location:
				frappe.throw(
					_("Asset Location for {0} must match Warehouse mapping {1}").format(
						output.asset_item_code, mapped_location
					)
				)
			if output.accounting_treatment == "Expense":
				expense_account = frappe.get_cached_value("Company", self.company, "low_value_asset_expense_account")
				if not expense_account:
					frappe.throw(_("Set Low Value Asset Expense Account in Company {0}").format(self.company))
				validate_company_ledger_account(expense_account, self.company)
			elif output.accounting_treatment in (
				"Immediate Depreciation",
				"Scheduled Depreciation",
				"Hold Value",
			):
				fixed_asset_account = get_asset_category_account("fixed_asset_account", item=output.asset_item_code, company=self.company)
				if not fixed_asset_account:
					frappe.throw(_("Set a Fixed Asset Account for Item {0}").format(output.asset_item_code))
			if output.accounting_treatment == "Immediate Depreciation":
				get_depreciation_accounts(
					frappe.get_cached_value("Item", output.asset_item_code, "asset_category"), self.company
				)
			if output.accounting_treatment == "Scheduled Depreciation":
				asset_category = frappe.get_cached_doc("Item", output.asset_item_code).asset_category
				if not asset_category:
					frappe.throw(_("Set an Asset Category on Item {0}").format(output.asset_item_code))
				if not frappe.get_cached_doc("Asset Category", asset_category).finance_books:
					frappe.throw(
						_("Configure a Finance Book and depreciation method in Asset Category {0} before converting this asset").format(asset_category)
					)
				get_depreciation_accounts(asset_category, self.company)

	def lock_material_request_items(self):
		request_items = sorted({row.material_request_item for row in self.stock_items if row.material_request_item})
		for request_item in request_items:
			frappe.db.sql(
				"select name from `tabMaterial Request Item` where name = %s for update", request_item
			)

	def on_update(self):
		if self.stock_items:
			self.set_serial_and_batch_bundle(table_name="stock_items")
		if self.docstatus == 0:
			self.sync_draft_assets()

	def on_submit(self):
		self.update_stock_ledger()
		self.make_gl_entries()
		self.submit_generated_assets()
		self.status = "Submitted"
		self.db_set("status", self.status)
		if self.pick_list:
			frappe.db.set_value("Pick List", self.pick_list, "status", "Completed")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Serial and Batch Bundle", "Asset")
		for row in self.assets:
			if row.asset and frappe.db.get_value("Asset", row.asset, "docstatus") == 1:
				self.validate_asset_has_no_follow_up_transactions(row.asset)
				if row.journal_entry and frappe.db.get_value("Journal Entry", row.journal_entry, "docstatus") == 1:
					asset = frappe.get_doc("Asset", row.asset)
					if asset.stock_to_asset_conversion != self.name or asset.stock_to_asset_depreciation_journal_entry != row.journal_entry:
						frappe.throw(_("Immediate Depreciation Entry does not belong to this converted Asset"))
					# Remove only our own backlink while reversing the journal. Other
					# submitted documents must still block its cancellation.
					asset.db_set("stock_to_asset_depreciation_journal_entry", None, update_modified=False)
					try:
						frappe.get_doc("Journal Entry", row.journal_entry).cancel()
					finally:
						asset.db_set("stock_to_asset_depreciation_journal_entry", row.journal_entry, update_modified=False)
				asset_serial = frappe.db.get_value("Asset", row.asset, "source_serial_no")
				asset = frappe.get_doc("Asset", row.asset)
				asset.flags.stock_conversion_token = _GENERATED_ASSET_TOKEN
				asset.flags.stock_conversion_source = self.name
				asset.cancel()
				if asset_serial and frappe.db.get_value("Serial No", asset_serial, "asset") == row.asset:
					frappe.db.set_value("Serial No", asset_serial, "asset", None)
		self.update_stock_ledger()
		self.make_gl_entries()
		if self.pick_list and frappe.db.get_value("Pick List", self.pick_list, "docstatus") == 1:
			frappe.get_doc("Pick List", self.pick_list).cancel()
		self.db_set("status", "Cancelled")

	def validate_asset_has_no_follow_up_transactions(self, asset):
		checks = (
			(
				"""select 1 from `tabAsset Movement Item` ami join `tabAsset Movement` am on am.name=ami.parent
				where ami.asset=%s and am.docstatus=1
				and not (
					am.purpose='Receipt'
					and am.reference_doctype='Stock to Asset Conversion'
					and am.reference_name=%s
				)
				limit 1""",
				_("Asset {0} has an active Asset Movement; cancel it before cancelling this conversion"),
			),
			(
				"""select 1 from `tabAsset Capitalization Asset Item` acai
				join `tabAsset Capitalization` ac on ac.name=acai.parent
				where acai.asset=%s and ac.docstatus=1 limit 1""",
				_("Asset {0} has been used in Asset Capitalization; cancel it before cancelling this conversion"),
			),
			(
				"select 1 from `tabAsset Value Adjustment` where asset=%s and docstatus=1 limit 1",
				_("Asset {0} has an active Value Adjustment; cancel it before cancelling this conversion"),
			),
			(
				"select 1 from `tabAsset Repair` where asset=%s and docstatus=1 limit 1",
				_("Asset {0} has an active Repair; cancel it before cancelling this conversion"),
			),
			(
				"select 1 from `tabAsset Shift Allocation` where asset=%s and docstatus=1 limit 1",
				_("Asset {0} has an active Shift Allocation; cancel it before cancelling this conversion"),
			),
			(
				"""select 1 from `tabSales Invoice Item` item join `tabSales Invoice` invoice on invoice.name=item.parent
				where item.asset=%s and invoice.docstatus=1 limit 1""",
				_("Asset {0} has been used in a submitted Sales Invoice; cancel it before cancelling this conversion"),
			),
		)
		for index, (query, message) in enumerate(checks):
			values = (asset, self.name) if index == 0 else asset
			if frappe.db.sql(query, values):
				frappe.throw(message.format(asset))

	def update_stock_ledger(self):
		entries = []
		for row in self.stock_items:
			entries.append(self.get_sl_entries(row, {"actual_qty": -1 * flt(row.qty), "serial_and_batch_bundle": row.serial_and_batch_bundle}))
		if self.docstatus == 2:
			entries.reverse()
		if entries:
			self.make_sl_entries(entries)

	def make_gl_entries(self):
		from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries
		if self.docstatus == 2:
			make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)
			return
		if self.docstatus != 1:
			return
		entries = self.get_gl_entries()
		if entries:
			make_gl_entries(entries, merge_entries=False)

	def get_gl_entries(self):
		entries = []
		perpetual = erpnext.is_perpetual_inventory_enabled(self.company)
		low_value_account = frappe.db.get_value("Company", self.company, "low_value_asset_expense_account")
		# the conversion has no Cost Center of its own: book it on the company's default
		cost_center = erpnext.get_default_cost_center(self.company)
		inventory_map = self.get_inventory_account_map()
		if self.use_item_inventory_account:
			inventory_map = get_item_wise_inventory_account_map(self.stock_items, self.company)
		sle_map = self.get_stock_ledger_details()
		stock_value_by_row = {}
		for row in self.stock_items:
			sle_value = sum(
				abs(flt(sle.stock_value_difference, self.get_debit_field_precision()))
				for sle in sle_map.get(row.name, [])
			)
			stock_value = sle_value or flt(row.amount, self.get_debit_field_precision())
			stock_value_by_row[row.name] = stock_value

		# SLE valuation is authoritative. Apply any bundle/rounding difference to
		# the last Asset for each source row so the conversion journal always balances.
		for source_row in self.stock_items:
			outputs = [output for output in self.assets if output.source_item_row == source_row.name]
			if outputs:
				assigned_value = sum(flt(output.asset_value, self.precision("total_value")) for output in outputs)
				outputs[-1].asset_value = flt(
					flt(outputs[-1].asset_value) + stock_value_by_row[source_row.name] - assigned_value,
					self.precision("total_value"),
				)
				outputs[-1].db_set("asset_value", outputs[-1].asset_value)
				if outputs[-1].asset:
					asset = frappe.get_doc("Asset", outputs[-1].asset)
					asset.net_purchase_amount = outputs[-1].asset_value
					asset.set_missing_values()
					asset.save(ignore_permissions=True)

		for row in self.stock_items:
			stock_value = stock_value_by_row[row.name]
			if stock_value:
				inventory = self.get_inventory_account_dict(row, inventory_map)
				credit_account = inventory["account"] if perpetual else self.get_company_default("default_expense_account")
				if not credit_account:
					frappe.throw(_("Set an Inventory Account for Warehouse {0}").format(row.warehouse) if perpetual else _("Set a Default Expense Account in Company {0}").format(self.company))
				entries.append(self.get_gl_dict({"account": credit_account, "credit": stock_value, "against": low_value_account, "cost_center": cost_center}, inventory.get("account_currency"), item=row))
		for output in self.assets:
			value = flt(output.asset_value, self.precision("total_value"))
			if output.accounting_treatment == "Expense":
				account = low_value_account
			else:
				account = get_asset_category_account("fixed_asset_account", item=output.asset_item_code, company=self.company)
			if not account:
				frappe.throw(_("Set a Fixed Asset Account for Item {0}").format(output.asset_item_code))
			entries.append(self.get_gl_dict({"account": account, "debit": value, "against": "Stock Conversion", "remarks": self.name, "cost_center": cost_center}, item=output))
		return entries

	def get_items_and_warehouses(self):
		return [row.item_code for row in self.stock_items], [row.warehouse for row in self.stock_items]

	def sync_draft_assets(self):
		if not self.name:
			return

		desired_keys = {
			(self.name, output.source_item_row, output.source_unit_index)
			for output in self.assets
			if output.source_item_row and output.source_unit_index
		}
		linked_assets = frappe.get_all(
			"Asset",
			filters={"stock_to_asset_conversion": self.name},
			fields=["name", "stock_to_asset_conversion_item", "stock_to_asset_conversion_unit"],
		)
		stale_assets = [
			asset.name
			for asset in linked_assets
			if (
				self.name,
				asset.stock_to_asset_conversion_item,
				asset.stock_to_asset_conversion_unit,
			) not in desired_keys
		]
		if stale_assets:
			frappe.throw(
				_("Draft Assets {0} no longer match this Conversion. Delete the obsolete Draft Assets before saving.").format(
					frappe.bold(", ".join(stale_assets))
				)
			)

		for output in self.assets:
			source_row = next((row for row in self.stock_items if row.name == output.source_item_row), None)
			asset = (
				frappe.get_doc("Asset", output.asset)
				if output.asset and frappe.db.exists("Asset", output.asset)
				else None
			)
			if output.asset and not asset:
				output.asset = None
			if asset and asset.stock_to_asset_conversion != self.name:
				frappe.throw(_("Asset {0} is not linked to this Conversion").format(asset.name))
			if asset and asset.docstatus != 0:
				frappe.throw(_("Asset {0} must be Draft while its Conversion is Draft").format(asset.name))
			if not asset:
				asset = frappe.new_doc("Asset")
				asset.asset_type = "Existing Asset"
				asset.stock_to_asset_conversion = self.name
				asset.stock_to_asset_conversion_item = output.source_item_row
				asset.stock_to_asset_conversion_unit = output.source_unit_index
				asset.stock_to_asset_accounting_treatment = normalize_accounting_treatment(
					output.accounting_treatment
				)
				asset.booked_fixed_asset = 1
			asset.item_code = output.asset_item_code
			asset.company = self.company
			asset.location = output.asset_location
			asset.stock_to_asset_accounting_treatment = normalize_accounting_treatment(output.accounting_treatment)
			asset.source_warehouse = source_row.warehouse if source_row else None
			asset.asset_serial_number = output.asset_serial_number
			asset.source_serial_no = output.source_serial_no
			asset.source_batch_no = output.source_batch_no
			asset.material_request = self.material_request
			asset.net_purchase_amount = output.asset_value
			asset.purchase_date = self.posting_date
			asset.available_for_use_date = self.posting_date
			asset.calculate_depreciation = (
				asset.stock_to_asset_accounting_treatment == "Scheduled Depreciation"
			)
			asset.booked_fixed_asset = 1
			asset.set_missing_values()
			if asset.is_new():
				asset.insert(ignore_permissions=True)
				output.asset = asset.name
				output.db_set("asset", asset.name)
			else:
				asset.save(ignore_permissions=True)

	def submit_generated_assets(self):
		for output in self.assets:
			asset = frappe.get_doc("Asset", output.asset)
			asset.flags.from_stock_to_asset_conversion = True
			asset.flags.stock_conversion_token = _GENERATED_ASSET_TOKEN
			asset.flags.stock_conversion_source = self.name
			# The conversion approver decided; the generated Asset is submitted by the system, as it was inserted.
			asset.flags.ignore_permissions = True
			asset.submit()
			if output.source_serial_no:
				frappe.db.set_value("Serial No", output.source_serial_no, "asset", asset.name)
			if asset.is_demo_asset:
				frappe.db.set_value("Asset", asset.name, "demo_loan_status", "Available")
			if output.accounting_treatment == "Immediate Depreciation":
				journal_entry = create_immediate_depreciation_entry(asset, self.posting_date)
				output.db_set("journal_entry", journal_entry.name)
				frappe.db.set_value(
					"Asset",
					asset.name,
					{
						"stock_to_asset_depreciation_journal_entry": journal_entry.name,
						"value_after_depreciation": 0,
					},
				)
				asset.reload()
				asset.set_status()

	def on_trash(self):
		if self.docstatus != 0:
			return
		for asset_name in frappe.get_all(
			"Asset", filters={"stock_to_asset_conversion": self.name, "docstatus": 0}, pluck="name"
		):
			frappe.delete_doc("Asset", asset_name, ignore_permissions=True)


@frappe.whitelist()
def get_conversion_defaults(company, request=None):
	frappe.has_permission("Stock to Asset Conversion", "create", throw=True)
	location = None
	if request:
		doc = frappe.get_doc("Material Request", request)
		for row in doc.items:
			if row.from_warehouse:
				location = frappe.db.get_value("Warehouse", row.from_warehouse, "asset_location")
				if location:
					break
	return {"asset_location": location, "company": company}


def get_accounting_treatment(asset_value, threshold):
	"""Legacy tuple API used by the original Customer Demo conversion flow."""
	if flt(threshold) > 0 and flt(asset_value) < flt(threshold):
		return "Expense", 0
	return "Capitalized", 1


def get_default_accounting_treatment(asset_value, threshold):
	if flt(threshold) > 0 and flt(asset_value) < flt(threshold):
		return "Expense"
	return "Scheduled Depreciation"


def normalize_accounting_treatment(treatment):
	# Older submitted Customer Demo conversions stored Capitalized and used the
	# Asset Category's regular depreciation schedule.
	if treatment == "Capitalized":
		return "Scheduled Depreciation"
	return treatment


def get_depreciation_accounts(asset_category, company):
	from erpnext.assets.doctype.asset.depreciation import get_depreciation_accounts as get_accounts

	accounts = get_accounts(asset_category, company)
	if not all(accounts):
		frappe.throw(_("Configure Fixed Asset, Accumulated Depreciation and Depreciation Expense accounts for {0}").format(asset_category))
	return accounts


def validate_company_ledger_account(account, company):
	account_details = frappe.db.get_value("Account", account, ["company", "is_group"], as_dict=True)
	if not account_details or account_details.company != company or account_details.is_group:
		frappe.throw(_("Account {0} must be a ledger account belonging to Company {1}").format(account, company))


def create_immediate_depreciation_entry(asset, posting_date):
	from erpnext.assets.doctype.asset.asset import make_journal_entry

	amount = flt(asset.net_purchase_amount, asset.precision("net_purchase_amount"))
	if amount <= 0:
		frappe.throw(_("Asset {0} must have a positive value to depreciate").format(asset.name))
	journal_entry = make_journal_entry(asset.name)
	conversion = frappe.get_doc("Stock to Asset Conversion", asset.stock_to_asset_conversion)
	if conversion.docstatus != 1 or conversion.company != asset.company:
		frappe.throw(_("Immediate depreciation requires a submitted Stock to Asset Conversion"))
	journal_entry.mark_as_system_generated()
	journal_entry.journal_entry_reason = _("Immediate depreciation from approved conversion {0}").format(conversion.name)
	journal_entry.posting_date = posting_date
	journal_entry.accounts[0].debit = amount
	journal_entry.accounts[0].debit_in_account_currency = amount
	journal_entry.accounts[1].credit = amount
	journal_entry.accounts[1].credit_in_account_currency = amount
	journal_entry.flags.ignore_permissions = True
	journal_entry.insert()
	journal_entry.submit()
	return journal_entry
