from __future__ import annotations

import argparse
import csv
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from food_ai.barcode.service import store_openfood_product
from food_ai.config import ARTIFACTS_DIR, DB_PATH, PROJECT_ROOT
from food_ai.core.daily_totals import get_daily_totals
from food_ai.core.log_consumption import log_consumption
from food_ai.db import initialize_db
from food_ai.generic_foods.service import build_generic_foods
from food_ai.sync.build_usda_backbone import build_usda_backbone
from food_ai.sync.extract_core_ingredients import rebuild_core_ingredients
from food_ai.tools.build_recipe_manifest import build_recipe_manifest
from food_ai.tools.build_usda_embeddings import build_usda_embeddings
from food_ai.ui.config import UISettings
from food_ai.ui.services.formatters import format_result_for_ui
from food_ai.ui.services.inference_service import UIInferenceOptions, run_food_inference
from food_ai.ui.services.log_service import save_test_log
from food_ai.usda.embeddings import load_usda_embeddings
from food_ai.usda.service import match_ingredient_to_usda
from food_ai.utils.ingredient_normalize import normalize_ingredient_with_metadata
from food_ai.config import FOOD101_DATA_DIR, USDA_DATA_DIR, resolve_recipe_data_path
from food_ai.vision import predict_dish


@dataclass
class Checkpoint:
    name: str
    status: str
    inputs_used: dict[str, Any]
    metrics: dict[str, Any]
    sample_output: Any
    artifact_paths: list[str]
    next_dependency: str
    notes: str | None = None


