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
		// Withhold Tax: receipts from a customer and payments to a supplier (tax_popups.js)
		erpnext.regional.thailand.tax_popups.add_withhold_tax_buttons(frm);
		// Submit makes the Withholding Tax Cert (Connections); the button is the fallback for an entry without one
		if (
			frm.doc.docstatus == 1 &&
			frm.doc.payment_type == "Pay" &&
			(frm.doc.tax_withholding_entries || []).length > 0
		) {
			frm.trigger("add_create_withholding_tax_cert_button");
		}
		// Create Clear Undue VAT Journal Entry. Only for users who can create a Journal Entry: for an approver without
		// that right the check was refused ("Not permitted"), and closing the message left the submitted payment.
		if (frm.doc.docstatus == 1 && frappe.model.can_create("Journal Entry")) {
			frm.trigger("add_create_undue_vat_journal_entry_button");
		}
		// Payment Receipt (ใบเสร็จรับเงิน) of a submitted receipt from a customer
		if (
			frm.doc.docstatus == 1 &&
			frm.doc.payment_type == "Receive" &&
			frm.doc.party_type == "Customer" &&
			frappe.model.can_create("Payment Receipt")
		) {
			frm.add_custom_button(
				__("Payment Receipt"),
				() => {
					frappe.model.open_mapped_doc({
						method: "erpnext.regional.doctype.payment_receipt.payment_receipt.make_payment_receipt",
						frm: frm,
					});
				},
				__("Create")
			);
		}
	},

	add_create_withholding_tax_cert_button: function (frm) {
		erpnext.regional.thailand.tax_popups.add_withholding_tax_cert_fallback(
			frm,
			frm.doc.party_type == "Supplier" ? frm.doc.party : null
		);
	},

	apply_tds: function (frm) {
		// Withhold Tax → Based On Paying Document's Line Items ticks it to load the items' categories instead
		if (!frm.doc.apply_tds || frm.__thai_skip_withholding_popup) return;
		frm.trigger("thai_ask_withholding");
	},

	thai_ask_withholding: function (frm) {
		// Ask what is withheld; the base is the paid invoices before VAT (the core handler has cleared the table)
		if (frm.doc.payment_type != "Pay" || frm.doc.party_type != "Supplier") return;
		erpnext.regional.thailand.tax_popups.ask_withholding_category(frm, {
			date: frm.doc.posting_date,
			current: frm.doc.tax_withholding_category,
			// closing the popup unticks the box, unless a type was chosen before (Withhold Tax opened it again)
			on_cancel: () =>
				!(frm.doc.tax_withholding_categories || []).length && frm.set_value("apply_tds", 0),
			on_pick: async (choice) => {
				const { message: base } = await frappe.call({
					method: "erpnext.regional.thailand.custom.payment_entry.get_payment_withholding_base",
					args: { doc: frm.doc },
				});
				frm.set_value("tax_withholding_category", choice.name);
				frm.clear_table("tax_withholding_categories");
				frm.add_child("tax_withholding_categories", {
					tax_withholding_category: choice.name,
					taxable_amount: base,
				});
				frm.clear_table("tax_withholding_entries");
				frm.refresh_field("tax_withholding_categories");
			},
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
					}, undefined, "Journal Entry");
				}
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
