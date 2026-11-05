frappe.ui.form.on("Custody Loan", {
	setup(frm) {
		// before_cancel reverses these generated documents itself and the request stays (on_cancel updates its
		// status); without this the form asks the user to cancel them first ("Cancel All Documents") and stops
		// when the user may not cancel them.
		frm.ignore_doctypes_on_cancel_all = ["Delivery Note", "Pick List", "Asset Movement", "Shipment", "Material Request"];
		frm.set_query("material_request", () => ({
			filters: {
				docstatus: 1,
				material_request_type: ["in", ["Customer Demo", "Customer Borrow / Loan"]],
			},
		}));
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
	refresh(frm) {
		frm.set_intro("");
		show_loan_summary(frm);
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.status === "Pending Pick") {
			frm.set_intro(__("Pick the goods and submit the Pick List. The Loan Delivery Note is made by the system."), "blue");
		} else if (frm.doc.status === "Pending Shipment") {
			frm.set_intro(__("Open the Loan Delivery Note to complete shipment and confirm delivery."), "blue");
		}
		if (frm.doc.pick_list && frappe.model.can_read("Pick List")) {
			frm.add_custom_button(__("Open Pick List"), () =>
				frappe.set_route("Form", "Pick List", frm.doc.pick_list)
			);
		}
		if (frm.doc.loan_type === "Stock" && frm.doc.pick_list) {
			if (frm.doc.delivery_note && frappe.model.can_read("Delivery Note")) {
				frm.add_custom_button(__("Open Loan Delivery Note"), () =>
					frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note)
				);
			} else if (frappe.model.can_create("Delivery Note")) {
				const loan_name = frm.doc.name;
				const pick_list = frm.doc.pick_list;
				frappe.db.get_value("Pick List", pick_list, "docstatus").then(({ message }) => {
					if (message?.docstatus === 1 && frm.doc.docstatus === 1 && frm.doc.name === loan_name && frm.doc.pick_list === pick_list && !frm.doc.delivery_note) {
						frm.add_custom_button(
							__("Create Loan Delivery Note"),
							() => open_generated_doc(frm, "make_delivery_note"),
							__("Create"),
							"Delivery Note"
						);
					}
				});
			}
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
		if (frappe.model.can_create("Custody Loan Return")) {
			frm.add_custom_button(__("Return"), () => open_generated_doc(frm, "make_return"), __("Create"), "Custody Loan Return");
		}
		if (frm.doc.loan_type === "Stock" && frappe.model.can_create("Custody Loan Adjustment")) {
			frm.add_custom_button(
				__("Adjustment"),
				() => open_generated_doc(frm, "make_adjustment"),
				__("Create"),
				"Custody Loan Adjustment"
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

// Who answers for the items, how many are still out, and the grid columns that fit the loan type.
function show_loan_summary(frm) {
	const stock = frm.doc.loan_type !== "Asset";
	// refresh runs more than once: start the headline and the figures over, never add them twice
	frm.dashboard.clear_headline();
	frm.dashboard.stats_area_row?.empty();
	const grid = frm.fields_dict.items.grid;
	grid.toggle_display("source_warehouse", stock);
	grid.toggle_display("asset", !stock);
	if (frm.doc.docstatus === 0 && !frm.is_new()) {
		frm.set_intro(__("The system could not submit this loan by itself. Check the Loan Qty and Source Warehouse of each item, then submit."), "blue");
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

function open_generated_doc(frm, method) {
	frm.call(method).then((r) => {
		const doc = r.message;
		if (!doc?.doctype) return;
		frappe.model.sync(doc);
		if (!doc.name) return;
		frappe.set_route("Form", doc.doctype, doc.name);
	});
}

frappe.ui.form.on("Custody Loan Item", {
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
