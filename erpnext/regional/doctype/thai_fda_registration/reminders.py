# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See license.txt

import hashlib

import frappe
from frappe import _
from frappe.desk.doctype.notification_settings.notification_settings import is_notifications_enabled
from frappe.utils import add_days, escape_html, formatdate, getdate, get_url_to_form, today


def send_expiry_reminders():
	settings = frappe.get_single("Thai FDA Settings")
	if not settings.enabled:
		return

	reminder_days = _get_reminder_days(settings.reminder_days)
	overdue_repeat_days = max(int(settings.overdue_repeat_days or 7), 1)
	today_date = getdate(today())
	max_days = max(reminder_days or [0])

	_pending_logs = frappe.get_all(
		"Thai FDA Reminder Log",
		filters={"status": ("in", ["กำลังส่ง", "รอส่งซ้ำ"])},
		fields=["registration", "expiry_date", "trigger"],
		limit_page_length=0,
	)
	for pending_log in _pending_logs:
		registration = frappe.db.get_value(
			"Thai FDA Registration",
			pending_log.registration,
			["name", "item", "item_name", "registration_number", "expiry_date", "responsible_user"],
			as_dict=True,
		)
		if registration:
			_send_reminder(registration, getdate(pending_log.expiry_date), pending_log.trigger, settings)

	registrations = frappe.get_all(
		"Thai FDA Registration",
		filters={
			"status": "มีผล",
			"expiry_basis": "มีวันหมดอายุตามเอกสาร",
			"expiry_date": ("<=", add_days(today_date, max_days)),
		},
		fields=["name", "item", "item_name", "registration_number", "expiry_date", "responsible_user"],
		limit_page_length=0,
	)

	for registration in registrations:
		expiry_date = getdate(registration.expiry_date)
		days_until_expiry = (expiry_date - today_date).days
		trigger = _get_trigger(days_until_expiry, reminder_days, overdue_repeat_days)
		if not trigger:
			continue

		_send_reminder(registration, expiry_date, trigger, settings)


def _get_reminder_days(value):
	try:
		return sorted({int(day.strip()) for day in (value or "90,60,30,7").split(",") if day.strip()})
	except (AttributeError, TypeError, ValueError):
		frappe.log_error(title="Invalid Thai FDA reminder days", message=frappe.get_traceback())
		return [90, 60, 30, 7]


def _get_trigger(days_until_expiry, reminder_days, overdue_repeat_days):
	if days_until_expiry in reminder_days:
		return f"before:{days_until_expiry}"

	if days_until_expiry < 0:
		days_overdue = abs(days_until_expiry)
		if (days_overdue - 1) % overdue_repeat_days == 0:
			return f"overdue:{days_overdue}"

	return None


