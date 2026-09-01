# RecoverAI — Architecture & Design Specification

> One unified AI-powered revenue recovery platform covering three recovery scenarios:
> **Failed Payment**, **Checkout Abandonment**, **Failed Subscription**.
>
> Core invariant: **AI decides what to do. The payment outcome decides whether money was recovered.**

---

## 0. Design decisions (and why)

Ambiguous choices resolved to "simplest production-quality option that preserves the architecture":

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Money stored as `BIGINT` minor units (paise)** in every `*_minor` column | Razorpay is paise-native. No float money, ever. Formatting happens in the UI only. |
| D2 | **PostgreSQL 16 via `docker-compose`**, async SQLAlchemy 2.0 + `asyncpg`, Alembic migrations | Spec mandates Postgres. JSONB is used heavily (context snapshots, ground truth, rule traces). No local psql needed. |
| D3 | **Fully async backend** | AI decisions for thousands of opportunities are network-bound; bounded-concurrency fan-out is the difference between a 3-minute and a 30-second demo. |
| D4 | **Three AI decision modes, one output schema**: `llm`, `heuristic`, `auto` (default) | `auto` = LLM with a per-run call budget + context-signature cache, deterministic heuristic engine as fallback. Every decision records `decision_source` so the dashboard never overstates how much was LLM-driven. |
| D5 | **Per-opportunity derived RNG**, not one global stream | `PRNG(seed, opportunity_ref, attempt_no)`. Makes outcomes reproducible *even under concurrent async AI calls*, where a shared global RNG would not be. |
| D6 | **Baselines by counterfactual replay** against the same hidden ground truth | Comparing RecoverAI to NO_RECOVERY / ALWAYS_RETRY / FIXED_RETRY on *the same* opportunities and *the same* latent success probabilities is the only apples-to-apples comparison. |
| D7 | **Razorpay attribution via `notes`** | Every recovery payment link / order carries `notes.recoverai_opportunity_id` + `notes.recoverai_attempt_id`. When `payment.captured` arrives, attribution is exact — not inferred from amount+timing. |
| D8 | **Settlement guarded by a partial unique index**, not application logic alone | `UNIQUE (opportunity_id) WHERE entry_type='RECOVERED'`. Double-counting becomes physically impossible at the storage layer. |
| D9 | **Ground truth lives in its own table**, is never joined into the context builder, and a test asserts this | Section 18 is a correctness property, not a convention. |
| D10 | Recovery of a **subscription counts one renewal**; future value is a separate, separately-labelled metric | `recovered_amount_minor` vs `projected_retention_minor` are never summed. |

---

## 1. System architecture

```
        ┌────────────────────┐        ┌──────────────────────────┐
        │  Razorpay Test Mode│        │  Synthetic Simulation    │
        │  webhooks + API    │        │  Engine (seeded)         │
        └─────────┬──────────┘        └────────────┬─────────────┘
                  │                                │
                  ▼                                ▼
        ┌───────────────────────────────────────────────────┐
        │  EVENT NORMALIZER  →  NormalizedEvent (one shape)  │
        └────────────────────────┬──────────────────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  OPPORTUNITY DETECTOR   │  dedupe_key → idempotent
                    │  → RecoveryOpportunity  │  status = DETECTED
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  CONTEXT BUILDER        │  observable features ONLY
                    │  → RecoveryContext      │  snapshotted to JSONB
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  AI RECOVERY AGENT      │  LLM → validated JSON
                    │  → AIDecision           │  or deterministic fallback
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  POLICY ENGINE          │  deterministic rules
                    │  → APPROVED / BLOCKED   │  AI can never bypass
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  RECOVERY EXECUTOR      │  Simulator | Razorpay
                    │  → RecoveryAttempt      │
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  OUTCOME OBSERVER       │  hidden ground truth
                    │  → RecoveryOutcome      │  or Razorpay webhook
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  RECOVERY LEDGER        │  idempotent settlement
                    │  → recovered revenue    │  ONE entry per opportunity
                    └────────────┬────────────┘
                                 ▼
                     ANALYTICS  →  DASHBOARD
```

**The one non-negotiable edge:** there is no arrow from AI Recovery Agent to Recovery Executor, and none from AI to Ledger. AI output reaches the executor only through the policy engine, and reaches the ledger never.

---

## 2. Unified domain model

### 2.1 NormalizedEvent — the single internal event shape

Both sources produce exactly this. No downstream component knows which source it came from except by reading `source`.

