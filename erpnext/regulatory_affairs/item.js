// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item", {
	refresh(frm) {
		add_category_button(frm);
		if (frm.is_new() || !frm.doc.regulatory_product_category) return;

		frm.add_custom_button(
			__("Coverage Matrix"),
			() =>
				frappe.set_route("query-report", "Regulatory Item Coverage Matrix", {
					item: frm.doc.name,
				}),
			__("Regulatory")
		);

		if (!frm.doc.current_regulatory_registration) {
			frm.dashboard.set_headline_alert(
				__("No active FDA registration covers this Item today. Transactions may be blocked."),
				"red"
			);
		} else if (frm.doc.current_registration_expiry) {
			const days = frappe.datetime.get_diff(
				frm.doc.current_registration_expiry,
				frappe.datetime.get_today()
			);
			if (days <= 90) {
				frm.dashboard.set_headline_alert(
					__("Registration {0} expires in {1} days.", [
						frm.doc.current_regulatory_registration,
						days,
					]),
					"orange"
				);
			}
		}
	},
});

// The Product Category is read-only on the form (permission level 1); the Regulatory roles change it here.
function add_category_button(frm) {
	if (frm.is_new()) return;
	const is_approver = frappe.user.has_role("Regulatory Approver");
	if (!is_approver && !frappe.user.has_role("Regulatory Operator")) return;

	frm.add_custom_button(
		__("Set Product Category"),
		() => {
			const categories = (frm.fields_dict.regulatory_product_category.df.options || "")
				.split("\n")
				.filter((category) => is_approver || category);
			frappe.prompt(
				{
					fieldname: "category",
					fieldtype: "Select",
					label: __("Product Category"),
					options: categories,
					default: frm.doc.regulatory_product_category,
					description: is_approver ? "" : __("Only a Regulatory Approver can clear it."),
				},
				({ category }) =>
					frappe
						.xcall("erpnext.regulatory_affairs.item.set_product_category", {
							item_code: frm.doc.name,
							category,
						})
						.then(() => frm.reload_doc()),
				__("Set Product Category")
			);
		},
		__("Regulatory")
	);
}
