// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Serial No", {
	setup(frm) {
		frm.add_fetch("customer", "customer_name", "customer_name");
		frm.add_fetch("supplier", "supplier_name", "supplier_name");
		frm.add_fetch("item_code", "item_name", "item_name");
		frm.add_fetch("item_code", "description", "description");
		frm.add_fetch("item_code", "item_group", "item_group");
		frm.add_fetch("item_code", "brand", "brand");

		frm.set_query("item_code", function () {
			return erpnext.queries.item({ is_stock_item: 1, has_serial_no: 1 });
		});

		frm.set_query("work_order", () => {
			return {
				filters: {
					docstatus: 1,
				},
			};
		});
	},

	refresh(frm) {
		frm.toggle_enable("item_code", frm.doc.__islocal);
		frm.trigger("view_ledgers");
		if (!frm.is_new()) {
			frm.add_custom_button(__("Inspection History"), async () => {
				const { message: rows } = await frappe.call({
					method: "erpnext.stock.doctype.quality_inspection.purchase_inspection_units.serial_inspection_history",
					args: { serial_no: frm.doc.name },
				});
				const escape = frappe.utils.escape_html;
				const lines = (rows || []).map((row) => {
					const name = escape(row.name);
					const reference = escape(`${row.reference_type || ""} ${row.reference_name || ""}`);
					const result = escape(__(row.serial_result || "Draft"));
					return `<a href="/app/quality-inspection/${encodeURIComponent(row.name)}">${name}</a> — ${result} — ${reference}`;
				});
				frappe.msgprint({ title: __("Inspection History"), message: lines.join("<br>") || __("No inspections found.") });
			});
		}
	},

	view_ledgers(frm) {
		frm.add_custom_button(__("View Ledgers"), () => {
			frappe.route_options = {
				item_code: frm.doc.item_code,
				serial_no: frm.doc.name,
				posting_date: frappe.datetime.now_date(),
				posting_time: frappe.datetime.now_time(),
			};
			frappe.set_route("query-report", "Serial No Ledger");
		});
	},
});