```jsonc
{
  "event_id":        "evt_01HX...",          // internal
  "source":          "RAZORPAY | SIMULATOR",
  "event_type":      "PAYMENT_FAILED | PAYMENT_CAPTURED | PAYMENT_AUTHORIZED |
                      ORDER_PAID | CHECKOUT_STARTED | CHECKOUT_ABANDONED |
                      CHECKOUT_COMPLETED | SUBSCRIPTION_PAYMENT_FAILED |
                      SUBSCRIPTION_RENEWED | SUBSCRIPTION_HALTED",
  "occurred_at":     "2026-08-31T10:42:15Z",
  "customer_ref":    "C001",
  "amount_minor":    500000,                 // paise
  "currency":        "INR",

  "payment_ref":     "P001",                 // scenario-specific refs, nullable
  "order_ref":       "O001",
  "checkout_ref":    "CHK001",
  "subscription_ref":"SUB001",
  "renewal_ref":     "REN001",

  "payment_method":  "upi | card | netbanking | wallet | emi",
  "failure_code":    "BANK_TIMEOUT",         // raw / gateway-level
  "failure_category":"TEMPORARY",            // normalized bucket
  "cart_value_minor": 700000,
  "product_count":   3,

  "external_ids":    { "razorpay_payment_id": "pay_...", "razorpay_order_id": "order_..." },
  "attribution":     { "opportunity_ref": "OPP001", "attempt_ref": "RA001" },  // recovery payments only
  "dedupe_key":      "RAZORPAY:payment.failed:pay_MnO123",
  "metadata":        {}
}
```

### 2.2 Failure category normalization

Raw gateway codes map into these buckets; unmapped codes fall to `UNKNOWN` (which policy treats conservatively).

| Category | Retryable? | Example raw codes |
|---|---|---|
| `TEMPORARY` | yes | `BANK_TIMEOUT`, `GATEWAY_TIMEOUT`, `ISSUER_UNAVAILABLE` |
| `NETWORK_ERROR` | yes | `NETWORK_ERROR`, `CONNECTION_RESET` |
| `TIMEOUT` | yes | `NETWORK_TIMEOUT`, `UPI_COLLECT_EXPIRED` |
| `BANK_DECLINE` | limited | `BANK_DECLINE`, `DO_NOT_HONOUR` |
| `INSUFFICIENT_FUNDS` | delayed only | `INSUFFICIENT_FUNDS` |
| `INVALID_PAYMENT_DETAILS` | **no retry** | `INVALID_CARD`, `INVALID_VPA`, `INCORRECT_CVV` |
| `EXPIRED_CARD` | **no retry** | `CARD_EXPIRED` |
| `CUSTOMER_ACTION_REQUIRED` | no blind retry | `AUTHENTICATION_FAILED`, `MANDATE_REVOKED` |
| `PERMANENT` | **no retry** | `CARD_BLOCKED`, `ACCOUNT_CLOSED`, `FRAUD_SUSPECTED` |
| `UNKNOWN` | conservative | anything unmapped |
| `ABANDONED` | n/a | checkout scenario (no gateway failure) |

### 2.3 Revenue at risk

| Scenario | Revenue at Risk | Explicitly **not** counted |
|---|---|---|
| `FAILED_PAYMENT` | amount of the eligible failed payment | prior failed attempts on the same payment |
| `CHECKOUT_ABANDONMENT` | cart value of the abandoned checkout | other carts by the same customer |
| `FAILED_SUBSCRIPTION` | **current failed renewal amount only** | future renewal months → `projected_retention_minor`, reported separately |

---

## 3. Database schema (PostgreSQL)

All tables carry `id UUID PK`, `created_at TIMESTAMPTZ`. Human-facing refs (`OPP001`, `C001`) are separate unique `*_ref` columns for demo readability.

### Core entities

**`customers`** — `customer_ref` UNIQUE, `source`, `simulation_run_id?`, `segment` (`HIGH_VALUE|REGULAR|NEW|AT_RISK|LOW_ENGAGEMENT`), `account_age_days`, `previous_transaction_count`, `previous_success_count`, `previous_failure_count`, `historical_success_rate NUMERIC(5,4)`, `average_order_value_minor`, `lifetime_value_minor`, `preferred_payment_method`, `previous_checkout_count`, `previous_checkout_conversions`, `previous_checkout_conversion_rate`, `previous_subscription_count`, `previous_subscription_failures`, `previous_recoveries`, `email`, `phone`.

**`orders`** — `order_ref` UNIQUE, `customer_id`, `amount_minor`, `currency`, `status` (`CREATED|ATTEMPTED|PAID|FAILED`), `source`, `external_id` (rzp order id), `metadata JSONB`.

