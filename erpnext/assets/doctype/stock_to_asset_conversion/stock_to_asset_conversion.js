frappe.ui.form.on("Stock to Asset Conversion", {
	setup(frm) {
		// Cancelling the conversion reverses the Assets it created (and their receipt movements) after checking they
		// have no later transactions. Frappe's "Cancel All Documents" dialog offered to cancel them first instead,
		// which skipped those checks and did not match the Cancel → Yes the user expects.
		frm.ignore_doctypes_on_cancel_all = ["Asset", "Asset Movement", "Serial and Batch Bundle"];
		frm.set_query("material_request", () => ({ filters: { docstatus: 1 } }));
		frm.set_query("warehouse", "stock_items", (doc) => ({ filters: { company: doc.company, is_group: 0 } }));
		frm.set_query("item_code", "stock_items", () => ({ filters: { is_stock_item: 1 } }));
		frm.set_query("asset_item_code", "stock_items", () => ({ filters: { is_fixed_asset: 1, is_stock_item: 0 } }));
		const bundle_field = frm.get_docfield("stock_items", "serial_and_batch_bundle");
		if (bundle_field) {
			bundle_field.get_route_options_for_new_doc = (row) => ({
				item_code: row.doc.item_code,
				warehouse: row.doc.warehouse,
				voucher_type: frm.doc.doctype,
				voucher_no: frm.doc.name,
				voucher_detail_no: row.doc.name,
				type_of_transaction: "Outward",
			});
		}
	},
	company(frm) {
		frm.set_value("asset_location", null);
	},
});

frappe.ui.form.on("Stock to Asset Conversion Item", {
	pick_serial_and_batch(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code || !row.warehouse) {
			frappe.msgprint(__("Select a Stock Item and Warehouse first."));
			return;
		}
		frappe.db.get_value("Item", row.item_code, ["has_batch_no", "has_serial_no"]).then(({ message }) => {
			if (!message || (!message.has_batch_no && !message.has_serial_no)) {
				frappe.msgprint(__("This Item does not use Serial or Batch Numbers."));
				return;
			}
			row.has_serial_no = message.has_serial_no;
			row.has_batch_no = message.has_batch_no;
			row.type_of_transaction = "Outward";
			new erpnext.SerialBatchPackageSelector(frm, row, (bundle) => {
				if (bundle) frappe.model.set_value(cdt, cdn, {
					serial_and_batch_bundle: bundle.name,
					warehouse: bundle.warehouse,
					qty: Math.abs(bundle.total_qty),
				});
			});
		});
	},
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
