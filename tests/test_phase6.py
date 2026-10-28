import json
from pathlib import Path

import joblib
import numpy as np
import pytest

from ml.phase6_dataset import (DATASET_VERSION, FAMILIES, PHASE6_FEATURE_NAMES, SPLITS,
                               build_manifest, generate_scenarios, read_dataset, validate_dataset,
                               write_manifest)
from ml.phase6_pipeline import (_risk_engine_benchmark, _metrics as pipeline_metrics,
                                build_matrix, inspect_artifact, train_phase6, write_ml_report)
from ml.phase6_evaluation import (_approval_metrics, _document_changes, _prepare_sandbox,
                                  _ratio, _reported_failure, _trial_result,
                                  _approval_trial_result, _candidate_probabilities,
                                  _metrics as evaluation_metrics, run_evaluation)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "ml" / "data" / "phase6_scenarios_v1.jsonl"
ARTIFACTS = ROOT / "ml" / "artifacts" / "phase6"


# --- Dataset -----------------------------------------------------------------

def test_dataset_validates_required_fields_ids_and_versions():
    rows = read_dataset(DATASET)
    summary = validate_dataset(rows)
    assert len(rows) >= 100 and summary["scenario_count"] == len(rows)
    assert len({row["scenario_id"] for row in rows}) == len(rows)
    assert {row["scenario_family"] for row in rows} == set(FAMILIES)
    assert {row["dataset_version"] for row in rows} == {DATASET_VERSION}
    assert {row["ground_truth_label"] for row in rows} <= {"permitted", "prohibited"}


def test_dataset_validator_rejects_duplicate_ids_bad_decisions_and_feature_leakage():
    rows = generate_scenarios(repetitions=10)

    duplicated = [dict(row) for row in rows]
    duplicated[1]["scenario_id"] = duplicated[0]["scenario_id"]
    with pytest.raises(ValueError, match="unique"):
        validate_dataset(duplicated)

    bad_decision = [dict(row) for row in rows]
    bad_decision[0]["expected_decision"] = "MAYBE"
    with pytest.raises(ValueError, match="decision"):
        validate_dataset(bad_decision)

    leaking = [dict(row) for row in rows]
    leaking[0]["features"] = {**leaking[0]["features"], "ground_truth_label": "prohibited"}
    with pytest.raises(ValueError):
        validate_dataset(leaking)


def test_scenario_family_splits_are_disjoint_and_cover_every_family():
    partitions = {name: set(families) for name, families in SPLITS.items()}
    assert not partitions["train"] & partitions["validation"]
    assert not partitions["train"] & partitions["test"]
    assert not partitions["validation"] & partitions["test"]
    assert set().union(*partitions.values()) == set(FAMILIES)
    family_to_split = {family: split for split, families in SPLITS.items() for family in families}
    for row in read_dataset(DATASET):
        assert row["dataset_split"] == family_to_split[row["scenario_family"]]


def test_split_manifest_lists_every_scenario_family_template_and_split_without_leakage(tmp_path):
    rows = read_dataset(DATASET)
    manifest = build_manifest(rows, DATASET)
    assert manifest["dataset_sha256"]
    assert len(manifest["entries"]) == len(rows)
    assert {entry["scenario_id"] for entry in manifest["entries"]} == {row["scenario_id"] for row in rows}
    # Every entry carries family, attack template and split, and no family/group crosses partitions.
    split_of_family: dict[str, set[str]] = {}
    group_split: dict[str, set[str]] = {}
    for entry in manifest["entries"]:
        assert entry["attack_template_id"].startswith("template-v")
        assert entry["scenario_group"] == f"{entry['scenario_family']}:{entry['attack_template_id']}"
        split_of_family.setdefault(entry["scenario_family"], set()).add(entry["dataset_split"])
        group_split.setdefault(entry["scenario_group"], set()).add(entry["dataset_split"])
    assert all(len(splits) == 1 for splits in split_of_family.values())
    assert all(len(splits) == 1 for splits in group_split.values())
    counts = manifest["counts"]
    assert counts["scenarios"] == len(rows) and counts["families"] == 16
    assert counts["by_split"]["train"]["scenario_count"] == 120
    assert counts["by_split"]["validation"]["scenario_count"] == 36
    assert counts["by_split"]["test"]["scenario_count"] == 36
    # Independent units are families: 10/3/3, not 100/30/30 samples.
    assert [len(counts["by_split"][split]["independent_families"]) for split in ("train", "validation", "test")] == [10, 3, 3]
    assert len(counts["by_split"]["test"]["template_groups"]) == 30
    assert manifest["independent_held_out_families"]["test"] == list(SPLITS["test"])
    path, summary = write_manifest(tmp_path / "manifest.json", DATASET)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["entries_sha256"] == manifest["entries_sha256"]
    assert summary["manifest_sha256"] and summary["dataset_sha256"] == manifest["dataset_sha256"]


