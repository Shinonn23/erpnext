// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Stock Count", {
	setup(frm) {
		frm.set_query("warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
			},
		}));
		frm.set_query("storage_location", () => ({
			filters: {
				warehouse: frm.doc.warehouse,
				disabled: 0,
			},
		}));
		frm.set_query("quarantine_warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				name: ["!=", frm.doc.warehouse || ""],
			},
		}));
		frm.set_query("quarantine_storage_location", () => ({
			filters: {
				warehouse: frm.doc.quarantine_warehouse,
				disabled: 0,
			},
		}));
		frm.set_query("inventory_dimension", "dimensions", () => ({ filters: { apply_to_all_doctypes: 1 } }));
		frm.set_query("batch_no", "items", (doc, cdt, cdn) => ({
			filters: { item: locals[cdt][cdn].item_code },
		}));
	},

	refresh(frm) {
		frm.toggle_display("item_count", frm.doc.count_type === "Monthly");
		frm.toggle_display("quarantine_warehouse", frm.doc.count_type === "Monthly");
		frm.toggle_display("scan_barcode", frm.doc.status === "In Progress" && !frm.doc.docstatus);
		frm.set_df_property("dimensions", "read_only", frm.doc.status !== "Draft");
		frm.set_df_property("storage_location", "read_only", frm.doc.status !== "Draft");
		frm.set_df_property("warehouse", "read_only", frm.doc.status !== "Draft");
		frm.set_df_property("count_type", "read_only", frm.doc.status !== "Draft");
		frm.set_df_property("item_count", "read_only", frm.doc.status !== "Draft");
		frm.set_df_property("quarantine_warehouse", "read_only", frm.doc.status !== "Draft");
		frm.set_df_property("quarantine_storage_location", "read_only", frm.doc.status !== "Draft");

		if (!frm.doc.docstatus && frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Prepare Count"), async () => {
				if (frm.is_dirty()) await frm.save();
				await frm.call("prepare_count");
				await frm.reload_doc();
				frappe.show_alert({ message: __("Stock snapshot created."), indicator: "green" });
			});
		}

		if (frm.doc.stock_entry) {
			frm.add_custom_button(__("Quarantine Stock Entry"), () =>
				frappe.set_route("Form", "Stock Entry", frm.doc.stock_entry)
			);
		}
		if (frm.doc.stock_reconciliation) {
			frm.add_custom_button(__("Stock Reconciliation"), () =>
				frappe.set_route("Form", "Stock Reconciliation", frm.doc.stock_reconciliation)
			);
		}
	},

	count_type(frm) {
		frm.toggle_display("item_count", frm.doc.count_type === "Monthly");
		frm.toggle_display("quarantine_warehouse", frm.doc.count_type === "Monthly");
	},

	scan_barcode(frm) {
		const value = (frm.doc.scan_barcode || "").trim();
		if (!value) return;

		frm.set_value("scan_barcode", "");
		frappe.call({
			method: "erpnext.stock.utils.scan_barcode",
			args: { search_value: value, ctx: { company: frm.doc.company } },
		}).then((r) => {
			const scanned = r.message;
			if (!scanned || !scanned.item_code) {
				frappe.show_alert({
					message: __("Item, Batch, or Serial No was not found."),
					indicator: "red",
				});
				return;
			}

			const rows = frm.doc.items || [];
			let row = scanned.serial_no
				? rows.find((d) => d.item_code === scanned.item_code && d.serial_no === scanned.serial_no)
				: scanned.batch_no
					? rows.find((d) => d.item_code === scanned.item_code && d.batch_no === scanned.batch_no && !d.serial_no)
					: rows.find((d) => d.item_code === scanned.item_code && !d.batch_no && !d.serial_no);

			if (!row) {
				frappe.show_alert({
					message: __("This item, batch, or serial number is not in the prepared sample."),
					indicator: "orange",
				});
				return;
			}

			if (scanned.serial_no) {
				if (flt(row.counted_qty)) {
					frappe.show_alert({
						message: __("Serial No {0} was already counted.", [scanned.serial_no]),
						indicator: "orange",
					});
					return;
				}
				frappe.model.set_value(row.doctype, row.name, "counted_qty", 1);
			} else {
				frappe.model.set_value(row.doctype, row.name, "counted_qty", flt(row.counted_qty) + 1);
			}
			frappe.model.set_value(row.doctype, row.name, "is_counted", 1);
			frm.refresh_field("items");
		});
	},
});

frappe.ui.form.on("Stock Count Dimension", {
	inventory_dimension(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		row.reference_document = "";
		row.dimension_value = "";
		if (!row.inventory_dimension) {
			frm.refresh_field("dimensions");
			return;
		}
		frappe.db.get_value("Inventory Dimension", row.inventory_dimension, "reference_document", (r) => {
			frappe.model.set_value(cdt, cdn, "reference_document", r.reference_document);
		});
	},
});

frappe.ui.form.on("Stock Count Item", {
	counted_qty(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "is_counted", 1);
		frappe.model.set_value(cdt, cdn, "difference_qty", flt(row.counted_qty) - flt(row.system_qty));
	},

	is_counted(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.is_counted) {
			frappe.model.set_value(cdt, cdn, "difference_qty", flt(row.counted_qty) - flt(row.system_qty));
		}
	},

	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.item_code && !row.system_qty) {
			frappe.db.get_value("Item", row.item_code, "item_name", (r) => {
				frappe.model.set_value(cdt, cdn, "item_name", r.item_name);
			});
		}
	},
});
