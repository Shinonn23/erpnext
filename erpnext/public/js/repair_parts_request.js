// Native repair parts actions.
frappe.ui.form.on("Asset Repair", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.return_movement && frm.doc.docstatus === 1 && frm.doc.repair_status === "Completed") {
			frm.add_custom_button(__("Reinspect"), async () => {
				const { message } = await frappe.call({ method: "erpnext.assets.doctype.asset_repair.reinspection.make_inspection",
					args: { repair_name: frm.doc.name } });
				frappe.model.sync(message);
				frappe.set_route("Form", message.doctype, message.name);
			}, __("Create"), "Quality Inspection");
		}
		frm.add_custom_button(__("Parts Ledger"), () => frappe.set_route("query-report", "Repair Parts Ledger", {
			repair_type: frm.doctype, repair: frm.doc.name,
		}));
		if (frm.doc.docstatus === 2 || frm.doc.repair_status !== "Pending") return;
		frm.add_custom_button(__("Request Parts"), async () => {
			const { message } = await frappe.call({
				method: "erpnext.assets.doctype.asset_repair.parts.make_parts_request",
				args: { repair_type: frm.doctype, repair_name: frm.doc.name },
			});
			frappe.model.sync(message);
			frappe.set_route("Form", message.doctype, message.name);
		});
	},
});
