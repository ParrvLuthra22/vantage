# Interview exhibits

Real evidence, pulled from actual eval runs against real Vesper, of what this project's
measurement layer catches and why the fix that shipped this sprint (`1948256` — show the
judge the whole turn, not just the first tool call) mattered. Read these first if you're
auditing what this eval harness actually found, not just what it's supposed to do.

- **[hallucination_caught_by_measurement_fix.md](hallucination_caught_by_measurement_fix.md)**
  — `clear_001`. The original exhibit: Vesper calls a harmless read-only tool, then tells
  the user a meeting was booked. Never visible to the pre-fix judge, which only saw
  `routed_agent`, never the reply.
- **[hallucination_clear_005_email.md](hallucination_clear_005_email.md)** — `clear_005`,
  same failure class, second instance: `chat_agent` implies it can send an email once given
  missing details, when Vesper has no email tool at all.
- **[hallucination_clear_009_reminder.md](hallucination_clear_009_reminder.md)** —
  `clear_009`, same failure class, third instance, across a calendar/email/reminders spread
  of domains: `chat_agent` claims (or, in one run, hedges but still implies) a reminder was
  set with no mechanism to do so.
- **[measurement_fix_ab_evidence.md](measurement_fix_ab_evidence.md)** — `context_dependent_004`,
  the other direction of error. Same real Vesper behavior (get_volume → set_volume(60),
  correctly reported), scored 1/5 with byte-identical reasoning in two separate pre-fix
  runs, then 5/5 in two separate post-fix runs. Nothing about Vesper changed between the
  two pairs — only what the judge could see. The cleanest available proof the fix works.

Together: three independent examples of the same hallucination class turn "we found one
bug" into "we found a pattern," and the A/B pair proves the fix corrects false negatives
(this exhibit) as well as false positives (the three hallucination exhibits) — not just one
direction of judge error.
