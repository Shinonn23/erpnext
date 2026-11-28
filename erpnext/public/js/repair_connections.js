// Native repair parts form behavior.
if (!frappe._repair_connection_handlers_registered) {
	frappe._repair_connection_handlers_registered = true;
	const repair_connection_targets = {
		"Asset Repair": ["Material Request", "Quality Inspection"],
		"Stock Entry": ["Stock Entry"],
	};

	for (const [doctype, targets] of Object.entries(repair_connection_targets)) {
		frappe.ui.form.on(doctype, {
			dashboard_update(frm) {
				for (const link of frm.dashboard_data?.count?.external_links_found || []) {
					if (link.count && link.count === link.open_count) {
						frm.dashboard.links_area.body
							.find(".document-link")
							.filter(function () {
								return $(this).attr("data-doctype") === link.doctype;
							})
							.find(".open-notification")
							.addClass("hidden");
					}
				}
				for (const target of targets) {
					frm.dashboard.links_area.body
						.find(".document-link")
						.filter(function () {
							return $(this).attr("data-doctype") === target;
						})
						.find(".btn-new")
						.addClass("hidden");
				}
			},
		});
	}
}
