// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

// A draft that holds an Item without an FDA registration in force is blocked (Siwat, 5 Nov 2026: there is no way
// around it). The form says which Items and offers one button that tells Regulatory Affairs.
frappe.provide("erpnext.regulatory");

erpnext.regulatory.notify_regulatory_affairs = function (args) {
	return frappe.xcall("erpnext.regulatory_affairs.transactions.notify_regulatory_affairs", args);
};

// Item list: select Items, then Actions > Classify as ... (Regulatory Operator / Approver). Filter by Item Group and
// "Product Category is not set" to see what is still unclassified.
erpnext.regulatory.add_bulk_classify = function (listview) {
	if (!frappe.user.has_role("Regulatory Operator") && !frappe.user.has_role("Regulatory Approver")) return;
	listview.page.add_actions_menu_item(__("Classify as ..."), () => {
		const names = listview.get_checked_items(true);
		if (!names.length) {
			frappe.msgprint(__("Select the Items first."));
			return;
		}
		frappe.prompt(
			[
				{
					fieldname: "category",
					fieldtype: "Select",
					label: __("Product Category"),
					options: [
						"Not Regulated",
						"Medical Device",
						"Food",
						"Drug",
						"Cosmetic",
						"Hazardous Substance",
						"Herbal Product",
					],
					reqd: 1,
				},
				{
					fieldname: "medical_device_category",
					fieldtype: "Select",
					label: __("Medical Device Category"),
					options: ["", "IVD", "Non-IVD", "Other"],
					depends_on: "eval:doc.category == 'Medical Device'",
					mandatory_depends_on: "eval:doc.category == 'Medical Device'",
				},
			],
			(values) =>
				frappe
					.xcall("erpnext.regulatory_affairs.item.classify_items", {
						item_codes: names,
						category: values.category,
						medical_device_category: values.medical_device_category,
					})
					.then((result) => {
						const failed = Object.keys(result.failed || {});
						frappe.msgprint(
							__("{0} Items classified.", [result.done.length]) +
								(failed.length ? "<br>" + __("Not classified: {0}", [failed.join(", ")]) : "")
						);
						listview.refresh();
					}),
			__("Classify {0} Items", [names.length]),
			__("Classify")
		);
	});
};

const REGULATED_DOCTYPES = [
	"Quotation",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
	"Purchase Order",
	"Purchase Receipt",
	"Stock Entry",
];

REGULATED_DOCTYPES.forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.is_new() || frm.doc.docstatus !== 0) return;
			if (!(frm.doc.items || []).some((row) => row.item_code)) return;
			frappe
				.xcall("erpnext.regulatory_affairs.transactions.get_blocked_items", {
					doctype: frm.doctype,
					name: frm.doc.name,
				})
				.then((items) => {
					if (!items || !items.length) return;
					const names = [...new Set(items.map((row) => row.item_code))].join(", ");
					frm.dashboard.set_headline_alert(
						__("Not registered with the FDA: {0}. Regulatory Affairs must register it first.", [
							names,
						]),
						"red"
					);
					frm.add_custom_button(
						__("Notify Regulatory Affairs"),
						() =>
							erpnext.regulatory.notify_regulatory_affairs({
								doctype: frm.doctype,
								name: frm.doc.name,
							}),
						__("Regulatory")
					);
				})
				.catch(() => {});
		},
	});
});
