// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Stock Count", {
	setup(frm) {
		frm.fields_dict.items.grid.on_row_upload = (row, fieldnames, values) => {
			const counted_qty_index = fieldnames.indexOf("counted_qty");
			if (counted_qty_index < 0) return;
			const quantity = String(values[counted_qty_index] ?? "").trim();
			// Blank quantities stay uncounted; an explicit zero is a completed count.
			if (quantity) {
				const parsed = Number(strip_number_groups(quantity));
				if (!Number.isFinite(parsed) || parsed !== row.counted_qty) {
					row.counted_qty = null;
					row.is_counted = 0;
					row.difference_qty = 0;
					frappe.throw(__("Row {0}: Counted Qty must be a valid finite number.", [row.idx]));
				}
				row.is_counted = 1;
			}
			row.difference_qty = row.is_counted ? flt(row.counted_qty) - flt(row.system_qty) : 0;
		};
		frm.set_query("warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
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
		frm.set_query("scan_storage_location", () => ({
			filters: { warehouse: frm.doc.warehouse, disabled: 0 },
		}));
		frm.set_query("storage_location", "items", (doc, cdt, cdn) =>
			erpnext.queries.storage_location(doc.warehouse, locals[cdt][cdn].item_code)
		);
		frm.set_query("inventory_dimension", "dimensions", () => ({ filters: { apply_to_all_doctypes: 1 } }));
		frm.set_query("batch_no", "items", (doc, cdt, cdn) => ({
			filters: { item: locals[cdt][cdn].item_code },
		}));
	},

	refresh(frm) {
		const can_count =
			frm.doc.status === "In Progress" &&
			!frm.doc.docstatus &&
			(!frm.doc.workflow_state || frm.doc.workflow_state === "Stock Count Draft");
		frm.toggle_display("item_count", frm.doc.count_type === "Monthly");
		frm.toggle_display("quarantine_warehouse", frm.doc.count_type === "Monthly");
		frm.toggle_display("scan_barcode", can_count);
		frm.toggle_display("scan_storage_location", can_count);
		frm.set_df_property("items", "read_only", !can_count);
		// set_df_property redraws only when the stored value changes: after Prepare Count reloads the form the
		// table kept its read-only drawing until the page was reloaded, so the counter could not type counts.
		frm.refresh_field("items");
		frm.set_df_property("dimensions", "read_only", frm.doc.status !== "Draft");
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
				frm.scroll_to_field("items");
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
		const location = frm.doc.scan_storage_location;

		frm.set_value("scan_barcode", "");
		frappe
			.call({
				method: "erpnext.stock.utils.scan_barcode",
				args: { search_value: value, ctx: { company: frm.doc.company } },
			})
			.then((r) => {
				const scanned = r.message;
				if (!scanned || !scanned.item_code) {
					frappe.show_alert({
						message: __("Item, Batch, or Serial No was not found."),
						indicator: "red",
					});
					return;
				}

				const rows = frm.doc.items || [];
				const matches = rows.filter(
					(d) =>
						d.item_code === scanned.item_code &&
						(scanned.serial_no
							? d.serial_no === scanned.serial_no
							: !d.serial_no &&
							  (!scanned.batch_no || d.batch_no === scanned.batch_no) &&
							  (!location || d.storage_location === location))
				);
				if (matches.length > 1) {
					frappe.msgprint(
						__(
							"Multiple count rows match this scan. Select a Scan Storage Location and scan the Batch No if needed, or enter the count in the matching row."
						)
					);
					return;
				}
				const row = matches[0];

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
