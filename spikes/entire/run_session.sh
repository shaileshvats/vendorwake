#!/usr/bin/env bash
# Simulates one Migration Agent session in a throwaway repo and checks what Entire records.
# The "model" turns here are scripted; this spike tests the Entire wiring, not the model.
set -euo pipefail

REPO=/tmp/fixture-repo
SID="vw-inv-0001-$(date +%s)"
rm -rf "$REPO" && mkdir -p "$REPO" && cd "$REPO"
git init -q -b main
git config user.email spike@vendorwake.invalid
git config user.name "Vendorwake spike"
mkdir -p payments
cat > payments/refunds.py <<'PY'
import stripe

def reverse_transfer(transfer_id: str) -> "stripe.Reversal":
    return stripe.Transfer.create_reversal(transfer_id)
PY
git add -A && git commit -qm "fixture: transfer reversal helper"

echo "== entire enable"
entire enable --agent vendorwake --telemetry=false </dev/null 2>&1 | tail -5 || true
entire status 2>&1 | tail -8 || true

SDIR=$(entire-agent-vendorwake get-session-dir --repo-path "$REPO" | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_dir"])')
REF="$SDIR/$SID.jsonl"
mkdir -p "$SDIR"
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$1" >> "$REF"; }
hook() { # hook-name extra-json; payload goes to `entire hooks` on stdin

  local payload
  payload=$(SID="$SID" REF="$REF" HOOK="$1" EXTRA="$2" python3 -c '
import json, os, datetime
p = {"hook_type": os.environ["HOOK"], "session_id": os.environ["SID"], "session_ref": os.environ["REF"],
     "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "model": "gemma4:e4b-it-qat",
     "metadata": {"investigation_id": "inv-0001", "agent": "migration"}}
p.update(json.loads(os.environ["EXTRA"]))
print(json.dumps(p))')
  echo "-> hook $1"
  echo "$payload" | entire hooks vendorwake "$1" 2>&1 | sed 's/^/   [entire] /' || true
}

PROMPT="Migrate payments/refunds.py for stripe-python 16.0.0: stripe.Reversal was renamed to stripe.TransferReversal."
log "{\"type\":\"user\",\"content\":\"$PROMPT\",\"ts\":\"$(now)\"}"
hook session-start '{}'
hook turn-start "{\"user_prompt\":\"$PROMPT\"}"

sed -i 's/stripe\.Reversal/stripe.TransferReversal/' payments/refunds.py
log '{"type":"tool","name":"write_file","input":{"path":"payments/refunds.py"},"ok":true}'
log "{\"type\":\"assistant\",\"content\":\"Renamed the type annotation to stripe.TransferReversal.\",\"model\":\"gemma4:e4b-it-qat\",\"usage\":{\"input_tokens\":812,\"output_tokens\":64}}"
log '{"type":"summary","content":"Updated the stripe.Reversal reference to stripe.TransferReversal per stripe-python 16.0.0."}'
hook turn-end '{}'

git add -A && git commit -qm "migrate: stripe.Reversal -> stripe.TransferReversal (stripe-python 16.0.0)" 2>&1 | sed 's/^/   [git] /' || true
hook session-end '{}'

echo "== last commit message"; git log -1 --format=%B
echo "== branches"; git branch -a
echo "== entire session list"; entire session list 2>&1 | tail -10 || true
echo "== entire checkpoint list"; entire checkpoint list 2>&1 | tail -10 || true
CP=$(git log -1 --format=%B | sed -n 's/^Entire-Checkpoint: *//p' | head -1)
if [ -n "$CP" ]; then
  echo "== entire checkpoint explain $CP"; entire checkpoint explain "$CP" --short 2>&1 | tail -25 || true
fi
