// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Location", {
	setup: function (frm) {
		frm.set_query("parent_location", function () {
			return {
				filters: {
					is_group: 1,
				},
			};
		});
	},

	refresh(frm) {
		frm.toggle_display(["address_html", "contact_html"], !frm.doc.__islocal);

		if (frm.is_new()) {
			frappe.contacts.clear_address_and_contact(frm);

			const last_doc = frappe.contacts.get_last_doc(frm);
			if (
				frappe.dynamic_link &&
				frappe.dynamic_link.doc &&
				frappe.dynamic_link.doc.name == last_doc.docname
			) {
				frm.set_value("links", "");
				frm.add_child("links", {
					link_doctype: frappe.dynamic_link.doctype,
					link_name: frappe.dynamic_link.doc[frappe.dynamic_link.fieldname],
				});
			}
		} else {
			frappe.contacts.render_address_and_contact(frm);
		}

		frm.set_query("link_doctype", "links", function () {
			return {
				query: "frappe.contacts.address_and_contact.filter_dynamic_link_doctypes",
				filters: {
					fieldtype: "HTML",
					fieldname: "address_html",
				},
			};
		});
		frm.refresh_field("links");

		for (const link of frm.doc.links || []) {
			if (!link.link_doctype || !link.link_name) continue;

			frm.add_custom_button(
				__("{0}: {1}", [__(link.link_doctype), __(link.link_name)]),
				() => frappe.set_route("Form", link.link_doctype, link.link_name),
				__("Links")
			);
		}
	},

	onload_post_render(frm) {
		if (!frm.doc.location && frm.doc.latitude && frm.doc.longitude) {
			frm.fields_dict.location.map.setView([frm.doc.latitude, frm.doc.longitude], 13);
		} else {
			frm.doc.latitude = frm.fields_dict.location.map.getCenter()["lat"];
			frm.doc.longitude = frm.fields_dict.location.map.getCenter()["lng"];
		}
	},
});
