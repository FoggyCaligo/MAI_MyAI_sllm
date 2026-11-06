---
name: document
description: Make explicitly supported edits to existing structured documents.
metadata:
  tool-category: document
---
# Structured documents

Use file_read to inspect source documents. Invoke document_edit only for operations exposed in its schema. Supported operations include selected structured formats and PDF page operations; arbitrary PDF text edits are not supported. Preserve files when changes are not expressly requested and report unsupported operations as such.
