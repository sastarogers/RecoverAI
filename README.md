# RecoverAI

**AI-powered revenue recovery intelligence for Razorpay merchants.**

One unified platform covering three revenue-loss scenarios — failed payments, abandoned
checkouts and failed subscription renewals — through a single pipeline.

```
Event → Normalize → Detect → Context → AI Decision → Policy → Execute → Outcome → Ledger → Analytics
```

---

## The one idea that matters

> **The AI decides what to do. The payment outcome decides whether money was recovered.**

An AI probability is a forecast, not revenue. RecoverAI never counts a prediction as
money. A ₹5,000 failed payment with an 82%-confidence recommendation contributes **₹0**
until a payment actually succeeds — and then it contributes **₹5,000**, not ₹4,100.

The dashboard shows all three quantities side by side and never adds them together:

| Quantity | What it is | Counts as revenue? |
|---|---|---|
| **Recovered revenue** | Settled in the ledger against real evidence | **Yes** |
| Expected recovery value | amount at risk × AI probability | No — a forecast |
| Projected retention value | future subscription cycles | No — reported separately |

Three independent database guards make double-counting impossible: a unique settlement
key, a partial unique index (`one RECOVERED entry per opportunity`), and a CHECK that a
recovery never exceeds the amount originally at risk.

---

## Quick start

Requires Docker (for Postgres), Python 3.11+, Node 20+.

```bash
cp .env.example .env          # optional: add ANTHROPIC_API_KEY / Razorpay test keys
make up                       # start Postgres (port 5433)
make install                  # backend venv + dependencies
make migrate                  # apply schema
make seed                     # generate a demo dataset (~40s)

make api                      # terminal 1 → http://localhost:8000/docs
make web                      # terminal 2 → http://localhost:3000
```

Open **http://localhost:3000**. Everything works with no API keys at all — the decision
engine falls back to its deterministic strategist and the platform runs end to end.

### Verify

```bash
make test     # 135 tests
make lint
```

---

## What to look at

| Page | What it shows |
|---|---|
| `/` | Revenue at risk, recovered revenue, recovery rate, per-scenario breakdown, live pipeline activity |
| `/opportunities` | Every unit of revenue at risk, filterable |
| `/opportunities/[ref]` | Full lifecycle: context the AI saw, its recommendation and reasoning, every policy rule evaluated, each attempt, the outcome, and the ledger entry |
| `/simulation` | Configure and run a simulation, with live streamed progress |
| `/analytics` | Baseline comparison, prediction calibration, AI decision quality |
| `/razorpay` | Test Mode connection state, live webhook events, opportunities from real gateway events |
| `/demo` | Three one-click scripted scenarios |
| `/settings` | Policy guardrails and decision-engine configuration |

---

## Architecture

Full design in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**. The load-bearing parts:

**One pipeline, two sources.** Razorpay webhooks and the synthetic simulator are both
translated into the same `NormalizedEvent` at the boundary. Nothing downstream branches
on where an event came from — it only records it.

**The AI cannot reach the money.** There is no code path from the decision agent to the
executor that does not pass through the policy engine, and none to the ledger at all.
The state machine independently refuses any transition into `EXECUTING` that did not
come from `APPROVED`. High confidence never unlocks a blocked action; confidence is only
ever used to make a rule *stricter*.

**The AI is never load-bearing.** If the model times out, returns malformed JSON, or
fails validation, a deterministic strategist produces the decision instead and the
rejection is recorded on the opportunity. Every decision stores how it was produced
(`LLM` / `LLM_CACHED` / `HEURISTIC` / `HEURISTIC_FALLBACK`), so the dashboard never
overstates how much of a run the model actually drove.

**Hidden ground truth.** The simulator computes a true success probability per action
from latent factors the AI cannot observe. It is read **only** by the outcome engine,
strictly after the decision and policy verdict are persisted. A test asserts the import
boundary statically and the absence of forbidden keys from the serialized context.

**Reproducibility.** Same seed ⇒ identical dataset, identical hidden truth, identical
outcomes. Randomness is keyed on `(seed, simulation_key, attempt)` rather than on a
shared stream, so a run reproduces even though decisions resolve concurrently — and on
a run-independent key, so a second run in the same database still reproduces the first.

