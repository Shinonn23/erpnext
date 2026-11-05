// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item", {
	refresh(frm) {
		add_category_button(frm);
		if (frm.is_new()) return;
		if (!frm.doc.regulatory_product_category) {
			if (frm.doc.is_sales_item) {
				frm.dashboard.set_headline_alert(
					__("Regulatory Affairs has not classified this Item yet, so it cannot be sold."),
					"red"
				);
			}
			return;
		}
		if (frm.doc.regulatory_product_category === "Not Regulated") return;

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

// The Product Category is read-only on the form (permission level 1) and the Regulatory roles cannot edit the Item:
// one dialog classifies the Item (Product Category, Medical Device Category, Regulatory Owner) in one step.
function add_category_button(frm) {
	if (frm.is_new()) return;
	const is_approver = frappe.user.has_role("Regulatory Approver");
	if (!is_approver && !frappe.user.has_role("Regulatory Operator")) return;

	frm.add_custom_button(
		frm.doc.regulatory_product_category ? __("Change Classification") : __("Classify Item"),
		() => {
			const categories = (frm.fields_dict.regulatory_product_category.df.options || "")
				.split("\n")
				.filter((category) => is_approver || category);
			const dialog = new frappe.ui.Dialog({
				title: __("Classify Item"),
				fields: [
					{
						fieldname: "category",
						fieldtype: "Select",
						label: __("Product Category"),
						options: categories,
						default: frm.doc.regulatory_product_category,
						reqd: is_approver ? 0 : 1,
						description: is_approver
							? __("Leave it blank when the Item needs no FDA registration.")
							: "",
					},
					{
						fieldname: "other_category",
						fieldtype: "Data",
						label: __("Other Category"),
						default: frm.doc.regulatory_other_category,
						depends_on: "eval:doc.category == 'Other'",
						mandatory_depends_on: "eval:doc.category == 'Other'",
					},
					{
						fieldname: "medical_device_category",
						fieldtype: "Select",
						label: __("Medical Device Category"),
						options: ["", "IVD", "Non-IVD", "Other"],
						default: frm.doc.medical_device_category,
						depends_on: "eval:doc.category == 'Medical Device'",
						mandatory_depends_on: "eval:doc.category == 'Medical Device'",
					},
					{
						fieldname: "regulatory_owner",
						fieldtype: "Link",
						options: "User",
						label: __("Regulatory Owner"),
						default: frm.doc.regulatory_owner || frappe.session.user,
					},
				],
				primary_action_label: __("Save Classification"),
				primary_action: (values) => {
					frappe
						.xcall("erpnext.regulatory_affairs.item.classify_item", {
							item_code: frm.doc.name,
							category: values.category,
							medical_device_category: values.medical_device_category,
							regulatory_owner: values.regulatory_owner,
							other_category: values.other_category,
						})
						.then(() => {
							dialog.hide();
							frm.reload_doc();
						});
				},
			});
			dialog.show();
		},
		__("Regulatory")
	);
}