class AuditRunner:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.console_log_path = self.output_root / "audit_console.log"
        self.report_path = self.output_root / "audit_report.json"
        self.summary_path = self.output_root / "audit_summary.json"
        self.checkpoints: list[Checkpoint] = []
        self.artifacts: list[str] = []
        self._console_handle = self.console_log_path.open("w", encoding="utf-8")

    def close(self) -> None:
        self._console_handle.close()

    def emit(self, text: str = "") -> None:
        print(text)
        self._console_handle.write(text + "\n")
        self._console_handle.flush()

    def add_artifact(self, path: str | Path | None) -> None:
        if path is None:
            return
        resolved = str(path)
        if resolved not in self.artifacts:
            self.artifacts.append(resolved)

    def record(self, checkpoint: Checkpoint) -> None:
        self.checkpoints.append(checkpoint)
        for artifact in checkpoint.artifact_paths:
            self.add_artifact(artifact)
        self.emit(f"CHECKPOINT NAME: {checkpoint.name}")
        self.emit(f"status: {checkpoint.status}")
        self.emit("inputs used:")
        self.emit(_pretty(checkpoint.inputs_used))
        self.emit("key metrics/counts:")
        self.emit(_pretty(checkpoint.metrics))
        self.emit("sample output:")
        self.emit(_pretty(checkpoint.sample_output))
        self.emit("artifact path(s):")
        self.emit(_pretty(checkpoint.artifact_paths))
        self.emit(f"next dependency: {checkpoint.next_dependency}")
        if checkpoint.notes:
            self.emit(f"notes: {checkpoint.notes}")
        self.emit("-" * 80)

    def finalize(self) -> None:
        report_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "output_root": str(self.output_root),
            "checkpoints": [asdict(item) for item in self.checkpoints],
            "artifacts": self.artifacts,
        }
        self.report_path.write_text(json.dumps(report_payload, indent=2, default=str), encoding="utf-8")
        summary_payload = {
            "summary": [{"name": item.name, "status": item.status, "next_dependency": item.next_dependency} for item in self.checkpoints],
            "artifacts": self.artifacts,
        }
        self.summary_path.write_text(json.dumps(summary_payload, indent=2, default=str), encoding="utf-8")
        self.add_artifact(self.report_path)
        self.add_artifact(self.summary_path)
        self.emit("FINAL SUMMARY TABLE")
        widths = {
            "name": max(len("CHECKPOINT"), *(len(item.name) for item in self.checkpoints)),
            "status": max(len("STATUS"), *(len(item.status) for item in self.checkpoints)),
            "next": max(len("NEXT DEPENDENCY"), *(len(item.next_dependency) for item in self.checkpoints)),
        }
        header = f"{'CHECKPOINT'.ljust(widths['name'])}  {'STATUS'.ljust(widths['status'])}  {'NEXT DEPENDENCY'.ljust(widths['next'])}"
        self.emit(header)
        self.emit("-" * len(header))
        for item in self.checkpoints:
            self.emit(f"{item.name.ljust(widths['name'])}  {item.status.ljust(widths['status'])}  {item.next_dependency.ljust(widths['next'])}")
        self.emit("ARTIFACTS GENERATED")
        self.emit(_pretty(self.artifacts))


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, default=str, ensure_ascii=True)


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def _sample_csv_rows(path: Path, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for _, row in zip(range(limit), reader):
            rows.append(row)
    return rows


def _sample_jsonl_rows(path: Path, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for _, line in zip(range(limit), handle):
            rows.append(json.loads(line))
    return rows

def _food101_metrics(food101_dir: Path) -> dict[str, Any]:
    meta_dir = food101_dir / "meta"
    images_dir = food101_dir / "images"
    train_file = meta_dir / "train.txt"
    test_file = meta_dir / "test.txt"
    classes = [path for path in images_dir.iterdir() if path.is_dir()] if images_dir.exists() else []
    metrics = {
        "class_count": len(classes),
        "sample_classes": [path.name for path in classes[:5]],
        "sample_image_paths": [],
        "train_split_count": 0,
        "test_split_count": 0,
    }
    if train_file.exists():
        metrics["train_split_count"] = sum(1 for _ in train_file.open("r", encoding="utf-8"))
    if test_file.exists():
        metrics["test_split_count"] = sum(1 for _ in test_file.open("r", encoding="utf-8"))
    if classes:
        sample_class = classes[0]
        metrics["sample_image_paths"] = [str(path) for path in list(sample_class.glob("*.jpg"))[:3]]
    return metrics


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return row is not None


def _table_count(connection: sqlite3.Connection, table_name: str) -> int | None:
    if not _table_exists(connection, table_name):
        return None
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _sample_table_rows(connection: sqlite3.Connection, table_name: str, limit: int = 3) -> list[dict[str, Any]]:
    if not _table_exists(connection, table_name):
        return []
    cursor = connection.execute(f"SELECT * FROM {table_name} LIMIT {int(limit)}")
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _copy_or_init_db(target_db: Path) -> Path:
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, target_db)
    else:
        initialize_db(target_db)
    return target_db


def _seed_known_product_and_log(audit_db: Path) -> dict[str, Any]:
    before_counts = {}
    with sqlite3.connect(audit_db) as connection:
        for table in ["products", "barcodes", "serving_sizes", "nutrition_facts", "nutrition_micros", "consumption_logs", "unknown_barcodes"]:
            before_counts[table] = _table_count(connection, table)

    product = {
        "barcode": "9990001112223",
        "product_name": "PALM Audit Protein Bar",
        "brand": "PALM",
        "source": "audit",
        "nutrition": {"calories": 240, "protein_g": 12, "total_fat_g": 7, "total_carbs_g": 26},
        "serving_sizes": [{"serving_name": "1 bar (60 g)", "grams": 60.0, "is_default": 1}],
    }
    product_id = store_openfood_product(product, db_path=audit_db)
    known_log = log_consumption(product["barcode"], 1.0, db_path=audit_db)
    unknown_log = log_consumption("0000000000000", 1.0, db_path=audit_db)
    daily_totals = get_daily_totals(audit_db)

    after_counts = {}
    with sqlite3.connect(audit_db) as connection:
        for table in before_counts:
            after_counts[table] = _table_count(connection, table)

    deltas = {
        table: None if before_counts[table] is None or after_counts[table] is None else after_counts[table] - before_counts[table]
        for table in before_counts
    }
    return {
        "product_id": product_id,
        "known_log": known_log,
        "unknown_log": unknown_log,
        "daily_totals": daily_totals,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "deltas": deltas,
    }


def _db_integrity_metrics(audit_db: Path) -> dict[str, Any]:
    with sqlite3.connect(audit_db) as connection:
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        schema_checks = {
            "products_columns": [row[1] for row in connection.execute("PRAGMA table_info(products)").fetchall()] if _table_exists(connection, "products") else [],
            "barcodes_columns": [row[1] for row in connection.execute("PRAGMA table_info(barcodes)").fetchall()] if _table_exists(connection, "barcodes") else [],
            "generic_ingredient_nutrition_columns": [row[1] for row in connection.execute("PRAGMA table_info(generic_ingredient_nutrition)").fetchall()] if _table_exists(connection, "generic_ingredient_nutrition") else [],
        }
        duplicate_checks = {}
        for table, key in [("barcodes", "barcode"), ("core_ingredients", "ingredient"), ("generic_ingredient_nutrition", "ingredient")]:
            if _table_exists(connection, table):
                duplicate_checks[f"{table}_{key}_duplicates"] = int(connection.execute(f"SELECT COUNT(*) FROM (SELECT {key}, COUNT(*) c FROM {table} GROUP BY {key} HAVING c > 1)").fetchone()[0])
        null_checks = {}
        for table, column in [("products", "product_name"), ("barcodes", "barcode"), ("generic_ingredient_nutrition", "ingredient")]:
            if _table_exists(connection, table):
                null_checks[f"{table}_{column}_nulls"] = int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL OR TRIM(CAST({column} AS TEXT)) = ''").fetchone()[0])
        return {
            "table_count": len(tables),
            "tables": tables,
            "foreign_key_error_count": len(foreign_key_errors),
            "foreign_key_errors": [list(item) for item in foreign_key_errors[:5]],
            "schema_checks": schema_checks,
            "duplicate_checks": duplicate_checks,
            "null_checks": null_checks,
        }


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _streamlit_headless_smoke(output_root: Path) -> dict[str, Any]:
    app_path = PROJECT_ROOT / "food_ai" / "ui" / "app.py"
    if not app_path.exists():
        return {"status": "FAIL", "message": f"Streamlit app not found: {app_path}", "artifact": None}

    port = _find_free_port()
    log_path = output_root / "streamlit_headless.log"
    command = [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.headless", "true", "--server.port", str(port), "--browser.gatherUsageStats", "false"]
    with log_path.open("w", encoding="utf-8") as handle:
        try:
            process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), stdout=handle, stderr=subprocess.STDOUT)
        except Exception as exc:
            return {"status": "FAIL", "message": f"Could not launch streamlit: {type(exc).__name__}: {exc}", "artifact": str(log_path)}
        try:
            deadline = time.time() + 20
            last_error: str | None = None
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=1.5) as response:
                        body = response.read(256).decode("utf-8", errors="ignore")
                        return {"status": "PASS", "message": f"Streamlit responded on port {port}", "artifact": str(log_path), "http_status": response.status, "body_prefix": body}
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    time.sleep(1)
            return {
                "status": "FAIL" if process.poll() is not None else "WARN",
                "message": "Streamlit did not become reachable before timeout" if process.poll() is None else f"Streamlit exited early with code {process.returncode}",
                "artifact": str(log_path),
                "last_error": last_error,
                "exit_code": process.poll(),
            }
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

