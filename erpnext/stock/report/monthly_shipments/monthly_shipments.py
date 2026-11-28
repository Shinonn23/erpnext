"""Monthly Shipments: the transport-cost KPI (Siwat, 18 Nov 2026; UX WP8).

Every submitted Shipment whose pickup date falls in the range, with why it was sent (Shipment Type), what it carried,
who sent it, the carrier and what it cost (``shipment_amount``, the freight, level 0 since WP8). "Freight Billed" is
what the Sales Invoices of the shipment's Delivery Notes billed on the Freight Item (``setup/freight_item.py``); an
invoice is counted once, against the first shipment of the run that carries one of its Delivery Notes. The views
"Month and Type" and "Carrier" sum the same rows. A money column is left out for a user who cannot read it (the
freight on Shipment; Freight Billed needs read on Sales Invoice).
"""

from __future__ import annotations

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from erpnext.stock.report_policy import freight_item_code

SHIPMENTS, BY_MONTH, BY_CARRIER = "Shipments", "Month and Type", "Carrier"
MISCELLANEOUS = "Miscellaneous"
SOURCE_DOCTYPES = ("Delivery Note", "Purchase Receipt", "Stock Entry")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	reads_cost = _can_read("Shipment", "shipment_amount")
	freight_item = (
		freight_item_code()
		if frappe.has_permission("Sales Invoice", "read")
		and _can_read("Sales Invoice Item", "base_net_amount", parenttype="Sales Invoice")
		else None
	)
	reads_billed = bool(freight_item)
	rows = shipment_rows(filters, reads_cost, freight_item)
	view = filters.get("view") or SHIPMENTS
	if view == BY_MONTH:
		columns, data = summarise(rows, ("month", "shipment_purpose"), reads_cost, reads_billed)
	elif view == BY_CARRIER:
		columns, data = summarise(rows, ("carrier",), reads_cost, reads_billed)
	else:
		columns, data = shipment_columns(reads_cost, reads_billed), rows
	return columns, data, None, None, report_summary(rows, reads_cost, reads_billed)


def _can_read(doctype, fieldname, parenttype=None):
	from frappe.model import get_permitted_fields

	return fieldname in get_permitted_fields(doctype, parenttype=parenttype, permission_type="read")


def shipment_rows(filters, reads_cost, freight_item):
	conditions = {"docstatus": 1}
	if filters.get("from_date") and filters.get("to_date"):
		conditions["pickup_date"] = ("between", [filters.from_date, filters.to_date])
	for field in ("shipment_purpose", "sent_by_employee"):
		if filters.get(field):
			conditions[field] = filters[field]
	if filters.get("carrier"):
		conditions["carrier"] = ("like", f"%{filters.carrier}%")
	fields = [
		"name",
		"pickup_date",
		"shipment_purpose",
		"carrier",
		"carrier_service",
		"awb_number",
		"delivery_to",
		"recipient_details",
		"description_of_content",
		"sent_by_employee",
		"total_weight",
		"pickup_company",
		"delivery_company",
	]
	if reads_cost:
		fields.append("shipment_amount")
	shipments = frappe.get_list(
		"Shipment", filters=conditions, fields=fields, order_by="pickup_date, name", limit_page_length=0
	)
	if not shipments:
		return []
	names = [row.name for row in shipments]
	documents = {}
	for row in frappe.get_all(
		"Shipment Document",
		filters={"parenttype": "Shipment", "parent": ("in", names)},
		fields=["parent", "reference_doctype", "reference_name"],
		order_by="parent, idx",
		limit_page_length=0,
	):
		documents.setdefault(row.parent, []).append((row.reference_doctype, row.reference_name))
	parcels = {}
	for row in frappe.get_all(
		"Shipment Parcel",
		filters={"parenttype": "Shipment", "parent": ("in", names)},
		fields=["parent", "count"],
		limit_page_length=0,
	):
		parcels[row.parent] = parcels.get(row.parent, 0) + (row.count or 0)
	companies = _document_companies(documents)
	employees = _employee_names({row.sent_by_employee for row in shipments if row.sent_by_employee})
	billed = _freight_billed(shipments, documents, freight_item) if freight_item else {}
	out = []
	for row in shipments:
		linked = documents.get(row.name, [])
		company = {companies.get(key) for key in linked} - {None} or {
			value for value in (row.pickup_company, row.delivery_company) if value
		}
		if filters.get("company") and filters.company not in company:
			continue
		entry = frappe._dict(
			shipment=row.name,
			month=getdate(row.pickup_date).strftime("%Y-%m") if row.pickup_date else "",
			pickup_date=row.pickup_date,
			shipment_purpose=row.shipment_purpose or "",
			carrier=row.carrier or "",
			carrier_service=row.carrier_service or "",
			awb_number=row.awb_number or "",
			destination=(
				row.recipient_details
				if row.shipment_purpose == MISCELLANEOUS and row.recipient_details
				else row.delivery_to or row.recipient_details or ""
			),
			description=row.description_of_content or "",
			linked_documents=", ".join(name for _doctype, name in linked),
			sent_by_employee=row.sent_by_employee,
			sent_by_name=employees.get(row.sent_by_employee, ""),
			parcels=parcels.get(row.name) or (1 if row.shipment_purpose == MISCELLANEOUS else 0),
			total_weight=flt(row.total_weight),
			count=1,
		)
		if reads_cost:
			entry.shipment_amount = flt(row.shipment_amount)
		if freight_item:
			entry.freight_billed = flt(billed.get(row.name))
		out.append(entry)
	return out