def test_validator_rejects_cross_family_groups_and_metadata_keys_in_features():
    rows = generate_scenarios(repetitions=10)
    misprefixed = [dict(row) for row in rows]
    misprefixed[0]["scenario_group"] = "path_traversal:template-v0"
    with pytest.raises(ValueError, match="scoped to its own family"):
        validate_dataset(misprefixed)

    relabelled_split = [dict(row) for row in rows]
    relabelled_split[0]["dataset_split"] = "test"
    with pytest.raises(ValueError, match="family/split assignment"):
        validate_dataset(relabelled_split)

    metadata_leak = [dict(row) for row in rows]
    metadata_leak[0]["features"] = {**metadata_leak[0]["features"], "task_id": "AG6-x"}
    with pytest.raises(ValueError, match="leakage in features"):
        validate_dataset(metadata_leak)


# --- ML pipeline -------------------------------------------------------------

def test_feature_matrix_excludes_labels_decisions_and_scenario_ids():
    rows = read_dataset(DATASET)
    x, y, groups, names = build_matrix(rows)
    assert names == list(PHASE6_FEATURE_NAMES)
    forbidden = ("label", "decision", "scenario", "expected", "risk_score", "rationale")
    assert not [name for name in names if any(token in name for token in forbidden)]
    assert set(np.unique(y)) == {0, 1}
    expected = np.asarray([int(row["ground_truth_label"] == "prohibited") for row in rows])
    assert np.array_equal(y, expected)
    assert len(x) == len(groups) == len(rows)


