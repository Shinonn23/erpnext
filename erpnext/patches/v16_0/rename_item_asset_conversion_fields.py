import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	if frappe.db.has_column("Item", "demo_asset_item_code"):
		if frappe.db.sql(
			"""select name from `tabItem` where ifnull(default_asset_item_for_conversion, '') != ''
			and not (default_asset_item_for_conversion <=> demo_asset_item_code) limit 1"""
		):
			frappe.throw("Cannot migrate Item asset conversion field: the new field already contains data.")
		rename_field("Item", "demo_asset_item_code", "default_asset_item_for_conversion")
	rename_field("Item", "demo_tab", "asset_conversion_tab", validate=False)