def _document_companies(documents):
	by_doctype = {}
	for linked in documents.values():
		for doctype, name in linked:
			if doctype in SOURCE_DOCTYPES and name:
				by_doctype.setdefault(doctype, set()).add(name)
	out = {}
	for doctype, names in by_doctype.items():
		for row in frappe.get_all(
			doctype, filters={"name": ("in", list(names))}, fields=["name", "company"], limit_page_length=0
		):
			out[(doctype, row.name)] = row.company
	return out


def _employee_names(employees):
	if not employees:
		return {}
	return {
		row.name: row.employee_name
		for row in frappe.get_all(
			"Employee",
			filters={"name": ("in", list(employees))},
			fields=["name", "employee_name"],
			limit_page_length=0,
		)
	}


def _freight_billed(shipments, documents, freight_item):
	"""{shipment: freight billed}: the Freight Item rows of the submitted Sales Invoices that bill one of the
	shipment's Delivery Notes, each invoice once (first shipment of the run, in pickup order)."""
	notes = {
		name
		for linked in documents.values()
		for doctype, name in linked
		if doctype == "Delivery Note" and name
	}
	if not notes:
		return {}
	invoice_notes = {}
	for row in frappe.get_all(
		"Sales Invoice Item",
		filters={"parenttype": "Sales Invoice", "delivery_note": ("in", list(notes)), "docstatus": 1},
		fields=["parent", "delivery_note"],
		limit_page_length=0,
	):
		invoice_notes.setdefault(row.parent, set()).add(row.delivery_note)
	if not invoice_notes:
		return {}
	readable_invoices = frappe.get_list(
		"Sales Invoice",
		filters={"name": ("in", list(invoice_notes)), "docstatus": 1},
		pluck="name",
		limit_page_length=0,
	)
	if not readable_invoices:
		return {}
	readable_invoices = set(readable_invoices)
	freight = {}
	for row in frappe.get_all(
		"Sales Invoice Item",
		filters={
			"parenttype": "Sales Invoice",
			"parent": ("in", sorted(readable_invoices)),
			"item_code": freight_item,
			"docstatus": 1,
		},
		fields=["parent", "base_net_amount"],
		limit_page_length=0,
	):
		freight[row.parent] = freight.get(row.parent, 0) + flt(row.base_net_amount)
	out, counted = {}, set()
	for shipment in shipments:
		shipment_notes = {
			name for doctype, name in documents.get(shipment.name, []) if doctype == "Delivery Note"
		}
		for invoice, billed_notes in invoice_notes.items():
			if invoice not in readable_invoices or invoice in counted or not billed_notes & shipment_notes:
				continue
			counted.add(invoice)
			out[shipment.name] = out.get(shipment.name, 0) + freight.get(invoice, 0)
	return out


