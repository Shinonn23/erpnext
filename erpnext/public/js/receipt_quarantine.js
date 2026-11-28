// Received first, inspected after (Siwat, 9 Nov 2026; workflows/receipt_quarantine.py).
//
// A Purchase Receipt, a Purchase Invoice with stock, a sales return and a borrowing's return Delivery Note are submitted first. The rows of an Item
// that is inspected on receipt are received into a Quarantine warehouse and wait there for the QI team. This script says so:
//  - on a Draft, new or saved (UX WP6, 19 Nov 2026), each row shows what the save will store: the server's
//    preview_receipt_route runs the save's own routing on the unsaved row and the form takes its Warehouse and Storage
//    Location (a row of an inspected Item: the Quarantine of its warehouse, which "Receive for Warehouse" remembers),
//    and a short note under the items table says "Received into <quarantine> (for <warehouse>)";
//  - after the submit, a headline names the rows that wait for the Quality Inspection (Create > Quality Inspection for
//    the QI team), or says that the goods are all inspected and the Stock team moves them out with a Stock Entry
//    (Create > Stock Entry); purchases that need no inspection go straight to storage;
//  - a submitted return Asset Movement of a borrowing is inspected through borrowing_inspections.js.
const RECEIPT_API = "erpnext.stock.quarantine.receipt_quarantine.";
const QUARANTINE_API = "erpnext.stock.quarantine.quarantine_inspection.";
const LOCATION_API = "erpnext.stock.doctype.storage_location.storage_location.";
const PURCHASE_DOCTYPES = ["Purchase Receipt", "Purchase Invoice"];
const PUTAWAY_TYPES = ["Storage", "Sales Counter"];

function is_receipt(frm) {
	return !frm.is_new() && receives_goods(frm);
}

// a receipt of goods that moves stock, saved or not (a purchase return is not one)
function receives_goods(frm) {
	const doc = frm.doc;
	if (PURCHASE_DOCTYPES.includes(doc.doctype)) {
		return !doc.is_return && (doc.doctype === "Purchase Receipt" || doc.update_stock);
	}
	return doc.is_return && (doc.doctype === "Delivery Note" || doc.update_stock);
}

function can_release() {
	return (
		frappe.user.has_role("Storage Location Manager") || frappe.user.has_role("Stock Manager")
	);
}

// -- a draft: the routing the save does, shown before it ---------------------------------------------------------------

const ROUTE_FIELDS = [
	"warehouse",
	"storage_location",
	"putaway_warehouse",
	"putaway_storage_location",
	"rejected_warehouse",
	"rejected_storage_location",
];
const ROUTE_ROW_FIELDS = [
	"name",
	"idx",
	"item_code",
	"warehouse",
	"storage_location",
	"rejected_warehouse",
	"rejected_storage_location",
	"qty",
	"rejected_qty",
	"serial_and_batch_bundle",
	"putaway_warehouse",
	"putaway_storage_location",
	"purchase_order_item",
	"po_detail",
	"dn_detail",
	"sales_invoice_item",
	"si_detail",
	"borrowing_item",
];

function routes_on_save(frm) {
	// a stock Borrowing Return's note is routed by the fork itself; a user who only reads the draft (accounting on a
	// Purchase Receipt, for example) is never asked: the preview needs create or write and the form changes nothing
	return (
		frm.doc.docstatus === 0 &&
		receives_goods(frm) &&
		!frm.doc.is_borrowing_transaction &&
		(frm.is_new() || frm.has_perm("write")) &&
		!frm.read_only
	);
}

