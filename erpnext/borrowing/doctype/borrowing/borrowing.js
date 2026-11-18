frappe.ui.form.on("Borrowing", {
	setup(frm) {
		// before_cancel reverses these generated documents itself and the request stays (on_cancel updates its
		// status); without this the form asks the user to cancel them first ("Cancel All Documents") and stops
		// when the user may not cancel them.
		frm.ignore_doctypes_on_cancel_all = ["Delivery Note", "Pick List", "Asset Movement", "Shipment", "Material Request"];
		frm.set_query("material_request", () => ({
			filters: {
				docstatus: 1,
				material_request_type: ["in", ["Customer Demo", "Borrow", "Customer Borrow"]],
			},
		}));
		// the recipient's address and contact are the ones of the Recipient Customer when it is set
		frm.set_query("recipient_address", () => recipient_link_filters(frm));
		frm.set_query("recipient_contact", () => recipient_link_filters(frm));
		frm.set_query("source_warehouse", "items", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
		frm.set_query("asset", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				filters: {
					company: doc.company,
					item_code: row.item_code,
					docstatus: 1,
					status: ["not in", ["Draft", "Scrapped", "Sold", "Capitalized"]],
					demo_loan_status: "Available",
					custodian: ["is", "not set"],
				},
			};
		});
		frm.set_query("batch_no", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: "erpnext.controllers.queries.get_batch_no",
				filters: {
					item_code: row.item_code,
					warehouse: row.source_warehouse,
					include_expired_batches: 1,
				},
			};
		});
	},
	loan_type(frm) {
		configure_item_columns(frm);
	},
	refresh(frm) {
		frm.set_intro("");
		show_loan_summary(frm);
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.status === "Pending Pick") {
			frm.set_intro(
				__(
					"Use Create → Pick List, then save and submit the picked quantities. Use Create → Delivery Note to review, save and submit the outgoing goods."
				),
				"blue"
			);
		} else if (frm.doc.status === "Pending Delivery") {
			frm.set_intro(
				__("Use Create → Delivery Note or Asset Movement, review the actual goods and destination, then save and submit that document."),
				"blue"
			);
		} else if (["Active", "Partially Returned"].includes(frm.doc.status)) {
			frm.set_intro(
				__(
					"Select Create → Return: it opens the return document of the issue document (as a sales return is made from its Delivery Note). Review what came back, then Save and Submit. Record used-up stock separately with Create → Write Off."
				),
				"blue"
			);
		}
		if (frm.doc.pick_list && frappe.model.can_read("Pick List")) {
			frm.add_custom_button(__("Open Pick List"), () =>
				frappe.set_route("Form", "Pick List", frm.doc.pick_list), __("View")
			);
		}
		if (frm.doc.loan_type === "Stock" && !frm.doc.pick_list && frappe.model.can_create("Pick List")) {
			frm.add_custom_button(__("Pick List"), () => open_generated_doc(frm, "prepare_pick_list"), __("Create"));
		}
		if (frm.doc.status === "Pending Delivery" && frm.doc.loan_type === "Stock" &&
			!frm.doc.delivery_note && frappe.model.can_create("Delivery Note")) {
			frm.add_custom_button(__("Delivery Note"), () =>
				open_generated_doc(frm, "make_delivery_note"), __("Create")
			);
		}
		if (frm.doc.loan_type === "Asset" && !frm.doc.asset_movement && frappe.model.can_create("Asset Movement")) {
			frm.add_custom_button(__("Asset Movement"), () => open_generated_doc(frm, "prepare_asset_movement"), __("Create"));
		}
		if (frm.doc.asset_movement && frappe.model.can_read("Asset Movement")) {
			frm.add_custom_button(__("Asset Movement"), () => frappe.set_route("Form", "Asset Movement", frm.doc.asset_movement), __("View"));
		}

		if (frm.doc.delivery_note && frappe.model.can_read("Delivery Note")) {
			frm.add_custom_button(__("Open Borrowing Delivery Note"), () =>
				frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note), __("View")
			);
		}
		if (frm.doc.sales_invoice && frappe.model.can_read("Sales Invoice")) {
			frm.add_custom_button(__("Open Asset Sale Invoice"), () =>
				frappe.set_route("Form", "Sales Invoice", frm.doc.sales_invoice), __("Sale")
			);
			if (frm.has_perm("write") && (frappe.model.can_cancel("Sales Invoice") || frappe.model.can_delete("Sales Invoice"))) {
				const invoice_name = frm.doc.sales_invoice;
				frappe.db.get_value("Sales Invoice", invoice_name, "docstatus").then(({ message }) => {
					if (frm.doc.docstatus !== 1 || frm.doc.sales_invoice !== invoice_name) return;
					const can_cancel = message?.docstatus === 1 && frappe.model.can_cancel("Sales Invoice");
					const can_delete = message?.docstatus === 0 && frappe.model.can_delete("Sales Invoice");
					if (!can_cancel && !can_delete) return;
					frm.add_custom_button(__(can_cancel ? "Cancel Asset Sale Invoice" : "Delete Draft Asset Sale Invoice"), () =>
						frm.call("cancel_asset_sale_invoice").then(() => frm.reload_doc()), __("Sale")
					);
				});
			}
		}
		if (!["Active", "Partially Returned"].includes(frm.doc.status)) return;
		const return_doctype = frm.doc.loan_type === "Asset" ? "Asset Movement" : "Delivery Note";
		if (frappe.model.can_create(return_doctype)) {
			frm.add_custom_button(__("Return"), () => make_borrowing_return(frm), __("Create"));
		}
		if (frm.doc.loan_type === "Stock" && frappe.model.can_create("Borrowing Adjustment")) {
			frm.add_custom_button(
				__("Write Off"),
				() => open_generated_doc(frm, "make_adjustment"),
				__("Create"),
				"Borrowing Adjustment"
			);
		} else if (frm.doc.loan_type === "Asset" && frm.doc.borrower_type === "Customer" && frappe.model.can_create("Sales Invoice")) {
			frm.add_custom_button(
				__("Asset Sale Invoice"),
				() => open_generated_doc(frm, "make_asset_sale_invoice"),
				__("Create"),
				"Sales Invoice"
			);
		}
	},
});

