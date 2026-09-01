# Acceptance criteria (§71)

Each criterion maps to the code that implements it and the test or screen that proves
it. Run `make test` to execute the automated column.

| # | Criterion | Implementation | Verified by |
|---|---|---|---|
| 1 | All three scenarios supported | `domain/enums.py` `SCENARIO_ACTIONS` | `test_pipeline_end_to_end.py::test_all_three_scenarios_are_supported` + one end-to-end test per scenario |
| 2 | Simulator generates realistic synthetic data | `simulation/generators/` — segment profiles, price ladder, method-consistent failure codes | `test_reproducibility.py`; distribution check in the seed output |
| 3 | Generates both successful and failed events | `generators/transactions.py` emits the whole population, not just losses | run counters `payments_succeeded/failed`, `checkouts_completed/abandoned`, `renewals_succeeded/failed` |
| 4 | Simulator contains hidden ground truth | `simulation/ground_truth.py` → `simulation_ground_truth` table | `test_outcome_engine.py` |
| 5 | AI cannot access ground truth | `context/builder.py` has no import path to it | `test_ground_truth_isolation.py` — static import-graph assertion + forbidden-key scan |
| 6 | AI produces structured recovery decisions | `ai/schema.py`, `ai/llm_client.py` (`output_config.format`) | `test_ai_validation.py` |
| 7 | Policy engine validates AI decisions | `policy/rules.py` (10 rules), `policy/engine.py` | `test_policy_engine.py` (19 tests) |
| 8 | Recovery executor handles actions | `executor/simulator.py`, `executor/base.py` | end-to-end tests per scenario |
| 9 | Successful recovery creates a ledger entry | `ledger/settlement.py` | `test_ledger_settlement.py::test_successful_recovery_settles_full_amount` |
| 10 | Failed recovery creates no revenue | settlement returns early unless outcome is SUCCESS | `test_ledger_settlement.py::test_failed_outcome_creates_no_revenue` |
| 11 | Duplicate events do not double-count | unique settlement key + partial unique index + status guard | `test_ledger_settlement.py` (4 tests), `test_webhook_idempotency.py` (3 tests) |
| 12 | Dashboard shows revenue at risk | `/` KPI tile, `analytics/metrics.py::revenue_totals` | screen |
| 13 | Dashboard shows actual recovered revenue | read only from `recovery_ledger` | screen |
| 14 | Dashboard shows recovery rate | recovered ÷ at risk | screen |
| 15 | Dashboard separates all three scenarios | scenario cards + part-to-whole chart | screen |
| 16 | Baseline comparison works | `simulation/baselines.py` — counterfactual replay on shared ground truth | `/analytics`; uplift stored on the run |
| 17 | Razorpay Test Mode integration works | `integrations/razorpay/client.py` | `/razorpay` status panel |
| 18 | Razorpay webhooks are validated | `integrations/razorpay/signature.py` (HMAC over raw body, `compare_digest`) | `test_razorpay_signature.py` (7 tests) |
| 19 | Razorpay events are idempotent | `UNIQUE (provider, event_id)`, insert-before-process | `test_webhook_idempotency.py::test_replayed_success_webhook_settles_once` |
| 20 | Every recovered amount traces to an opportunity | ledger row links opportunity + attempt + outcome + evidence | `test_ledger_settlement.py::test_ledger_entry_traces_back_to_its_evidence` |
| 21 | Simulation runs are reproducible | seed-derived per-opportunity RNG on a run-independent key | `test_reproducibility.py` (6 tests) |
| 22 | AI failure has a deterministic fallback | `ai/agent.py` → `ai/heuristic.py` | `test_ai_validation.py` — timeout, malformed JSON, invalid action, budget exhaustion |
| 23 | Polished enough to demo | 8 pages, live SSE progress, one-click demo scenarios | `/demo`, `/simulation` |

## Business rules (§61)

| Rule | Where it is enforced | Test |
|---|---|---|
| 1. AI probability is not recovered revenue | `ledger/settlement.py` reads no probability; `expected_recovery_value` is a separate field | `test_recovered_revenue_is_the_full_amount_not_amount_times_probability` |
| 2. Only successful outcomes create revenue | settlement guards on `Outcome.SUCCESS` | `test_failed_recovery_records_no_revenue` |
| 3. One opportunity contributes once | partial unique index + `RECOVERED` is absorbing in the state machine | `test_three_attempts_then_success_settle_once` |
| 4. AI cannot bypass policy | no code path AI → executor; `RECOMMENDED → EXECUTING` is not a legal transition | `test_ai_cannot_skip_policy`, `test_high_confidence_cannot_unlock_a_blocked_action` |
| 5. Permanent failures not blindly retried | rule `P04_NON_RETRYABLE_FAILURE` | `test_permanent_failures_are_never_retried` |
| 6. Razorpay events idempotent | `UNIQUE (provider, event_id)` | `test_duplicate_failure_webhook_creates_one_opportunity` |
| 7. Ground truth hidden from AI | import boundary + `extra="forbid"` context model | `test_ground_truth_isolation.py` |
| 8. Both sources use one pipeline | single `NormalizedEvent`; detector and orchestrator are source-agnostic | webhook tests exercise the same orchestrator |
| 9. Recovered revenue is traceable | ledger → attempt → outcome → evidence ref | `test_full_audit_chain_exists_for_a_recovery` |
| 10. Future value ≠ recovered revenue | `projected_retention_minor` is a separate column, never summed | `test_subscription_recovery_counts_one_renewal_only` |

## Known limits

- **Reproducibility applies to the dataset and, in `heuristic` mode, the whole run.** With
  a live model the decisions are model-dependent; the run row records `ai_mode` and each
  decision records its source, so this is visible rather than implied.
- **`action_accuracy` is measured against a hindsight optimum** that is partly
  unobservable, so its ceiling is well below 100%. It is labelled accordingly in the UI.
- **Policy thresholds set via `PUT /api/settings/policy` are per-process**, not persisted.
  They are stamped into each simulation run's config, so a run always carries the policy
  that produced it.
- **Razorpay subscription recovery** covers the events Test Mode exposes; the richer
  subscription lifecycle is exercised through the simulator.