**`payments`** — `payment_ref` UNIQUE, `order_id?`, `customer_id`, `amount_minor`, `currency`, `method`, `status` (`CREATED|AUTHORIZED|CAPTURED|FAILED|REFUNDED`), `failure_code`, `failure_category`, `source`, `external_id` (rzp payment id), `attempt_number`, **`is_recovery_payment BOOL`**, **`recovers_opportunity_id FK?`**, `created_at`.
> A payment is a *lifecycle*, not a terminal fact. `FAILED` is a state, not death.

**`payment_events`** — `payment_id`, `event_type`, `source`, `raw JSONB`, `occurred_at`. Full audit of state transitions.

**`checkout_sessions`** — `checkout_ref` UNIQUE, `customer_id`, `order_id?`, `cart_value_minor`, `product_count`, `products JSONB`, `started_at`, `last_activity_at`, `status` (`STARTED|ABANDONED|COMPLETED|RECOVERED`), `abandonment_reason`, `payment_method_intended`, `completed_payment_id?`, `source`, `external_id`.

**`subscriptions`** — `subscription_ref` UNIQUE, `customer_id`, `plan_id`, `plan_name`, `billing_cycle` (`MONTHLY|QUARTERLY|ANNUAL`), `amount_minor`, `currency`, `start_date`, `current_renewal_date`, `status` (`ACTIVE|PAST_DUE|GRACE_PERIOD|CANCELLED`), `renewal_count`, `previous_successful_renewals`, `previous_failed_renewals`, `payment_method`, `source`, `external_id`.

**`subscription_events`** — `renewal_ref` UNIQUE, `subscription_id`, `cycle_number`, `event_type` (`RENEWAL_ATTEMPTED|RENEWAL_SUCCESS|RENEWAL_FAILED|PAYMENT_METHOD_UPDATED|GRACE_GRANTED|HALTED`), `amount_minor`, `failure_code`, `failure_category`, **`is_recovery_renewal BOOL`**, **`recovers_opportunity_id FK?`**, `occurred_at`.

### Recovery pipeline

**`recovery_opportunities`** — the spine.

| column | notes |
|---|---|
| `opportunity_ref` | UNIQUE, `OPP001` |
| `scenario` | `FAILED_PAYMENT \| CHECKOUT_ABANDONMENT \| FAILED_SUBSCRIPTION` |
| `source`, `simulation_run_id?` | provenance |
| `customer_id` | FK |
| `payment_id? / checkout_session_id? / subscription_id? / subscription_event_id?` | typed links; exactly one branch populated |
| `amount_at_risk_minor`, `currency` | §7 rules |
| `failure_category`, `failure_code`, `reason_code` | |
| `status` | state machine (§7 below) |
| `attempt_count`, `notification_count` | policy counters |
| `recovered_amount_minor` DEFAULT 0, `recovered_at?` | written **only** by ledger settlement |
| `projected_retention_minor` DEFAULT 0 | subscriptions only, never summed into recovered |
| `context_snapshot JSONB` | exact observable context handed to the AI |
| **`dedupe_key`** | **UNIQUE** — `SOURCE:scenario:subject_ref`. Duplicate webhook ⇒ no duplicate opportunity |
| `detected_at`, `closed_at?` | |

**`ai_decisions`** — `opportunity_id`, `attempt_number`, `action`, `recovery_probability NUMERIC(5,4)`, `confidence NUMERIC(5,4)`, `reason TEXT`, `risk_level`, `decision_source` (`LLM|LLM_CACHED|HEURISTIC|HEURISTIC_FALLBACK`), `model`, `latency_ms`, `context_signature`, `raw_response JSONB`, `validation_errors JSONB?`. UNIQUE `(opportunity_id, attempt_number)`.

**`policy_decisions`** — `opportunity_id`, `ai_decision_id`, `verdict` (`APPROVED|BLOCKED|MODIFIED`), `requested_action`, `effective_action`, `blocked_by_rule`, `reason`, `rules_evaluated JSONB` (full trace: rule id → pass/fail). UNIQUE `(opportunity_id, ai_decision_id)`.

**`recovery_attempts`** — `attempt_ref` UNIQUE (`RA001`), `opportunity_id`, `attempt_number`, `action`, `ai_decision_id`, `policy_decision_id`, `executor` (`SIMULATOR|RAZORPAY|NOOP`), `execution_status` (`PENDING|EXECUTED|EXECUTION_FAILED|SKIPPED`), `external_ref` (rzp payment-link/order id), `attribution_token`, `scheduled_for?`, `executed_at?`, `cost_minor`, `error?`. UNIQUE `(opportunity_id, attempt_number)`.

