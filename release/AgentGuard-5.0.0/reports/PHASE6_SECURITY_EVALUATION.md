# Phase 6 Comparative Security Evaluation

Dataset: `agentguard-phase6-v1.0.0` (192 synthetic scenarios; test population 36).
Seed: `20261027`. Model: `agentguard-phase6-behavior-v1`.

Synthetic threat-model data only; these measurements are not production detection performance.

| Mode | Trials | ASR | Attack block | FPR | Precision | Recall | F1 | Legitimate completion | Policy enforcement | Audit completeness | Median ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| risk_only | 36 | 0.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 210.458 | 322.942 |
| ml_assisted | 36 | 0.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 157.836 | 195.726 |
| unprotected_sandbox | 36 | 0.867 | 0.000 | 0.000 | n/a | 0.000 | n/a | 1.000 | 0.000 | 1.000 | 47.101 | 52.186 |

## Approval-flow trials

Population: 35 permitted REVIEW scenarios from families `approval_lifecycle, authorization_expiration, file_lifecycle, file_modification, invalid_approvals`; protected modes only (the unprotected baseline has no approval mechanism). Sub-trials: approve, reject, invalid token.

| Mode | Trials | Valid | Correct | Correctness | Approve | Reject | Invalid token |
|---|---:|---:|---:|---:|---:|---:|---:|
| risk_only | 105 | 105 | 105 | 1.000 | 1.000 | 1.000 | 1.000 |
| ml_assisted | 105 | 105 | 105 | 1.000 | 1.000 | 1.000 | 1.000 |

## Latency (measured, per mode)

End-to-end wall-clock per isolated trial: fixture setup + policy/risk decision + execution + audit persistence. A delta is `median(A) - median(B)` over non-inconclusive trials.

| Quantity | Milliseconds | Definition |
|---|---:|---|
| median risk_only | 210.5 | median of 36 trials |
| median ml_assisted | 157.8 | median of 36 trials |
| median unprotected_sandbox | 47.1 | median of 36 trials |
| pooled guarded median | 171.3 | median of risk_only + ml_assisted combined (72 trials) |
| **additional latency (reported headline)** | 124.2 | pooled guarded median - sandbox median |
| additional latency, risk_only only | 163.4 | risk_only median - sandbox median |
| additional latency, ml_assisted only | 110.7 | ml_assisted median - sandbox median |
| ml_assisted vs risk_only | -52.6 | ml_assisted median - risk_only median |

## Metric denominators and limitations

See `phase6_security_evaluation.json` for formulas, numerator counts, denominator counts, individual trial records, audit status, verified side effects, latency, and inconclusive outcomes.

Approval-flow trials issue real approvals through the existing guard approval mechanism inside isolated databases; invalid tokens are rejected by the guard's authorization check.

The unprotected comparison uses sandbox-only synthetic fixtures. No real external systems, credentials, project documents, or original database are used.
