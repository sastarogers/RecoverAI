import type { Money } from "./format";

export type Envelope<T> = { data: T; meta?: Record<string, unknown> };

export type Totals = {
  revenue_at_risk: Money;
  recovered_revenue: Money;
  recovery_rate: number;
  opportunities: number;
  opportunities_recovered: number;
  /** An AI artifact — deliberately separate from recovered_revenue (§39). */
  expected_recovery_value: Money;
  /** Subscription future value — never summed into recovered revenue (RULE 10). */
  projected_retention_value: Money;
};

export type Funnel = {
  opportunities_detected: number;
  ai_decisions: number;
  policy_approved: number;
  policy_blocked: number;
  recovery_attempts: number;
  successful_recoveries: number;
  failed_attempts: number;
  recovered: number;
  by_status: Record<string, number>;
};

export type ScenarioRow = {
  scenario: string;
  revenue_at_risk: Money;
  recovered_revenue: Money;
  recovery_rate: number;
  opportunities: number;
  opportunities_recovered: number;
  projected_retention: Money;
};

export type DashboardSummary = {
  totals: Totals;
  funnel: Funnel;
  scenarios: ScenarioRow[];
};

export type OpportunityRow = {
  id: string;
  opportunity_ref: string;
  scenario: string;
  source: string;
  customer_ref: string | null;
  customer_name: string | null;
  customer_segment: string | null;
  amount_at_risk: Money;
  recovered_amount: Money;
  projected_retention: Money;
  failure_category: string | null;
  failure_code: string | null;
  reason_code: string | null;
  status: string;
  attempt_count: number;
  notification_count: number;
  detected_at: string;
  recovered_at: string | null;
  closed_at: string | null;
  currency: string;
};

export type AIDecision = {
  id: string;
  sequence: number;
  attempt_number: number;
  action: string;
  recovery_probability: number;
  confidence: number;
  reason: string;
  risk_level: string;
  decision_source: string;
  model: string | null;
  latency_ms: number | null;
  validation_errors: string[] | null;
  created_at: string;
};

export type PolicyRule = { rule_id: string; passed: boolean; message: string };

export type PolicyDecision = {
  id: string;
  ai_decision_id: string;
  verdict: string;
  requested_action: string;
  effective_action: string | null;
  blocked_by_rule: string | null;
  reason: string;
  rules_evaluated: PolicyRule[];
  created_at: string;
};

export type AttemptOutcome = {
  outcome: string;
  realized_amount: Money;
  evidence_type: string;
  evidence_ref: string | null;
  observed_at: string;
};

export type Attempt = {
  id: string;
  attempt_ref: string;
  attempt_number: number;
  action: string;
  ai_decision_id: string | null;
  policy_decision_id: string | null;
  executor: string;
  execution_status: string;
  external_ref: string | null;
  executed_at: string | null;
  error: string | null;
  outcome: AttemptOutcome | null;
};

export type LedgerEntry = {
  id: string;
  entry_type: string;
  scenario: string;
  original_amount: Money;
  recovered_amount: Money;
  action: string | null;
  attempt_number: number | null;
  source: string;
  settlement_key: string;
  settled_at: string;
};

export type TimelineEvent = {
  at: string;
  actor: string;
  title: string;
  detail: Record<string, unknown>;
};

export type OpportunityDetail = OpportunityRow & {
  customer: {
    customer_ref: string;
    name: string | null;
    segment: string;
    account_age_days: number;
    historical_success_rate: number;
    previous_transaction_count: number;
    average_order_value: Money;
    lifetime_value: Money;
    preferred_payment_method: string | null;
    previous_recoveries: number;
  };
  context_snapshot: Record<string, unknown>;
  ai_decisions: AIDecision[];
  policy_decisions: PolicyDecision[];
  attempts: Attempt[];
  ledger: LedgerEntry[];
  timeline: TimelineEvent[];
};

export type ActivityEntry = {
  id: string;
  entity_type: string;
  entity_id: string | null;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  occurred_at: string;
};

export type BaselineStrategy = {
  strategy: string;
  revenue_at_risk: Money;
  recovered_revenue: Money;
  recovery_rate: number;
  opportunities: number;
  opportunities_recovered: number;
  attempts: number;
  unnecessary_attempts: number;
  customer_notifications: number;
  avg_attempts_per_recovery: number;
  recovery_efficiency: Money;
  per_scenario: Record<string, Record<string, number>>;
};

export type Uplift = {
  best_baseline?: string;
  best_baseline_recovered_minor?: number;
  recoverai_recovered_minor?: number;
  uplift_minor?: number;
  uplift_percent?: number | null;
  attempt_delta?: number;
  unnecessary_attempt_delta?: number;
  notification_delta?: number;
  efficiency_minor?: number;
  best_baseline_efficiency_minor?: number;
  efficiency_uplift_percent?: number | null;
};

export type SimulationRun = {
  id: string;
  run_ref: string;
  seed: number;
  label: string | null;
  status: string;
  config: Record<string, unknown>;
  progress: {
    stage?: string;
    stage_index?: number;
    total_stages?: number;
    percent?: number;
    message?: string;
    counters?: Record<string, number>;
  };
  results: Record<string, unknown>;
  ai_mode: string;
  ai_model: string | null;
  engine_version: string;
  data_version: string;
  policy_snapshot: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  error: string | null;
  created_at: string;
};

export type AIPerformance = {
  decisions: number;
  action_accuracy: number | null;
  average_predicted_probability: number | null;
  actual_recovery_rate: number | null;
  calibration_error: number | null;
  brier_score: number | null;
  unnecessary_action_rate: number | null;
  decision_sources: Record<string, number>;
  stop_decisions?: number;
};

export type CalibrationPoint = {
  bucket: string;
  predicted: number;
  actual: number | null;
  count: number;
};

export type RazorpayStatus = {
  integration: {
    configured: boolean;
    webhook_configured: boolean;
    enabled: boolean;
    reachable: boolean | null;
    key_id: string | null;
    mode: string;
    error: string | null;
  };
  webhook: {
    endpoint: string;
    events_received: number;
    last_event_at: string | null;
    signature_verification: string;
  };
  recent_events: {
    id: string;
    event_id: string;
    event_type: string | null;
    signature_valid: boolean;
    status: string;
    received_at: string;
    error: string | null;
  }[];
  live_opportunities: OpportunityRow[];
};

export type DemoResult = {
  opportunity: OpportunityDetail;
  narrative: {
    step: number;
    action: string | null;
    decision_source: string | null;
    policy_verdict: string | null;
    blocked_by_rule: string | null;
    outcome: string | null;
    recovered: Money;
    reason: string;
  }[];
  headline: string;
};
