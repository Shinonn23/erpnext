frappe.listview_settings["Borrowing"] = {
	onload(listview) {
		listview.page.add_inner_button(__("My Demo Consumables"), () =>
			frappe.set_route("my-demo-consumption")
		);
	},
};
