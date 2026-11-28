frappe.provide("erpnext.borrowing.demo_consumption");

erpnext.borrowing.demo_consumption.open = async (name, on_done) => {
	const api = "erpnext.borrowing.demo_consumption.";
	const { message: parts } = await frappe.call({ method: api + "options", args: { name } });
	if (!parts?.length) return frappe.msgprint(__("No demo consumables remain on this borrowing."));

	const token = crypto.randomUUID();
	const dialog = new frappe.ui.Dialog({
		title: __("Confirm Customer Demo Consumption"),
		fields: [
			{
				fieldname: "demo_date",
				label: __("Demo Date"),
				fieldtype: "Date",
				default: frappe.datetime.get_today(),
				reqd: 1,
			},
			{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer", reqd: 1 },
			{
				fieldname: "items",
				label: __("Consumed Items"),
				fieldtype: "Table",
				cannot_add_rows: true,
				data: parts.flatMap((part) => {
					if (!part.serial_nos.length) {
						return [{ ...part, selected: 0, available: part.qty, qty: 0, serial_no: "" }];
					}
					const qty = 1 / part.serial_per_unit;
					return part.serial_nos.map((serial_no) => ({
						...part,
						selected: 0,
						available: qty,
						qty,
						serial_no,
					}));
				}),
				fields: [
					{ fieldname: "loan_item", fieldtype: "Data", hidden: 1 },
					{ fieldname: "selected", label: __("Select"), fieldtype: "Check", in_list_view: 1 },
					{
						fieldname: "item_code",
						label: __("Item"),
						fieldtype: "Data",
						read_only: 1,
						in_list_view: 1,
					},
					{
						fieldname: "batch_no",
						label: __("Batch"),
						fieldtype: "Data",
						read_only: 1,
						in_list_view: 1,
					},
					{
						fieldname: "available",
						label: __("Available"),
						fieldtype: "Float",
						read_only: 1,
						in_list_view: 1,
					},
					{ fieldname: "qty", label: __("Consumed Quantity"), fieldtype: "Float", in_list_view: 1 },
					{ fieldname: "uom", label: __("UOM"), fieldtype: "Data", read_only: 1 },
					{
						fieldname: "serial_no",
						label: __("Serial Number"),
						fieldtype: "Data",
						read_only: 1,
						in_list_view: 1,
					},
				],
			},
		],
		primary_action_label: __("Confirm and Issue"),
		primary_action: async (values) => {
			const grouped = new Map();
			for (const row of (values.items || []).filter((item) => item.selected && item.qty > 0)) {
				const batch_no = row.batch_no || null;
				const key = JSON.stringify([row.loan_item, batch_no]);
				const item = grouped.get(key) || {
					loan_item: row.loan_item,
					batch_no,
					qty: 0,
					serial_no: [],
				};
				item.qty += Number(row.qty);
				if (row.serial_no) item.serial_no.push(row.serial_no);
				grouped.set(key, item);
			}
			const selections = [...grouped.values()].map((item) => ({
				...item,
				serial_no: item.serial_no.join("\n"),
			}));
			const { message } = await frappe.call({
				method: api + "confirm",
				args: { name, customer: values.customer, demo_date: values.demo_date, selections, token },
				freeze: true,
			});
			dialog.hide();
			frappe.msgprint(
				__("Demo consumption recorded. Stock Entry: {0}", [
					frappe.utils.escape_html(message.stock_entry),
				])
			);
			on_done?.();
		},
	});
	dialog.show();
};
