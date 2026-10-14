// Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Warehouse Type", {
	refresh(frm) {
		const can_edit = ["Stock Manager", "Quality Manager", "System Manager"].some((role) => frappe.user.has_role(role));
		if (!can_edit) frm.disable_save();
		for (const field of [
			"handling_category",
			"policy_change_reason",
			"allow_demo",
			"allow_manufacture",
			"allow_disposal",
			"allow_receipt",
			"allow_issue",
			"allow_transfer",
			"allow_sales",
			"allow_loan",
			"allow_reconciliation",
			"freeze_stock",
			"outgoing_role",
		]) {
			frm.set_df_property(field, "read_only", !can_edit);
		}
	},
});