**`recovery_outcomes`** — `attempt_id` **UNIQUE**, `opportunity_id`, `outcome` (`SUCCESS|FAILURE|PENDING|NO_RESPONSE`), `realized_amount_minor`, `evidence_type` (`SIMULATED_GROUND_TRUTH|RAZORPAY_WEBHOOK|MANUAL`), `evidence_ref`, `observed_at`, `raw JSONB`.

**`recovery_ledger`** — the money.

```sql
entry_type          TEXT NOT NULL,           -- 'RECOVERED'
opportunity_id      UUID NOT NULL,
outcome_id          UUID NOT NULL,
attempt_id          UUID NOT NULL,
scenario            TEXT NOT NULL,
original_amount_minor  BIGINT NOT NULL,
recovered_amount_minor BIGINT NOT NULL,
currency            TEXT NOT NULL,
source              TEXT NOT NULL,
settlement_key      TEXT NOT NULL,
settled_at          TIMESTAMPTZ NOT NULL,

CONSTRAINT uq_settlement_key UNIQUE (settlement_key);
CREATE UNIQUE INDEX uq_ledger_one_recovery_per_opportunity
  ON recovery_ledger (opportunity_id) WHERE entry_type = 'RECOVERED';
CONSTRAINT ck_recovered_not_exceeding
  CHECK (recovered_amount_minor <= original_amount_minor);
```
> Three independent guards: unique settlement key, partial unique index (§9 double-counting), and an amount ceiling. Even a buggy caller cannot inflate recovered revenue.

### Simulation & governance

**`simulation_runs`** — `run_ref` UNIQUE, `seed BIGINT`, `config JSONB`, `status` (`PENDING|RUNNING|COMPLETED|FAILED`), `progress JSONB` (stage, pct, counters), `ai_mode`, `engine_version`, `data_version`, `started_at`, `completed_at`, `results JSONB`, `error?`.

**`simulation_ground_truth`** — **restricted**. `simulation_run_id`, `opportunity_id` UNIQUE, `latent_factors JSONB`, `action_success_probs JSONB` (`{"DELAYED_RETRY":0.82,...}`), `optimal_action`, `optimal_probability`, `is_recoverable BOOL`.
> Accessed only by the outcome engine (post-decision) and post-hoc analytics. Never by the context builder. Enforced by `tests/test_ground_truth_isolation.py`.

**`baseline_results`** — `simulation_run_id`, `strategy` (`NO_RECOVERY|ALWAYS_RETRY|FIXED_RETRY|RECOVERAI`), `metrics JSONB`. UNIQUE `(simulation_run_id, strategy)`.

**`webhook_events`** — `provider`, `event_id`, `event_type`, `signature_valid BOOL`, `payload JSONB`, `processing_status` (`RECEIVED|PROCESSED|DUPLICATE|INVALID|FAILED`), `received_at`, `processed_at?`, `error?`, `opportunity_id?`. **UNIQUE `(provider, event_id)`** — the idempotency key (§5, RULE 6).

**`audit_logs`** — `entity_type`, `entity_id`, `actor` (`SYSTEM|AI|POLICY|EXECUTOR|WEBHOOK|USER|SIMULATOR`), `action`, `detail JSONB`, `created_at`. Never contains secrets.

### Key relationships

```
customer 1─┬─* orders ──* payments ──* payment_events
           ├─* checkout_sessions
           └─* subscriptions ──* subscription_events

payment/checkout/subscription_event ─1:1─ recovery_opportunity (via dedupe_key)

recovery_opportunity 1─* ai_decisions
                     1─* policy_decisions
                     1─* recovery_attempts 1─1 recovery_outcomes
                     1─0..1 recovery_ledger (RECOVERED)      ← at most one
                     1─0..1 simulation_ground_truth (hidden)
```

---

## 4. Recovery opportunity state machine

```
                    ┌──────────┐
                    │ DETECTED │
                    └────┬─────┘
                         ▼
                   ┌───────────┐
                   │ ANALYZING │  context built, AI called
                   └────┬──────┘
                        ▼
                  ┌─────────────┐
                  │ RECOMMENDED │  validated AIDecision exists
                  └──┬───────┬──┘
            approved │       │ blocked
                     ▼       ▼
              ┌──────────┐  ┌─────────┐
              │ APPROVED │  │ BLOCKED │ (terminal for this attempt)
              └────┬─────┘  └────┬────┘
                   ▼             │ retry allowed? no → EXHAUSTED
             ┌───────────┐       │
             │ EXECUTING │       │
             └─────┬─────┘       │
        ┌──────────┴─────────┐   │
        ▼                    ▼   │
  ┌───────────┐        ┌────────┐│
  │  SUCCESS  │        │ FAILED ├┘  attempt failed → back to ANALYZING
  └─────┬─────┘        └────┬───┘   if attempts remain
        ▼                   │
  ┌───────────┐             ├──► EXHAUSTED  (max attempts / policy STOP)
  │ RECOVERED │ ◄── ledger  └──► EXPIRED    (opportunity aged out)
  └───────────┘   settlement
```

