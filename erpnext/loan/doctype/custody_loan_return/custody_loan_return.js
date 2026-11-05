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
	onload_post_render(frm) {
		ask_who_returns(frm);
	},
	custody_loan(frm) {
		load_loan_rows(frm);
		fit_form(frm);
	},
	refresh(frm) {
		frm.set_intro("");
		fit_form(frm);
		show_draft_guidance(
			frm,
			frm.doc.loan_type === "Asset"
				? __(
						"Check the asset and the Location it goes back to, then Save. If its Item is inspected the QI team is asked first and the return is submitted when they submit the inspection; otherwise Submit."
				  )
				: __(
						"Check what is coming back (the batches and serial numbers are filled in), then Save and Submit. The system receives the items, after the QI team inspects those that need it."
				  )
		);
		if (frm.doc.docstatus === 0 && frm.doc.loan_type === "Stock" && frm.doc.custody_loan) {
			frm.add_custom_button(__("Select Serial / Batch"), () => select_open_serial_batches(frm, "returned_qty"));
		}
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.loan_type === "Stock") {
			if (frm.doc.status === "Pending Shipment") {
				frm.set_intro(
					__(
						"The items are on their way back: create the Shipment and mark it Delivered when they arrive. The system then receives them, or asks the QI team to inspect them first."
					),
					"blue"
				);
			} else if (frm.doc.status === "Pending Quality Inspection") {
				frm.set_intro(
					__(
						"The QI team inspects the returned items. They are received when the last Quality Inspection is submitted: accepted ones go back to stock, rejected ones to Quarantine."
					),
					"blue"
				);
			} else if (frm.doc.status === "Pending Receipt") {
				frm.set_intro(
					__(
						"The system could not receive the items by itself (see the comments). Open the receiving Delivery Note, fix what it says and submit it."
					),
					"blue"
				);
			}
		}
		if (
			frm.doc.delivery_note &&
			frm.doc.status === "Pending Receipt" &&
			frappe.model.can_read("Delivery Note")
		) {
			frm.add_custom_button(__("Open Receiving Delivery Note"), () =>
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
					fit_form(frm);
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
	if (frm.doc.docstatus === 0 && frm.doc.custody_loan) frm.set_intro(message, "blue");
}

// A return started from the list has no loan yet: ask who is returning and list their open loans (the user
// never types a Custody Loan ID). Started from a loan (its Return button) the form opens filled in.
function ask_who_returns(frm) {
	if (!frm.is_new() || frm.doc.custody_loan || frm._asked_who) return;
	frm._asked_who = true;
	let loans = [];
	const load = () => {
		const values = dialog.get_values(true) || {};
		frappe
			.xcall("erpnext.loan.doctype.custody_loan.custody_loan_tracking.get_open_loans", {
				borrower_type: values.borrower_type,
				borrower: values.borrower,
			})
			.then((rows) => {
				loans = rows || [];
				dialog.set_df_property(
					"loan",
					"options",
					loans.map((loan) => ({
						value: loan.name,
						label: `${loan.name} - ${loan.borrower} - ${loan.items || ""}${
							loan.expected_return_date
								? ` (${__("due")} ${frappe.datetime.str_to_user(loan.expected_return_date)})`
								: ""
						}`,
					}))
				);
				dialog.set_value("loan", loans.length ? loans[0].name : "");
				dialog.set_df_property("loans_hint", "hidden", loans.length ? 1 : 0);
			});
	};
	const dialog = new frappe.ui.Dialog({
		title: __("Who is returning items?"),
		fields: [
			{
				fieldname: "borrower_type",
				fieldtype: "Select",
				label: __("Borrower Type"),
				options: ["Customer", "Employee", "Supplier", "Contact"],
				default: "Customer",
				onchange() {
					dialog.set_value("borrower", "");
					load();
				},
			},
			{
				fieldname: "borrower",
				fieldtype: "Dynamic Link",
				label: __("Borrower"),
				options: "borrower_type",
				onchange: load,
			},
			{ fieldname: "loan", fieldtype: "Select", label: __("Open Loan"), reqd: 1 },
			{
				fieldname: "loans_hint",
				fieldtype: "HTML",
				options: `<p class="text-muted">${__("No open loan found. Choose another borrower.")}</p>`,
				hidden: 1,
			},
		],
		primary_action_label: __("Return Items"),
		primary_action(values) {
			const loan = loans.find((row) => row.name === values.loan);
			if (!loan) return;
			dialog.hide();
			frappe
				.xcall("erpnext.loan.doctype.custody_loan.custody_loan_tracking.get_return_items", {
					custody_loan: loan.name,
				})
				.then((rows) => {
					frm.doc.loan_type = loan.loan_type;
					frm.set_value("custody_loan", loan.name);
					frm.clear_table("items");
					(rows || []).forEach((row) => {
						frm.add_child("items", {
							custody_loan_item: row.custody_loan_item,
							item_code: row.item_code,
							returned_qty: row.returned_qty,
							batch_no: row.batch_no,
							serial_no: row.serial_no,
							asset: row.asset,
							target_location: row.target_location,
						});
					});
					frm.refresh_fields();
					fit_form(frm);
				});
		},
	});
	dialog.show();
	load();
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

// What a return shows: rows only from the loan's outstanding items (no free Add row), batch / serial columns only
// where the rows carry them, the Asset column only on an Asset loan, no Status or Series on a new form, and whose
// items come back (the borrower and the Responsible Employee by name, from the loan).
function fit_form(frm) {
	const grid = frm.fields_dict.items.grid;
	const stock = frm.doc.loan_type !== "Asset";
	const tracked = (frm.doc.items || []).some((row) => row.batch_no || row.serial_no);
	grid.cannot_add_rows = true;
	grid.toggle_display("batch_no", stock && tracked);
	grid.toggle_display("serial_no", stock && tracked);
	grid.toggle_display("asset", !stock);
	grid.toggle_display("target_location", !stock);
	frm.toggle_display("status", !frm.is_new());
	frm.toggle_display("naming_series", false);
	show_whose_items(frm);
}

function show_whose_items(frm) {
	if (!frm.doc.custody_loan || !frm.doc.borrower_type || !frm.doc.borrower) return;
	const key = `${frm.doc.borrower_type}::${frm.doc.borrower}`;
	const show = (name) => {
		if (frm.doc.docstatus === undefined || frm._whose_key !== key) return;
		const parts = [__("Items of {0}", [`<b>${frappe.utils.escape_html(name)}</b>`])];
		if (frm.doc.requested_by_name) {
			parts.push(
				__("Responsible Employee: {0}", [
					`<b>${frappe.utils.escape_html(frm.doc.requested_by_name)}</b>`,
				])
			);
		}
		frm.dashboard.set_headline(parts.join(" &middot; "));
	};
	frm._whose_key = key;
	frappe
		.xcall("frappe.desk.search.get_link_title", {
			doctype: frm.doc.borrower_type,
			docname: frm.doc.borrower,
		})
		.then((title) => show(title || frm.doc.borrower))
		.catch(() => show(frm.doc.borrower));
}
