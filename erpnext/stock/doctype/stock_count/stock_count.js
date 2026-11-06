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
		// the Lost Warehouse (the field keeps its old name): where the quantity not found is moved
		frm.set_query("quarantine_warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				warehouse_type: "Lost",
				name: ["!=", frm.doc.warehouse || ""],
			},
		}));
		// the Found Warehouse: where what the count found beyond the system quantity is received
		frm.set_query("found_warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				warehouse_type: "Found",
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
		// the system names the Lost and the Found Warehouse (the company's LOST and FOUND); nobody picks them
		frm.set_df_property("quarantine_warehouse", "read_only", 1);
		frm.set_df_property("quarantine_storage_location", "read_only", frm.doc.status !== "Draft");
		frm.set_df_property("found_warehouse", "read_only", 1);
		erpnext.stock_count.fill_loss_warehouses?.(frm);
		erpnext.stock_count.show_review_summary?.(frm);
		erpnext.stock_count.guard_first_counter?.(frm);

		if (can_count && (frm.doc.items || []).some((row) => !row.is_counted)) {
			// explicit, never automatic: the counter says that what was not found is zero
			frm.add_custom_button(__("Set uncounted rows to 0"), () => {
				let changed = 0;
				for (const row of frm.doc.items || []) {
					if (row.is_counted) continue;
					row.counted_qty = 0;
					row.is_counted = 1;
					row.difference_qty = 0 - flt(row.system_qty);
					changed += 1;
				}
				frm.refresh_field("items");
				if (changed) frm.dirty();
				frappe.show_alert({
					message: __("{0} row(s) set to 0. Save to keep them.", [changed]),
					indicator: "green",
				});
			});
		}
		if (!frm.doc.docstatus && frm.doc.status === "Draft" && frm.doc.warehouse) {
			frm.add_custom_button(__("Add Storage Locations"), () =>
				erpnext.stock_count.add_storage_locations(frm)
			);
		}
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
			frm.add_custom_button(__("Lost Stock Entry"), () =>
				frappe.set_route("Form", "Stock Entry", frm.doc.stock_entry)
			);
		}
		if (frm.doc.found_stock_entry) {
			frm.add_custom_button(__("Found Stock Entry"), () =>
				frappe.set_route("Form", "Stock Entry", frm.doc.found_stock_entry)
			);
		}
		if (frm.doc.found_transfer_entry) {
			frm.add_custom_button(__("Serial Transfer Entry"), () =>
				frappe.set_route("Form", "Stock Entry", frm.doc.found_transfer_entry)
			);
		}
		// what a count found beyond the system quantity is counted again by another person first
		if (frm.doc.recount_stock_count) {
			frm.add_custom_button(__("Recount"), () =>
				frappe.set_route("Form", "Stock Count", frm.doc.recount_stock_count)
			);
		}
		if (frm.doc.recount_of) {
			frm.add_custom_button(__("First Count"), () =>
				frappe.set_route("Form", "Stock Count", frm.doc.recount_of)
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
		erpnext.stock_count.fill_loss_warehouses?.(frm);
		frm.toggle_display("item_count", monthly && frm.doc.item_selection !== "Selected Items");
		frm.toggle_display("quarantine_warehouse", monthly);
		frm.toggle_display("found_warehouse", monthly);
		// the Lost Storage Location is rarely used: shown only when the count names one
		frm.toggle_display("quarantine_storage_location", monthly && !!frm.doc.quarantine_storage_location);
		// the series has one option and the counter never chooses it
		frm.toggle_display("naming_series", false);
		// the Accounts, Final and CEO steps exist only on an Annual count (company policy fields)
		for (const stage of ["accounts_approved", "final_approved", "ceo_acknowledged"]) {
			for (const suffix of ["by", "on"]) {
				frm.toggle_display(`${stage}_${suffix}`, !monthly && !!frm.doc[`${stage}_${suffix}`]);
			}
		}
	},

	warehouse(frm) {
		erpnext.stock_count.fill_loss_warehouses?.(frm);
	},

	company(frm) {
		erpnext.stock_count.fill_loss_warehouses?.(frm);
	},

	scan_storage_location(frm) {
		const value = (frm.doc.scan_storage_location || "").trim();
		if (!value || !frm.doc.warehouse) return;
		// a scanned label may be the location's code as well as its full name
		frappe
			.call({
				method: "erpnext.stock.doctype.stock_count.stock_count.resolve_storage_location",
				args: { warehouse: frm.doc.warehouse, value },
			})
			.then((r) => {
				if (r.message && r.message !== frm.doc.scan_storage_location) {
					frm.set_value("scan_storage_location", r.message);
				}
			})
			.catch(() => frm.set_value("scan_storage_location", ""));
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
					// say which scan failed and where, and stay long enough to be read
					frappe.show_alert(
						{
							message: location
								? __("Code {0} was not found at {1}.", [value, location])
								: __("Code {0} was not found.", [value]),
							indicator: "red",
						},
						20
					);
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

// Count a whole zone or a range of locations at once: fills the Inventory Dimensions table with one Storage Location
// row each (Connect Diagnostics, 5 Nov 2026).
frappe.provide("erpnext.stock_count");
erpnext.stock_count.add_storage_locations = async (frm) => {
	const method = "erpnext.stock.doctype.stock_count.stock_count.";
	const zones =
		(
			await frappe.call({
				method: method + "get_storage_location_zones",
				args: { warehouse: frm.doc.warehouse },
			})
		).message || [];
	const location_query = () => ({ filters: { warehouse: frm.doc.warehouse, disabled: 0 } });
	const dialog = new frappe.ui.Dialog({
		title: __("Add Storage Locations"),
		fields: [
			{
				fieldname: "by",
				fieldtype: "Select",
				label: __("Choose by"),
				options: [
					{ value: "zone", label: __("Zone") },
					{ value: "range", label: __("Range of locations") },
				],
				default: "zone",
				reqd: 1,
			},
			{
				fieldname: "zone",
				fieldtype: "Select",
				label: __("Zone"),
				options: ["", ...zones],
				depends_on: "eval:doc.by === 'zone'",
			},
			{
				fieldname: "from_location",
				fieldtype: "Link",
				options: "Storage Location",
				label: __("From Location"),
				get_query: location_query,
				depends_on: "eval:doc.by === 'range'",
			},
			{
				fieldname: "to_location",
				fieldtype: "Link",
				options: "Storage Location",
				label: __("To Location"),
				get_query: location_query,
				depends_on: "eval:doc.by === 'range'",
			},
		],
		primary_action_label: __("Add"),
		async primary_action(values) {
			const args = { warehouse: frm.doc.warehouse };
			if (values.by === "zone") {
				if (!values.zone) return frappe.msgprint(__("Choose a Zone."));
				args.zone = values.zone;
			} else {
				if (!values.from_location || !values.to_location) {
					return frappe.msgprint(__("Choose the first and last location of the range."));
				}
				args.from_location = values.from_location;
				args.to_location = values.to_location;
			}
			const locations =
				(await frappe.call({ method: method + "get_count_storage_locations", args, freeze: true }))
					.message || [];
			const present = new Set(
				(frm.doc.dimensions || [])
					.filter((row) => row.inventory_dimension === "Storage Location")
					.map((row) => row.dimension_value)
			);
			let added = 0;
			for (const location of locations) {
				if (present.has(location)) continue;
				const row = frm.add_child("dimensions");
				row.inventory_dimension = "Storage Location";
				row.reference_document = "Storage Location";
				row.dimension_value = location;
				added += 1;
			}
			frm.refresh_field("dimensions");
			if (added) frm.dirty();
			dialog.hide();
			frappe.show_alert({
				message: __("{0} Storage Location(s) added", [added]),
				indicator: added ? "green" : "orange",
			});
		},
	});
	dialog.show();
};

// A Monthly count moves what it did not find to the company's LOST warehouse and what a recount found into FOUND; the
// system fills both when the company, the warehouse or the type changes (they are read-only).
erpnext.stock_count.fill_loss_warehouses = async (frm) => {
	// a saved count keeps what it holds (the server fills a blank on save); opening it never marks it unsaved
	if (
		!frm.is_new() ||
		frm.doc.docstatus ||
		frm.doc.status !== "Draft" ||
		frm.doc.count_type !== "Monthly" ||
		!frm.doc.company
	)
		return;
	const key = `${frm.doc.company}|${frm.doc.warehouse || ""}`;
	if (frm.__loss_warehouses_for === key) return;
	frm.__loss_warehouses_for = key;
	const r = await frappe.call({
		method: "erpnext.stock.doctype.stock_count.stock_count.get_loss_warehouses",
		args: { company: frm.doc.company, warehouse: frm.doc.warehouse || null },
	});
	const found = r?.message || {};
	for (const [fieldname, value] of [
		["quarantine_warehouse", found.lost],
		["found_warehouse", found.found],
	]) {
		// an empty field, or one that now names the counted warehouse itself, takes the default; a saved value stays
		if (value && (!frm.doc[fieldname] || frm.doc[fieldname] === frm.doc.warehouse))
			await frm.set_value(fieldname, value);
	}
};

// The approver of a counted result sees how far it differs before approving: rows short and over, and the units
// (money is never shown here), on the Review tab, which opens first for the approver's states.
erpnext.stock_count.variance_summary = (rows) => {
	let short = 0;
	let over = 0;
	let short_qty = 0;
	let over_qty = 0;
	for (const row of rows || []) {
		if (!row.is_counted) continue;
		const difference = flt(row.counted_qty) - flt(row.system_qty);
		if (difference < 0) {
			short += 1;
			short_qty += -difference;
		} else if (difference > 0) {
			over += 1;
			over_qty += difference;
		}
	}
	return { short, over, short_qty, over_qty };
};

erpnext.stock_count.show_review_summary = (frm) => {
	const state = frm.doc.workflow_state || "";
	const waiting = !frm.doc.docstatus && frm.doc.status === "In Progress" && /Pending/.test(state);
	if (!waiting) return;
	const v = erpnext.stock_count.variance_summary(frm.doc.items);
	frm.set_intro?.(
		__("{0} row(s) short ({1} units), {2} row(s) over ({3} units) against the system quantity.", [
			v.short,
			v.short_qty,
			v.over,
			v.over_qty,
		]),
		v.short || v.over ? "orange" : "green"
	);
	if (!frm.__review_tab_opened) {
		frm.__review_tab_opened = true;
		frm.layout?.tabs?.find((tab) => tab.df.fieldname === "review_tab")?.set_active();
	}
};

// A recount is done by another person (the server refuses the first counter at the counted stamp): the first counter
// sees it at once instead of losing the work on Save.
erpnext.stock_count.guard_first_counter = async (frm) => {
	if (!frm.doc.recount_of || frm.doc.docstatus || frm.doc.status === "Completed") return;
	const r = await frappe.db.get_value("Stock Count", frm.doc.recount_of, "counted_by");
	if (r?.message?.counted_by !== frappe.session.user) return;
	frm.set_df_property("items", "read_only", 1);
	frm.remove_custom_button?.(__("Prepare Count"));
	frm.set_intro?.(__("You counted the first count; another person must do this recount."), "red");
};
