---
paths:
  - "fe/**/*.{ts,tsx,css,scss}"
exclude:
  - "fe/**/*.test.{ts,tsx}"
events:
  - read
  - edit
priority: 100
---

# Frontend rules

- Follow the frontend coding convention before editing implementation files.
- Reuse existing components and tokens before adding new UI primitives.
- Run the frontend type check after changing TypeScript.
