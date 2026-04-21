---
name: frappe-api-patterns
description: >-
  Frappe/ERPNext API patterns including whitelisted methods, DocType CRUD,
  hooks placement, and bench command usage. Use when generating or reviewing
  Frappe Python code.
tags: [python, frappe, erp]
---

# Frappe API Patterns

## Whitelisted Methods

Use `@frappe.whitelist()` to expose Python functions as HTTP endpoints:

```python
@frappe.whitelist()
def get_customer_balance(customer_name: str) -> float:
    return frappe.db.get_value("Customer", customer_name, "outstanding_amount")
```

Always validate permissions inside whitelisted methods — the decorator only makes the function callable via HTTP.

## DocType CRUD

```python
doc = frappe.get_doc({"doctype": "Sales Order", "customer": "ACME"})
doc.insert()
doc.submit()
```

Use `frappe.get_doc` for single documents, `frappe.get_all` for lists.

## Hooks

Place hooks in `hooks.py` at the app root:

```python
doc_events = {
    "Sales Order": {
        "on_submit": "myapp.api.handle_submit",
    }
}
```
