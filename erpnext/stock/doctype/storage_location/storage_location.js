async function sync_location_type(frm) {
	if (!frm.doc.warehouse) return;
	const { message: allowed } = await frappe.call({
		method: "erpnext.stock.doctype.storage_location.storage_location.allowed_location_types",
		args: { warehouse: frm.doc.warehouse },
	});
	if (!Array.isArray(allowed) || !allowed.length) return;
	frm.set_df_property("location_type", "options", allowed.join("\n"));
	if (!allowed.includes(frm.doc.location_type)) {
		frm.set_value("location_type", allowed[0]);
	}
}

frappe.ui.form.on("Storage Location", {
	setup(frm) {
		frm.set_query("warehouse", () => ({ filters: { is_group: 0, disabled: 0 } }));
	},
	refresh(frm) {
		sync_location_type(frm);
	},
	warehouse(frm) {
		sync_location_type(frm);
	},
});
