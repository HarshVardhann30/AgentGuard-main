# Phase 6 ML Evaluation

Model: `agentguard-phase6-behavior-v1` (selected: `random_forest`).
Dataset: `agentguard-phase6-v1.0.0` (192 scenarios, sha256 `72a7ba2080a4b178…`), seed `20261027`.
Split: predeclared scenario-family/template groups; families never cross partitions; counts {'train': 120, 'validation': 36, 'test': 36}; class counts {'train': {'permitted': 70, 'prohibited': 50}, 'validation': {'permitted': 21, 'prohibited': 15}, 'test': {'permitted': 21, 'prohibited': 15}}.
Versions: Python 3.14.6, scikit-learn 1.9.1, numpy 2.5.4, joblib 1.6.0.
Threshold: fixed classifier cut 0.5; no post-hoc security-policy tuning. Preprocessing: median imputer inside estimator pipeline; fit on train partition only.

Synthetic threat-model scenarios only; these are not production detection results.

## Held-out metrics

| Split | Model | Support | Precision | Recall | F1 | FPR | FNR | PR-AUC | TN/FP/FN/TP |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| validation | logistic_regression | 36 | 1.0000 | 0.3333 | 0.5000 | 0.0000 | 0.6667 | 0.7018 | 21/0/10/5 |
| validation | random_forest | 36 | 1.0000 | 0.3333 | 0.5000 | 0.0000 | 0.6667 | 0.7018 | 21/0/10/5 |
| validation | existing_risk_rules | 36 | 1.0000 | 0.3333 | 0.5000 | 0.0000 | 0.6667 | 0.6111 | 21/0/10/5 |
| test | logistic_regression | 36 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 21/0/0/15 |
| test | random_forest | 36 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 21/0/0/15 |
| test | existing_risk_rules | 36 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 21/0/0/15 |

## Calibration (probability outputs)

| Split | Model | Brier | Log loss | ECE (10 bins) |
|---|---|---:|---:|---:|
| validation | logistic_regression | 0.1919 | 0.5538 | 0.1645 |
| validation | random_forest | 0.2224 | 0.6592 | 0.2254 |
| test | logistic_regression | 0.0385 | 0.1820 | 0.1576 |
| test | random_forest | 0.0105 | 0.0841 | 0.0781 |

Expected calibration error is reported as measured. The probabilities are **not** claimed to be calibrated; no calibration method was fitted or validated.

## Model selection and conclusion

Selection rule: highest validation F1, then validation PR-AUC, using the validation split only. Selected: `random_forest`.
On this dataset neither Logistic Regression nor Random Forest outperforms the existing deterministic risk benchmark: all three tie on the validation split (F1 0.5000) and on the held-out test split (F1 1.0000, 36 scenarios). The dataset therefore does not demonstrate an ML advantage over the existing rules; the candidate model is integrated only as an additional signal that cannot override mandatory policy.

## Split independence and why validation differs from test

Splitting unit: whole scenario families. Train 10 families, validation 3 independent families (`agentguard_bypass, invalid_approvals, authorization_expiration`), test 3 independent families (`protected_paths, path_traversal, sensitive_data`). The 100/30/30 scenario-group counts are attack-template variants *within* those families, not 160 independent samples; generalization claims rest on 3 held-out test families (36 scenarios).
- `validation`: 10 of 15 prohibited scenarios have feature vectors identical to a permitted sibling, so no features-only classifier can exceed recall 0.33 there. Validation recall is therefore bounded by those label conflicts, while test targets are separable by simple features already present in training (sensitive names, traversal tokens) — the two splits are not comparable difficulty.
- `test`: 0 of 15 prohibited scenarios have feature vectors identical to a permitted sibling, so no features-only classifier can exceed recall 1.00 there. On this split the bound is not active: every prohibited target is trivially separable by simple features that also appear in training, which is why all three approaches score 1.0000 here and why this small split cannot support a generalization claim.

Perfect test scores on 36 authored scenarios do **not** establish generalization to unseen attack families; they only show that the existing rules and both learners behave identically on these templates.

## Limitations

- Scenarios are authored and synthetic; the test split covers three scenario families (36 trials).
- The validation split (36 scenarios) was used for model selection; test metrics are held out by scenario family.
- Feature vectors are captured at scenario creation time from request data and prior-history slots; no labels, decisions, or outcomes are features.

Raw metrics: `ml/artifacts/phase6/evaluation.json`. Reproduce with `python -m ml.phase6_pipeline train` then `python -m ml.phase6_pipeline evaluate`.
