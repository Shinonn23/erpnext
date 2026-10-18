import frappe


def execute():
	"""Thai Tax now lives under Accounting > Taxes; drop the standalone workspace and its desktop icon.

	The workspace is module-owned, so Workspace.on_trash does not remove the sidebar
	and desktop icon that Frappe auto-created for it. Delete them explicitly,
	icon first because it links to the sidebar.
	"""
	frappe.delete_doc_if_exists("Desktop Icon", "Thai Tax")
	frappe.delete_doc_if_exists("Workspace Sidebar", "Thai Tax")
	frappe.delete_doc_if_exists("Workspace", "Thai Tax")
