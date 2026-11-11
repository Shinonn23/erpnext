frappe.ui.form.on("Stock to Asset Conversion", {
	async refresh(frm) {
		const value_field = frm.get_docfield("total_value");
		if (
			!frm.is_new() && frm.doc.docstatus === 0 && value_field &&
			frappe.perm.has_perm(frm.doctype, value_field.permlevel || 0, "read", frm.doc)
		) {
			for (const [label, kind] of [["Accounting Ledger", "accounting"], ["Stock Ledger", "stock"]]) {
				frm.add_custom_button(__(label), async () => {
					if (frm.is_dirty()) {
						frappe.msgprint(__("Save your changes before previewing the ledger."));
						return;
					}
					const { message } = await frappe.call({
						method: `erpnext.controllers.stock_controller.show_${kind}_ledger_preview`,
						args: { company: frm.doc.company, doctype: frm.doctype, docname: frm.doc.name },
						freeze: true,
					});
					const prefix = kind === "stock" ? "sl" : "gl";
					erpnext.accounts.ledger_preview.make_dialog(
						__(`${label} Preview`), `${kind}_ledger_preview_html`,
						message[`${prefix}_columns`], message[`${prefix}_data`]
					);
				}, __("Preview"));
			}
		}

	},
	setup(frm) {
		// Cancelling the conversion reverses the Assets it created (and their receipt movements) after checking they
		// have no later transactions. Frappe's "Cancel All Documents" dialog offered to cancel them first instead,
		// which skipped those checks and did not match the Cancel → Yes the user expects.
		frm.ignore_doctypes_on_cancel_all = ["Asset", "Asset Movement", "Serial and Batch Bundle"];
		frm.set_query("material_request", () => ({ filters: { docstatus: 1 } }));
		frm.set_query("warehouse", "stock_items", (doc) => ({ filters: { company: doc.company, is_group: 0 } }));
		frm.set_query("item_code", "stock_items", () => ({ filters: { is_stock_item: 1 } }));
		frm.set_query("asset_item_code", "stock_items", () => ({ filters: { is_fixed_asset: 1, is_stock_item: 0 } }));
		frm.set_query("purchase_reference", "assets", (doc) => ({
			filters: { company: doc.company, docstatus: 1, is_return: 0 },
		}));
		frm.set_query("expense_account", "assets", (doc) => ({
			filters: { company: doc.company, root_type: "Expense", is_group: 0, disabled: 0 },
		}));
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

frappe.ui.form.on("Stock to Asset Conversion Asset", {
	purchase_reference_type(frm, cdt, cdn) {
		return frappe.model.set_value(cdt, cdn, {
			purchase_reference: null, purchase_reference_item: null, purchase_unit_price: null,
		});
	},
	async purchase_reference(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		await frappe.model.set_value(cdt, cdn, {
			purchase_reference_item: null, purchase_unit_price: null,
		});
		if (!row.purchase_reference || !row.purchase_reference_type) return;
		const source = frm.doc.stock_items.find((item) => item.name === row.source_item_row);
		if (!source) return;
		const reference = row.purchase_reference;
		const reference_type = row.purchase_reference_type;
		const purchase = await frappe.db.get_doc(reference_type, reference);
		if (row.purchase_reference !== reference || row.purchase_reference_type !== reference_type) return;
		const items = purchase.items.filter((item) => item.item_code === source.item_code);
		if (!items.length) {
			frappe.msgprint(__("The purchase document has no matching Stock Item."));
			return;
		}
		if (items.length === 1) {
			return frappe.model.set_value(cdt, cdn, "purchase_reference_item", items[0].name);
		}
		const choices = items.map((item) => `${item.idx}: ${item.item_code} (${item.name})`);
		frappe.prompt([
			{ fieldname: "purchase_row", fieldtype: "Select", label: __("Purchase Row"),
				options: choices, reqd: 1 },
		], (values) => {
			if (row.purchase_reference !== reference || row.purchase_reference_type !== reference_type) return;
			const selected = items[choices.indexOf(values.purchase_row)];
			if (selected) frappe.model.set_value(cdt, cdn, "purchase_reference_item", selected.name);
		}, __("Select Purchase Row"), __("Select"));
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
		frappe.db.get_value("Item", row.item_code, ["stock_uom", "default_asset_item_for_conversion"]).then(({ message }) => {
			if (message) frappe.model.set_value(cdt, cdn, { stock_uom: message.stock_uom, asset_item_code: message.default_asset_item_for_conversion, serial_and_batch_bundle: null });
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
