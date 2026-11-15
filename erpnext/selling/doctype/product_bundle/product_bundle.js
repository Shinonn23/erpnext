// Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Product Bundle", {
	refresh: function (frm) {
		frm.toggle_enable("new_item_code", frm.is_new());
		frm.set_query("new_item_code", () => {
			return {
				query: "erpnext.selling.doctype.product_bundle.product_bundle.get_new_item_code",
			};
		});
		frm.set_query("item_code", "items", () => {
			return {
				query: "erpnext.controllers.queries.item_query",
			};
		});
	},
	new_item_code: function (frm) {
		if (!frm.doc.item_name && frm.doc.new_item_code) {
			frappe.db.get_value("Item", frm.doc.new_item_code, "item_name").then(({ message }) => {
				if (message?.item_name && !frm.doc.item_name) {
					frm.set_value("item_name", message.item_name);
				}
			});
		}
	},
});
