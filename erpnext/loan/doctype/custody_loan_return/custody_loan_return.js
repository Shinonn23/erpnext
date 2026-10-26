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
		frm.set_query("batch_no", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: "erpnext.controllers.queries.get_batch_no",
				filters: { item_code: row.item_code, include_expired_batches: 1 },
			};
		});
	},
	refresh(frm) {
		frm.set_intro("");
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
				frappe.model.set_value(cdt, cdn, {
					item_code: message.item_code,
					batch_no: message.batch_no,
					serial_no: message.serial_no,
					asset: message.asset,
				});
			});
	},
});

function open_generated_doc(doc) {
	if (!doc?.doctype || !doc?.name) return;
	frappe.model.sync(doc);
	frappe.set_route("Form", doc.doctype, doc.name);
}
