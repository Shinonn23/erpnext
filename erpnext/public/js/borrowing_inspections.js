// Create opens one unsaved document; the responsible user reviews and saves it.
async function loan_follow_up(frm, target) {
	const api = "erpnext.borrowing.loan_inspection.";
	const loan_note = frm.doctype === "Delivery Note";
	const { message: choices } = await frappe.call({
		method: api + (loan_note ? "loan_note_inspection_choices" : "loan_follow_up_choices"),
		args: { doctype: frm.doctype, name: frm.doc.name, target },
	});
	if (!choices?.length) {
		frappe.msgprint(__("No eligible rows remain for {0}.", [__(target)]));
		return;
	}
	const dialog = new frappe.ui.Dialog({
		title: __(loan_note ? "Select goods to inspect" : "Select asset"),
		fields: [{
			fieldname: "row_name",
			fieldtype: "Select",
			label: __(loan_note ? "Delivery Note row" : "Asset row"),
			reqd: 1,
			default: choices.length === 1 ? choices[0].name : undefined,
			options: choices.map((row) => ({
				value: row.name,
				label: `${__("Row")} ${row.idx}: ${row.item_code} (${row.asset})`,
			})),
		}],
		primary_action_label: __("Open {0}", [__(target)]),
		async primary_action({ row_name }) {
			const { message } = await frappe.call({
				method: api + (loan_note ? "make_loan_note_inspection" : target === "Asset Repair"
					? "make_return_repairs" : "make_loan_inspections"),
				args: { doctype: frm.doctype, name: frm.doc.name, row_name },
				freeze: true,
			});
			if (message) {
				await frappe.model.with_doctype(message.doctype);
				const [doc] = frappe.model.sync(message);
				frappe.set_route("Form", doc.doctype, doc.name);
				dialog.hide();
			}
		},
	});
	dialog.show();
}

function add_inspect_assets(frm) {
	if (
		frm.is_new() ||
		!(frm.doc.docstatus === 0 ||
			(frm.doc.docstatus === 1 && frm.doc.status === "Pending Delivery")) ||
		frm.doc.loan_type !== "Asset" ||
		!frappe.model.can_create("Quality Inspection")
	) return;
	frm.add_custom_button(
		__("Quality Inspection"), () => loan_follow_up(frm, "Quality Inspection"), __("Create")
	);
}

// The return Asset Movement of a borrowing (made from its issue movement, UX WP4): its assets are inspected once it
// is submitted, and a rejected one is repaired.
function is_return_movement(doc) {
	return !!doc.borrowing && doc.borrowing_source_doctype === "Borrowing" && doc.purpose === "Receipt";
}

function add_return_actions(frm) {
	if (frm.is_new() || frm.doc.docstatus !== 1 || !is_return_movement(frm.doc)) return;
	if (frappe.model.can_create("Quality Inspection")) {
		frm.add_custom_button(
			__("Quality Inspection"), () => loan_follow_up(frm, "Quality Inspection"), __("Create")
		);
	}
	if (frappe.model.can_create("Asset Repair")) {
		frm.add_custom_button(
			__("Asset Repair"), () => loan_follow_up(frm, "Asset Repair"), __("Create")
		);
	}
}

frappe.ui.form.on("Borrowing", { refresh: add_inspect_assets });
frappe.ui.form.on("Asset Movement", { refresh: add_return_actions });

frappe.ui.form.on("Delivery Note", {
	refresh(frm) {
		if (!frm.doc.is_borrowing_transaction) return;
		frm.remove_custom_button(__("Quality Inspection(s)"), __("Create"));
		frm.remove_custom_button(__("Quality Inspection"), __("Create"));
		const stage = frm.doc.is_return ? 1 : 0;
		if (frm.is_new() || frm.doc.docstatus !== stage ||
			!frappe.model.can_create("Quality Inspection")) return;
		frm.add_custom_button(
			__("Quality Inspection"), () => loan_follow_up(frm, "Quality Inspection"), __("Create")
		);
	},
});
