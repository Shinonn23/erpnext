/* global connect_diagnostics */
// Pick List: one action picks (the Auto Pick button, server side: earliest expiry first, with the
// Storage Location, Batch and Serial Nos, Picked Qty filled). A row can still be changed by hand: the
// Storage Location and Batch No lists show only stock that exists. See stock/README.md.

frappe.provide("connect_diagnostics.pick_stock");

const PICK_STOCK_API = "erpnext.stock.pick_stock.";

Object.assign(connect_diagnostics.pick_stock, {
	has_unpicked_rows(frm) {
		return (frm.doc.locations || []).some((row) => !flt(row.picked_qty));
	},

	// A Delivery Pick List for a Sales Order cannot be saved without the Warehouse to pick from
	// while a row still waits for the auto pick (nothing picked on it), as the server,
	// refuse_save_without_warehouse. Pick Manually is not read: the fork keeps it on for every draft.
	require_warehouse(frm) {
		const for_sales_order =
			cint(frm.doc.docstatus) === 0 &&
			frm.doc.purpose === "Delivery" &&
			(frm.doc.locations || []).some((row) => row.sales_order) &&
			this.has_unpicked_rows(frm);
		frm.set_df_property("parent_warehouse", "reqd", for_sales_order ? 1 : 0);
	},
});

frappe.ui.form.on("Pick List", {
	setup(frm) {
		// Only the places (and batches) that hold the item now, with their balance.
		frm.set_query("storage_location", "locations", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: PICK_STOCK_API + "storage_location_query",
				filters: {
					item_code: row.item_code,
					warehouse: row.warehouse,
					batch_no: row.batch_no || "",
					pick_list: doc.__islocal ? "" : doc.name,
				},
			};
		});
		frm.set_query("batch_no", "locations", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: PICK_STOCK_API + "batch_query",
				filters: {
					item_code: row.item_code,
					warehouse: row.warehouse,
					storage_location: row.storage_location || "",
					pick_list: doc.__islocal ? "" : doc.name,
				},
			};
		});
	},

	refresh(frm) {
		connect_diagnostics.pick_stock.require_warehouse(frm);
	},

	purpose(frm) {
		connect_diagnostics.pick_stock.require_warehouse(frm);
	},

	// The stock is suggested once the Warehouse is chosen, not before.
	parent_warehouse(frm) {
		const me = connect_diagnostics.pick_stock;
		if (cint(frm.doc.docstatus) === 0 && frm.doc.parent_warehouse && me.has_unpicked_rows(frm)) {
			frm.events.set_item_locations(frm, false);
		}
	},
});

// The Storage Location comes by itself from what the row names (stock/pick_stock.locate_pick). The place it sets
// is where the row's batch and serial nos are, so the fork's location handler keeps them (frm.__auto_location).
// A row with no place when the form opens gets it on save (locate_draft_rows), not on refresh: an untouched form is
// not marked Not Saved.
function fill_storage_location(frm, cdt, cdn, { force = false } = {}) {
	const row = (locals[cdt] || {})[cdn];
	if (!row || !row.item_code || !row.warehouse || frm.doc.docstatus !== 0) return;
	if (row.storage_location && !force) return;
	frappe
		.xcall(PICK_STOCK_API + "locate_pick", {
			item_code: row.item_code,
			warehouse: row.warehouse,
			qty: flt(row.stock_qty) || flt(row.qty),
			batch_no: row.batch_no || "",
			serial_no: row.serial_no || "",
			pick_list: frm.is_new() ? "" : frm.doc.name,
		})
		.then((location) => {
			if (location && location !== row.storage_location) {
				frm.__auto_location = true;
				return Promise.resolve(
					frappe.model.set_value(cdt, cdn, "storage_location", location)
				).finally(() => {
					frm.__auto_location = false;
				});
			}
		});
}

frappe.ui.form.on("Pick List Item", {
	batch_no(frm, cdt, cdn) {
		if (locals[cdt][cdn].batch_no) fill_storage_location(frm, cdt, cdn);
	},

	serial_no(frm, cdt, cdn) {
		if (locals[cdt][cdn].serial_no) fill_storage_location(frm, cdt, cdn, { force: true });
	},

	item_code(frm, cdt, cdn) {
		fill_storage_location(frm, cdt, cdn);
	},

	// The last row waiting for the auto pick deleted: the Warehouse is no longer needed.
	locations_remove(frm) {
		connect_diagnostics.pick_stock.require_warehouse(frm);
	},

	// The place and batch belong to a warehouse; another warehouse starts them over. A warehouse set by a serial
	// scan (frm.__auto_location) is where the scanned serial is: nothing is cleared.
	warehouse(frm, cdt, cdn) {
		if (frm.__auto_location) return;
		frappe.model
			.set_value(cdt, cdn, {
				storage_location: null,
				batch_no: null,
				serial_no: null,
			})
			.then(() => fill_storage_location(frm, cdt, cdn));
	},
});
