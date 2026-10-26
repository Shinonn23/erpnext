// Read-only "Branches" box on Customer and Supplier: addresses with their branch codes.
// The HTML is rendered (and escaped) on the server into __onload.branches_html.
erpnext.thailand = erpnext.thailand || {};

erpnext.thailand.render_party_branches = function (frm) {
	const field = frm.fields_dict.branch_html;
	if (!field) return;
	if (frm.is_new()) {
		field.$wrapper.empty();
		return;
	}
	const html = (frm.doc.__onload && frm.doc.__onload.branches_html) || "";
	field.$wrapper.html(html);
	if (frappe.model.can_create("Address")) {
		const $btn = $(`<button class="btn btn-xs btn-default" type="button"></button>`).text(
			__("Add Branch")
		);
		$btn.on("click", () => {
			frappe.dynamic_link = { doc: frm.doc, fieldname: "name", doctype: frm.doctype };
			// Address.js reads frappe.dynamic_link to pre-link the new Address to this party.
			frappe.new_doc("Address");
		});
		field.$wrapper.append($("<div class='mt-2'></div>").append($btn));
	}
};

for (const doctype of ["Customer", "Supplier"]) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			erpnext.thailand.render_party_branches(frm);
		},
	});
}
