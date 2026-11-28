// Native repair parts form behavior.
frappe.ui.form.on("Stock Entry", {
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.purpose !== "Material Issue") return;
		const { message } = await frappe.call({
			method: "erpnext.assets.doctype.asset_repair.parts.is_repair_issue",
			type: "GET",
			args: { issue_name: frm.doc.name },
		});
		if (!message) return;
		frm.add_custom_button(__("Return Repair Parts"), async () => {
			const { message } = await frappe.call({
				method: "erpnext.assets.doctype.asset_repair.parts.make_parts_return",
				args: { issue_name: frm.doc.name }, freeze: true,
			});
			frappe.model.sync(message);
			frappe.set_route("Form", message.doctype, message.name);
		}, __("Create"), "Stock Entry");
	},
});
