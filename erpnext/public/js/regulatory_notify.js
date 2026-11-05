// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

// A draft that holds an Item without an FDA registration in force is blocked (Siwat, 5 Nov 2026: there is no way
// around it). The form says which Items and offers one button that tells Regulatory Affairs.
frappe.provide("erpnext.regulatory");

erpnext.regulatory.notify_regulatory_affairs = function (args) {
	return frappe.xcall("erpnext.regulatory_affairs.transactions.notify_regulatory_affairs", args);
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
