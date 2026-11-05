import frappe

from erpnext.loan.report.requester import (
	REQUEST_JOIN,
	REQUESTER_SELECT,
	UNIT_VALUE,
	add_requester_filter,
	insert_columns_after,
	requester_columns,
	value_columns,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["loan.docstatus = 1"]
	values = {}
	for field in ("company", "loan_type", "borrower_type", "borrower", "status"):
		if filters.get(field):
			conditions.append(f"loan.{field} = %({field})s")
			values[field] = filters[field]
	if filters.get("from_date"):
		conditions.append("loan.expected_return_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("loan.expected_return_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	add_requester_filter(filters, conditions, values)

	columns = [
		{"label": "Custody Loan", "fieldname": "loan", "fieldtype": "Link", "options": "Custody Loan", "width": 165},
		{"label": "Material Request", "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 165},
		{"label": "Loan Type", "fieldname": "loan_type", "fieldtype": "Data", "width": 90},
		{"label": "Borrower Type", "fieldname": "borrower_type", "fieldtype": "Data", "width": 110},
		{"label": "Borrower", "fieldname": "borrower", "fieldtype": "Dynamic Link", "options": "borrower_type", "width": 170},
		{"label": "Expected Return", "fieldname": "expected_return_date", "fieldtype": "Date", "width": 115},
		{"label": "Items", "fieldname": "item_count", "fieldtype": "Int", "width": 65},
		{"label": "Issued Qty", "fieldname": "issued_qty", "fieldtype": "Float", "width": 90},
		{"label": "Returned Qty", "fieldname": "returned_qty", "fieldtype": "Float", "width": 95},
		{"label": "Written Off", "fieldname": "adjusted_qty", "fieldtype": "Float", "width": 90},
		{"label": "Outstanding", "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 95},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 130},
		{"label": "Shipment", "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 145},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	columns = insert_columns_after(columns, "borrower", requester_columns())
	columns = insert_columns_after(columns, "outstanding_qty", value_columns())
	data = frappe.db.sql(
		f"""SELECT loan.name AS loan, loan.material_request, loan.loan_type,
			loan.borrower_type, loan.borrower, {REQUESTER_SELECT}, loan.expected_return_date,
			COUNT(item.name) AS item_count, SUM(item.qty) AS issued_qty,
			SUM(item.returned_qty) AS returned_qty, SUM(item.adjusted_qty) AS adjusted_qty,
			SUM(item.outstanding_qty) AS outstanding_qty,
			SUM(item.qty * {UNIT_VALUE}) AS loan_value,
			SUM(item.outstanding_qty * {UNIT_VALUE}) AS outstanding_value, loan.status,
			COALESCE(
				(SELECT shipment.name FROM `tabShipment` shipment
				 WHERE shipment.custody_loan = loan.name AND shipment.docstatus < 2
				 ORDER BY shipment.modified DESC LIMIT 1),
				delivery_note.custody_loan_shipment
			) AS shipment, loan.company
		FROM `tabCustody Loan` loan
		LEFT JOIN `tabCustody Loan Item` item ON item.parent = loan.name
		LEFT JOIN `tabDelivery Note` delivery_note ON delivery_note.name = loan.delivery_note
		{REQUEST_JOIN}
		WHERE {' AND '.join(conditions)}
		GROUP BY loan.name
		ORDER BY loan.expected_return_date, loan.name""",
		values,
		as_dict=True,
	)
	if filters.get("summary_by_requester"):
		return summary_by_requester(columns, data)
	return columns, data


def summary_by_requester(columns, data):
	"""One row per requester: how many loans, how much is still out and what it is worth."""
	keep = {"requested_by", "requested_by_name", "requested_by_user"}
	totals = {}
	for row in data:
		key = row.requested_by or row.requested_by_user
		total = totals.setdefault(
			key,
			frappe._dict(
				{field: row.get(field) for field in keep},
				loan_count=0, issued_qty=0, returned_qty=0, outstanding_qty=0, loan_value=0, outstanding_value=0,
			),
		)
		total.loan_count += 1
		for field in ("issued_qty", "returned_qty", "outstanding_qty", "loan_value", "outstanding_value"):
			total[field] += row.get(field) or 0
	summary_columns = [column for column in columns if column["fieldname"] in keep]
	summary_columns.append({"label": "Loans", "fieldname": "loan_count", "fieldtype": "Int", "width": 70})
	summary_columns += [
		column
		for column in columns
		if column["fieldname"] in ("issued_qty", "returned_qty", "outstanding_qty", "loan_value", "outstanding_value")
	]
	return summary_columns, sorted(totals.values(), key=lambda row: -(row.outstanding_value or 0))
