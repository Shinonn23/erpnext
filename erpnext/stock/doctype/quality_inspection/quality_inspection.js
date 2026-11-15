// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

cur_frm.cscript.refresh = cur_frm.cscript.inspection_type;

frappe.ui.form.on("Quality Inspection", {
	onload(frm) {
		frm.trigger("set_default_company");
	},

	set_default_company(frm) {
		if (frm.doc.docstatus === 0 && !frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_default("company"));
		}
	},

	setup: function (frm) {
		frm.fields_dict.readings.grid.set_column_disp_in_list_view(
			["specification", "parameter_group", "acceptance_display", "manual_inspection", "reading_value", ...Array.from({ length: 10 }, (_, i) => `reading_${i + 1}`), "status"],
			true, true
		);
		frm.set_query("reference_name", function (doc) {
			let filters = { docstatus: ["!=", 2] };

			if (doc.company) {
				filters["company"] = doc.company;
			}

			return {
				filters: filters,
			};
		});

		frm.set_query("batch_no", function () {
			return {
				filters: {
					item: frm.doc.item_code,
				},
			};
		});

		// Serial No based on item_code
		frm.set_query("item_serial_no", function () {
			let filters = {};
			if (frm.doc.item_code) {
				filters = {
					item_code: frm.doc.item_code,
				};
			}
			return { filters: filters };
		});

		// item code based on GRN/DN
		frm.set_query("item_code", function (doc) {
			if (doc.reference_type && doc.reference_name) {
				return {
					query: "erpnext.stock.doctype.quality_inspection.quality_inspection.item_query",
					filters: {
						reference_doctype: doc.reference_type,
						reference_name: doc.reference_name,
						inspection_type: doc.inspection_type,
					},
				};
			}
		});
	},

	refresh: async function (frm) {
		// Ignore cancellation of reference doctype on cancel all.
		frm.ignore_doctypes_on_cancel_all = [frm.doc.reference_type, "Serial and Batch Bundle"];
		show_reading_criteria(frm);
		if (frm.doc.docstatus === 0 && !frm.read_only && frm.perm?.[0]?.write &&
			(frm.doc.readings || []).some(can_fill_expected_text)) {
			frm.add_custom_button(__("Fill Expected Text"), () => {
				frappe.confirm(__("Fill empty text readings with the expected values? Check them against the actual inspection before submitting."), () => {
					let changed = false;
					for (const row of frm.doc.readings || []) {
						if (can_fill_expected_text(row)) {
							row.reading_value = row.value;
							changed = true;
						}
					}
					if (changed) {
						frm.dirty();
						frm.refresh_field("readings");
					}
				});
			});
		}
		const grid = frm.fields_dict.readings.grid;
		const label = __("Use automatic where available");
		if (frm.doc.docstatus === 0 && frm.doc.purchase_unit_qty) {
			grid.add_custom_button(label, async () => {
				frm.__manual_row_sync = true;
				try { await frm.set_value("manual_inspection", 0); }
				finally { frm.__manual_row_sync = false; }
				apply_manual_to_readings(frm, false);
			}, "top");
		} else {
			grid.custom_buttons[label]?.addClass("hidden");
		}
		await frm.trigger("show_purchase_tracking_fields");
	},

	manual_inspection(frm) {
		if (!frm.__manual_row_sync) apply_manual_to_readings(frm, Boolean(frm.doc.manual_inspection));
	},

	async show_purchase_tracking_fields(frm) {
		frm.set_df_property("purchase_unit_qty", "label", frm.doc.reference_type === "Pick List" ? __("Picked Unit Quantity") : __("Received Unit Quantity"));
		const purchase_unit = Number(frm.doc.purchase_unit_qty) > 0;
		const serial_unit = purchase_unit && Boolean(frm.doc.item_serial_no);
		frm.set_df_property("item_serial_no", "hidden", purchase_unit && !serial_unit);
		frm.set_df_property("batch_no", "hidden", purchase_unit && !frm.doc.batch_no);
		frm.set_df_property("purchase_rejected_serials", "hidden", !frm.doc.purchase_rejected_serials);
		frm.set_df_property("purchase_serials_section", "hidden", !frm.doc.purchase_serial_results?.length);
		frm.set_df_property("purchase_serial_results", "hidden", !frm.doc.purchase_serial_results?.length);
		frm.set_df_property("purchase_accepted_qty", "read_only", serial_unit);
		frm.set_df_property("purchase_rejected_qty", "read_only", serial_unit);
	},

	item_code: function (frm) {
		if (frm.doc.item_code && !frm.doc.quality_inspection_template) {
			return frm.call({
				method: "get_quality_inspection_template",
				doc: frm.doc,
				callback: function () {
					show_reading_criteria(frm);
					refresh_field(["quality_inspection_template", "readings"]);
				},
			});
		}
	},

	quality_inspection_template: function (frm) {
		if (frm.doc.quality_inspection_template) {
			return frm.call({
				method: "get_item_specification_details",
				doc: frm.doc,
				callback: function () {
					show_reading_criteria(frm);
					refresh_field("readings");
				},
			});
		}
	},
});

function show_reading_criteria(frm) {
	for (const row of frm.doc.readings || []) {
		if (row.formula_based_criteria) {
			row.acceptance_display = row.acceptance_formula || __("Formula not set");
		} else if (row.numeric) {
			row.acceptance_display = row.min_value || row.max_value
				? `${row.min_value ?? 0} – ${row.max_value ?? 0}` : __("Manual assessment");
		} else {
			row.acceptance_display = row.value || __("Manual assessment");
		}
	}
	frm.refresh_field("readings");
}

function has_automatic_criteria(row) {
	if (Number(row.formula_based_criteria)) return Boolean((row.acceptance_formula || "").trim());
	if (Number(row.numeric)) return Boolean(Number(row.min_value) || Number(row.max_value));
	return Boolean((row.value || "").trim());
}

function can_fill_expected_text(row) {
	return !Number(row.numeric) && !Number(row.formula_based_criteria) &&
		Boolean(String(row.value || "").trim()) && !String(row.reading_value || "").trim();
}

function apply_manual_to_readings(frm, manual) {
	let changed = false;
	for (const row of frm.doc.readings || []) {
		const value = manual || (frm.doc.purchase_unit_qty && !has_automatic_criteria(row)) ? 1 : 0;
		if (Number(row.manual_inspection) !== value) {
			row.manual_inspection = value;
			changed = true;
		}
	}
	if (changed) {
		frm.dirty();
		frm.refresh_field("readings");
	}
}

frappe.ui.form.on("Quality Inspection Reading", {
	numeric: show_reading_criteria,
	value: show_reading_criteria,
	min_value: show_reading_criteria,
	max_value: show_reading_criteria,
	formula_based_criteria: show_reading_criteria,
	acceptance_formula: show_reading_criteria,
	async manual_inspection(frm) {
		if (frm.doc.manual_inspection && (frm.doc.readings || []).some((row) => !Number(row.manual_inspection))) {
			frm.__manual_row_sync = true;
			try { await frm.set_value("manual_inspection", 0); }
			finally { frm.__manual_row_sync = false; }
		}
	},
});