async function preview_route(frm, row) {
	if (!row || frm._cd_route_applying || !routes_on_save(frm)) return;
	frm._cd_route ||= {};
	if (!row.item_code || (!row.warehouse && !Number(row.rejected_qty))) {
		delete frm._cd_route[row.name];
		return show_route_note(frm);
	}
	const doc = frm.doc;
	frm._cd_receipt_requests ||= {};
	const request = (frm._cd_receipt_requests[row.name] = {});
	const { message } = await frappe.call({
		method: RECEIPT_API + "preview_receipt_route",
		args: {
			doctype: doc.doctype,
			row: Object.fromEntries(ROUTE_ROW_FIELDS.map((field) => [field, row[field]])),
			parent: {
				company: doc.company,
				is_return: doc.is_return,
				update_stock: doc.update_stock,
				is_borrowing_transaction: doc.is_borrowing_transaction,
				set_warehouse: doc.set_warehouse,
				rejected_warehouse: doc.rejected_warehouse,
			},
		},
	});
	const result = message || {};
	if (
		frm.doc !== doc ||
		!routes_on_save(frm) ||
		frm._cd_receipt_requests[row.name] !== request ||
		!locals[row.doctype]?.[row.name]
	)
		return;
	if (result.message) {
		// the save would refuse the row: the form keeps what was typed and says why
		frappe.show_alert({
			message: frappe.utils.escape_html(result.message),
			indicator: "orange",
		});
		return;
	}
	frm._cd_route_applying = true;
	try {
		for (const field of ROUTE_FIELDS) {
			if (!(field in result)) continue;
			if (!frappe.meta.has_field(row.doctype, field)) continue;
			if ((row[field] || "") !== (result[field] || "")) {
				await frappe.model.set_value(row.doctype, row.name, field, result[field] || "");
			}
		}
	} finally {
		frm._cd_route_applying = false;
	}
	frm._cd_route[row.name] = {
		quarantine: result.quarantine,
		home: result.putaway_warehouse,
		location: result.storage_location,
		inspection_zone: result.inspection_zone,
		damage_zone: result.damage_zone,
	};
	show_route_note(frm);
}

async function preview_all_routes(frm) {
	if (!routes_on_save(frm)) return show_route_note(frm);
	for (const row of frm.doc.items || []) {
		await preview_route(frm, row);
	}
	show_route_note(frm);
}

// "Received into <quarantine> (for <warehouse>)" under the items table: what the save stores, not a popup
function show_route_note(frm) {
	if (!frm.fields_dict || !frm.fields_dict.items) return;
	const routes = frm._cd_route || {};
	const rows = routes_on_save(frm) ? frm.doc.items || [] : [];
	const lines = rows.length
		? rows
				.filter((row) => routes[row.name]?.quarantine && row.warehouse)
				.map((row) => {
					const home = routes[row.name].home;
					const into = frappe.utils.escape_html(row.warehouse);
					if (routes[row.name].inspection_zone) {
						return __("Row {0} ({1}): received into {2}.", [
							row.idx,
							frappe.utils.escape_html(row.item_code),
							frappe.utils.escape_html(row.storage_location),
						]);
					}
					return home
						? __("Row {0} ({1}): received into {2} (for {3}).", [
								row.idx,
								frappe.utils.escape_html(row.item_code),
								into,
								frappe.utils.escape_html(home),
							])
						: __("Row {0} ({1}): received into {2}.", [
								row.idx,
								frappe.utils.escape_html(row.item_code),
								into,
							]);
				})
		: [];
	if (lines.length) lines.push(__("These rows wait there for the Quality Inspection."));
	if (PURCHASE_DOCTYPES.includes(frm.doc.doctype)) {
		for (const row of rows) {
			if (
				row.is_stock_item &&
				Number(row.qty) > 0 &&
				row.warehouse &&
				!row.storage_location
			) {
				lines.push(
					__("Row {0}: select a Target Storage Location before submitting.", [row.idx]),
				);
			}
			if (Number(row.rejected_qty) > 0 && !row.rejected_storage_location) {
				lines.push(
					__("Row {0}: select a Rejected Warehouse and Storage Location.", [row.idx]),
				);
			}
		}
	}
	const note = lines.join("<br>");
	if ((frm.fields_dict.items.df.description || "") !== note) {
		frm.set_df_property("items", "description", note);
	}
}

