import frappe


def demo_request_where(filters=None, alias="r", date_field="transaction_date"):
	filters = frappe._dict(filters or {})
	conditions = [
		f"{alias}.docstatus = 1",
		f"{alias}.material_request_type = 'Customer Demo'",
	]
	values = {}
	for field in ("company", "customer", "request_progress_status"):
		if filters.get(field):
			conditions.append(f"{alias}.{field} = %({field})s")
			values[field] = filters[field]
	if date_field and filters.get("from_date"):
		conditions.append(f"{alias}.{date_field} >= %(from_date)s")
		values["from_date"] = filters.from_date
	if date_field and filters.get("to_date"):
		conditions.append(f"{alias}.{date_field} <= %(to_date)s")
		values["to_date"] = filters.to_date
	return " AND ".join(conditions), values
