frappe.listview_settings["Material Request"] = {
	add_fields: [
		"material_request_type",
		"workflow_state",
		"status",
		"per_ordered",
		"per_received",
		"transfer_status",
		"request_progress_status",
		"asset_transfer_status",
	],
	get_indicator: function (doc) {
		var precision = frappe.defaults.get_default("float_precision");
		if (["Pending Approval", "Rejected"].includes(doc.workflow_state)) {
			const color = doc.workflow_state === "Rejected" ? "red" : "orange";
			return [__(doc.workflow_state), color, `workflow_state,=,${doc.workflow_state}`];
		}

		if (doc.material_request_type === "Asset Transfer" && doc.asset_transfer_status) {
			return get_request_status_indicator("asset_transfer_status", doc.asset_transfer_status);
		}

		if (["Customer Demo", "Customer Borrow / Loan"].includes(doc.material_request_type)) {
			if (doc.request_progress_status) {
				return get_request_status_indicator("request_progress_status", doc.request_progress_status);
			}
		}

		if (doc.status == "Stopped") {
			return [__("Stopped"), "red", "status,=,Stopped"];
		} else if (doc.transfer_status && doc.docstatus != 2) {
			if (doc.transfer_status == "Not Started") {
				return [__("Not Started"), "orange", "transfer_status,=,Not Started"];
			} else if (doc.transfer_status == "In Transit") {
				return [__("In Transit"), "yellow", "transfer_status,=,In Transit"];
			} else if (doc.transfer_status == "Completed") {
				if (doc.status == "Transferred") {
					return [__("Completed"), "green", "transfer_status,=,Completed"];
				} else {
					return [__("Partially Received"), "yellow", "per_ordered,<,100"];
				}
			}
		} else if (doc.docstatus == 1 && flt(doc.per_ordered, precision) == 0) {
			return [__("Pending"), "orange", "per_ordered,=,0|docstatus,=,1"];
		} else if (
			doc.docstatus == 1 &&
			flt(doc.per_ordered, precision) < 100 &&
			(doc.material_request_type == "Material Transfer" ||
				doc.material_request_type == "Customer Provided")
		) {
			return [__("Partially Received"), "yellow", "per_ordered,<,100"];
		} else if (doc.docstatus == 1 && flt(doc.per_ordered, precision) < 100) {
			return [__("Partially Ordered"), "yellow", "per_ordered,<,100"];
		} else if (doc.docstatus == 1 && flt(doc.per_ordered, precision) == 100) {
			if (
				doc.material_request_type == "Purchase" &&
				flt(doc.per_received, precision) < 100 &&
				flt(doc.per_received, precision) > 0
			) {
				return [__("Partially Received"), "yellow", "per_received,<,100"];
			} else if (doc.material_request_type == "Purchase" && flt(doc.per_received, precision) == 100) {
				return [__("Received"), "green", "per_received,=,100"];
			} else if (["Purchase", "Manufacture", "Subcontracting"].includes(doc.material_request_type)) {
				return [__("Ordered"), "green", "per_ordered,=,100"];
			} else if (doc.material_request_type == "Material Transfer") {
				return [__("Transferred"), "green", "per_ordered,=,100"];
			} else if (doc.material_request_type == "Material Issue") {
				return [__("Issued"), "green", "per_ordered,=,100"];
			} else if (doc.material_request_type == "Customer Provided") {
				return [__("Received"), "green", "per_ordered,=,100"];
			}
		}
	},
};

function get_request_status_indicator(fieldname, status) {
	const colors = {
		Draft: "gray",
		Pending: "orange",
		"In Transit": "yellow",
		"Partially Fulfilled": "yellow",
		Fulfilled: "green",
		"Partially Returned": "yellow",
		Returned: "green",
		"On Hold": "red",
		Closed: "gray",
		Cancelled: "gray",
	};
	return [__(status), colors[status] || "gray", `${fieldname},=,${status}`];
}
