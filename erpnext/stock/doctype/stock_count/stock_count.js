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
		frm.set_query("count_template", () => ({
			filters: {
				company: frm.doc.company,
				disabled: 0,
				warehouse: ["in", ["", frm.doc.warehouse || ""]],
			},
		}));
		frm.set_query("item_code", "selected_items", () => ({
			filters: { is_stock_item: 1, disabled: 0 },
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
		frm.__previous_template = frm.doc.count_template;
		const can_count =
			frm.doc.status === "In Progress" &&
			!frm.doc.docstatus &&
			(!frm.doc.workflow_state || frm.doc.workflow_state === "Stock Count Draft");
		frm.trigger("count_type");
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
		// The selection is frozen once the count is prepared; the server refuses a change as well.
		for (const fieldname of ["item_selection", "count_template", "selected_items"]) {
			frm.set_df_property(fieldname, "read_only", frm.doc.status !== "Draft");
		}
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

		if (
			!frm.doc.docstatus &&
			frm.doc.status === "Draft" &&
			!frm.is_new() &&
			frm.doc.count_type === "Monthly" &&
			frm.doc.item_selection === "Selected Items" &&
			(frm.doc.selected_items || []).length
		) {
			frm.add_custom_button(__("Save as Template"), () => {
				frappe.prompt(
					{ fieldname: "title", fieldtype: "Data", label: __("Title"), reqd: 1 },
					async ({ title }) => {
						if (frm.is_dirty()) await frm.save();
						const r = await frm.call({
							method: "erpnext.stock.doctype.stock_count.stock_count.make_count_template",
							args: { name: frm.doc.name, title },
						});
						frappe.show_alert({
							message: __("Count Template {0} created.", [r.message]),
							indicator: "green",
						});
					},
					__("Save as Count Template")
				);
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
		const monthly = frm.doc.count_type === "Monthly";
		frm.toggle_display("item_count", monthly && frm.doc.item_selection !== "Selected Items");
		frm.toggle_display("quarantine_warehouse", monthly);
	},

	item_selection(frm) {
		frm.trigger("count_type");
	},

	count_template(frm) {
		if (frm.__reverting_template) return;
		if (frm.doc.status !== "Draft" || frm.doc.docstatus || !frm.doc.count_template) {
			frm.__previous_template = frm.doc.count_template;
			return;
		}
		const previous = frm.__previous_template || "";
		const fill = () => {
			frm.__previous_template = frm.doc.count_template;
			frm.clear_table("selected_items");
			frappe.db.get_doc("Stock Count Template", frm.doc.count_template).then((template) => {
				for (const row of template.items || []) {
					frm.add_child("selected_items", { item_code: row.item_code, item_name: row.item_name });
				}
				frm.refresh_field("selected_items");
			});
		};
		const keep_previous = () => {
			frm.__reverting_template = true;
			frm.set_value("count_template", previous).then(() => {
				frm.__reverting_template = false;
			});
		};
		if ((frm.doc.selected_items || []).length) {
			frappe.confirm(
				__("Replace the items already chosen with the items of this template?"),
				fill,
				keep_previous
			);
		} else {
			fill();
		}
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
