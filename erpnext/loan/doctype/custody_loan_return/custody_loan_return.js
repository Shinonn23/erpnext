frappe.ui.form.on("Custody Loan Return", {
	setup(frm) {
		// before_cancel reverses the receipt, its inspections and shipment itself; without this the form asks the
		// user to cancel them first ("Cancel All Documents") and stops when the user may not cancel them.
		frm.ignore_doctypes_on_cancel_all = ["Delivery Note", "Asset Movement", "Quality Inspection", "Shipment"];
		frm.set_query("custody_loan", () => ({
			filters: { docstatus: 1, status: ["in", ["Active", "Partially Returned"]] },
		}));
		frm.set_query("custody_loan_item", "items", () => ({
			query: "erpnext.loan.doctype.custody_loan.custody_loan_core.custody_loan_item_query",
			filters: { parent: frm.doc.custody_loan },
		}));
		set_open_batch_query(frm);
		set_loan_item_query(frm);
	},
	onload(frm) {
		load_loan_rows(frm);
	},
	custody_loan(frm) {
		load_loan_rows(frm);
	},
	refresh(frm) {
		frm.set_intro("");
		show_draft_guidance(
			frm,
			__("Use Select Serial / Batch to tick the serial numbers and batches that came back, then submit.")
		);
		if (frm.doc.docstatus === 0 && frm.doc.loan_type === "Stock" && frm.doc.custody_loan) {
			frm.add_custom_button(__("Select Serial / Batch"), () => select_open_serial_batches(frm, "returned_qty"));
		}
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.loan_type === "Stock") {
			if (frm.doc.status === "Pending Quality Inspection") {
				frm.set_intro(__("Open Loan Receive, inspect each returned item, and submit an Accepted Quality Inspection for every row."), "blue");
			} else if (frm.doc.status === "Pending Receipt") {
				frm.set_intro(__("Open Loan Receive to complete any required shipment and submit the receiving Delivery Note."), "blue");
			}
		}
		if (frm.doc.delivery_note && frappe.model.can_read("Delivery Note")) {
			frm.add_custom_button(__("Loan Receive"), () =>
				frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note)
			);
		}
		if (frm.doc.asset_movement && frappe.model.can_read("Asset Movement")) {
			frm.add_custom_button(__("Open Asset Movement"), () =>
				frappe.set_route("Form", "Asset Movement", frm.doc.asset_movement)
			);
		}
		if (frm.doc.requires_shipment && frm.doc.delivery_note) {
			const return_name = frm.doc.name;
			const delivery_note = frm.doc.delivery_note;
			frappe.db.get_value("Delivery Note", delivery_note, "custody_loan_shipment").then(({ message }) => {
				if (frm.doc.docstatus !== 1 || frm.doc.name !== return_name || frm.doc.delivery_note !== delivery_note || !frm.doc.requires_shipment) return;
				if (message?.custody_loan_shipment) {
					if (frappe.model.can_read("Shipment")) {
						frm.add_custom_button(__("Open Loan Return Shipment"), () =>
							frappe.set_route("Form", "Shipment", message.custody_loan_shipment)
						);
					}
					return;
				}
				if (!frappe.model.can_create("Shipment")) return;
				frm.add_custom_button(__("Create Loan Return Shipment"), () => {
					frappe.call({
						method: "erpnext.stock.doctype.shipment.shipment.make_shipment_from_document",
						args: { source_doctype: "Delivery Note", source_name: frm.doc.delivery_note },
					}).then((r) => open_generated_doc(r.message));
				});
			});
		}
	},
});

frappe.ui.form.on("Custody Loan Return Item", {
	item_code(frm, cdt, cdn) {
		link_loan_row(frm, cdt, cdn);
	},
	custody_loan_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.custody_loan_item) return;
		frappe.db
			.get_value(
				"Custody Loan Item",
				row.custody_loan_item,
				["item_code", "batch_no", "serial_no", "asset"],
				null,
				"Custody Loan"
			)
			.then(({ message }) => {
				if (!message) return;
				frappe.model.set_value(cdt, cdn, { item_code: message.item_code, asset: message.asset });
				if (frm.doc.loan_type === "Stock") fill_open_serial_batch(frm, cdt, cdn, "returned_qty");
			});
	},
});

function open_generated_doc(doc) {
	if (!doc?.doctype || !doc?.name) return;
	frappe.model.sync(doc);
	frappe.set_route("Form", doc.doctype, doc.name);
}