Legal transitions are declared in one table (`domain/state_machine.py`); every write goes through `transition(opportunity, to_state)` which raises on an illegal move. `RECOVERED` is **terminal and absorbing** — nothing can leave it, which is the state-machine half of the anti-double-count guarantee.

Terminal states: `RECOVERED`, `EXHAUSTED`, `EXPIRED`, `BLOCKED` (when no attempts remain).

---

## 5. AI contract

### 5.1 Input — `RecoveryContext` (observable only)

Everything here is derivable from what a real merchant would actually know at decision time.

```jsonc
{
  "opportunity_ref": "OPP001",
  "scenario": "FAILED_PAYMENT",
  "amount_at_risk": 5000.00, "currency": "INR",
  "amount_percentile_vs_customer_aov": 1.4,

  "failure": { "category": "TEMPORARY", "code": "BANK_TIMEOUT", "is_retryable_class": true },

  "customer": {
    "segment": "HIGH_VALUE", "account_age_days": 412,
    "previous_transaction_count": 37, "historical_success_rate": 0.92,
    "average_order_value": 4200.0, "lifetime_value": 155400.0,
    "preferred_payment_method": "upi", "previous_recoveries": 2
  },
  "payment": { "method": "upi", "attempt_number": 1 },

  "checkout":     { "cart_value": 7000.0, "product_count": 3, "minutes_since_abandonment": 45,
                    "previous_checkout_count": 8, "previous_checkout_conversion_rate": 0.62,
                    "has_previously_converted": true },
  "subscription": { "amount": 999.0, "billing_cycle": "MONTHLY", "subscription_age_days": 210,
                    "renewal_count": 7, "previous_successful_renewals": 6,
                    "previous_failed_renewals": 1, "status": "PAST_DUE" },

  "recovery_history": {
    "attempt_count": 0, "notification_count": 0,
    "previous_actions": [], "previous_outcomes": [],
    "hours_since_detection": 0.2
  },
  "allowed_actions": ["IMMEDIATE_RETRY","DELAYED_RETRY","PAYMENT_LINK",
                      "ALTERNATE_PAYMENT_METHOD","CUSTOMER_NOTIFICATION","STOP"]
}
```

Scenario-irrelevant blocks are omitted. **Absent by construction:** `true_probability`, `optimal_action`, `action_success_probs`, `is_recoverable`, any outcome. The context builder has no import path to the ground-truth module.

### 5.2 Output — `AIDecision`

```jsonc
{
  "action": "DELAYED_RETRY",
  "recovery_probability": 0.82,
  "confidence": 0.91,
  "reason": "Temporary bank timeout with a 92% historical success rate and no prior recovery attempt.",
  "risk_level": "LOW"
}
```

### 5.3 Validation gate (§49) — reject before policy ever sees it

1. Parses as JSON matching the schema.
2. `action ∈ allowed_actions` **for that scenario** (a checkout opportunity may not return `RETRY_SUBSCRIPTION`).
3. `0 ≤ recovery_probability ≤ 1`, `0 ≤ confidence ≤ 1`.
4. `risk_level ∈ {LOW, MEDIUM, HIGH}`.
5. `reason` non-empty, ≤ 400 chars, no chain-of-thought dump.

Any failure → record `validation_errors` → **deterministic fallback**, never a crash.

### 5.4 Fallback ladder (§48)

`LLM` → (timeout / invalid JSON / failed validation / budget exhausted) → `HEURISTIC_FALLBACK`:

| Condition | Fallback action |
|---|---|
| `PERMANENT`, `EXPIRED_CARD`, `INVALID_PAYMENT_DETAILS` | `STOP` |
| `TEMPORARY` / `NETWORK_ERROR` / `TIMEOUT`, attempt 1 | `DELAYED_RETRY` |
| `INSUFFICIENT_FUNDS` | `DELAYED_RETRY` (attempt 1) → `PAYMENT_LINK` |
| `CUSTOMER_ACTION_REQUIRED` | `PAYMENT_UPDATE_REQUEST` (sub) / `PAYMENT_LINK` |
| Checkout, high value + converted before | `PAYMENT_LINK` |
| Checkout, low value or attempt ≥ 2 | `REMINDER` → `DISCOUNT_INCENTIVE` → `STOP` |
| Subscription, card-class failure | `PAYMENT_UPDATE_REQUEST` |
| anything unmapped | `CUSTOMER_NOTIFICATION` then `STOP` |

