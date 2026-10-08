# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See license.txt

import frappe
from frappe.utils import getdate, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	settings = frappe.get_single("Thai FDA Settings")
	try:
		reminder_days = [int(day.strip()) for day in (settings.reminder_days or "90,60,30,7").split(",") if day.strip()]
	except (AttributeError, TypeError, ValueError):
		reminder_days = [90, 60, 30, 7]
	max_reminder_days = max(reminder_days or [0])
	today_date = getdate(today())

	item_filters = {}
	if filters.item:
		item_filters["name"] = filters.item
	if filters.assessment_status:
		item_filters["custom_thai_fda_applicability"] = filters.assessment_status

	items = frappe.get_all(
		"Item",
		filters=item_filters,
		fields=["name", "item_name", "custom_thai_fda_applicability", "custom_thai_fda_assessment_note"],
		limit_page_length=0,
	)
	item_by_name = {item.name: item for item in items}
	if not item_by_name:
		return _get_columns(), []

	registration_filters = {"item": ("in", list(item_by_name))}
	if filters.regulatory_category:
		registration_filters["regulatory_category"] = filters.regulatory_category
	if filters.responsible_user:
		registration_filters["responsible_user"] = filters.responsible_user
	if filters.renewal_status:
		registration_filters["renewal_status"] = filters.renewal_status

	registrations = frappe.get_all(
		"Thai FDA Registration",
		filters=registration_filters,
		fields=[
			"name",
			"item",
			"item_name",
			"regulatory_category",
			"other_category",
			"approval_type",
			"registration_number",
			"status",
			"expiry_basis",
			"expiry_date",
			"renewal_status",
			"responsible_user",
		],
		limit_page_length=0,
	)
	registrations_by_item = {}
	for registration in registrations:
		registrations_by_item.setdefault(registration.item, []).append(registration)

	data = []
	for item in items:
		item_registrations = registrations_by_item.get(item.name, [])
		if not item_registrations:
			status = _get_empty_item_status(item.custom_thai_fda_applicability)
			if (
				filters.responsible_user
				or filters.renewal_status
				or (filters.regulatory_category and not filters.item)
			):
				continue
			if filters.compliance_status and filters.compliance_status != status:
				continue
			data.append(
				{
					"item": item.name,
					"item_name": item.item_name,
					"assessment_status": item.custom_thai_fda_applicability,
					"compliance_status": status,
					"assessment_note": item.custom_thai_fda_assessment_note,
				}
			)
			continue

		for registration in item_registrations:
			status = _get_registration_status(registration, today_date, max_reminder_days)
			if filters.compliance_status and filters.compliance_status != status:
				continue
			data.append(
				{
					"registration": registration.name,
					"item": item.name,
					"item_name": registration.item_name or item.item_name,
					"assessment_status": item.custom_thai_fda_applicability,
					"regulatory_category": registration.other_category or registration.regulatory_category,
					"approval_type": registration.approval_type,
					"registration_number": registration.registration_number,
					"compliance_status": status,
					"expiry_date": registration.expiry_date,
					"renewal_status": registration.renewal_status,
					"responsible_user": registration.responsible_user,
				}
			)

		if item.custom_thai_fda_applicability == "ต้องมีทะเบียน" and not any(
			_is_current_registration(registration, today_date) for registration in item_registrations
		):
			status = "ต้องมีทะเบียนแต่ยังไม่มี"
			if not filters.compliance_status or filters.compliance_status == status:
				if not filters.responsible_user:
					data.append(
						{
							"item": item.name,
							"item_name": item.item_name,
							"assessment_status": item.custom_thai_fda_applicability,
							"compliance_status": status,
							"assessment_note": item.custom_thai_fda_assessment_note,
						}
					)

	return _get_columns(), data


def _get_empty_item_status(applicability):
	if applicability == "ต้องมีทะเบียน":
		return "ต้องมีทะเบียนแต่ยังไม่มี"
	if applicability == "ไม่ต้องมี/ได้รับยกเว้น":
		return "ไม่ต้องมี/ได้รับยกเว้น"
	return "ยังไม่ประเมิน"


def _get_registration_status(registration, today_date, max_reminder_days):
	if registration.status != "มีผล":
		return registration.status
	if registration.expiry_basis == "เอกสารไม่ระบุวันหมดอายุ":
		return "เอกสารไม่ระบุวันหมดอายุ"
	if registration.expiry_basis != "มีวันหมดอายุตามเอกสาร" or not registration.expiry_date:
		return "ยังยืนยันวันหมดอายุไม่ได้"

	days_until_expiry = (getdate(registration.expiry_date) - today_date).days
	if days_until_expiry < 0:
		return "หมดอายุแล้ว"
	if days_until_expiry <= max_reminder_days:
		return "ใกล้หมดอายุ"
	return "มีผล"


def _is_current_registration(registration, today_date):
	if registration.status != "มีผล":
		return False
	if registration.expiry_basis == "มีวันหมดอายุตามเอกสาร":
		return bool(registration.expiry_date and getdate(registration.expiry_date) >= today_date)
	return True


def _get_columns():
	return [
		{"label": "ทะเบียน อย.", "fieldname": "registration", "fieldtype": "Link", "options": "Thai FDA Registration", "width": 140},
		{"label": "Item", "fieldname": "item", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "ชื่อสินค้า", "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": "ผลประเมิน อย.", "fieldname": "assessment_status", "fieldtype": "Data", "width": 165},
		{"label": "กลุ่มผลิตภัณฑ์", "fieldname": "regulatory_category", "fieldtype": "Data", "width": 145},
		{"label": "ประเภทเอกสาร", "fieldname": "approval_type", "fieldtype": "Data", "width": 180},
		{"label": "เลขทะเบียน/ใบอนุญาต", "fieldname": "registration_number", "fieldtype": "Data", "width": 180},
		{"label": "สถานะ", "fieldname": "compliance_status", "fieldtype": "Data", "width": 170},
		{"label": "วันหมดอายุ", "fieldname": "expiry_date", "fieldtype": "Date", "width": 110},
		{"label": "สถานะการต่ออายุ", "fieldname": "renewal_status", "fieldtype": "Data", "width": 150},
		{"label": "ผู้รับผิดชอบ", "fieldname": "responsible_user", "fieldtype": "Link", "options": "User", "width": 140},
		{"label": "หมายเหตุการประเมิน", "fieldname": "assessment_note", "fieldtype": "Data", "width": 200},
	]
