frappe.ui.form.on("Stock to Asset Conversion", {
	setup(frm) {
		frm.set_query("material_request", () => ({ filters: { docstatus: 1 } }));
		frm.set_query("warehouse", "stock_items", (doc) => ({ filters: { company: doc.company, is_group: 0 } }));
		frm.set_query("item_code", "stock_items", () => ({ filters: { is_stock_item: 1 } }));
	},
	company(frm) {
		frm.set_value("asset_location", null);
	},
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Rebuild Asset Preview"), () => frm.save());
		}
	},
});

frappe.ui.form.on("Stock to Asset Conversion Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		frappe.db.get_value("Item", row.item_code, ["stock_uom", "demo_asset_item_code"]).then(({ message }) => {
			if (message) frappe.model.set_value(cdt, cdn, { stock_uom: message.stock_uom, asset_item_code: message.demo_asset_item_code, serial_and_batch_bundle: null });
		});
	},
	warehouse(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.warehouse) return;
		frappe.db.get_value("Warehouse", row.warehouse, "asset_location").then(({ message }) => {
			if (message && message.asset_location && !frm.doc.asset_location) frm.set_value("asset_location", message.asset_location);
		});
	},
});
