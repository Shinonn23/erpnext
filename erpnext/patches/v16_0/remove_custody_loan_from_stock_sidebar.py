import frappe


MOVED_LINKS = {
	"Asset Movement",
	"Custody Loan",
	"Custody Loan Return",
	"Custody Loan Adjustment",
	"Stock to Asset Conversion",
	"Stock to Asset Conversion Audit",
	"Overdue Custody Loans",
	"Custody Loan Register",
	"Custody Loan Asset Balance",
	"Custody Loan Stock Balance",
	"Custody Loan Movement History",
}


def execute():
	for sidebar_name in frappe.get_all("Workspace Sidebar", pluck="name"):
		if sidebar_name != "Stock" and not sidebar_name.startswith("Stock-"):
			continue

		sidebar = frappe.get_doc("Workspace Sidebar", sidebar_name)
		items = sidebar.get("items") or []
		updated_items = []
		index = 0
		changed = False

		while index < len(items):
			item = items[index]

			if item.type == "Section Break" and item.label == "Customer Requests and Custody":
				end = index + 1
				while end < len(items) and items[end].child:
					end += 1

				remaining_children = [
					child for child in items[index + 1 : end] if child.get("link_to") not in MOVED_LINKS
				]
				if remaining_children:
					updated_items.extend([item, *remaining_children])
				else:
					changed = True
				if len(remaining_children) != end - index - 1:
					changed = True
				index = end
				continue

			if item.get("link_to") in MOVED_LINKS:
				changed = True
			else:
				updated_items.append(item)
			index += 1

		if changed:
			sidebar.set("items", updated_items)
			sidebar.save(ignore_permissions=True)
