---
name: linear-declarations
description: Translate a bounded integer optimization problem into domains, individual restrictions and ordered objectives.
license: MIT
---
Identify decisions and their explicitly stated domains first. Keep the domains separate from additional restrictions. For every restriction identify the quantity on the left, threshold on the right and whether equality is allowed. “At least” and 以上 allow equality. “Under”, 未満 and “strictly greater than” exclude equality. When exchanging sides, reverse the comparison.

Assign each unit coefficient to its own named decision. A missing term has coefficient zero; an unweighted occurrence has coefficient one. Handle goals separately in their stated priority order. A range is not an extra objective, and a resource limit is not a decision. If asked for evidence, copy each relevant clause exactly from the passage. Use the task's missing-information form for an unspecified domain or goal.