async function show_status(frm) {
	if (!is_receipt(frm) || frm.doc.docstatus !== 1) return;
	// one headline: the script is loaded for several DocTypes, never stack a second bar
	frm.dashboard.clear_headline();
	const { message } = await frappe.call({
		method: RECEIPT_API + "receipt_quarantine_status",
		args: { doctype: frm.doc.doctype, name: frm.doc.name },
	});
	const status = message || {};
	const where = (status.quarantine || []).map(frappe.utils.escape_html).join(", ");
	// The app's Create > Stock Entry (below) is the one way out of quarantine or STAGING; ERPNext's own "Make Stock
	// Entry" of the receipt is not offered next to it (Siwat, 17 Nov 2026: one button per target).
	const due = status.due ? ` ${__("Due")} ${frappe.datetime.str_to_user(status.due)}.` : "";
	if (status.stage === "waiting") {
		const count = status.waiting.length;
		frm.dashboard.set_headline(
			__("{0} row(s) wait for Quality Inspection in {1}.", [count, where]) + due,
			"orange",
			true,
		);
		if (frappe.user.has_role("Quality Inspection User") && !frm.doc.is_borrowing_transaction) {
			frm.add_custom_button(
				__("Quality Inspection"),
				() => open_inspections(frm),
				__("Create"),
				"Quality Inspection",
			);
		}
	} else if (status.stage === "inspected" || status.stage === "putaway") {
		const put_away = status.stage === "putaway";
		frm.dashboard.set_headline(
			put_away
				? can_release()
					? __(
							"These goods need no inspection - put them away with a Stock Entry (Create > Stock Entry).",
						)
					: __("These goods need no inspection - the Stock team puts them away.")
				: can_release()
					? __(
							"All rows inspected - move them out with a Stock Entry (Create > Stock Entry).",
						)
					: __("All rows inspected - the Stock team moves them out of quarantine."),
			"blue",
			true,
		);
		if (can_release()) {
			frm.add_custom_button(
				put_away ? __("Put Away") : __("Move out of Quarantine"),
				() => create_release_entry(frm),
				__("Create"),
				"Stock Entry",
			);
		}
	}
}

async function create_release_entry(frm) {
	const { message } = await frappe.call({
		method: QUARANTINE_API + "make_release_entry",
		args: { doctype: frm.doc.doctype, name: frm.doc.name },
		freeze: true,
	});
	if (message) {
		frappe.model.sync(message);
		frappe.set_route("Form", message.doctype, message.name);
	}
}

async function open_inspections(frm, child_row_reference) {
	const { message } = await frappe.call({
		method: QUARANTINE_API + "make_receipt_inspections",
		args: {
			doctype: frm.doc.doctype,
			name: frm.doc.name,
			...(child_row_reference ? { child_row_reference } : {}),
		},
		freeze: true,
	});
	if (message && message.doctype === "Quality Inspection") {
		// the server's unsaved inspection of one borrowing receipt row
		const [doc] = frappe.model.sync(message);
		frappe.set_route("Form", doc.doctype, doc.name);
		return;
	}
	const names = (message && message.inspections) || [];
	const skipped = (message && message.skipped) || [];
	if (skipped.length) {
		frappe.msgprint({
			title: __("Not your QI team"),
			message: skipped.map((line) => frappe.utils.escape_html(line)).join("<br>"),
			indicator: "orange",
		});
	}
	if (names.length === 1) {
		frappe.set_route("Form", "Quality Inspection", names[0]);
	} else if (names.length) {
		frappe.route_options = { name: ["in", names] };
		frappe.set_route("List", "Quality Inspection");
	}
}