// The Items grid shows the columns of the borrowing's type only (UX WP4): a depends_on blanks a cell but keeps its
// column, so the column itself is switched on the docfield (in_list_view and hidden) and the grid is drawn again.
const STOCK_COLUMNS = ["source_warehouse", "batch_no", "serial_no"];
const ASSET_COLUMNS = ["asset", "target_location"];
const LISTED = ["source_warehouse", "asset"];

function configure_item_columns(frm) {
	const grid = frm.fields_dict.items?.grid;
	if (!grid) return;
	const stock = frm.doc.loan_type !== "Asset";
	for (const [fields, shown] of [
		[STOCK_COLUMNS, stock],
		[ASSET_COLUMNS, !stock],
	]) {
		for (const fieldname of fields) {
			grid.update_docfield_property(fieldname, "hidden", shown ? 0 : 1);
			if (LISTED.includes(fieldname)) {
				grid.update_docfield_property(fieldname, "in_list_view", shown ? 1 : 0);
			}
		}
	}
	grid.visible_columns = null;
	grid.setup_visible_columns?.();
	grid.refresh();
}

// Who answers for the items, how many are still out, and the grid columns that fit the loan type.
function show_loan_summary(frm) {
	const stock = frm.doc.loan_type !== "Asset";
	// refresh runs more than once: start the headline and the figures over, never add them twice
	frm.dashboard.clear_headline();
	frm.dashboard.stats_area_row?.empty();
	configure_item_columns(frm);
	if (frm.doc.docstatus === 0 && !frm.is_new()) {
		frm.set_intro(__("Check the Borrowed Qty, selected Assets and source of each item, then submit to prepare this borrowing. Nothing is sent until the outgoing Delivery Note or Asset Movement is submitted."), "blue");
	}
	if (frm.doc.requested_by_name) {
		frm.dashboard.set_headline(
			__("Responsible Employee {0}: answers for these items until they come back.", [
				`<b>${frappe.utils.escape_html(frm.doc.requested_by_name)}</b>`,
			])
		);
	}
	if (frm.doc.docstatus !== 1) return;
	const total = (field) => (frm.doc.items || []).reduce((sum, row) => sum + flt(row[field]), 0);
	const outstanding = total("outstanding_qty");
	frm.dashboard.add_indicator(__("Issued: {0}", [total("issued_qty")]), "blue");
	frm.dashboard.add_indicator(__("Returned: {0}", [total("returned_qty")]), "green");
	if (total("adjusted_qty") || total("sold_qty")) {
		frm.dashboard.add_indicator(__("Written Off / Sold: {0}", [total("adjusted_qty") + total("sold_qty")]), "grey");
	}
	frm.dashboard.add_indicator(__("Outstanding: {0}", [outstanding]), outstanding > 0 ? "orange" : "green");
	if (outstanding > 0 && frm.doc.expected_return_date && frm.doc.expected_return_date < frappe.datetime.get_today()) {
		frm.dashboard.set_headline_alert(
			__("Overdue since {0}: {1} still with {2}.", [
				frappe.datetime.str_to_user(frm.doc.expected_return_date),
				outstanding,
				frappe.utils.escape_html(frm.doc.borrower || ""),
			]),
			"red"
		);
	}
}