---

## Measuring whether it works

Baselines are replayed over **the same opportunities** and **the same hidden truth**,
facing the same dice rolls per opportunity and attempt. The difference is decision
quality, not luck.

A representative run (1,000 customers / 2,000 payments / 1,000 checkouts / 500 subscriptions):

| Strategy | Recovered | Rate | Attempts | Wasted | Messages | ₹/attempt |
|---|---|---|---|---|---|---|
| No recovery | ₹0 | 0.0% | 0 | 0 | 0 | ₹0 |
| Always retry | ₹10,70,194 | 24.3% | 2,400 | 486 | 793 | ₹446 |
| Fixed retry | ₹18,54,106 | 42.1% | 1,502 | 324 | 875 | ₹1,234 |
| **RecoverAI** | **₹22,79,772** | **51.8%** | 1,561 | 374 | 1,126 | **₹1,460** |

**+₹4.3L (+23%) over the best baseline, at +18% revenue per attempt.**

RecoverAI spends *more* customer touches than a short static ladder — the advantage is
what each touch is worth. Both numbers are shown; neither is framed as a saving.

Prediction quality is reported honestly too: expected calibration error ≈ 5%, so when
the system says 40% it means it. "Chose the hindsight-optimal action" sits around 32% —
the best action is partly unobservable, so that ceiling is well below 100% and is
labelled as such rather than presented as accuracy.

---

## Razorpay Test Mode

Test Mode only; credentials come from the environment and never reach the browser.

```bash
RAZORPAY_KEY_ID=rzp_test_xxx
RAZORPAY_KEY_SECRET=xxx
RAZORPAY_WEBHOOK_SECRET=xxx
```

Point a webhook at `POST /api/webhooks/razorpay` and subscribe to `payment.failed`,
`payment.captured`, `order.paid`.

- **Signature verification** is HMAC-SHA256 over the *raw* request body, compared with
  `hmac.compare_digest`. An unverified event is stored and refused; it never reaches the
  pipeline.
- **Idempotency** is `UNIQUE (provider, event_id)`. A redelivered event is acknowledged
  with `200 duplicate` and dropped — a replayed `payment.captured` cannot recover the
  same money twice.
- **Attribution** travels in Razorpay `notes` (`recoverai_opportunity_ref`). A successful
  payment without that attribution is *not* counted as a recovery — it is just an
  ordinary purchase.
- **Recoveries are never faked.** A Razorpay-sourced recovery settles only when a webhook
  confirms it. Writing a row does not create revenue.

---

## Layout

```
backend/app/
  core/         config, money (paise ints), deterministic RNG, errors, logging
  domain/       enums, normalized events, state machine, AI context contract
  ingestion/    Razorpay + simulator normalizers, failure mapping, detector
  context/      observable-only context builder  ← must not see ground truth
  ai/           agent, LLM client, prompts, validation gate, heuristic strategist
  policy/       deterministic rules + engine     ← the AI cannot bypass this
  executor/     simulator + Razorpay executors
  ledger/       settlement — the only writer of recovered revenue
  pipeline/     the orchestrator that wires it together
  simulation/   config, generators, hidden ground truth, outcome engine, baselines
  analytics/    business + AI metrics, calibration
  api/routes/   30 endpoints
frontend/       Next.js 14 · App Router · Tailwind · Recharts
docs/           ARCHITECTURE.md, ACCEPTANCE.md
```

---

## Notes

- **Money** is stored and transported as integer **paise** everywhere (`*_minor`). Floats
  never touch stored amounts; the frontend does no currency arithmetic.
- **Postgres** is the production target. The test suite also runs on SQLite so it needs
  no Docker daemon — every column type carries both variants.
- **Model**: defaults to `claude-opus-5`, configurable via `AI_MODEL`. In `auto` mode the
  agent works within a per-run call budget and reuses decisions across opportunities whose
  decision-relevant features fall in the same buckets, which keeps a 2,000-opportunity run
  affordable. Set `AI_MODE=heuristic` for a fully reproducible run.
