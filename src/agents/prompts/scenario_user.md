{context}

---

Task: based on the Bull/Bear opinions and price context above, generate **exactly three** scenarios (bull / base / bear) for **{symbol}** as of **{as_of_date}**.

Reminders (the system prompt is authoritative — these are short pointers):

- Do NOT estimate prices — only `probability`, `narrative`, and `invalidation_trigger`. Prices are computed downstream.
- The three probabilities must sum to 1.0.
- Each `narrative` must cite specific evidence from the Bull or Bear opinion above.
- Each `invalidation_trigger` must be a measurable event that would *negate that scenario*. Prefer auto-measurable metrics; `threshold` is null only for qualitative triggers.

Return only the JSON object matching the schema.
