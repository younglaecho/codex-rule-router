---
paths:
  - "fe/**"
events:
  - edit
priority: 100
---

# End-to-end test rule

Whenever a file under `fe/` is created or edited, ensure its final line is
exactly `PATH_RULES_LOADED=true`.
