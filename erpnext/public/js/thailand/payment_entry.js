frappe.ui.form.on("Payment Entry", {
	refresh(frm) {
		// Filter company tax address
		frm.set_query("company_tax_address", function () {
			return {
				filters: {
					is_your_company_address: true,
				},
			};
		});
		// Add button to create withholding tax cert
		if (
			frm.doc.docstatus != 2 &&
			frm.doc.payment_type == "Pay" &&
			frm.doc.tax_withholding_entries.length > 0
		) {
			frm.trigger("add_create_withholding_tax_cert_button");
		}
		if (frm.doc.docstatus === 0 && frm.doc.apply_tds && frm.doc.references?.length) {
			frm.add_custom_button(
				__("Load Categories From Invoice Items"),
				() => frm.trigger("load_tax_withholding_categories"),
				__("Tax Withholding")
			);
		}
		// Create Clear Undue VAT Journal Entry
		if (frm.doc.docstatus == 1) {
			frm.trigger("add_create_undue_vat_journal_entry_button");
		}
	},

	add_create_withholding_tax_cert_button: function (frm) {
		frm.add_custom_button(__("Create Withholding Tax Cert"), async function () {
			let income_tax_form = "";
			if (frm.doc.party_type == "Supplier") {
				supplier_type = (
					await frappe.db.get_value(frm.doc.party_type, frm.doc.party, "supplier_type")
				).message.supplier_type;
				if (supplier_type == "Individual") {
					income_tax_form = "PND3";
				} else {
					income_tax_form = "PND53";
				}
			}
			const fields = [
				{
					fieldtype: "Date",
					label: __("Date"),
					fieldname: "date",
					reqd: 1,
				},
				{
					fieldtype: "Select",
					label: __("Income Tax Form"),
					fieldname: "income_tax_form",
					options: "PND3\nPND53",
					default: income_tax_form,
				},
				{
					fieldtype: "Link",
					label: __("Company Address"),
					fieldname: "company_address",
					options: "Address",
					get_query: () => {
						return {
							filters: {
								is_your_company_address: 1,
							},
						};
					},
				},
			];
			frappe.prompt(
				fields,
				function (filters) {
					frm.events.make_withholding_tax_cert(frm, filters);
				},
				__("Withholding Tax Cert"),
				__("Create Withholding Tax Cert")
			);
		});
	},

	load_tax_withholding_categories: function (frm) {
		return frappe.call({
			method: "erpnext.regional.thailand.custom.payment_entry.get_payment_entry_tax_withholding_categories",
			args: { doc: frm.doc },
			callback: (response) => {
				frm.clear_table("tax_withholding_categories");
				for (const category of response.message || []) {
					frm.add_child("tax_withholding_categories", category);
				}
				frm.clear_table("tax_withholding_entries");
				frm.refresh_field("tax_withholding_categories");
				frm.refresh_field("tax_withholding_entries");
			},
		});
	},

	add_create_undue_vat_journal_entry_button: function (frm) {
		// Check first whether all tax has been cleared, to add button
		frappe.call({
			method: "erpnext.regional.thailand.custom.custom_api.to_clear_undue_tax",
			args: {
				dt: cur_frm.doc.doctype,
				dn: cur_frm.doc.name,
			},
			callback: function (r) {
				if (r.message == true) {
					// Add button
					frm.add_custom_button(__("Clear Undue Tax"), function () {
						frm.trigger("make_clear_vat_journal_entry");
					});
				}
			},
		});
	},

	make_withholding_tax_cert: function (frm, filters) {
		return frappe.call({
			method: "erpnext.regional.thailand.custom.payment_entry.make_withholding_tax_cert",
			args: {
				filters: filters,
				doc: frm.doc,
			},
			callback: function (r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			},
		});
	},

	make_clear_vat_journal_entry() {
		return frappe.call({
			method: "erpnext.regional.thailand.custom.custom_api.make_clear_vat_journal_entry",
			args: {
				dt: cur_frm.doc.doctype,
				dn: cur_frm.doc.name,
			},
			callback: function (r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			},
		});
	},

	// --------------- Thai Billing ---------------

	get_invoices_from_sales_billing: function (frm) {
		const fields = [
			{
				fieldtype: "Link",
				label: __("Sales Billing"),
				fieldname: "sales_billing",
				options: "Sales Billing",
				reqd: 0,
				get_query: function () {
					return {
						filters: {
							company: frm.doc.company,
							customer: frm.doc.party,
							docstatus: 1,
							total_outstanding_amount: [">", 0],
						},
					};
				},
			},
			{
				fieldtype: "Check",
				label: __("Allocate Payment Amount"),
				fieldname: "allocate_payment_amount",
				default: 1,
			},
		];

		frappe.prompt(
			fields,
			function (filters) {
				frm.set_value("sales_billing", filters["sales_billing"]);
				if (!filters["sales_billing"]) {
					return;
				}
				frm.events.get_outstanding_documents(frm, filters, true, false);
			},
			__("Filters"),
			__("Get Invoices From Billing")
		);
	},

	get_invoices_from_purchase_billing: function (frm) {
		const fields = [
			{
				fieldtype: "Link",
				label: __("Purchase Billing"),
				fieldname: "purchase_billing",
				options: "Purchase Billing",
				reqd: 0,
				get_query: function () {
					return {
						filters: {
							company: frm.doc.company,
							supplier: frm.doc.party,
							docstatus: 1,
							total_outstanding_amount: [">", 0],
						},
					};
				},
			},
			{
				fieldtype: "Check",
				label: __("Allocate Payment Amount"),
				fieldname: "allocate_payment_amount",
				default: 1,
			},
		];

		frappe.prompt(
			fields,
			function (filters) {
				frm.set_value("purchase_billing", filters["purchase_billing"]);
				if (!filters["purchase_billing"]) {
					return;
				}
				frm.events.get_outstanding_documents(frm, filters, true, false);
			},
			__("Filters"),
			__("Get Invoices From Billing")
		);
	},

	// --------------- END Thai Billing -----------

	is_petty_cash: function (frm) {
		frm.set_value("petty_cash_holder", "");
		frm.set_value("petty_cash_holder_name", "");
	},
});
