frappe.ui.form.on("Stock Freeze", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.status === "Active" && frm.perm[0]?.write) {
			frm.add_custom_button(__("Release"), () => {
				frappe.prompt({ fieldname: "reason", fieldtype: "Small Text", label: __("Release Reason"), reqd: 1 },
					({ reason }) => frm.call("release", { reason }).then(() => frm.reload_doc()), __("Release Stock Freeze"), __("Release"));
			});
		}
	},
	freeze_level(frm) {
		for (const [level, field] of Object.entries({ Item: "item_code", Batch: "batch_no", "Serial No": "serial_no", Asset: "asset" })) {
			if (level !== frm.doc.freeze_level) frm.set_value(field, null);
		}
	},
});
