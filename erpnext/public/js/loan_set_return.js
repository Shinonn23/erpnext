// Return documents are opened unsaved; posting requires the user's Save and Submit. A borrowing comes back on the
// return document of its issue document (UX WP4, Siwat 18 Nov 2026): a return Delivery Note (Stock) or a return
// Asset Movement (Asset); there is no Borrowing Return.
const SET_API = "erpnext.borrowing.doctype.borrowing.borrowing_set.";

function open_loan_return(loan) {
	return frappe.xcall(SET_API + "make_return", { borrowing: loan }).then((doc) => {
		frappe.model.sync(doc);
		frappe.set_route("Form", doc.doctype, doc.name);
	});
}

function choose_loan_return(loans) {
	if (loans.length === 1) return open_loan_return(loans[0].name);
	frappe.prompt(
		{
			fieldname: "loan",
			fieldtype: "Select",
			label: __("Borrowing"),
			options: loans.map((loan) => ({
				value: loan.name,
				label: `${loan.name} - ${__(loan.loan_type)}`,
			})),
			reqd: 1,
		},
		({ loan }) => open_loan_return(loan),
		__("Create Return"),
		__("Continue")
	);
}

// The loans of one request as one block: what is out, what came back and the combined status.
function loan_set_html(summary) {
	const esc = (text) => frappe.utils.escape_html(String(text ?? ""));
	const rows = summary.loans
		.map((loan) => {
			const link = frappe.utils.get_form_link("Borrowing", loan.name, true);
			const lines = loan.items
				.map((row) => {
					const what = esc(row.asset || row.item_code);
					const state = [
						__("out {0}", [row.outstanding]),
						__("back {0}", [row.returned]),
						row.used_up ? __("used up {0}", [row.used_up]) : "",
					]
						.filter(Boolean)
						.join(", ");
					return `<div>${what}: ${state}</div>`;
				})
				.join("");
			return `<tr><td>${link}</td><td>${esc(__(loan.loan_type))}</td><td>${esc(
				__(loan.status)
			)}</td><td>${lines}</td></tr>`;
		})
		.join("");
	return `<div class="loan-set"><p><b>${esc(
		__(summary.status)
	)}</b></p><table class="table table-bordered"><thead><tr><th>${__("Borrowing")}</th><th>${__(
		"Type"
	)}</th><th>${__("Status")}</th><th>${__(
		"What is out"
	)}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

frappe.ui.form.on("Material Request", {
	refresh(frm) {
		if (
			frm.doc.docstatus !== 1 ||
			!["Customer Demo", "Borrow", "Customer Borrow"].includes(frm.doc.material_request_type)
		)
			return;
		const name = frm.doc.name;
		frappe
			.xcall(SET_API + "request_loan_summary", { material_request: name })
			.then((summary) => {
				if (frm.doc.name !== name || !summary?.loans.length) return;
				frm.dashboard.add_section(loan_set_html(summary), __("Borrowing"));
			});
		const can_return_stock = frappe.model.can_create("Delivery Note");
		const can_return_assets = frappe.model.can_create("Asset Movement");
		if (!can_return_stock && !can_return_assets) return;
		return frappe
			.xcall(SET_API + "loan_set_overview", { material_request: name })
			.then((overview) => {
				if (frm.doc.name !== name || frm.doc.docstatus !== 1) return;
				const returnable_rows = [
					...(can_return_stock ? overview.stock : []),
					...(can_return_assets ? overview.assets : []),
				];
				const returnable = new Set(returnable_rows.map((row) => row.loan));
				const loans = overview.loans.filter((loan) => returnable.has(loan.name));
				if (loans.length) {
					const targets = [...new Set(loans.map(({ loan_type }) =>
						loan_type === "Asset" ? "Asset Movement" : "Delivery Note"
					))];
					frm.add_custom_button(__("Return"), () => choose_loan_return(loans), __("Create"), targets);
				}
			});
	},
});