// Serial / batch picker (custody_loan_tracking.get_open_serial_batches): lists what is still with the borrower and
// fills one row per loan item and batch, like the Serial / Batch selector of a stock transaction.
function select_open_serial_batches(frm, qty_field) {
	if (!frm.doc.custody_loan) {
		frappe.msgprint(__("Select the Custody Loan first"));
		return;
	}
	frappe
		.xcall("erpnext.loan.doctype.custody_loan.custody_loan_tracking.get_open_serial_batches", {
			custody_loan: frm.doc.custody_loan,
		})
		.then((entries) => {
			if (!entries?.length) {
				frappe.msgprint(__("No batch or serial number of this loan is still with the borrower"));
				return;
			}
			const dialog = new frappe.ui.Dialog({
				title: __("Select Serial / Batch"),
				size: "extra-large",
				fields: [
					{
						fieldname: "entries",
						fieldtype: "Table",
						cannot_add_rows: true,
						in_place_edit: true,
						data: entries.map((entry) => ({ ...entry, qty: entry.open_qty })),
						fields: [
							{ fieldname: "custody_loan_item", fieldtype: "Data", label: __("Loan Item"), read_only: 1, hidden: 1 },
							{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Item"), read_only: 1, in_list_view: 1 },
							{ fieldname: "batch_no", fieldtype: "Link", options: "Batch", label: __("Batch No"), read_only: 1, in_list_view: 1 },
							{ fieldname: "serial_no", fieldtype: "Link", options: "Serial No", label: __("Serial No"), read_only: 1, in_list_view: 1 },
							{ fieldname: "open_qty", fieldtype: "Float", label: __("Open Qty"), read_only: 1, in_list_view: 1 },
							{ fieldname: "qty", fieldtype: "Float", label: __("Qty"), in_list_view: 1 },
						],
					},
				],
				primary_action_label: __("Use Selected"),
				primary_action() {
					const selected = dialog.fields_dict.entries.grid.get_selected_children();
					if (!selected.length) {
						frappe.msgprint(__("Tick the serial numbers or batches to use"));
						return;
					}
					const groups = {};
					selected.forEach((entry) => {
						const key = `${entry.custody_loan_item}::${entry.batch_no || ""}`;
						const group = (groups[key] ||= {
							custody_loan_item: entry.custody_loan_item,
							item_code: entry.item_code,
							batch_no: entry.batch_no || null,
							serials: [],
							qty: 0,
						});
						if (entry.serial_no) {
							group.serials.push(entry.serial_no);
							group.qty += 1;
						} else {
							group.qty += Math.min(flt(entry.qty), flt(entry.open_qty));
						}
					});
					const chosen = new Set(selected.map((entry) => entry.custody_loan_item));
					// rows of the picked loan items are replaced; other rows stay
					frm.doc.items = (frm.doc.items || []).filter((row) => !chosen.has(row.custody_loan_item));
					Object.values(groups).forEach((group) => {
						if (group.qty <= 0) return;
						const row = frm.add_child("items", {
							custody_loan_item: group.custody_loan_item,
							item_code: group.item_code,
							batch_no: group.batch_no,
							serial_no: group.serials.join("\n") || null,
						});
						row[qty_field] = group.qty;
					});
					frm.refresh_field("items");
					frm.dirty();
					dialog.hide();
				},
			});
			dialog.show();
		});
}

// Draft guidance and the grid columns that fit the loan type.
function show_draft_guidance(frm, message) {
	const stock = frm.doc.loan_type !== "Asset";
	const grid = frm.fields_dict.items.grid;
	grid.toggle_display("batch_no", stock);
	grid.toggle_display("serial_no", stock);
	if (frm.fields_dict.items.grid.get_docfield("asset")) grid.toggle_display("asset", !stock);
	if (frm.doc.docstatus === 0 && stock && frm.doc.custody_loan) frm.set_intro(message, "blue");
}

function set_open_batch_query(frm) {
	frm.set_query("batch_no", "items", (doc, cdt, cdn) => {
		const row = locals[cdt][cdn];
		return {
			query: "erpnext.loan.doctype.custody_loan.custody_loan_tracking.custody_loan_batch_query",
			filters: { custody_loan: doc.custody_loan, custody_loan_item: row.custody_loan_item },
		};
	});
}

// A picked loan item starts with its first open batch and that batch's serial numbers.
function fill_open_serial_batch(frm, cdt, cdn, qty_field) {
	const row = locals[cdt][cdn];
	if (!row.custody_loan_item || !frm.doc.custody_loan) return;
	frappe
		.xcall("erpnext.loan.doctype.custody_loan.custody_loan_tracking.get_open_serial_batches", {
			custody_loan: frm.doc.custody_loan,
			custody_loan_item: row.custody_loan_item,
		})
		.then((entries) => {
			if (!entries?.length) return;
			const batch = entries[0].batch_no || null;
			const same = entries.filter((entry) => (entry.batch_no || null) === batch);
			const serials = same.filter((entry) => entry.serial_no).map((entry) => entry.serial_no);
			const qty = serials.length || same.reduce((total, entry) => total + flt(entry.open_qty), 0);
			frappe.model.set_value(cdt, cdn, {
				batch_no: batch,
				serial_no: serials.join("\n") || null,
				[qty_field]: qty,
			});
		});
}

// The user chooses the Item; the hidden loan row link follows (as ERPNext links a Delivery Note row to its Sales
// Order row). custody_loan_tracking.get_loan_item_rows lists the loan's rows.
function load_loan_rows(frm) {
	frm._loan_rows = [];
	if (!frm.doc.custody_loan) return Promise.resolve([]);
	return frappe
		.xcall("erpnext.loan.doctype.custody_loan.custody_loan_tracking.get_loan_item_rows", {
			custody_loan: frm.doc.custody_loan,
		})
		.then((rows) => (frm._loan_rows = rows || []));
}

function set_loan_item_query(frm) {
	frm.set_query("item_code", "items", () => ({
		filters: { name: ["in", [...new Set((frm._loan_rows || []).map((row) => row.item_code))]] },
	}));
}

function link_loan_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.item_code) return;
	const current = (frm._loan_rows || []).find((loan_row) => loan_row.name === row.custody_loan_item);
	if (current && current.item_code === row.item_code) return;
	const candidates = (frm._loan_rows || []).filter((loan_row) => loan_row.item_code === row.item_code);
	if (!candidates.length) {
		frappe.msgprint(__("Item {0} is not on Custody Loan {1}", [row.item_code, frm.doc.custody_loan]));
		frappe.model.set_value(cdt, cdn, "item_code", null);
		return;
	}
	const chosen = candidates.find((loan_row) => flt(loan_row.outstanding_qty) > 0) || candidates[0];
	frappe.model.set_value(cdt, cdn, "custody_loan_item", chosen.name);
}
