// Desk page for the employee's borrowed stock and customer demo consumables.
frappe.pages["my-demo-consumption"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("My Demo Consumables"),
		single_column: true,
	});
	const body = $("<div>").appendTo(page.body);
	const format_quantity = (item) => `${item.item_code}: ${item.qty} ${item.uom || ""}`;
	const load = async () => {
		const { message } = await frappe.call({
			method: "erpnext.borrowing.demo_consumption.my_outstanding",
			freeze: true,
		});
		body.empty();
		if (!message.totals.length) {
			body.text(__("You have no outstanding borrowed stock."));
			return;
		}
		$("<h4>").text(__("My Outstanding Borrowed Stock")).appendTo(body);
		const summary = $("<ul>").appendTo(body);
		for (const item of message.totals) {
			$("<li>").text(format_quantity(item)).appendTo(summary);
		}
		$("<h4>").text(__("Consumables Available for Customer Demo")).appendTo(body);
		if (!message.loans.length) {
			$("<p>").text(__("No borrowed consumables are available for demo consumption.")).appendTo(body);
		}
		for (const loan of message.loans) {
			const card = $('<div class="card mb-3"><div class="card-body"></div></div>')
				.appendTo(body)
				.find(".card-body");
			$("<h5>").text(loan.name).appendTo(card);
			const list = $("<ul>").appendTo(card);
			for (const item of loan.items) {
				const batch = item.batch_no ? ` — ${item.batch_no}` : "";
				$("<li>")
					.text(format_quantity(item) + batch)
					.appendTo(list);
			}
			$('<button class="btn btn-primary btn-sm">')
				.text(__("Use for Customer Demo"))
				.appendTo(card)
				.on("click", async () => {
					await frappe.require("/assets/erpnext/js/loan_demo_consumption.js");
					erpnext.borrowing.demo_consumption.open(loan.name, load);
				});
		}
	};
	page.set_primary_action(__("Refresh"), load, "refresh");
	load();
};
