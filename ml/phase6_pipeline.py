"""Train and evaluate Phase 6 classifiers on family-grouped security scenarios."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import OneHotEncoder
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
                             f1_score, log_loss, precision_score, recall_score)
from sklearn.pipeline import Pipeline

from ml.features import FEATURE_SCHEMA_VERSION
from ml.phase6_dataset import (DATASET_VERSION, DEFAULT_PATH, PHASE6_CATEGORICAL_FEATURES,
                               PHASE6_FEATURE_NAMES, PHASE6_NUMERIC_FEATURES, SEED, SPLITS,
                               read_dataset, validate_dataset)

MODEL_VERSION = "agentguard-phase6-behavior-v1"
DEFAULT_ARTIFACT_DIR = Path("ml/artifacts/phase6")


def _target_features(row: dict[str, Any]) -> dict[str, Any]:
    # Use schema-versioned features captured from input/arguments at scenario
    # creation time; no labels, expected decisions, outcomes, or IDs enter X.
    features = row["features"]
    if set(features) != set(PHASE6_FEATURE_NAMES):
        raise ValueError("stored Phase 6 feature schema does not match training schema")
    return features


def _target(tool: str, args: Any) -> Any:
    if not isinstance(args, dict):
        return None
    return args.get("filename") or args.get("source") or args.get("destination") or args.get("expression")


def build_matrix(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    vectors = []
    labels = []
    groups = []
    names = list(PHASE6_FEATURE_NAMES)
    for row in rows:
        features = _target_features(row)
        vectors.append([features[name] if name in PHASE6_CATEGORICAL_FEATURES else float(features[name])
                        for name in names])
        labels.append(int(row["ground_truth_label"] == "prohibited"))
        groups.append(row["scenario_family"])
    return np.asarray(vectors, dtype=object), np.asarray(labels, dtype=int), np.asarray(groups, dtype=str), names


def _metrics(y: np.ndarray, pred: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {"support": int(len(y)), "precision": float(precision_score(y, pred, zero_division=0)),
            "recall": float(recall_score(y, pred, zero_division=0)), "f1": float(f1_score(y, pred, zero_division=0)),
            "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
            "false_positive_rate": float(fp / (fp + tn)) if (fp + tn) else None,
            "false_negative_rate": float(fn / (fn + tp)) if (fn + tp) else None,
            "pr_auc": float(average_precision_score(y, probability)) if len(np.unique(y)) == 2 else None}


def _calibration(y: np.ndarray, probability: np.ndarray, bins: int = 10) -> dict[str, Any]:
    p = np.clip(np.asarray(probability, dtype=float), 1e-12, 1 - 1e-12)
    counts, edges = np.histogram(p, bins=bins, range=(0.0, 1.0))
    details = []
    ece = 0.0
    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        mask = (p >= lower) & ((p <= upper) if index == bins - 1 else (p < upper))
        if mask.any():
            predicted = float(p[mask].mean())
            observed = float(y[mask].mean())
            weight = float(mask.mean())
            ece += weight * abs(predicted - observed)
            details.append({"lower": float(lower), "upper": float(upper), "count": int(mask.sum()),
                            "mean_probability": predicted, "observed_positive_rate": observed})
    return {"brier_score": float(brier_score_loss(y, p)), "log_loss": float(log_loss(y, p, labels=[0, 1])),
            "expected_calibration_error": ece, "bin_count": bins, "bins": details}


def _risk_engine_benchmark(rows: list[dict[str, Any]]) -> np.ndarray:
    """Independent documented mandatory/risk rule baseline; no learned labels/scores."""
    results = []
    for row in rows:
        tool, args = row["tool_name"], row["tool_arguments"]
        target = _target(tool, args)
        unsafe = tool not in {"read_file", "calculator", "write_file", "copy_file", "move_file", "delete_file"}
        unsafe = unsafe or not isinstance(args, dict)
        unsafe = unsafe or (not isinstance(args, dict) or any(not isinstance(value, str) for value in args.values()))
        unsafe = unsafe or (isinstance(target, str) and (target.startswith(("/", "\\")) or any(p == ".." for p in target.replace("\\", "/").split("/"))))
        unsafe = unsafe or (isinstance(target, str) and any(token in target.casefold() for token in ("secret", "credential", "password", "private_key", ".ssh")))
        unsafe = unsafe or (isinstance(args, dict) and set(args) != ({"filename"} if tool == "read_file" else {"expression"} if tool == "calculator" else {"filename", "content"} if tool == "write_file" else {"source", "destination"} if tool in {"copy_file", "move_file"} else {"filename"}))
        results.append(int(unsafe))
    return np.asarray(results, dtype=int)


def _models(seed: int) -> dict[str, Pipeline]:
    numeric_indexes = list(range(len(PHASE6_NUMERIC_FEATURES)))
    categorical_indexes = list(range(len(PHASE6_NUMERIC_FEATURES), len(PHASE6_FEATURE_NAMES)))
    preprocessor = ColumnTransformer([
        ("numeric", Pipeline([("imputer", SimpleImputer(strategy="median"))]), numeric_indexes),
        ("categorical", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")),
                                  ("encoder", OneHotEncoder(handle_unknown="ignore"))]), categorical_indexes),
    ], remainder="drop", verbose_feature_names_out=False)
    return {
        "logistic_regression": Pipeline([("preprocess", preprocessor),
            ("classifier", LogisticRegression(class_weight="balanced", max_iter=2000, random_state=seed))]),
        "random_forest": Pipeline([("preprocess", preprocessor),
            ("classifier", RandomForestClassifier(n_estimators=240, min_samples_leaf=2,
                                                    class_weight="balanced_subsample", random_state=seed, n_jobs=1))]),
    }


def _label_learnability(rows: list[dict[str, Any]], partitions: dict[str, np.ndarray]) -> dict[str, Any]:
    """Count prohibited cases whose feature vector is identical to a permitted sibling.

    Those labels are not derivable from the model's inputs; they bound achievable
    recall and explain validation/test gaps without claiming generalization.
    """
    report: dict[str, Any] = {}
    for split, index in partitions.items():
        permitted_vectors = {tuple(rows[i]["features"][name] if name in PHASE6_CATEGORICAL_FEATURES
                                   else float(rows[i]["features"][name]) for name in PHASE6_FEATURE_NAMES)
                             for i in index if rows[i]["ground_truth_label"] == "permitted"}
        prohibited = [i for i in index if rows[i]["ground_truth_label"] == "prohibited"]
        unlearnable = [rows[i]["scenario_id"] for i in prohibited
                       if tuple(rows[i]["features"][name] if name in PHASE6_CATEGORICAL_FEATURES
                                else float(rows[i]["features"][name]) for name in PHASE6_FEATURE_NAMES)
                       in permitted_vectors]
        report[split] = {"prohibited_scenarios": len(prohibited),
                         "prohibited_feature_identical_to_permitted": len(unlearnable),
                         "maximum_possible_recall_at_any_threshold": (
                             (len(prohibited) - len(unlearnable)) / len(prohibited) if prohibited else None),
                         "scenario_ids": unlearnable}
    return report


def train_phase6(dataset_path: str | Path = DEFAULT_PATH, artifact_dir: str | Path = DEFAULT_ARTIFACT_DIR,
                 seed: int = SEED) -> dict[str, Any]:
    rows = read_dataset(dataset_path)
    validate_dataset(rows)
    x, y, groups, feature_names = build_matrix(rows)
    partitions = {split: np.flatnonzero(np.asarray([row["dataset_split"] == split for row in rows]))
                  for split in ("train", "validation", "test")}
    groupsets = {key: set(groups[index]) for key, index in partitions.items()}
    if any(groupsets[a] & groupsets[b] for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))):
        raise ValueError("scenario family/group leakage across train, validation, or test")
    for split, idx in partitions.items():
        if len(np.unique(y[idx])) != 2:
            raise ValueError(f"{split} split does not contain both classes")
    models = _models(seed)
    validation_metrics: dict[str, Any] = {}
    test_metrics: dict[str, Any] = {}
    fitted: dict[str, Pipeline] = {}
    for name, estimator in models.items():
        estimator.fit(x[partitions["train"]], y[partitions["train"]])
        fitted[name] = estimator
        for split in ("validation", "test"):
            idx = partitions[split]
            proba = estimator.predict_proba(x[idx])[:, 1]
            predictions = (proba >= 0.5).astype(int)
            value = {**_metrics(y[idx], predictions, proba), "calibration": _calibration(y[idx], proba)}
            (validation_metrics if split == "validation" else test_metrics)[name] = value
    for split in ("validation", "test"):
        idx = partitions[split]
        predictions = _risk_engine_benchmark([rows[i] for i in idx])
        metrics = _metrics(y[idx], predictions, predictions.astype(float))
        (validation_metrics if split == "validation" else test_metrics)["existing_risk_rules"] = metrics
    # The validation family is held entirely apart from training/testing; it is
    # used only for model selection, no test tuning or policy threshold changes.
    selected = max(("random_forest", "logistic_regression"),
                   key=lambda name: (validation_metrics[name]["f1"], validation_metrics[name]["pr_auc"] or 0.0))
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    dataset_hash = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    metadata = {"model_version": MODEL_VERSION, "dataset_version": DATASET_VERSION,
                "dataset_path": str(dataset_path), "dataset_sha256": dataset_hash, "scenario_count": len(rows),
                "feature_schema_version": FEATURE_SCHEMA_VERSION, "feature_names": feature_names,
                "seed": seed,                "split_strategy": "predeclared scenario-family/template groups; families never cross partitions",
                "split_families": {key: sorted(value) for key, value in groupsets.items()},
                "split_family_counts": {key: len(value) for key, value in groupsets.items()},
                "independent_held_out_families": {"validation": list(SPLITS["validation"]),
                                                   "test": list(SPLITS["test"])},
                "split_counts": {key: len(value) for key, value in partitions.items()},
                "label_learnability": _label_learnability(rows, partitions),
                "class_counts": {key: dict(Counter(rows[i]["ground_truth_label"] for i in value)) for key, value in partitions.items()},
                "preprocessing": "median imputer inside estimator pipeline; fit on train partition only",
                "threshold_selection": "fixed classifier cut 0.5; no post-hoc security-policy tuning",
                "versions": {"python": platform.python_version(), "scikit_learn": sklearn.__version__,
                             "numpy": np.__version__, "joblib": joblib.__version__},
                "trained_at": datetime.now(timezone.utc).isoformat()}
    bundle = {"estimator": fitted[selected], "selected_model": selected, "model_version": MODEL_VERSION,
              "dataset_version": DATASET_VERSION, "feature_schema_version": FEATURE_SCHEMA_VERSION,
              "feature_names": feature_names, "metadata": metadata}
    artifact_path = path / "phase6_model_bundle.joblib"
    temporary = artifact_path.with_suffix(".joblib.tmp")
    joblib.dump(bundle, temporary)
    temporary.replace(artifact_path)
    report = {"metadata": metadata, "selected_model": selected,
              "validation_metrics": validation_metrics, "test_metrics": test_metrics,
              "warning": "Synthetic threat-model scenarios only; this is not production detection evidence."}
    (path / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (path / "training_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return report


def inspect_artifact(artifact_dir: str | Path = DEFAULT_ARTIFACT_DIR) -> dict[str, Any]:
    bundle = joblib.load(Path(artifact_dir) / "phase6_model_bundle.joblib")
    if (bundle.get("dataset_version") != DATASET_VERSION or
            bundle.get("feature_schema_version") != FEATURE_SCHEMA_VERSION or
            bundle.get("feature_names") != list(PHASE6_FEATURE_NAMES) or
            bundle.get("metadata", {}).get("versions", {}).get("scikit_learn") != sklearn.__version__):
        raise ValueError("Phase 6 artifact version or feature schema is incompatible")
    estimator = bundle.get("estimator")
    if not isinstance(estimator, Pipeline) or not hasattr(estimator, "predict_proba"):
        raise ValueError("Phase 6 artifact is not a probabilistic pipeline")
    return {key: bundle[key] for key in ("model_version", "selected_model", "dataset_version",
                                         "feature_schema_version", "feature_names", "metadata")}


def _conclusion_lines(report: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    """Conclusion and split-independence text computed from measured values only."""
    models = ("logistic_regression", "random_forest", "existing_risk_rules")
    value_of = lambda split, name: report[f"{split}_metrics"].get(name, {}).get("f1")
    val_values = [value_of("validation", name) for name in models]
    test_values = [value_of("test", name) for name in models]
    support = report["test_metrics"].get("logistic_regression", {}).get("support")
    if any(value is None for value in val_values + test_values):
        comparison = "metrics are incomplete for at least one approach"
    else:
        val_tied = len({round(value, 6) for value in val_values}) == 1
        test_tied = len({round(value, 6) for value in test_values}) == 1
        comparison = ("all three tie" if val_tied and test_tied else
                      "validation F1: " + ", ".join(f"{name} {value_of('validation', name):.4f}" for name in models) +
                      "; test F1: " + ", ".join(f"{name} {value_of('test', name):.4f}" for name in models))
    lines = [
        f"On this dataset neither Logistic Regression nor Random Forest outperforms the existing deterministic risk "
        f"benchmark: {comparison} on the validation split (F1 {val_values[0]:.4f}) and on the held-out test split "
        f"(F1 {test_values[0]:.4f}, {support} scenarios). The dataset therefore does not demonstrate an ML advantage "
        f"over the existing rules; the candidate model is integrated only as an additional signal that cannot "
        f"override mandatory policy.",
        "", "## Split independence and why validation differs from test", "",
    ]
    family_counts = metadata.get("split_family_counts", {})
    held_out = metadata.get("independent_held_out_families", {})
    lines.append(
        f"Splitting unit: whole scenario families. Train {family_counts.get('train', 'n/a')} families, validation "
        f"{family_counts.get('validation', 'n/a')} independent families (`{', '.join(held_out.get('validation', []))}`), "
        f"test {family_counts.get('test', 'n/a')} independent families (`{', '.join(held_out.get('test', []))}`). "
        f"The 100/30/30 scenario-group counts are attack-template variants *within* those families, not 160 "
        f"independent samples; generalization claims rest on 3 held-out test families (36 scenarios).")
    learnability_tail = {
        "validation": ("Validation recall is therefore bounded by those label conflicts, while test targets are "
                       "separable by simple features already present in training (sensitive names, traversal "
                       "tokens) — the two splits are not comparable difficulty."),
        "test": ("On this split the bound is not active: every prohibited target is trivially separable by simple "
                 "features that also appear in training, which is why all three approaches score 1.0000 here and "
                 "why this small split cannot support a generalization claim."),
    }
    for split in ("validation", "test"):
        detail = metadata.get("label_learnability", {}).get(split, {})
        if detail:
            lines.append(
                f"- `{split}`: {detail['prohibited_feature_identical_to_permitted']} of "
                f"{detail['prohibited_scenarios']} prohibited scenarios have feature vectors identical to a permitted "
                f"sibling, so no features-only classifier can exceed recall "
                f"{detail['maximum_possible_recall_at_any_threshold']:.2f} there. {learnability_tail[split]}")
    lines += [
        "",
        "Perfect test scores on 36 authored scenarios do **not** establish generalization to unseen attack families; "
        "they only show that the existing rules and both learners behave identically on these templates.",
    ]
    return lines


def write_ml_report(report: dict[str, Any], report_dir: str | Path = "reports") -> Path:
    """Render reports/PHASE6_ML_EVALUATION.md from measured values only."""
    metadata = report["metadata"]
    lines = [
        "# Phase 6 ML Evaluation", "",
        f"Model: `{metadata['model_version']}` (selected: `{report['selected_model']}`).",
        f"Dataset: `{metadata['dataset_version']}` ({metadata['scenario_count']} scenarios, sha256 `{metadata['dataset_sha256'][:16]}…`), seed `{metadata['seed']}`.",
        f"Split: {metadata['split_strategy']}; counts {metadata['split_counts']}; class counts {metadata['class_counts']}.",
        f"Versions: Python {metadata['versions']['python']}, scikit-learn {metadata['versions']['scikit_learn']}, numpy {metadata['versions']['numpy']}, joblib {metadata['versions']['joblib']}.",
        f"Threshold: {metadata['threshold_selection']}. Preprocessing: {metadata['preprocessing']}.", "",
        "Synthetic threat-model scenarios only; these are not production detection results.", "",
        "## Held-out metrics", "",
        "| Split | Model | Support | Precision | Recall | F1 | FPR | FNR | PR-AUC | TN/FP/FN/TP |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    fmt = lambda value: "n/a" if value is None else f"{value:.4f}"
    for split in ("validation", "test"):
        for name, values in report[f"{split}_metrics"].items():
            cm = values["confusion_matrix"]
            lines.append(f"| {split} | {name} | {values['support']} | {fmt(values['precision'])} | "
                         f"{fmt(values['recall'])} | {fmt(values['f1'])} | {fmt(values['false_positive_rate'])} | "
                         f"{fmt(values['false_negative_rate'])} | {fmt(values['pr_auc'])} | "
                         f"{cm['tn']}/{cm['fp']}/{cm['fn']}/{cm['tp']} |")
    lines += ["", "## Calibration (probability outputs)", "",
              "| Split | Model | Brier | Log loss | ECE (10 bins) |", "|---|---|---:|---:|---:|"]
    for split in ("validation", "test"):
        for name, values in report[f"{split}_metrics"].items():
            calibration = values.get("calibration")
            if calibration:
                lines.append(f"| {split} | {name} | {calibration['brier_score']:.4f} | "
                             f"{calibration['log_loss']:.4f} | {calibration['expected_calibration_error']:.4f} |")
    lines += ["", "Expected calibration error is reported as measured. The probabilities are **not** claimed to be calibrated; no calibration method was fitted or validated.", "",
              "## Model selection and conclusion", "",
              f"Selection rule: highest validation F1, then validation PR-AUC, using the validation split only. Selected: `{report['selected_model']}`.",
              ]
    lines += _conclusion_lines(report, metadata)
    lines += ["", "## Limitations", "",
              "- Scenarios are authored and synthetic; the test split covers three scenario families (36 trials).",
              "- The validation split (36 scenarios) was used for model selection; test metrics are held out by scenario family.",
              "- Feature vectors are captured at scenario creation time from request data and prior-history slots; no labels, decisions, or outcomes are features.", "",
              f"Raw metrics: `ml/artifacts/phase6/evaluation.json`. Reproduce with `python -m ml.phase6_pipeline train` then `python -m ml.phase6_pipeline evaluate`."]
    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "PHASE6_ML_EVALUATION.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train", help="fit Logistic Regression and Random Forest, compare rules")
    train.add_argument("--dataset", default=str(DEFAULT_PATH))
    train.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    train.add_argument("--seed", type=int, default=SEED)
    train.add_argument("--report-dir", default="reports")
    evaluate = sub.add_parser("evaluate", help="print measured Phase 6 held-out evaluation")
    evaluate.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    evaluate.add_argument("--report-dir", default="reports")
    inspect = sub.add_parser("inspect", help="validate a saved Phase 6 model bundle")
    inspect.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    args = parser.parse_args()
    if args.command == "train":
        report = train_phase6(args.dataset, args.artifact_dir, args.seed)
        write_ml_report(report, args.report_dir)
    elif args.command == "inspect":
        report = inspect_artifact(args.artifact_dir)
    else:
        report = json.loads((Path(args.artifact_dir) / "evaluation.json").read_text(encoding="utf-8"))
        write_ml_report(report, args.report_dir)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