The platform never stops because the AI did.

---

## 6. Policy engine (deterministic, AI cannot bypass)

Rules are pure functions `(opportunity, context, ai_decision) → RuleResult`, evaluated in order, all results recorded in `rules_evaluated` for the audit trail.

| id | Rule | Verdict on trip |
|---|---|---|
| `P01` | Opportunity already `RECOVERED` | **BLOCK** (`ALREADY_RECOVERED`) |
| `P02` | Opportunity terminal (`EXHAUSTED`/`EXPIRED`) | BLOCK |
| `P03` | `attempt_count >= max_attempts` (default 3) | BLOCK (`MAX_ATTEMPTS`) |
| `P04` | Retry action on `PERMANENT` / `EXPIRED_CARD` / `INVALID_PAYMENT_DETAILS` | BLOCK (`NON_RETRYABLE_FAILURE`) |
| `P05` | `IMMEDIATE_RETRY` when a retry already failed for the same cause | BLOCK (`REPEATED_RETRY`) |
| `P06` | `notification_count >= max_notifications` (default 2) | BLOCK (`NOTIFICATION_FATIGUE`) |
| `P07` | Action not permitted for this scenario | BLOCK (`ACTION_SCENARIO_MISMATCH`) |
| `P08` | `DISCOUNT_INCENTIVE` above `max_discount_value` without approval | BLOCK (`HIGH_RISK_REQUIRES_APPROVAL`) |
| `P09` | Cooldown not elapsed since last attempt | BLOCK (`COOLDOWN`) |
| `P10` | `action == STOP` | APPROVED-as-STOP → `EXHAUSTED` (no execution) |
| `P11` | Ledger already holds a `RECOVERED` entry | BLOCK (`DUPLICATE_SETTLEMENT`) |
| `P12` | `risk_level == HIGH` and `confidence < 0.5` | BLOCK (`LOW_CONFIDENCE_HIGH_RISK`) |

Thresholds live in config, are shown on `/settings`, and are stamped into `simulation_runs.config` for reproducibility.

---

## 7. Simulation model

### 7.1 Generation pipeline (seeded, deterministic)