function setup_purchase_location_queries(frm) {
	const row_for = (cdt, cdn) => locals[cdt]?.[cdn] || {};
	for (const field of [
		"storage_location",
		"putaway_storage_location",
		"rejected_storage_location",
	]) {
		frm.set_query(field, "items", (doc, cdt, cdn) => {
			const row = row_for(cdt, cdn);
			const route = frm._cd_route?.[row.name] || {};
			const rejected = field === "rejected_storage_location";
			const putaway = field === "putaway_storage_location";
			const warehouse = rejected
				? row.rejected_warehouse
				: putaway
					? row.putaway_warehouse
					: row.warehouse;
			const filters = { warehouse: warehouse || "", item_code: row.item_code || "" };
			if (doc.is_return) {
				filters.require_stock = 1;
				filters.batch_no = row.batch_no || "";
				filters.serial_no = (rejected ? row.rejected_serial_no : row.serial_no) || "";
				filters.serial_and_batch_bundle =
					(rejected
						? row.rejected_serial_and_batch_bundle
						: row.serial_and_batch_bundle) || "";
			} else if (rejected) {
				filters.location_types = [route.damage_zone ? "Damage" : "Holding"];
			} else if (!putaway && row.putaway_warehouse) {
				filters.location_types = [route.inspection_zone ? "Quality Inspection" : "Holding"];
				filters.location_name = route.location || row.storage_location || "__none__";
			} else {
				filters.location_types = PUTAWAY_TYPES;
			}
			return { query: LOCATION_API + "storage_location_query", filters };
		});
	}
	for (const field of ["warehouse", "rejected_warehouse", "set_warehouse"]) {
		const query = (doc, cdt, cdn) => {
			const row = row_for(cdt, cdn);
			const filters = { company: doc.company || "__none__", disabled: 0, is_group: 0 };
			const rejected = field === "rejected_warehouse";
			if (!receives_goods(frm) && !(doc.is_return && row.item_code)) return { filters };
			const damage_zone = rejected && frm._cd_route?.[row.name]?.damage_zone;
			if (rejected && !doc.is_return) {
				if (damage_zone) filters.name = row.putaway_warehouse || row.warehouse || "__none__";
				else filters.warehouse_type = "Write-Off";
			}
			return {
				query: LOCATION_API + "warehouse_query",
				filters: {
					filters,
					...(doc.is_return
						? { require_stock: 1 }
						: {
							location_types: rejected ? [damage_zone ? "Damage" : "Holding"] : PUTAWAY_TYPES,
						}),
					storage_condition_item: row.item_code || "",
				},
			};
		};
		if (field === "set_warehouse") frm.set_query(field, query);
		else frm.set_query(field, "items", query);
	}
	frm.set_query("rejected_warehouse", (doc) => {
		const warehouses = (doc.items || [])
			.filter((row) => frm._cd_route?.[row.name]?.damage_zone)
			.map((row) => row.putaway_warehouse || row.warehouse);
		const filters = {
			company: doc.company || "__none__",
			disabled: 0,
			is_group: 0,
		};
		if (!doc.is_return) {
			if (warehouses.length) filters.name = ["in", [...new Set(warehouses)]];
			else filters.warehouse_type = "Write-Off";
		}
		return { filters };
	});
}

// frappe.ui.form.on adds a handler every time it runs and this file is loaded for five DocTypes: register once.
if (!frappe._cd_receipt_quarantine) {
	frappe._cd_receipt_quarantine = true;
	for (const doctype of [
		"Purchase Receipt",
		"Purchase Invoice",
		"Delivery Note",
		"Sales Invoice",
	]) {
		frappe.ui.form.on(doctype, {
			onload(frm) {
				if (PURCHASE_DOCTYPES.includes(doctype)) setup_purchase_location_queries(frm);
			},
			refresh(frm) {
				if (frm.doc.docstatus === 0) return preview_all_routes(frm);
				show_route_note(frm);
				return show_status(frm);
			},
			is_return: (frm) => preview_all_routes(frm),
			update_stock: (frm) => preview_all_routes(frm),
			company: (frm) => preview_all_routes(frm),
			rejected_warehouse: (frm) => frappe.after_ajax(() => preview_all_routes(frm)),
			items_remove: (frm) => show_route_note(frm),
		});
		const child = `${doctype} Item`;
		const events = {};
		// ERPNext's item_code handler fills the row's details (its default warehouse among them) from a call that
		// fires no field event: the preview waits for it, or it races the details and shows the wrong warehouse
		events.item_code = (frm, cdt, cdn) =>
			frappe.after_ajax(() => preview_route(frm, locals[cdt] && locals[cdt][cdn]));
		events.warehouse = (frm, cdt, cdn) => preview_route(frm, locals[cdt][cdn]);
		if (PURCHASE_DOCTYPES.includes(doctype)) {
			events.qty = events.rejected_qty = (frm, cdt, cdn) =>
				frappe.after_ajax(() => preview_route(frm, locals[cdt]?.[cdn]));
			events.storage_location = events.rejected_storage_location = (frm) => show_route_note(frm);
			events.rejected_warehouse = async (frm, cdt, cdn) => {
				if (!routes_on_save(frm) || frm._cd_route_applying) return;
				const row = locals[cdt][cdn];
				if (row.rejected_storage_location) {
					await frappe.model.set_value(cdt, cdn, "rejected_storage_location", "");
				}
				return preview_route(frm, row);
			};
		}
		frappe.ui.form.on(child, events);
	}
}