def _send_reminder(registration, expiry_date, trigger, settings):
	dedupe_key = hashlib.sha256(
		f"{registration.name}|{expiry_date.isoformat()}|{trigger}".encode()
	).hexdigest()
	log_name = frappe.db.get_value("Thai FDA Reminder Log", {"dedupe_key": dedupe_key}, "name")
	if log_name:
		log = frappe.get_doc("Thai FDA Reminder Log", log_name)
	else:
		log = frappe.get_doc(
			{
				"doctype": "Thai FDA Reminder Log",
				"dedupe_key": dedupe_key,
				"registration": registration.name,
				"expiry_date": expiry_date,
				"trigger": trigger,
				"status": "กำลังส่ง",
			}
		)
		try:
			log.insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			log_name = frappe.db.get_value("Thai FDA Reminder Log", {"dedupe_key": dedupe_key}, "name")
			if not log_name:
				raise
			log = frappe.get_doc("Thai FDA Reminder Log", log_name)

	if log.status in ("ส่งแล้ว", "ยกเลิก"):
		return

	current = frappe.db.get_value(
		"Thai FDA Registration",
		registration.name,
		["status", "expiry_date", "expiry_basis"],
		as_dict=True,
	)
	if (
		not current
		or current.status != "มีผล"
		or current.expiry_basis != "มีวันหมดอายุตามเอกสาร"
		or not current.expiry_date
		or getdate(current.expiry_date) != expiry_date
	):
		log.status = "ยกเลิก"
		log.save(ignore_permissions=True)
		return

	users = _get_recipients(registration.responsible_user, settings)
	url = get_url_to_form("Thai FDA Registration", registration.name)
	message = _get_message(registration, expiry_date, trigger, url)
	subject = message["subject"]

	if settings.send_system_notification and not log.system_sent:
		for user in users:
			if frappe.db.exists(
				"Notification Log",
				{
					"type": "Alert",
					"document_type": "Thai FDA Registration",
					"document_name": registration.name,
					"for_user": user,
					"subject": subject,
				},
			):
				continue
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"subject": subject,
					"email_content": message["body"],
					"document_type": "Thai FDA Registration",
					"document_name": registration.name,
					"for_user": user,
					"from_user": "Administrator",
					"link": url,
				}
			).insert(ignore_permissions=True)
		log.system_sent = 1
		log.save(ignore_permissions=True)

	if settings.send_email and not log.email_sent:
		emails = sorted(
			set(
				frappe.get_all(
					"User",
					filters={"name": ("in", users), "enabled": 1, "email": ("is", "set")},
					pluck="email",
				)
			)
		)
		if emails:
			try:
				frappe.sendmail(recipients=emails, subject=subject, message=message["body"])
				log.email_sent = 1
			except frappe.OutgoingEmailError:
				log.delivery_note = _("ระบบส่งการแจ้งเตือนในแอปแล้ว แต่อีเมลยังส่งไม่สำเร็จ")
				frappe.log_error(title="Thai FDA reminder email failed", message=frappe.get_traceback())
		else:
			log.delivery_note = (
				"ไม่มีอีเมลของผู้รับ ระบบส่งการแจ้งเตือนในแอปแล้ว"
				if users and settings.send_system_notification
				else "ไม่พบผู้รับที่เปิดใช้งาน"
			)
			log.email_sent = 1

	if (not settings.send_system_notification or log.system_sent) and (not settings.send_email or log.email_sent):
		log.status = "ส่งแล้ว"
	else:
		log.status = "รอส่งซ้ำ"
	log.recipients = "\n".join(users)
	log.save(ignore_permissions=True)


def _get_recipients(responsible_user, settings):
	users = {responsible_user} if responsible_user else set()
	for row in settings.recipients:
		if row.enabled and row.user:
			users.add(row.user)
	enabled_users = frappe.get_all(
		"User", filters={"name": ("in", list(users)), "enabled": 1}, pluck="name"
	)
	return sorted(user for user in enabled_users if is_notifications_enabled(user))


def _get_message(registration, expiry_date, trigger, url):
	item_name = registration.item_name or registration.item
	days_until_expiry = (expiry_date - getdate(today())).days
	if trigger.startswith("before:"):
		trigger_text = _("เตือนล่วงหน้า {0} วัน").format(trigger.split(":", 1)[1])
	else:
		trigger_text = _("ติดตามหลังวันหมดอายุ")

	if days_until_expiry < 0:
		due_text = _("หมดอายุแล้ว {0} วัน").format(abs(days_until_expiry))
	elif days_until_expiry == 0:
		due_text = _("หมดอายุวันนี้")
	else:
		due_text = _("หมดอายุใน {0} วัน").format(days_until_expiry)

	subject = _("ทะเบียน อย. {0}: {1} ({2})").format(
		registration.registration_number, trigger_text, formatdate(expiry_date)
	)
	body = _(
		"สินค้า: {0}<br>เลขทะเบียน: {1}<br>วันหมดอายุ: {2} ({3})<br>สถานะต่ออายุ: {4}<br><a href=\"{5}\">เปิดทะเบียน</a>"
	).format(
		escape_html(item_name),
		escape_html(registration.registration_number),
		formatdate(expiry_date),
		due_text,
		escape_html(registration.renewal_status or "ยังไม่เริ่ม"),
		url,
	)
	return {"subject": subject, "body": body, "trigger": trigger}
