// A Borrow loan unit placed with a customer becomes a Demo Asset (Siwat, 16 Nov 2026;
// stock/loan_demo_conversion.py). No tick and no attachment is asked for.
frappe.provide("frappe.loan_demo_conversion");

frappe.loan_demo_conversion.API = "erpnext.borrowing.loan_demo_conversion.";

// the serial numbers picked for a row, as the server takes them (one per unit)
frappe.loan_demo_conversion.selection = (values) =>
	(values.serial_nos || []).filter((serial) => serial && String(serial).trim());

frappe.loan_demo_conversion.open = async (name, on_done) => {
	const api = frappe.loan_demo_conversion.API;
	const { message: rows } = await frappe.call({ method: api + "options", args: { name } });
	if (!rows?.length) return frappe.msgprint(__("No unit of this borrowing can be converted now."));
	const by_row = Object.fromEntries(rows.map((row) => [row.loan_item, row]));
	const label = (row) => `${row.item_code}: ${row.item_name || ""} (${row.available})`;
	const dialog = new frappe.ui.Dialog({
		title: __("Convert to Demo Asset"),
		fields: [
			{
				fieldname: "loan_item",
				label: __("Borrowing Item"),
				fieldtype: "Select",
				reqd: 1,
				options: rows.map((row) => ({ value: row.loan_item, label: label(row) })),
				default: rows[0].loan_item,
				onchange: () => {
					const row = by_row[dialog.get_value("loan_item")];
					const field = dialog.get_field("serial_nos");
					field.df.options = (row?.serial_nos || []).map((serial) => ({
						label: serial,
						value: serial,
					}));
					field.refresh();
				},
			},
			{
				fieldname: "serial_nos",
				label: __("Serial Numbers"),
				fieldtype: "MultiCheck",
				options: (rows[0].serial_nos || []).map((serial) => ({
					label: serial,
					value: serial,
				})),
			},
			{
				fieldname: "customer",
				label: __("Customer"),
				fieldtype: "Link",
				options: "Customer",
			},
			{ fieldname: "reason", label: __("Reason"), fieldtype: "Small Text" },
		],
		primary_action_label: __("Draft Conversion"),
		primary_action: async (values) => {
			const serials = frappe.loan_demo_conversion.selection(values);
			if (!serials.length)
				return frappe.msgprint(__("Select the Serial Numbers to convert."));
			const { message } = await frappe.call({
				method: api + "convert",
				args: {
					name,
					loan_item: values.loan_item,
					qty: serials.length,
					serial_nos: JSON.stringify(serials),
					customer: values.customer || null,
					reason: values.reason || null,
				},
				freeze: true,
			});
			dialog.hide();
			if (message.open) {
				frappe.set_route("Form", "Stock to Asset Conversion", message.conversion);
			} else {
				frappe.msgprint(
					__(
						"Conversion {0} is drafted. The Stock team is asked to request its review.",
						[frappe.utils.escape_html(message.conversion)]
					)
				);
			}
			on_done?.();
		},
	});
	dialog.show();
};

frappe.ui.form.on("Borrowing", {
	async refresh(frm) {
		const doc = frm.doc;
		if (doc.docstatus !== 1 || doc.loan_type !== "Stock" || doc.borrower_type !== "Employee")
			return;
		const api = frappe.loan_demo_conversion.API;
		const { message: allowed } = await frappe.call({
			method: api + "can_convert",
			args: { name: doc.name },
		});
		if (!allowed) return;
		frm.add_custom_button(
			__("Convert to Demo Asset"),
			() => frappe.loan_demo_conversion.open(doc.name, () => frm.reload_doc()),
			__("Create"),
			// The borrower herself may press; the server decides (`can_convert`), not her Create right.
			false
		);
	},
});

frappe.ui.form.on("Stock to Asset Conversion", {
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.borrowing) return;
		const api = frappe.loan_demo_conversion.API;
		const { message: allowed } = await frappe.call({
			method: api + "can_make_asset_loan",
			args: { name: frm.doc.name },
		});
		if (!allowed) return;
		frm.add_custom_button(
			__("Asset Borrowing"),
			async () => {
				const { message: loan } = await frappe.call({
					method: api + "make_asset_loan_doc",
					args: { source_name: frm.doc.name },
				});
				frappe.model.sync(loan);
				frappe.set_route("Form", loan.doctype, loan.name);
			},
			__("Create"),
			"Borrowing"
		);
	},
});