def run_audit(output_root: Path) -> AuditRunner:
    runner = AuditRunner(output_root)
    try:
        recipe_path = resolve_recipe_data_path()
        usda_food_path = USDA_DATA_DIR / "food.csv"
        food101_dir = FOOD101_DATA_DIR
        shared_db_path = DB_PATH

        source_metrics = {
            "recipe_csv_exists": recipe_path.exists(),
            "usda_food_exists": usda_food_path.exists(),
            "food101_exists": food101_dir.exists(),
            "db_exists": shared_db_path.exists(),
        }
        sample_output: dict[str, Any] = {}
        status = "PASS"
        if recipe_path.exists():
            source_metrics["recipe_row_count"] = _line_count(recipe_path)
            sample_output["recipe_rows"] = _sample_csv_rows(recipe_path)
        else:
            status = "FAIL"
        if usda_food_path.exists():
            source_metrics["usda_row_count"] = _line_count(usda_food_path)
            sample_output["usda_rows"] = _sample_csv_rows(usda_food_path)
        else:
            status = "FAIL"
        if food101_dir.exists():
            source_metrics["food101"] = _food101_metrics(food101_dir)
        else:
            status = "FAIL"
        runner.record(Checkpoint("source discovery", status, {"recipe_csv": str(recipe_path), "usda_food_csv": str(usda_food_path), "food101_dir": str(food101_dir), "db_path": str(shared_db_path)}, source_metrics, sample_output, [str(path) for path in [recipe_path, usda_food_path, food101_dir, shared_db_path] if Path(path).exists()], "raw transform complete"))

        transform_dir = output_root / "transforms"
        recipe_manifest_path = transform_dir / "recipenlg_manifest.jsonl"
        usda_backbone_path = transform_dir / "usda_backbone.csv"
        transform_status = "PASS"
        transform_notes: list[str] = []
        try:
            build_recipe_manifest(recipe_path, recipe_manifest_path)
            recipe_manifest_count = sum(1 for _ in recipe_manifest_path.open("r", encoding="utf-8"))
        except Exception as exc:
            transform_status = "FAIL"
            recipe_manifest_count = 0
            transform_notes.append(f"recipe_manifest_failed: {type(exc).__name__}: {exc}")
        try:
            usda_backbone_result = build_usda_backbone(usda_backbone_path, food_csv_path=usda_food_path)
        except Exception as exc:
            transform_status = "FAIL"
            usda_backbone_result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            transform_notes.append(f"usda_backbone_failed: {type(exc).__name__}: {exc}")
        runner.record(Checkpoint("raw transform complete", transform_status, {"recipe_csv": str(recipe_path), "usda_food_csv": str(usda_food_path)}, {"recipe_manifest_row_count": recipe_manifest_count, "usda_backbone": usda_backbone_result}, {"recipe_manifest_rows": _sample_jsonl_rows(recipe_manifest_path) if recipe_manifest_path.exists() else [], "usda_backbone_rows": _sample_csv_rows(usda_backbone_path) if usda_backbone_path.exists() else []}, [str(path) for path in [recipe_manifest_path, usda_backbone_path] if path.exists()], "clean dataset validation", "; ".join(transform_notes) if transform_notes else None))

        validation_status = "PASS"
        validation_notes: list[str] = []
        manifest_metrics = {}
        if recipe_manifest_path.exists():
            recipe_manifest_frame = pd.read_json(recipe_manifest_path, lines=True)
            manifest_metrics = {"row_count": int(len(recipe_manifest_frame)), "null_title_count": int(recipe_manifest_frame["title"].isna().sum()), "duplicate_recipe_id_count": int(recipe_manifest_frame["recipe_id"].duplicated().sum()), "schema_columns": recipe_manifest_frame.columns.tolist()}
            if manifest_metrics["null_title_count"] > 0 or manifest_metrics["duplicate_recipe_id_count"] > 0:
                validation_status = "WARN"
                validation_notes.append("recipe manifest contains null titles or duplicate ids")
        else:
            validation_status = "FAIL"
            validation_notes.append("recipe manifest missing")
        backbone_metrics = {}
        if usda_backbone_path.exists():
            usda_backbone_frame = pd.read_csv(usda_backbone_path)
            backbone_metrics = {"row_count": int(len(usda_backbone_frame)), "null_description_count": int(usda_backbone_frame["description"].fillna("").eq("").sum()), "null_normalized_ingredient_count": int(usda_backbone_frame["normalized_ingredient"].fillna("").eq("").sum()), "duplicate_fdc_id_count": int(usda_backbone_frame["fdc_id"].duplicated().sum()), "schema_columns": usda_backbone_frame.columns.tolist()}
            if backbone_metrics["null_description_count"] > 0 or backbone_metrics["duplicate_fdc_id_count"] > 0:
                validation_status = "WARN"
                validation_notes.append("USDA backbone contains null descriptions or duplicate fdc_id values")
        else:
            validation_status = "FAIL"
            validation_notes.append("USDA backbone missing")
        runner.record(Checkpoint("clean dataset validation", validation_status, {"recipe_manifest": str(recipe_manifest_path), "usda_backbone": str(usda_backbone_path)}, {"recipe_manifest": manifest_metrics, "usda_backbone": backbone_metrics}, {"recipe_manifest_rows": _sample_jsonl_rows(recipe_manifest_path) if recipe_manifest_path.exists() else [], "usda_backbone_rows": _sample_csv_rows(usda_backbone_path) if usda_backbone_path.exists() else []}, [str(path) for path in [recipe_manifest_path, usda_backbone_path] if path.exists()], "USDA embedding readiness", "; ".join(validation_notes) if validation_notes else None))

        embeddings_dir = output_root / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        audit_embeddings_path = embeddings_dir / "usda_embeddings.pkl"
        embedding_status = "PASS"
        embedding_note = None
        try:
            build_usda_embeddings(output_path=audit_embeddings_path, food_csv_path=usda_food_path, model_name="BAAI/bge-small-en-v1.5", local_files_only=False)
            embedding_payload = load_usda_embeddings(audit_embeddings_path)
        except Exception as exc:
            embedding_status = "FAIL"
            embedding_payload = {"backend": "error", "error": f"{type(exc).__name__}: {exc}"}
            embedding_note = embedding_payload["error"]
        else:
            if str(embedding_payload.get("backend")) == "deterministic_fallback":
                embedding_status = "WARN"
                embedding_note = str(embedding_payload.get("fallback_reason") or "deterministic fallback triggered")
        sample_embedding_output = {"backend": embedding_payload.get("backend"), "model_name": embedding_payload.get("model_name"), "device": embedding_payload.get("device"), "sample_fdc_ids": [int(item) for item in list(embedding_payload.get("fdc_ids", [])[:5])], "sample_descriptions": [str(item) for item in list(embedding_payload.get("descriptions", [])[:3])]}
        metrics = {"backend": embedding_payload.get("backend"), "count": int(embedding_payload.get("count") or 0), "dim": int(embedding_payload.get("dim") or 0), "fallback_reason": embedding_payload.get("fallback_reason")}
        runner.record(Checkpoint("USDA embedding readiness", embedding_status, {"usda_food_csv": str(usda_food_path), "output_embeddings": str(audit_embeddings_path)}, metrics, sample_embedding_output, [str(audit_embeddings_path)] if audit_embeddings_path.exists() else [], "ingredient normalization validation", embedding_note))
        sample_ingredients = ["1 large diced green pepper", "2 tbsp olive oil", "1 lb ground turkey", "1 tsp. vanilla", "salt and pepper to taste"]
        normalization_samples = []
        normalization_status = "PASS"
        matched_count = 0
        unmatched_count = 0
        multi_fragment_count = 0
        for item in sample_ingredients:
            metadata = normalize_ingredient_with_metadata(item)
            match = match_ingredient_to_usda(item, embeddings_path=audit_embeddings_path, model_name="BAAI/bge-small-en-v1.5", local_files_only=False)
            component_matches = match.get("component_matches") or []
            matched_components = sum(1 for component in component_matches if component.get("usda_match_id") is not None)
            if component_matches:
                multi_fragment_count += 1
            if match.get("usda_match_id") is not None or matched_components > 0:
                matched_count += 1
            else:
                unmatched_count += 1
                normalization_status = "WARN"
            normalization_samples.append({"raw_ingredient": item, "cleaned": metadata.get("cleaned"), "canonical": metadata.get("canonical"), "alias_applied": metadata.get("alias_applied"), "normalization_status": match.get("normalization_status") or metadata.get("normalization_status"), "usda_match_method": match.get("usda_match_method"), "usda_match_id": match.get("usda_match_id"), "usda_match_label": match.get("usda_match_label"), "embedding_backend": match.get("embedding_backend"), "confidence": match.get("confidence"), "component_matches": component_matches})
        normalization_path = output_root / "ingredient_normalization_samples.json"
        normalization_path.write_text(json.dumps(normalization_samples, indent=2, default=str), encoding="utf-8")
        runner.record(Checkpoint("ingredient normalization validation", normalization_status, {"sample_ingredients": sample_ingredients, "embeddings_path": str(audit_embeddings_path)}, {"sample_count": len(normalization_samples), "matched_count": matched_count, "unmatched_count": unmatched_count, "multi_fragment_count": multi_fragment_count}, normalization_samples, [str(normalization_path)], "DB load complete"))

        db_dir = output_root / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        audit_db = _copy_or_init_db(db_dir / "palm_audit.db")
        db_before_metrics = {}
        with sqlite3.connect(audit_db) as connection:
            for table in ["core_ingredients", "generic_foods", "generic_food_nutrition", "generic_ingredient_nutrition", "products", "barcodes", "serving_sizes", "nutrition_facts", "nutrition_micros", "consumption_logs", "unknown_barcodes"]:
                db_before_metrics[f"before_{table}"] = _table_count(connection, table)
        core_count = rebuild_core_ingredients(recipe_path, db_path=audit_db, min_recipe_count=2)
        generic_food_nutrition_updates = build_generic_foods(audit_db)
        barcode_flow = _seed_known_product_and_log(audit_db)
        db_after_metrics = {}
        with sqlite3.connect(audit_db) as connection:
            for table in ["core_ingredients", "generic_foods", "generic_food_nutrition", "generic_ingredient_nutrition", "products", "barcodes", "serving_sizes", "nutrition_facts", "nutrition_micros", "consumption_logs", "unknown_barcodes"]:
                db_after_metrics[f"after_{table}"] = _table_count(connection, table)
            db_sample_output = {"core_ingredients": _sample_table_rows(connection, "core_ingredients", limit=3), "generic_food_nutrition": _sample_table_rows(connection, "generic_food_nutrition", limit=3), "barcode_flow": {"known_log": barcode_flow["known_log"], "unknown_log": barcode_flow["unknown_log"], "daily_totals": barcode_flow["daily_totals"]}}
        db_metrics = {**db_before_metrics, **db_after_metrics, "core_ingredients_rebuilt_count": core_count, "generic_food_nutrition_updates": generic_food_nutrition_updates, "barcode_flow_deltas": barcode_flow["deltas"]}
        runner.record(Checkpoint("DB load complete", "PASS", {"audit_db": str(audit_db), "recipe_csv": str(recipe_path)}, db_metrics, db_sample_output, [str(audit_db)], "DB integrity check"))

        integrity_metrics = _db_integrity_metrics(audit_db)
        integrity_status = "PASS"
        if integrity_metrics["foreign_key_error_count"] > 0 or any(value > 0 for value in integrity_metrics["duplicate_checks"].values()):
            integrity_status = "WARN"
        with sqlite3.connect(audit_db) as connection:
            integrity_sample_output = {"products": _sample_table_rows(connection, "products", limit=3), "barcodes": _sample_table_rows(connection, "barcodes", limit=3), "consumption_logs": _sample_table_rows(connection, "consumption_logs", limit=3)}
        runner.record(Checkpoint("DB integrity check", integrity_status, {"audit_db": str(audit_db)}, integrity_metrics, integrity_sample_output, [str(audit_db)], "inference smoke"))

        inference_dir = output_root / "inference"
        inference_dir.mkdir(parents=True, exist_ok=True)
        smoke_image = PROJECT_ROOT / "food_ai" / "tests" / "food_1.jpg"
        try:
            dish_result = predict_dish(image_path=smoke_image)
            dish_rows = [
                {
                    "label": prediction.label,
                    "name": prediction.name,
                    "score": prediction.confidence,
                    "confidence": prediction.confidence,
                    "source": dish_result.backend,
                }
                for prediction in dish_result.predictions
            ]
            inference_result = {
                "image_path": dish_result.image_path,
                "dish_classifier_topk": dish_rows,
                "dish_fused_topk": dish_rows,
                "confidence": {"overall": dish_rows[0]["confidence"] if dish_rows else None},
                "warnings": [],
                "palm_ready": {"status": "classifier_only"},
                "metadata": {
                    "inference_mode": "classifier_only",
                    "backend": dish_result.backend,
                    "model_path": dish_result.model_path,
                },
            }
        except Exception as exc:
            inference_result = {
                "image_path": str(smoke_image),
                "dish_classifier_topk": [],
                "dish_fused_topk": [],
                "confidence": {"overall": None},
                "warnings": ["classifier_inference_failed"],
                "palm_ready": {"status": "not_ready"},
                "metadata": {
                    "inference_mode": "classifier_only",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            }
        inference_result_path = inference_dir / "inference_result.json"
        inference_result_path.write_text(json.dumps(inference_result, indent=2, default=str), encoding="utf-8")
        inference_warnings = set(inference_result.get("warnings") or [])
        inference_status = "PASS" if inference_result.get("dish_fused_topk") else "FAIL"
        inference_note = None if inference_status == "PASS" else (inference_result.get("metadata") or {}).get("error")
        inference_artifacts = [str(inference_result_path)]
        run_report_path = (inference_result.get("metadata") or {}).get("run_report_path")
        if run_report_path:
            inference_artifacts.append(str(run_report_path))
        runner.record(Checkpoint("inference smoke", inference_status, {"image_path": str(smoke_image), "audit_db": str(audit_db)}, {"dish_count": len(inference_result.get("dish_fused_topk") or []), "visible_ingredient_count": len(inference_result.get("visible_ingredients") or []), "recipe_count": len(inference_result.get("candidate_recipes") or []), "warnings_count": len(inference_result.get("warnings") or []), "warnings": sorted(inference_warnings), "embedding_backend": (inference_result.get("usda_status") or {}).get("embedding_backend"), "palm_ready_status": (inference_result.get("palm_ready") or {}).get("status"), "classifier_checkpoint_exists": ((inference_result.get("metadata") or {}).get("classifier_checkpoint_exists")), "calibration_artifact_exists": ((inference_result.get("metadata") or {}).get("calibration_artifact_exists")), "inference_mode": ((inference_result.get("metadata") or {}).get("inference_mode"))}, {"top_dishes": (inference_result.get("dish_fused_topk") or [])[:3], "visible_ingredients": (inference_result.get("visible_ingredients") or [])[:5], "candidate_recipes": (inference_result.get("candidate_recipes") or [])[:3], "palm_ready": inference_result.get("palm_ready"), "fallbacks": inference_result.get("dish_fusion"), "classifier_assets": (inference_result.get("debug") or {}).get("classifier_assets")}, inference_artifacts, "logging/artifact generation", inference_note))

        ui_logs_dir = output_root / "ui_logs"
        test_log_path = save_test_log(image_path=smoke_image, inference_result=inference_result, notes="audit runner log save", review_status="not_reviewed", logs_dir=ui_logs_dir)
        runner.record(Checkpoint("logging/artifact generation", "PASS", {"image_path": str(smoke_image), "logs_dir": str(ui_logs_dir)}, {"test_log_exists": test_log_path.exists(), "record_count": len(list((ui_logs_dir / "records").glob("*.json"))), "image_copy_count": len(list((ui_logs_dir / "images").glob("*")))}, json.loads(test_log_path.read_text(encoding="utf-8")), [str(test_log_path)], "UI service smoke"))
        ui_options = UIInferenceOptions(save_run_report=True, run_report_dir=output_root / "ui_service_reports", local_files_only=False)
        ui_inference = run_food_inference(smoke_image, ui_options, db_path=audit_db)
        formatted = format_result_for_ui(ui_inference)
        missing_sections = [section for section in ["summary", "dishes", "ingredients", "recipes", "nutrition", "debug"] if section not in formatted]
        ui_service_status = "PASS" if not missing_sections else "FAIL"
        ui_service_artifacts: list[str] = []
        ui_run_report = (ui_inference.get("metadata") or {}).get("run_report_path")
        if ui_run_report:
            ui_service_artifacts.append(str(ui_run_report))
        runner.record(Checkpoint("UI service smoke", ui_service_status, {"image_path": str(smoke_image), "audit_db": str(audit_db)}, {"missing_sections": missing_sections, "summary_top_dish": (formatted.get("summary") or {}).get("top_dish"), "fused_count": len((formatted.get("dishes") or {}).get("fused") or []), "normalization_rows": len((formatted.get("ingredients") or {}).get("normalization") or [])}, {"summary": formatted.get("summary"), "dishes": (formatted.get("dishes") or {}).get("fused"), "ingredients_normalization": (formatted.get("ingredients") or {}).get("normalization")}, ui_service_artifacts, "UI user-flow smoke"))

        streamlit_smoke = _streamlit_headless_smoke(output_root)
        ui_flow_status = streamlit_smoke.pop("status")
        ui_flow_note = "Automated user-flow smoke covers headless Streamlit launch, inference service execution, formatted panel data, and local save_test_log. Manual-only checks remaining: visually confirm panel layout, image preview rendering, button clicks, and browser-side interactions."
        streamlit_artifacts = [path for path in [streamlit_smoke.get("artifact"), str(test_log_path)] if path]
        runner.record(Checkpoint("UI user-flow smoke", ui_flow_status, {"app_path": str(PROJECT_ROOT / 'food_ai' / 'ui' / 'app.py'), "smoke_image": str(smoke_image)}, {"streamlit_message": streamlit_smoke.get("message"), "http_status": streamlit_smoke.get("http_status"), "ui_log_record_exists": test_log_path.exists(), "formatted_panel_counts": {"dishes": len((formatted.get("dishes") or {}).get("fused") or []), "recipes": len(formatted.get("recipes") or []), "ingredients": len((formatted.get("ingredients") or {}).get("ranked") or [])}}, {"streamlit": streamlit_smoke, "saved_log_path": str(test_log_path), "formatted_summary": formatted.get("summary")}, streamlit_artifacts, "final summary", ui_flow_note))

        summary_status = "PASS" if all(item.status == "PASS" for item in runner.checkpoints) else "WARN"
        runner.record(Checkpoint("final summary", summary_status, {"audit_output_root": str(output_root)}, {"checkpoint_count": len(runner.checkpoints), "pass_count": sum(1 for item in runner.checkpoints if item.status == "PASS"), "warn_count": sum(1 for item in runner.checkpoints if item.status == "WARN"), "fail_count": sum(1 for item in runner.checkpoints if item.status == "FAIL"), "skip_count": sum(1 for item in runner.checkpoints if item.status == "SKIP")}, {"statuses": [{"name": item.name, "status": item.status} for item in runner.checkpoints]}, runner.artifacts, "none", "Manual-only UI checks remain for visual confirmation in a browser session."))
        runner.finalize()
        return runner
    finally:
        runner.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a checkpointed PALM full-pipeline audit.")
    parser.add_argument("--output-root", type=Path, default=ARTIFACTS_DIR / "audit" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()
    run_audit(args.output_root)


if __name__ == "__main__":
    main()

