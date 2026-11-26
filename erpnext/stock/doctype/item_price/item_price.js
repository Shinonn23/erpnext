// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Item Price", {
	setup(frm) {
		frm.set_query("item_code", function () {
			return {
				filters: {
					has_variants: 0,
				},
			};
		});

		frm._price_list_filters = {};
		frm.set_query("price_list", () => ({ filters: frm._price_list_filters }));
	},

	refresh(frm) {
		return update_item_details(frm);
	},

	onload(frm) {
		// Fetch price list details
		frm.add_fetch("price_list", "buying", "buying");
		frm.add_fetch("price_list", "selling", "selling");
		frm.add_fetch("price_list", "currency", "currency");

		// Fetch item details
		frm.add_fetch("item_code", "item_name", "item_name");
		frm.add_fetch("item_code", "description", "item_description");

		frm.set_df_property(
			"bulk_import_help",
			"options",
			'<a href="/app/data-import-tool/Item Price">' + __("Import in Bulk") + "</a>"
		);

		frm.set_query("batch_no", function () {
			return {
				filters: {
					item: frm.doc.item_code,
				},
			};
		});
	},

	item_code(frm) {
		return update_item_details(frm, true);
	},
});

async function update_item_details(frm, set_stock_uom = false) {
	const doc = frm.doc;
	const item_code = doc.item_code;
	frm._price_list_filters = {};
	if (!item_code) return;

	const { message: item } = await frappe.db.get_value("Item", item_code, [
		"is_sales_item",
		"is_purchase_item",
		"stock_uom",
	]);
	if (!item || frm.doc !== doc || frm.doc.item_code !== item_code) return;

	if (item.is_sales_item && !item.is_purchase_item) {
		frm._price_list_filters.selling = 1;
	} else if (item.is_purchase_item && !item.is_sales_item) {
		frm._price_list_filters.buying = 1;
	}

	// Validate the stock UOM only after the selected Item is set on the form.
	if (set_stock_uom) return frm.set_value("uom", item.stock_uom);
}
