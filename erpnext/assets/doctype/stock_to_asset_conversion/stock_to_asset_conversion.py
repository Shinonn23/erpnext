import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, nowtime

import erpnext
from erpnext.assets.doctype.asset_category.asset_category import get_asset_category_account
from erpnext.controllers.stock_controller import StockController, get_item_wise_inventory_account_map
from erpnext.stock.stock_ledger import get_previous_sle
from erpnext.stock.utils import _get_incoming_rate


class StocktoAssetConversion(StockController):
	def validate(self):
		self.validate_company_settings()
		self.set_missing_values()
		self.sync_pick_list_selections()
		self.validate_stock_items()
		self.build_asset_preview()
		self.calculate_totals()

	def validate_company_settings(self):
		if not self.company:
			frappe.throw(_("Company is required"))
		company = frappe.get_cached_doc("Company", self.company)
		if flt(company.asset_capitalization_threshold) > 0 and not company.low_value_asset_expense_account:
			frappe.throw(_("Set Low Value Asset Expense Account in Company {0}").format(self.company))
		if not self.posting_time:
			self.posting_time = nowtime()
		if self.material_request:
			request = frappe.get_doc("Material Request", self.material_request)
			if request.docstatus != 1:
				frappe.throw(_("Conversion can only be made from a submitted Material Request"))
			if request.material_request_type != "Customer Demo":
				frappe.throw(_("Stock to Asset Conversion is only available for Customer Demo requests"))
			if request.company != self.company:
				frappe.throw(_("Request and Conversion must belong to the same Company"))

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
					)
					row.actual_qty = get_previous_sle(args).get("qty_after_transaction") or 0
					row.valuation_rate = _get_incoming_rate(args, raise_error_if_no_rate=False)
					row.amount = flt(row.qty) * flt(row.valuation_rate)

	def validate_stock_items(self):
		if not self.stock_items:
			frappe.throw(_("At least one Stock Item is required"))
		if not self.asset_location:
			frappe.throw(_("Asset Location is required. Set it on the source Warehouse or select it here."))
		for row in self.stock_items:
			item = frappe.get_cached_doc("Item", row.item_code)
			if not item.is_stock_item:
				frappe.throw(_("Row {0}: {1} must be a Stock Item").format(row.idx, row.item_code))
			if not item.demo_asset_item_code:
				frappe.throw(_("Row {0}: Set Demo Asset Item Code on {1}").format(row.idx, row.item_code))
			target = frappe.get_cached_doc("Item", item.demo_asset_item_code)
			if not target.is_fixed_asset or target.is_stock_item:
				frappe.throw(_("Row {0}: Target Item must be a non-stock Fixed Asset Item").format(row.idx))
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
				request = frappe.get_cached_doc("Material Request", self.material_request)
				request_row = frappe.db.get_value(
					"Material Request Item", row.material_request_item,
					["parent", "item_code", "item_type", "from_warehouse"], as_dict=True,
				)
				if not request_row or request_row.parent != request.name or request_row.item_code != row.item_code or request_row.item_type != "Stock Item" or request_row.from_warehouse != row.warehouse:
					frappe.throw(_("Row {0}: Stock Item is not requested on the linked Material Request").format(row.idx))
			location = frappe.db.get_value("Warehouse", row.warehouse, "asset_location")
			if location and location != self.asset_location:
				frappe.throw(_("Row {0}: Asset Location does not match Warehouse mapping {1}").format(row.idx, location))

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
			frappe.throw(_("Row {0}: Create a Pick List to select Serial / Batch details").format(row.idx))
		if bundle.type_of_transaction != "Outward":
			frappe.throw(_("Row {0}: The Serial and Batch Bundle must be Outward").format(row.idx))
		if abs(flt(bundle.total_qty)) != flt(row.qty):
			frappe.throw(_("Row {0}: Bundle Qty must match conversion Qty").format(row.idx))

	def sync_pick_list_selections(self):
		if not self.pick_list:
			return

		pick_list = frappe.get_doc("Pick List", self.pick_list)
		if pick_list.docstatus != 1:
			return
		if (
			pick_list.company != self.company
			or pick_list.purpose != "Material Transfer"
			or pick_list.pick_list_type != "Customer Demo - Asset Conversion"
			or pick_list.material_request != self.material_request
		):
			frappe.throw(_("Pick List must be submitted and linked to this Material Request and Company"))

		locations_by_conversion_row = {}
		for location in pick_list.locations:
			locations_by_conversion_row.setdefault(location.material_request_item, []).append(location)

		for row in self.stock_items:
			locations = locations_by_conversion_row.get(row.material_request_item, [])
			if len(locations) != 1:
				frappe.throw(_("Row {0}: Pick List must contain exactly one matching picked row").format(row.idx))
			location = locations[0]
			if location.item_code != row.item_code or location.warehouse != row.warehouse:
				frappe.throw(_("Row {0}: Picked Item and Warehouse must match this Conversion row").format(row.idx))
			row.material_request_item = location.material_request_item
			if flt(location.picked_qty) != flt(row.qty):
				frappe.throw(_("Row {0}: Picked Qty must match Conversion Qty {1}").format(row.idx, row.qty))
			row.serial_and_batch_bundle = location.serial_and_batch_bundle

	def build_asset_preview(self):
		self.set("assets", [])
		threshold = flt(frappe.db.get_value("Company", self.company, "asset_capitalization_threshold"))
		for row in self.stock_items:
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
					asset_value = flt(entry.incoming_rate or row.valuation_rate)
					treatment, calculate_depreciation = get_accounting_treatment(asset_value, threshold)
					self.append(
						"assets",
						{
							"source_item_row": row.name,
							"asset_item_code": row.asset_item_code,
							"asset_serial_number": entry.serial_no,
							"source_serial_no": entry.serial_no,
							"source_batch_no": entry.batch_no,
							"asset_value": asset_value,
							"asset_location": self.asset_location,
							"accounting_treatment": treatment,
							"calculate_depreciation": calculate_depreciation,
						},
					)

	def calculate_totals(self):
		self.total_value = sum(flt(row.amount) for row in self.stock_items)

	def before_submit(self):
		if not self.assets:
			frappe.throw(_("Asset Preview is required"))
		if not self.pick_list or frappe.db.get_value("Pick List", self.pick_list, "docstatus") != 1:
			frappe.throw(_("Submit the linked Pick List before submitting this conversion"))
		for row in self.stock_items:
			item = frappe.get_cached_doc("Item", row.item_code)
			if not self.pick_list:
				frappe.throw(_("Create and submit a Pick List from the Customer Demo Material Request before conversion"))
			if (item.has_serial_no or item.has_batch_no) and not row.serial_and_batch_bundle:
				frappe.throw(_("Row {0}: Select Serial / Batch details on the linked Pick List").format(row.idx))
			if row.serial_and_batch_bundle:
				self.validate_bundle(row, item)
		for output in self.assets:
			if output.calculate_depreciation:
				asset_category = frappe.get_cached_doc("Item", output.asset_item_code).asset_category
				if not frappe.get_cached_doc("Asset Category", asset_category).finance_books:
					frappe.throw(
						_("Configure a Finance Book and depreciation method in Asset Category {0} before converting this asset").format(asset_category)
					)

	def on_update(self):
		if self.stock_items:
			self.set_serial_and_batch_bundle(table_name="stock_items")

	def on_submit(self):
		self.update_stock_ledger()
		self.make_gl_entries()
		self.create_assets()
		self.status = "Submitted"
		self.db_set("status", self.status)
		if self.pick_list:
			frappe.db.set_value("Pick List", self.pick_list, "status", "Completed")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Serial and Batch Bundle", "Asset")
		for row in self.assets:
			if row.asset and frappe.db.get_value("Asset", row.asset, "docstatus") == 1:
				if frappe.db.sql(
					"""select 1 from `tabAsset Movement Item` ami join `tabAsset Movement` am on am.name=ami.parent
					where ami.asset=%s and am.docstatus=1 limit 1""",
					row.asset,
				):
					frappe.throw(_("Asset {0} has already been moved; cancel its movements before cancelling this conversion").format(row.asset))
				frappe.get_doc("Asset", row.asset).cancel()
		self.update_stock_ledger()
		self.make_gl_entries()
		if self.pick_list and frappe.db.get_value("Pick List", self.pick_list, "docstatus") == 1:
			frappe.get_doc("Pick List", self.pick_list).cancel()
		self.db_set("status", "Cancelled")

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
		inventory_map = self.get_inventory_account_map()
		if self.use_item_inventory_account:
			inventory_map = get_item_wise_inventory_account_map(self.stock_items, self.company)
		sle_map = self.get_stock_ledger_details()
		for row in self.stock_items:
			sle_value = sum(
				abs(flt(sle.stock_value_difference, self.get_debit_field_precision()))
				for sle in sle_map.get(row.name, [])
			)
			stock_value = sle_value or flt(row.amount, self.get_debit_field_precision())
			if stock_value:
				inventory = self.get_inventory_account_dict(row, inventory_map)
				credit_account = inventory["account"] if perpetual else self.get_company_default("default_expense_account")
				if credit_account and stock_value:
					entries.append(self.get_gl_dict({"account": credit_account, "credit": stock_value, "against": low_value_account}, inventory.get("account_currency"), item=row))
		for output in self.assets:
			value = flt(output.asset_value, self.precision("total_value"))
			if output.accounting_treatment == "Expense":
				account = low_value_account
			else:
				account = get_asset_category_account("fixed_asset_account", item=output.asset_item_code, company=self.company)
			if not account:
				frappe.throw(_("Set a Fixed Asset Account for Item {0}").format(output.asset_item_code))
			entries.append(self.get_gl_dict({"account": account, "debit": value, "against": "Stock Conversion", "remarks": self.name}, item=output))
		return entries

	def get_items_and_warehouses(self):
		return [row.item_code for row in self.stock_items], [row.warehouse for row in self.stock_items]

	def create_assets(self):
		request = frappe.get_doc("Material Request", self.material_request)
		for output in self.assets:
			asset = frappe.new_doc("Asset")
			asset.item_code = output.asset_item_code
			asset.company = self.company
			asset.asset_type = "Existing Asset"
			asset.location = output.asset_location
			source_row = next((row for row in self.stock_items if row.name == output.source_item_row), None)
			asset.source_warehouse = source_row.warehouse if source_row else None
			asset.asset_serial_number = output.asset_serial_number
			asset.source_serial_no = output.source_serial_no
			asset.source_batch_no = output.source_batch_no
			asset.material_request = request.name
			asset.is_demo_asset = 1
			asset.net_purchase_amount = output.asset_value
			asset.purchase_date = self.posting_date
			asset.available_for_use_date = self.posting_date
			asset.calculate_depreciation = output.calculate_depreciation
			asset.booked_fixed_asset = 1
			asset.set_missing_values()
			asset.insert(ignore_permissions=True)
			asset.db_set("status", "Submitted")
			asset.submit()
			output.asset = asset.name
			output.db_set("asset", asset.name)
			if output.source_serial_no:
				frappe.db.set_value("Serial No", output.source_serial_no, "asset", asset.name)
			frappe.db.set_value("Asset", asset.name, "demo_loan_status", "Available")


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
	if flt(asset_value) < flt(threshold):
		return "Expense", 0
	return "Capitalized", 1
