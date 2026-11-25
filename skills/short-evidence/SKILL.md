---
name: short-evidence
description: Answer a question from a supplied passage with a short verbatim answer and distinguish missing information.
license: MIT
---
Match the entity, requested attribute and time in the question to the passage. An earlier plan, another person's action and an explicit cancellation can change which sentence supplies evidence. If the requested attribute is absent, use the task's missing-answer representation.

Copy the smallest phrase that answers the whole question. For a name, place or count, omit the surrounding predicate. Keep a unit or a qualifier when it is part of the answer. Use the original spelling and language. A sentence-selection task asks for sentence IDs; a span task asks for the answer itself. Preserve the requested output format.