def _column(fieldname, label, fieldtype="Data", options=None, width=120):
	return {
		"fieldname": fieldname,
		"label": _(label),
		"fieldtype": fieldtype,
		"options": options,
		"width": width,
	}


def _money_columns(reads_cost, reads_billed):
	columns = []
	if reads_cost:
		columns.append(_column("shipment_amount", "Freight Cost", "Currency", width=120))
	if reads_billed:
		columns.append(_column("freight_billed", "Freight Billed", "Currency", width=120))
	return columns


def shipment_columns(reads_cost=True, reads_billed=True):
	return [
		_column("month", "Month", width=80),
		_column("shipment_purpose", "Shipment Type", width=120),
		_column("shipment", "Shipment", "Link", "Shipment", 130),
		_column("pickup_date", "Pickup Date", "Date", width=100),
		_column("carrier", "Carrier", width=120),
		_column("carrier_service", "Carrier Service", width=120),
		_column("awb_number", "AWB Number", width=120),
		_column("destination", "Destination", width=180),
		_column("description", "What is sent / Reason", width=200),
		_column("linked_documents", "Linked Documents", width=180),
		_column("sent_by_employee", "Sent by Employee", "Link", "Employee", 120),
		_column("sent_by_name", "Sender Name", width=150),
		_column("parcels", "Parcels", "Int", width=70),
		_column("total_weight", "Weight (kg)", "Float", width=90),
		*_money_columns(reads_cost, reads_billed),
	]


def summarise(rows, keys, reads_cost=True, reads_billed=True):
	labels = {"month": ("Month", 90), "shipment_purpose": ("Shipment Type", 140), "carrier": ("Carrier", 160)}
	groups = OrderedDict()
	for row in sorted(rows, key=lambda row: tuple(row.get(key) or "" for key in keys)):
		key = tuple(row.get(field) or "" for field in keys)
		group = groups.setdefault(
			key,
			frappe._dict(
				{field: value for field, value in zip(keys, key, strict=True)},
				count=0,
				parcels=0,
				total_weight=0,
				shipment_amount=0,
				freight_billed=0,
			),
		)
		for field in ("count", "parcels", "total_weight", "shipment_amount", "freight_billed"):
			group[field] += flt(row.get(field))
	data = []
	for group in groups.values():
		if not reads_cost:
			group.pop("shipment_amount")
		if not reads_billed:
			group.pop("freight_billed")
		data.append(group)
	columns = [_column(field, labels[field][0], width=labels[field][1]) for field in keys]
	columns += [
		_column("count", "Shipments", "Int", width=90),
		_column("parcels", "Parcels", "Int", width=80),
		_column("total_weight", "Weight (kg)", "Float", width=100),
		*_money_columns(reads_cost, reads_billed),
	]
	return columns, data


def report_summary(rows, reads_cost=True, reads_billed=True):
	summary = [{"label": _("Shipments"), "value": len(rows), "datatype": "Int", "indicator": "Blue"}]
	if reads_cost:
		summary.append(
			{
				"label": _("Freight Cost"),
				"value": sum(flt(row.get("shipment_amount")) for row in rows),
				"datatype": "Currency",
				"indicator": "Orange",
			}
		)
	if reads_billed:
		summary.append(
			{
				"label": _("Freight Billed"),
				"value": sum(flt(row.get("freight_billed")) for row in rows),
				"datatype": "Currency",
				"indicator": "Green",
			}
		)
	return summary
