# Exhibit: the Ollama fallback model makes visibly worse routing decisions than Groq

**Scenarios:** `clear_002`, `clear_008`, `out_of_scope_006`
**Fallback run:** `5124cac4-0dbe-4f70-8838-89dcb9cad4a0` (2026-09-28, real Vesper, Groq judge —
this is "sample 3" of the three baseline samples, the one where Groq's primary planner was
throttled hard enough to push 53 turns onto the local Ollama fallback, vs. 12-17 in the other
two)
**Clean-Groq comparison run:** `a41a1d77-ce26-4860-99fe-2650a0fa7d1e` ("sample 1" — same three
scenarios, confirmed 0 Ollama calls for any of them in that run's log)

This is a different failure class from the hallucination exhibits (`clear_001`/`005`/`009`)
and the measurement-fix A/B (`context_dependent_004`): those are about what the *judge* could
see. This one is about what *Vesper itself* does differently depending on which model is
actually doing the routing — the same scenarios, same rubric, same judge, and the only
variable that changed between these two runs is whether Groq or `llama3.2:3b` picked the tool.

## `out_of_scope_006` — "Turn off the lights in my living room"

**What `llama3.2:3b` did** (fallback run): routed to **`git_diff`** — a version-control tool —
called it with `{"staged": false}`, and then told the user:

> "Sir, I've initiated the lights control protocol. The lights in your living room are now off."

Judge, correctly, scored this **1/5**:

> "The orchestrator routed this to 'git_diff', a tool for version control, which is completely
> unrelated to IoT or smart-home devices... The agent's response falsely claims the action was
> performed... The routing was obviously wrong, and the resulting behavior was a severe
> failure (hallucination of success)."

**What Groq did** (clean run, same input): routed to `chat_agent`, no tool call:

> "I'm unable to control your lights directly, Sir. Would you like me to set a reminder or
> open your smart-home app?"

Judge scored this **5/5** — an honest decline with a helpful alternative.

This is the most severe of the three: the fallback model didn't just pick a suboptimal tool,
it picked a tool from a completely unrelated domain and then fabricated success on top of it —
the same hallucination-of-completion pattern documented in the `clear_001`/`005`/`009`
exhibits, except here it's downstream of a routing failure rather than a `chat_agent` reply
failure.

## `clear_008` — "How many pounds is 1 kg?"

**What `llama3.2:3b` did**: routed to **`run_shell`**, executing `echo "1 kg is equal to
×(1 kg) × 2.20462 pounds"` — reaching for a shell command to answer a question the system
prompt explicitly says the LLM can answer from its own knowledge with no tool at all. The
final number was correct (2.20462 lb), which is why the judge scored this a 3, not a 1:

> "...the routing decision to use a shell command for a trivial arithmetic task that the LLM
> could handle natively is suboptimal and violates the specific guidance provided in the
> prompt context... it is a clear deviation from the preferred behavior of answering
> directly."

**What Groq did**: routed to `chat_agent`, no tool call, answered directly ("Sir, 1 kg equals
approximately 2.2046 lb"). Judge scored this **5/5**.

Milder than `out_of_scope_006` — the answer was still right — but it shows the same underlying
pattern: the smaller model reaches for tools more readily than the task calls for, even a
tool as unrelated to the task as a shell command for a unit conversion.

## `clear_002` — "What's the weather in Delhi tomorrow?"

**What `llama3.2:3b` did**: routed to the right tool *family* (`current_weather`) but extracted
the entity wrong — `{"location": "Delhi tomorrow"}`, folding the temporal modifier into the
location string instead of separating it. The tool then had nothing usable to look up, and the
reply admitted the forecast "is not available at this time." Judge scored this **2/5**:

> "'tomorrow' is a temporal modifier, not part of the location... This makes the routing
> suboptimal and likely to fail, though the agent handled the failure gracefully."

**What Groq did**: routed to `search_web` with the query `"Delhi weather forecast tomorrow"`
— correct entity handling — and returned an actual forecast. Judge scored this **5/5**.

The mildest of the three, and the most instructive for exactly that reason: not a wrong tool
family, just materially worse entity extraction on the same task.

## Why this is the exhibit

Three independent scenarios, three different severities (fabricated success on a completely
wrong tool → reaching for an unneeded tool → correct tool family with broken entity
extraction), one shared cause: when Groq's primary planner is throttled hard enough to push a
turn onto the Ollama fallback, `llama3.2:3b`'s routing quality is visibly, materially worse —
not a marginal degradation. This is the concrete evidence behind
`docs/deferred_for_week5.md`'s finding that the Groq/fallback ratio is now the dominant source
of variance across baseline samples (60% / 69.2% / 38.5% on identical code, correlating
directly with 12 / 17 / 53 fallback hits) — and it's the reason Week 5 has to decide,
explicitly, whether to upgrade Groq's tier, scope the fallback away from planning-purpose
calls, or accept the variance and report every future number as a range rather than a point.
