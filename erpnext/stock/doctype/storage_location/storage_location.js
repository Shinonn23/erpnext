frappe.ui.form.on("Storage Location", {
	setup(frm) {
		frm.set_query("warehouse", () => ({ filters: { is_group: 0, disabled: 0 } }));
	},
});
