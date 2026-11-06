---
name: filesystem
description: Read, search, create, edit, copy, move, or inspect local files and CSV data.
metadata:
  tool-category: filesystem
---
# Filesystem operations

Use the native file tools for the selected machine and account. Discover paths before assuming their existence. A file_read operation supports PDF, DOCX, XLSX, CSV, PPTX, and plain text. For CSV use the declared encoding when necessary. A file_write creates or replaces text; owner OS permissions and trial upload isolation are enforced by the actual tool handler. Temporary scripts and probes created for this turn should use file_write with lifecycle=temporary. A terminal-created file is not automatically registered for cleanup. Do not delete or overwrite a user file unless requested. Inspect the activated tool's exact input schema, including is_remote when available.