1. **Customers** — segment mixture (`HIGH_VALUE 10% / REGULAR 45% / NEW 20% / AT_RISK 15% / LOW_ENGAGEMENT 10%`), each segment driving distributions for account age, historical success rate, AOV, LTV, preferred method, checkout conversion, subscription history. History is generated *first* so the AI sees a coherent past.
2. **Amounts** — sampled from a realistic price ladder (₹199 / ₹499 / ₹999 / ₹1,499 / ₹2,499 / ₹4,999 / ₹7,999 / ₹9,999 / ₹19,999) weighted by customer segment, with configurable min/max clamping — not `uniform(100, 20000)`.
3. **Payments** — `success_rate` default 0.70. Failure draws a `failure_category` from a configurable distribution, then a raw `failure_code` consistent with that category and the payment method (UPI can't produce `CARD_EXPIRED`).
4. **Checkouts** — default 70% completed / 30% abandoned, with abandonment reason and a time-since-abandonment distribution.
5. **Subscriptions** — default 85% renewed / 15% failed, with subscription age, renewal counts and prior failure history.
6. **Ground truth** — computed per failed/abandoned item (§7.2) and written to the restricted table.

Correlation is the point: an `AT_RISK` customer with a low historical success rate is *more likely* to fail **and** has genuinely lower recovery probabilities. A model that just guesses "retry" cannot beat one that reads context.

### 7.2 Hidden ground truth

For each opportunity, latent factors (customer reliability, failure persistence, urgency, price sensitivity — none observable) produce a **true success probability per action**:

```
p(action) = clamp( base[scenario][failure_category][action]
                   × customer_reliability_multiplier
                   × attempt_decay(attempt_number)
                   × amount_sensitivity(action, amount)
                   × timing_multiplier(action, hours_since_event),
                   0.0, 0.97 )
```

Example (`FAILED_PAYMENT` / `BANK_TIMEOUT`, reliable customer): `IMMEDIATE_RETRY 0.45`, `DELAYED_RETRY 0.82`, `PAYMENT_LINK 0.61`, `ALTERNATE_PAYMENT_METHOD 0.55`, `CUSTOMER_NOTIFICATION 0.30`, `STOP 0.00`.

Some opportunities are drawn as **truly unrecoverable** (`is_recoverable=false` ⇒ all probabilities 0) so that `STOP` is genuinely the right answer sometimes — otherwise "always retry" would be an unbeatable strategy and the benchmark would be meaningless.

### 7.3 Outcome engine

```python
rng = derive_rng(seed, opportunity_ref, attempt_number)   # D5: concurrency-safe
p   = ground_truth.action_success_probs[executed_action]
outcome = SUCCESS if rng.random() < p else FAILURE
```

Ground truth is read **only here**, strictly after the AI decision and policy verdict are persisted.

### 7.4 Reproducibility guarantee (stated precisely)

- Same `seed` + same `config` ⇒ **bit-identical generated dataset and ground truth**, always.
- Same `seed` + same config + `ai_mode=heuristic` ⇒ **the entire run is identical**, including every decision, outcome and metric.
- With `ai_mode=llm`, decisions are model-dependent, so runs may differ; the run row records `ai_mode`, `model` and per-decision `decision_source` so this is visible rather than implied. Outcome draws remain seed-derived per opportunity, so identical decisions still yield identical outcomes.

### 7.5 Baselines (§36) — counterfactual replay

Replayed over the same opportunities and the same ground truth:

| Strategy | Behaviour |
|---|---|
| `NO_RECOVERY` | never acts; recovered = ₹0 |
| `ALWAYS_RETRY` | `IMMEDIATE_RETRY` up to max attempts, ignores failure category (burns attempts on permanent failures) |
| `FIXED_RETRY` | static rule: retry once, then one notification, then stop |
| `RECOVERAI` | actual AI + policy pipeline |

Compared on: recovered revenue, recovery rate, attempts, unnecessary attempts (actions on unrecoverable opportunities), customer notifications sent, blocked actions, avg attempts per recovery, recovery efficiency (₹ recovered / attempt).

---

## 8. API contract

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/simulation/run` | start a run (async); returns `run_id` |
| `GET` | `/api/simulation/{id}` | run status, progress, config, results |
| `GET` | `/api/simulation/{id}/report` | full report (§56) incl. baselines & uplift |
| `GET` | `/api/simulation/{id}/stream` | **SSE** live progress for the control centre |
| `GET` | `/api/simulation` | list runs |
| `GET` | `/api/dashboard/summary` | unified KPIs (§27) |
| `GET` | `/api/dashboard/scenarios` | per-scenario breakdown (§28) |
| `GET` | `/api/dashboard/activity` | live activity feed (§31) |
| `GET` | `/api/dashboard/pipeline` | pipeline funnel counts (§30) |
| `GET` | `/api/opportunities` | filter: scenario, status, source, customer, q; paginated |
| `GET` | `/api/opportunities/{id}` | full lifecycle: context, AI, policy, attempts, outcomes, ledger, timeline (§32/§45/§59) |
| `POST` | `/api/opportunities/{id}/recover` | run one more pipeline cycle manually |
| `GET` | `/api/recovery/metrics` | AI performance + business metrics (§37/§38) |
| `GET` | `/api/recovery/attempts` | attempt log |
| `GET` | `/api/analytics/baselines` | baseline comparison table |
| `GET` | `/api/analytics/calibration` | predicted vs realized bucket curve |
| `POST` | `/api/webhooks/razorpay` | signature-verified, idempotent ingest |
| `GET` | `/api/razorpay/status` | keys configured?, webhook seen?, recent events |
| `POST` | `/api/razorpay/test-payment` | create a Test Mode order/link for the demo |
| `POST` | `/api/demo/failed-payment` | scripted §43 scenario |
| `POST` | `/api/demo/checkout-abandonment` | scripted scenario |
| `POST` | `/api/demo/subscription-failure` | scripted scenario |
| `POST` | `/api/demo/reset` | clear demo data |
| `GET`/`PUT` | `/api/settings/policy` | view/update policy thresholds |
| `GET` | `/api/health` | liveness + DB + AI + Razorpay readiness |

Envelope: `{ "data": ..., "meta": {...} }`; errors `{ "error": { "code", "message", "details" } }`. Money crosses the wire as **both** `amount_minor` (integer paise, authoritative) and a preformatted `amount_display`.

---

## 9. Folder structure

```
RecoverAI/
├── docker-compose.yml            # postgres + backend + frontend
├── Makefile                      # make up / migrate / seed / test / dev
├── .env.example
├── docs/ARCHITECTURE.md
├── backend/
│   ├── pyproject.toml
│   ├── alembic/versions/
│   └── app/
│       ├── main.py
│       ├── core/            config.py  logging.py  errors.py  ids.py  money.py  rng.py  security.py
│       ├── db/              session.py  base.py  models/*.py
│       ├── domain/          enums.py  events.py  state_machine.py  context.py
│       ├── ingestion/       normalizer_razorpay.py  normalizer_simulator.py  detector.py
│       ├── context/         builder.py  features.py
│       ├── ai/              agent.py  llm_client.py  prompts.py  validation.py  heuristic.py  cache.py
│       ├── policy/          engine.py  rules.py
│       ├── executor/        base.py  simulator.py  razorpay.py  registry.py
│       ├── ledger/          settlement.py
│       ├── pipeline/        orchestrator.py
│       ├── simulation/      config.py  generators/  ground_truth.py  outcome.py  runner.py  baselines.py
│       ├── analytics/       metrics.py  calibration.py  reports.py
│       ├── integrations/razorpay/  client.py  signature.py  webhooks.py
│       ├── api/routes/      dashboard.py  opportunities.py  simulation.py  recovery.py
│       │                    razorpay.py  demo.py  analytics.py  settings.py  health.py
│       └── services/        opportunity_service.py  customer_service.py
│   └── tests/               unit/  integration/  (see §12)
└── frontend/
    ├── app/  (dashboard) (opportunities) (opportunities/[id]) (simulation)
    │         (analytics) (razorpay) (settings) (demo)
    ├── components/  kpi/  charts/  pipeline/  tables/  timeline/  ui/
    └── lib/         api.ts  format.ts  types.ts  hooks/
```

Business logic never lives in a frontend component; the frontend renders API responses.

---

## 10. Razorpay Test Mode integration

- Credentials from env only: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`. Never hard-coded, never sent to the frontend (`/api/razorpay/status` returns booleans and a masked key id).
- **Signature verification**: HMAC-SHA256 over the *raw* request body vs `X-Razorpay-Signature`, compared with `hmac.compare_digest`. Invalid ⇒ stored with `signature_valid=false`, `403`, no pipeline execution.
- **Idempotency**: insert into `webhook_events (provider, event_id)` first; a unique violation returns `200 duplicate` and stops. Replaying the same `payment.captured` twice cannot settle twice.
- Handled: `payment.failed`, `payment.authorized`, `payment.captured`, `order.paid`, and subscription events when available.
- **Recovery via Razorpay is never faked.** A Razorpay-sourced recovery settles only when a webhook confirms a successful payment carrying `notes.recoverai_opportunity_id`. Writing a row in the DB does not create recovered revenue.

---

## 11. Implementation phases

| Phase | Deliverable | Verified by |
|---|---|---|
| 1 | Scaffold, config, Docker, DB models, migrations, health | `make up`, migration applies, `/api/health` green |
| 2 | Domain enums, normalized events, state machine, detector, opportunities + ledger settlement | unit tests: state machine, double-count |
| 3 | Simulation generators + hidden ground truth + outcome engine | reproducibility test, ground-truth isolation test |
| 4 | AI agent: heuristic engine, LLM client, validation, cache, fallback | validation + fallback tests |
| 5 | Policy engine + rules | rule tests incl. retry cap & permanent-failure block |
| 6 | Executors (simulator + Razorpay) + pipeline orchestrator | end-to-end single-opportunity test |
| 7 | Simulation runner for all three scenarios + SSE progress | full run of 1000 customers |
| 8 | Analytics, baselines, reports, metrics | baseline comparison test |
| 9 | Razorpay Test Mode + webhooks + idempotency | signature + duplicate-webhook tests |
| 10 | Frontend: dashboard, opportunities, detail, simulation, analytics, razorpay, settings | manual walkthrough |
| 11 | Demo mode + polish + seed script | §65 demo flow rehearsal |

---

## 12. Test plan (§70)

`payment failure detection` · `checkout abandonment detection` · `subscription failure detection` · `AI output validation (bad action / out-of-range / non-JSON)` · `AI fallback on timeout` · `policy: max retries` · `policy: permanent failure block` · `policy: notification fatigue` · `policy: already-recovered block` · `recovery attribution (payment→checkout, renewal→subscription)` · `duplicate webhook handling` · `duplicate recovery outcome handling` · `recovered revenue never exceeds amount at risk` · `three failed attempts then success settles once` · `simulation reproducibility (same seed ⇒ same dataset)` · `ground truth never reaches AI context` · `baseline comparison ordering` · `state machine illegal transitions`.

---

## 13. Acceptance criteria mapping

Every item in §71 maps to a test or a screen; the table is maintained in `docs/ACCEPTANCE.md` as phases land.