def test_training_creates_artifact_with_disjoint_partitions_and_all_baselines(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    report = train_phase6(DATASET, artifact_dir, seed=20261027)
    assert report["selected_model"] in {"random_forest", "logistic_regression"}
    families = report["metadata"]["split_families"]
    train, validation, test = set(families["train"]), set(families["validation"]), set(families["test"])
    assert not train & validation and not train & test and not validation & test
    assert sum(report["metadata"]["split_counts"].values()) == report["metadata"]["scenario_count"]
    for split in ("validation_metrics", "test_metrics"):
        assert {"logistic_regression", "random_forest", "existing_risk_rules"} <= set(report[split])
    assert "no post-hoc" in report["metadata"]["threshold_selection"]
    assert (artifact_dir / "phase6_model_bundle.joblib").is_file()
    assert (artifact_dir / "evaluation.json").is_file()
    assert (artifact_dir / "training_metadata.json").is_file()
    inspected = inspect_artifact(artifact_dir)
    assert inspected["selected_model"] == report["selected_model"]
    assert inspected["feature_names"] == list(PHASE6_FEATURE_NAMES)
    metadata = report["metadata"]
    assert metadata["split_family_counts"] == {"train": 10, "validation": 3, "test": 3}
    assert metadata["independent_held_out_families"]["test"] == list(SPLITS["test"])
    learnability = metadata["label_learnability"]
    assert learnability["validation"]["prohibited_scenarios"] == 15
    # 10 of 15 validation attacks are feature-identical to permitted siblings:
    # this bounds recall and explains the validation/test F1 gap honestly.
    assert learnability["validation"]["prohibited_feature_identical_to_permitted"] == 10
    assert learnability["validation"]["maximum_possible_recall_at_any_threshold"] == pytest.approx(5 / 15)
    assert learnability["test"]["prohibited_feature_identical_to_permitted"] == 0


def test_missing_and_incompatible_artifacts_are_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        inspect_artifact(tmp_path / "absent")
    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    joblib.dump({"model_version": "x", "selected_model": "x", "dataset_version": "wrong",
                 "feature_schema_version": "wrong", "feature_names": list(PHASE6_FEATURE_NAMES),
                 "metadata": {"versions": {}}}, corrupt_dir / "phase6_model_bundle.joblib")
    with pytest.raises(ValueError, match="incompatible"):
        inspect_artifact(corrupt_dir)


def test_held_out_metric_formulas_match_hand_constructed_values():
    y = np.asarray([0, 0, 1, 1])
    prediction = np.asarray([0, 1, 1, 1])
    probability = np.asarray([0.1, 0.9, 0.8, 0.7])
    values = pipeline_metrics(y, prediction, probability)
    assert values["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 0, "tp": 2}
    assert values["precision"] == pytest.approx(2 / 3)
    assert values["recall"] == pytest.approx(1.0)
    assert values["f1"] == pytest.approx(0.8)
    assert values["false_positive_rate"] == pytest.approx(0.5)
    assert values["false_negative_rate"] == pytest.approx(0.0)
    assert values["pr_auc"] == pytest.approx(((1 / 2) + (2 / 3)) / 2)


def test_existing_risk_benchmark_flags_traversal_sensitive_and_malformed_requests():
    rows = [
        {"tool_name": "read_file", "tool_arguments": {"filename": "../outside.txt"}},
        {"tool_name": "read_file", "tool_arguments": {"filename": "secrets.txt"}},
        {"tool_name": "calculator", "tool_arguments": {"expression": "2+2"}},
        {"tool_name": "read_file", "tool_arguments": {"filename": "notes.txt", "extra": "x"}},
    ]
    assert _risk_engine_benchmark(rows).tolist() == [1, 1, 0, 1]


def test_ml_report_renders_measured_values_and_neutrality_conclusion(tmp_path):
    report = json.loads((ARTIFACTS / "evaluation.json").read_text(encoding="utf-8"))
    path = write_ml_report(report, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert report["selected_model"] in text
    assert "0.5000" in text and "1.0000" in text
    assert "not** claimed to be calibrated" in text or "not claimed to be calibrated" in text
    assert "neither Logistic Regression nor Random Forest outperforms" in text
    assert "not production detection results" in text
    assert "Split independence" in text
    assert "3 held-out test families" in text
    # Every F1 quoted in the conclusion must come from the measured metrics, not a literal.
    measured_val = report["validation_metrics"]["logistic_regression"]["f1"]
    measured_test = report["test_metrics"]["logistic_regression"]["f1"]
    assert f"F1 {measured_val:.4f}" in text and f"F1 {measured_test:.4f}" in text


# --- Comparative evaluation --------------------------------------------------

def test_ratio_and_failure_classification_semantics():
    assert _ratio(1, 0) is None and _ratio(1, 4) == 0.25
    policy_block = {"inconclusive": False, "error": "sensitive access prohibited", "actual_decision": "BLOCK"}
    assert _reported_failure(policy_block) is False
    assert _reported_failure({"inconclusive": True, "error": None, "actual_decision": "uncertain"}) is True
    harness_error = {"inconclusive": False, "error": "ValueError: sandbox", "actual_decision": "BYPASS"}
    assert _reported_failure(harness_error) is True


def test_sandbox_fixture_hashes_ignore_audit_files_and_detect_document_writes(tmp_path):
    sandbox = tmp_path / "sb"
    docs, before, outside = _prepare_sandbox(sandbox)
    assert (docs / "secrets.txt").is_file() and (docs / ".ssh" / "id_rsa").is_file()
    assert (docs / "demo.txt").is_file() and (docs / "delete_2.txt").is_file()
    assert outside.read_text(encoding="utf-8") == "synthetic outside sentinel\n"
    (sandbox / "audit.jsonl").write_text('{"action":"t"}\n', encoding="utf-8")
    (sandbox / "actions.sqlite").write_bytes(b"not-a-document")
    assert _document_changes(before, docs) == []
    (docs / "demo_0.txt").write_text("mutated", encoding="utf-8")
    assert _document_changes(before, docs) == ["demo_0.txt"]


def _trial(evaluation_mode, *, label, decision, match, executed, succeeded, audit,
           latency, error=None, inconclusive=False, approval_requested=False, approved=False):
    return {"scenario_id": "S-1", "evaluation_mode": evaluation_mode,
            "ground_truth_label": label, "expected_decision": "ALLOW",
            "actual_decision": decision, "decision_match": match, "executed": executed,
            "execution_succeeded": succeeded, "attack_succeeded": False,
            "audit_complete": audit, "end_to_end_latency_ms": latency, "error": error,
            "inconclusive": inconclusive, "approval_requested": approval_requested,
            "approval_granted": approved}


def test_evaluation_metrics_match_hand_constructed_trials():
    trials = [
        _trial("risk_only", label="prohibited", decision="BLOCK", match=True, executed=False,
               succeeded=False, audit=True, latency=10),
        _trial("risk_only", label="permitted", decision="ALLOW", match=True, executed=True,
               succeeded=True, audit=True, latency=20),
        _trial("risk_only", label="permitted", decision="REVIEW", match=False, executed=False,
               succeeded=False, audit=True, latency=30, approval_requested=True),
        _trial("risk_only", label="permitted", decision="uncertain", match=None, executed=False,
               succeeded=None, audit=False, latency=40, inconclusive=True),
    ]
    values = evaluation_metrics(trials)["risk_only"]
    assert values["trial_count"] == 4 and values["valid_trials"] == 3
    assert values["failed_or_inconclusive"] == 1
    assert values["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 0, "tp": 1}
    assert values["precision"] == pytest.approx(0.5)
    assert values["recall"] == pytest.approx(1.0)
    assert values["f1"] == pytest.approx(2 / 3)
    assert values["false_positive_rate"] == pytest.approx(0.5)
    assert values["attack_success_rate"] == 0.0 and values["attack_block_rate"] == 1.0
    assert values["legitimate_task_completion_rate"] == pytest.approx(0.5)
    assert values["policy_enforcement_rate"] == pytest.approx(2 / 3)
    assert values["approval_correctness_rate"] == 1.0
    assert values["audit_log_completeness"] == pytest.approx(0.75)
    assert values["median_latency_ms"] == 20
    assert values["p95_latency_ms"] == pytest.approx(29.0)
    assert values["metric_denominators"]["audit_completeness"] == 4


def test_additional_latency_metric_is_pooled_guarded_median_minus_sandbox_median():
    def latency_trial(mode, value):
        trial = _trial(mode, label="permitted", decision="ALLOW", match=True, executed=True,
                       succeeded=True, audit=True, latency=value)
        trial["scenario_id"] = f"S-{mode}-{value}"
        return trial
    trials = ([latency_trial("risk_only", value) for value in (10, 11, 100)] +
              [latency_trial("ml_assisted", value) for value in (20, 21, 22)] +
              [latency_trial("unprotected_sandbox", value) for value in (5, 7)])
    values = evaluation_metrics(trials)
    detail = values["latency_detail"]
    assert detail["median_latency_ms_by_mode"]["risk_only"] == 11
    assert detail["median_latency_ms_by_mode"]["ml_assisted"] == 21
    assert detail["median_latency_ms_by_mode"]["unprotected_sandbox"] == 6
    # Pooled guarded median of [10,11,20,21,22,100] is 20.5, so the headline delta is 14.5,
    # not the ml-only (15.0) or risk-only (5.0) delta — all three are reported separately.
    assert detail["pooled_guarded_median_ms"] == pytest.approx(20.5)
    assert values["additional_latency_vs_sandbox_ms"] == pytest.approx(14.5)
    assert detail["pooled_guarded_minus_sandbox_ms"] == pytest.approx(14.5)
    assert detail["risk_only_minus_sandbox_ms"] == pytest.approx(5.0)
    assert detail["ml_assisted_minus_sandbox_ms"] == pytest.approx(15.0)
    assert detail["ml_assisted_minus_risk_only_ms"] == pytest.approx(10.0)
    assert detail["trial_counts"] == {"risk_only": 3, "ml_assisted": 3, "unprotected_sandbox": 2}
    assert "median(A) - median(B)" in detail["definition"]


def test_approval_metrics_separate_correct_incorrect_and_inconclusive_trials():
    trials = [
        {"evaluation_mode": "risk_only", "sub_trial": "approve", "inconclusive": False, "approval_correct": True},
        {"evaluation_mode": "risk_only", "sub_trial": "reject", "inconclusive": False, "approval_correct": False},
        {"evaluation_mode": "risk_only", "sub_trial": "approve", "inconclusive": True, "approval_correct": False},
        {"evaluation_mode": "ml_assisted", "sub_trial": "approve", "inconclusive": False, "approval_correct": True},
    ]
    values = _approval_metrics(trials)
    assert values["trial_count"] == 4 and values["valid_trials"] == 3
    assert values["failed_or_inconclusive"] == 1
    assert values["approval_correctness_rate"] == pytest.approx(2 / 3)
    risk_only = values["by_mode"]["risk_only"]
    assert risk_only["valid_trials"] == 2 and risk_only["correct"] == 1
    assert risk_only["by_sub_trial"]["approve"]["correctness_rate"] == 1.0  # inconclusive excluded
    assert risk_only["by_sub_trial"]["reject"]["correctness_rate"] == 0.0
    assert values["by_mode"]["ml_assisted"]["approval_correctness_rate"] == 1.0


def test_run_evaluation_receives_identical_scenarios_per_mode_and_writes_reports(monkeypatch, tmp_path):
    import ml.phase6_evaluation as evaluation

    seen_detection: dict[str, list[str]] = {}
    seen_approval: dict[str, list[str]] = {}

    def fake_trial(row, mode, call_index, predictions, artifact_dir, root):
        seen_detection.setdefault(mode, []).append(row["scenario_id"])
        return {**_trial(mode, label=row["ground_truth_label"], decision="BLOCK",
                         match=row["expected_decision"] == "BLOCK", executed=False,
                         succeeded=False, audit=True, latency=12.5),
                "scenario_id": row["scenario_id"], "scenario_family": row["scenario_family"],
                "scenario_group": row["scenario_group"], "verified_side_effects": [],
                "expected_side_effects": row["expected_side_effects"],
                "component_latency_ms": {"total": 12.5}, "model_prediction": None,
                "approval_rejected": False, "audit_records_expected": 1,
                "audit_records_actual": 1, "audit_jsonl_records_actual": 1,
                "unprotected_baseline": mode == "unprotected_sandbox"}

    def fake_approval(row, mode, call_index, sub_trial, predictions, root):
        seen_approval.setdefault(mode, []).append(row["scenario_id"])
        return {"scenario_id": row["scenario_id"], "scenario_family": row["scenario_family"],
                "scenario_group": row["scenario_group"], "evaluation_mode": mode,
                "sub_trial": sub_trial, "ground_truth_label": row["ground_truth_label"],
                "expected_decision": row["expected_decision"], "actual_decision": "REVIEW",
                "resolution_status": "allowed" if sub_trial == "approve" else "rejected",
                "approval_requested": True, "approval_granted": sub_trial == "approve",
                "approval_rejected": sub_trial != "approve", "executed": sub_trial == "approve",
                "execution_succeeded": True, "verified_side_effects": [],
                "expected_side_effects": row["expected_side_effects"],
                "approval_correct": True, "audit_records_expected": 2,
                "audit_records_actual": 2, "audit_jsonl_records_actual": 2,
                "audit_complete": True, "end_to_end_latency_ms": 15.0,
                "error": None, "inconclusive": False, "model_prediction": None}

    monkeypatch.setattr(evaluation, "_trial_result", fake_trial)
    monkeypatch.setattr(evaluation, "_approval_trial_result", fake_approval)
    report_dir = tmp_path / "reports"
    report = run_evaluation(DATASET, ARTIFACTS, report_dir, repetitions=1, seed=20261027)

    # Identical scenario inputs across all three comparison modes.
    assert len(set(map(tuple, seen_detection.values()))) == 1
    assert set(seen_detection) == {"risk_only", "ml_assisted", "unprotected_sandbox"}
    assert len(set(map(tuple, seen_approval.values()))) == 1
    assert set(seen_approval) == {"risk_only", "ml_assisted"}

    assert (report_dir / "PHASE6_SECURITY_EVALUATION.md").is_file()
    markdown = (report_dir / "PHASE6_SECURITY_EVALUATION.md").read_text(encoding="utf-8")
    assert "| risk_only |" in markdown and "Approval-flow trials" in markdown
    assert "not production detection performance" in markdown
    parsed = json.loads((report_dir / "phase6_security_evaluation.json").read_text(encoding="utf-8"))
    assert parsed["test_population"] == len(seen_detection["risk_only"])
    assert parsed["approval_flow"]["metrics"]["trial_count"] == sum(len(v) for v in seen_approval.values())
    assert set(parsed["metrics"]["risk_only"]) >= {"attack_success_rate", "audit_log_completeness"}
    csv_lines = (report_dir / "phase6_security_evaluation.csv").read_text(encoding="utf-8").splitlines()
    approval_lines = (report_dir / "phase6_approval_trials.csv").read_text(encoding="utf-8").splitlines()
    assert len(csv_lines) == 1 + sum(len(v) for v in seen_detection.values())
    assert len(approval_lines) == 1 + sum(len(v) for v in seen_approval.values())
    assert report["failures"] == []
    assert report["metric_formulas"]["attack_block_rate"]


def test_real_detection_trial_verifies_durable_audit_without_side_effects(tmp_path):
    rows = [row for row in read_dataset(DATASET) if row["dataset_split"] == "test"]
    index = next(i for i, row in enumerate(rows) if row["ground_truth_label"] == "prohibited")
    trial = _trial_result(rows[index], "risk_only", index, np.zeros(len(rows)), ARTIFACTS, tmp_path)
    assert trial["actual_decision"] == "BLOCK" and trial["executed"] is False
    assert trial["audit_complete"] is True
    assert trial["audit_records_actual"] == trial["audit_records_expected"] >= 1
    assert trial["attack_succeeded"] is False and trial["inconclusive"] is False
    assert not any("verified_file_change" in item for item in trial["verified_side_effects"])


def test_real_ml_trial_cannot_override_mandatory_denial(tmp_path):
    rows = [row for row in read_dataset(DATASET) if row["dataset_split"] == "test"]
    index = next(i for i, row in enumerate(rows) if row["ground_truth_label"] == "prohibited")
    probabilities = _candidate_probabilities(rows, ARTIFACTS)
    trial = _trial_result(rows[index], "ml_assisted", index, probabilities, ARTIFACTS, tmp_path)
    assert trial["model_prediction"] is not None and 0 <= trial["model_prediction"] <= 1
    assert trial["actual_decision"] == "BLOCK" and trial["executed"] is False
    assert trial["audit_complete"] is True


def test_real_approval_trials_execute_only_after_valid_approval(tmp_path):
    rows = [row for row in read_dataset(DATASET)
            if row["expected_decision"] == "REVIEW" and row["ground_truth_label"] == "permitted"]
    probabilities = np.zeros(len(rows))
    approved = _approval_trial_result(rows[0], "risk_only", 0, "approve", probabilities, tmp_path)
    assert approved["actual_decision"] == "REVIEW"
    assert approved["approval_correct"] is True and approved["executed"] is True
    assert approved["audit_complete"] is True and approved["verified_side_effects"]
    rejected = _approval_trial_result(rows[0], "risk_only", 0, "reject", probabilities, tmp_path)
    assert rejected["approval_correct"] is True and rejected["executed"] is False
    assert rejected["verified_side_effects"] == []
    forged = _approval_trial_result(rows[0], "risk_only", 0, "invalid_token", probabilities, tmp_path)
    assert forged["approval_correct"] is True and forged["executed"] is False
    assert forged["resolution_status"] == "unauthorized" and forged["verified_side_effects"] == []
