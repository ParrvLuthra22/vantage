#!/usr/bin/env bash
# Week 1 verification: health, trace count, and span-tree structure.
#
# Usage: bash scripts/verify_week1.sh [project]
# Env:   BASE_URL (default http://localhost:8000)
#        API_KEY  (default dev-key-change-me)

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-dev-key-change-me}"
PROJECT="${1:-vesper}"
AUTH="Authorization: Bearer ${API_KEY}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
ok()  { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
summary() { echo; echo "Result: ${pass} passed, ${fail} failed"; }

echo "=== 1. health ==="
curl -s --max-time 10 "${BASE_URL}/health" -o "$TMP/health.json" || true
echo "  response: $(cat "$TMP/health.json" 2>/dev/null || echo '<no response>')"
if HEALTH="$TMP/health.json" python3 <<'PY'
import json, os, sys
try:
    with open(os.environ["HEALTH"]) as fh:
        sys.exit(0 if json.load(fh).get("status") == "ok" else 1)
except Exception:
    sys.exit(1)
PY
then ok "status is ok"
else bad "health did not report status=ok — is the API running?"; summary; exit 1
fi

echo
echo "=== 2. traces for project '${PROJECT}' ==="
# -L because /traces (no trailing slash) 307-redirects to /traces/
curl -sL --max-time 10 -H "$AUTH" "${BASE_URL}/traces?project=${PROJECT}" -o "$TMP/traces.json" || true

count="$(TRACES="$TMP/traces.json" python3 <<'PY'
import json, os
try:
    with open(os.environ["TRACES"]) as fh:
        print(len(json.load(fh)))
except Exception:
    print(-1)
PY
)"
echo "  trace count: ${count}"
if [ "$count" -gt 0 ] 2>/dev/null; then
  ok "found ${count} trace(s)"
else
  bad "no traces found — run examples/vesper_integration/main.py first"; summary; exit 1
fi

if TRACES="$TMP/traces.json" python3 <<'PY'
import json, os, sys
with open(os.environ["TRACES"]) as fh:
    rows = json.load(fh)
bad = [t for t in rows if not (t["total_tokens"] > 0 and t["total_cost_usd"] > 0)]
print("  %d/%d traces have total_tokens > 0 and total_cost_usd > 0" % (len(rows) - len(bad), len(rows)))
for t in rows:
    print("    %s  tokens=%-5d cost=$%.5f" % (t["trace_id"][:8], t["total_tokens"], t["total_cost_usd"]))
sys.exit(1 if bad else 0)
PY
then ok "every trace has non-zero tokens and cost"
else bad "some traces have zero tokens or cost"
fi

echo
echo "=== 3. span tree of latest trace ==="
latest="$(TRACES="$TMP/traces.json" python3 <<'PY'
import json, os
with open(os.environ["TRACES"]) as fh:
    rows = json.load(fh)
rows.sort(key=lambda t: t["start_time"], reverse=True)
print(rows[0]["trace_id"] if rows else "")
PY
)"
echo "  trace_id: ${latest}"
curl -s --max-time 10 -H "$AUTH" "${BASE_URL}/traces/${latest}" -o "$TMP/detail.json" || true

if DETAIL="$TMP/detail.json" python3 <<'PY'
import json, os, sys
from datetime import datetime

with open(os.environ["DETAIL"]) as fh:
    d = json.load(fh)

spans = d["spans"]
by_parent = {}
for s in spans:
    by_parent.setdefault(s["parent_span_id"], []).append(s)
for kids in by_parent.values():
    kids.sort(key=lambda s: s["start_time"])


def dur(s):
    if not s["end_time"]:
        return ""
    parse = lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))
    ms = (parse(s["end_time"]) - parse(s["start_time"])).total_seconds() * 1000
    return "  %6.1fms" % ms


def walk(parent=None, depth=0):
    for s in by_parent.get(parent, []):
        extra = ""
        if s["model"]:
            extra = "  [%s in=%s out=%s $%.5f]" % (
                s["model"], s["input_tokens"], s["output_tokens"], s["cost_usd"],
            )
        print("    " + "  " * depth + "- %-22s%s%s" % (s["name"], dur(s), extra))
        if s["attributes"]:
            print("    " + "  " * depth + "    attrs: " + json.dumps(s["attributes"], sort_keys=True))
        walk(s["span_id"], depth + 1)


print("  spans: %d   total_tokens=%d  total_cost_usd=$%.5f"
      % (len(spans), d["total_tokens"], d["total_cost_usd"]))
walk()

roots = by_parent.get(None, [])
sys.exit(0 if len(roots) == 1 and roots[0]["name"] == "orchestrator.handle" else 1)
PY
then ok "single root 'orchestrator.handle' with nested children"
else bad "unexpected span tree shape"
fi

summary
[ "$fail" -eq 0 ]
