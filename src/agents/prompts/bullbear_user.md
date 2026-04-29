{context}

---

Task: based strictly on the data above, write your **{stance}** opinion of **{symbol}** as of **{as_of_date}**.

Reminders (the system prompt is authoritative — these are short pointers):

- Each `arguments[i].evidence` must cite a specific figure or comparison from the data above.
- `arguments` length 3–5. Use lower `confidence` rather than padding.
- `key_risks_to_thesis` length 1–3 — concrete scenarios under which your **{stance}** thesis fails. Specific to this company.
- Do not recommend Buy / Hold / Sell, price targets, or position sizes.
- The Screening Signals section is context for *why this stock was selected*, not evidence by itself. Derive your reasoning from Price Summary, Fundamentals, and Peer Context.

Return only the JSON object matching the schema.