// Create > Return (UX WP4, Siwat 18 Nov 2026: borrowing documents chain like selling): the return document of the
// issue document; with several issue documents the user picks one.
function make_borrowing_return(frm) {
	const asset = frm.doc.loan_type === "Asset";
	const doctype = asset ? "Asset Movement" : "Delivery Note";
	const filters = asset
		? { borrowing: frm.doc.name, purpose: ["!=", "Receipt"], docstatus: 1 }
		: { borrowing: frm.doc.name, is_return: 0, docstatus: 1 };
	frappe.db.get_list(doctype, { filters, fields: ["name"], limit: 20 }).then((rows) => {
		const open = (source_name) => open_generated_doc(frm, "make_return", { source_name });
		if (rows.length <= 1) return open(rows[0]?.name);
		const dialog = new frappe.ui.Dialog({
			title: __("Return against"),
			fields: [
				{
					fieldname: "source_name",
					fieldtype: "Select",
					label: __(doctype),
					options: rows.map((row) => row.name),
					default: rows[0].name,
					reqd: 1,
				},
			],
			primary_action_label: __("Create"),
			primary_action(values) {
				dialog.hide();
				open(values.source_name);
			},
		});
		dialog.show();
	});
}

function open_generated_doc(frm, method, args) {
	frm.call(method, args || {}).then((r) => {
		const doc = r.message;
		if (!doc?.doctype) return;
		frappe.model.sync(doc);
		if (!doc.name) return;
		frappe.set_route("Form", doc.doctype, doc.name);
	});
}

frappe.ui.form.on("Borrowing Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code || frm.doc.loan_type !== "Stock") return;
		frappe.db.get_value("Item", row.item_code, ["has_batch_no", "has_serial_no"]).then(({ message }) => {
			if (!message) return;
			frappe.model.set_value(cdt, cdn, {
				batch_no: message.has_batch_no ? row.batch_no : null,
				serial_no: message.has_serial_no ? row.serial_no : null,
			});
		});
	},
});

function recipient_link_filters(frm) {
	if (!frm.doc.recipient_customer) return {};
	return {
		filters: [
			["Dynamic Link", "link_doctype", "=", "Customer"],
			["Dynamic Link", "link_name", "=", frm.doc.recipient_customer],
		],
	};
}
