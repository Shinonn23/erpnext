frappe.ui.form.on("Stock to Asset Conversion", {
	setup(frm) {
		// Cancelling the conversion reverses the Assets it created (and their receipt movements) after checking they
		// have no later transactions. Frappe's "Cancel All Documents" dialog offered to cancel them first instead,
		// which skipped those checks and did not match the Cancel → Yes the user expects.
		frm.ignore_doctypes_on_cancel_all = ["Asset", "Asset Movement", "Serial and Batch Bundle"];
		frm.set_query("material_request", () => ({ filters: { docstatus: 1 } }));
		frm.set_query("pick_list", () => ({
			filters: { docstatus: 1, purpose: "Material Transfer", company: frm.doc.company },
		}));
		frm.set_query("warehouse", "stock_items", (doc) => ({
			filters: { company: doc.company, is_group: 0 },
		}));
		frm.set_query("item_code", "stock_items", () => ({ filters: { is_stock_item: 1 } }));
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
	refresh(frm) {
		// Draft and the approval states (Pending Accounts Review, Pending Level 1 Approval) are all docstatus 0;
		// the buttons are also shown to read-only reviewers. The server re-checks the role.
		const roles = frappe.user_roles || [];
		const has_any = (list) => list.some((role) => roles.includes(role));
		const money_roles = [
			"Accounts User",
			"Accounts Manager",
			"Finance Controller",
			"Managing Director",
			"Chief Executive Officer",
			"System Manager",
		];
		if (has_any(money_roles)) erpnext.accounts.ledger_preview.show_accounting_ledger_preview(frm);
		if (has_any([...money_roles, "Stock Manager"])) {
			erpnext.accounts.ledger_preview.show_stock_ledger_preview(frm);
		}
	},
	company(frm) {
		frm.set_value("asset_location", null);
	},
	pick_list(frm) {
		if (!frm.doc.pick_list) return;
		frappe.db.get_value("Pick List", frm.doc.pick_list, "material_request").then(({ message }) => {
			if (message && message.material_request && !frm.doc.material_request) {
				frm.set_value("material_request", message.material_request);
			}
		});
	},
});

frappe.ui.form.on("Stock to Asset Conversion Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		frappe.db
			.get_value("Item", row.item_code, ["stock_uom", "demo_asset_item_code"])
			.then(({ message }) => {
				if (message)
					frappe.model.set_value(cdt, cdn, {
						stock_uom: message.stock_uom,
						asset_item_code: message.demo_asset_item_code,
						serial_and_batch_bundle: null,
					});
			});
	},
	warehouse(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.warehouse) return;
		frappe.db.get_value("Warehouse", row.warehouse, "asset_location").then(({ message }) => {
			if (message && message.asset_location && !frm.doc.asset_location)
				frm.set_value("asset_location", message.asset_location);
		});
	},
});

frappe.ui.form.on("Stock to Asset Conversion Asset", {
	accounting_treatment(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(
			cdt,
			cdn,
			"calculate_depreciation",
			row.accounting_treatment === "Scheduled Depreciation"
		);
	},
});
