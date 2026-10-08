frappe.ui.form.on("Custody Loan", {
	setup(frm) {
		frm.set_query("material_request", () => ({
			filters: {
				docstatus: 1,
				material_request_type: "Customer Borrow / Loan",
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
		if (frm.doc.docstatus !== 1 || !["Active", "Partially Returned"].includes(frm.doc.status)) return;
		frm.add_custom_button(__("Return"), () => open_generated_doc(frm, "make_return"), __("Create"));
		if (frm.doc.loan_type === "Stock") {
			frm.add_custom_button(
				__("Adjustment"),
				() => open_generated_doc(frm, "make_adjustment"),
				__("Create")
			);
		}
	},
});

function open_generated_doc(frm, method) {
	frm.call(method).then((r) => {
		const doc = r.message;
		if (!doc?.doctype || !doc?.name) return;
		frappe.model.sync(doc);
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
