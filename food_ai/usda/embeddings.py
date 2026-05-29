from __future__ import annotations

import csv
import hashlib
import logging
import pickle
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from food_ai.config import ARTIFACTS_DIR, DATA_DIR


logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBEDDINGS_PATH = DATA_DIR / "usda_embeddings.pkl"
EMBEDDINGS_PATH = DEFAULT_EMBEDDINGS_PATH
DEFAULT_MODEL_CACHE_DIR = ARTIFACTS_DIR / "models" / "sentence_transformers"
DEFAULT_FALLBACK_DIM = 64
ALLOWED_DATA_TYPES = {
    "foundation_food",
    "sr_legacy_food",
    "survey_fndds_food",
    "sample_food",
    "agricultural_acquisition",
    "market_acquistion",
    "market_acquisition",
    "experimental_food",
}


class DeterministicUsdaEmbedder:
    def __init__(self, dim: int = DEFAULT_FALLBACK_DIM) -> None:
        self.dim = dim
        self.model_name = f"hash-token-{dim}d-v1"

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = [_hash_vector(text, self.dim) for text in texts]
        if not vectors:
            return np.empty((0, self.dim), dtype=np.float32)
        return np.vstack(vectors).astype(np.float32)



def _hash_vector(text: str, dim: int) -> np.ndarray:
    vector = np.zeros(dim, dtype=np.float32)
    for token in str(text).lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % dim
        vector[index] += 1.0
    norm = float(np.linalg.norm(vector))
    if norm > 0.0:
        vector /= norm
    return vector



def _empty_payload(expected_model_name: str | None = None) -> dict[str, Any]:
    return {
        "model_name": expected_model_name or MODEL_NAME,
        "backend": "unknown",
        "device": None,
        "source_path": None,
        "fdc_ids": np.asarray([], dtype=np.int64),
        "descriptions": np.asarray([], dtype=object),
        "embeddings": np.empty((0, DEFAULT_FALLBACK_DIM), dtype=np.float32),
        "count": 0,
        "dim": DEFAULT_FALLBACK_DIM,
        "fallback_reason": None,
    }



def _import_torch() -> Any | None:
    try:
        import torch
    except Exception:
        return None
    return torch



def _import_sentence_transformer() -> Any | None:
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None
    return SentenceTransformer



def get_device() -> str:
    torch_mod = _import_torch()
    if torch_mod is not None:
        try:
            if torch_mod.cuda.is_available():
                return "cuda"
        except Exception:
            return "cpu"
    return "cpu"



def _build_deterministic_backend(reason: str) -> dict[str, Any]:
    return {
        "backend": "deterministic_fallback",
        "device": "cpu",
        "model_name": f"hash-token-{DEFAULT_FALLBACK_DIM}d-v1",
        "model": DeterministicUsdaEmbedder(DEFAULT_FALLBACK_DIM),
        "fallback_triggered": True,
        "fallback_reason": reason,
    }


@lru_cache(maxsize=4)
def load_usda_embedder(
    model_name: str = MODEL_NAME,
    cache_dir: str | Path = DEFAULT_MODEL_CACHE_DIR,
    local_files_only: bool = True,
) -> dict[str, Any]:
    sentence_transformer_cls = _import_sentence_transformer()
    if sentence_transformer_cls is None:
        reason = "sentence_transformers import failed"
        logger.warning("USDA embedder fallback: %s", reason)
        return _build_deterministic_backend(reason)

    torch_mod = _import_torch()
    device = "cpu"
    if torch_mod is not None:
        try:
            if torch_mod.cuda.is_available():
                device = "cuda"
        except Exception as exc:  # pragma: no cover - environment specific
            logger.warning("CUDA probe failed for USDA embeddings: %s", exc)
    kwargs = {
        "device": device,
        "cache_folder": str(Path(cache_dir)),
    }
    if local_files_only:
        kwargs["local_files_only"] = True
    try:
        model = sentence_transformer_cls(model_name, **kwargs)
        backend = f"sentence_transformers_{device}"
        logger.info("Loaded USDA embedder backend=%s model=%s", backend, model_name)
        return {
            "backend": backend,
            "device": device,
            "model_name": model_name,
            "model": model,
            "fallback_triggered": False,
            "fallback_reason": None,
        }
    except TypeError:
        kwargs.pop("local_files_only", None)
        try:
            model = sentence_transformer_cls(model_name, **kwargs)
            backend = f"sentence_transformers_{device}"
            logger.info("Loaded USDA embedder backend=%s model=%s", backend, model_name)
            return {
                "backend": backend,
                "device": device,
                "model_name": model_name,
                "model": model,
                "fallback_triggered": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # pragma: no cover - model load environment specific
            reason = f"model load failed: {type(exc).__name__}: {exc}"
            logger.warning("USDA embedder fallback: %s", reason)
            return _build_deterministic_backend(reason)
    except Exception as exc:  # pragma: no cover - model load environment specific
        reason = f"model load failed: {type(exc).__name__}: {exc}"
        logger.warning("USDA embedder fallback: %s", reason)
        return _build_deterministic_backend(reason)



def get_model(model_name: str = MODEL_NAME, cache_dir: str | Path = DEFAULT_MODEL_CACHE_DIR, local_files_only: bool = True) -> Any:
    return load_usda_embedder(model_name=model_name, cache_dir=cache_dir, local_files_only=local_files_only).get("model")



def _encode_texts(texts: list[str], backend_info: dict[str, Any]) -> np.ndarray:
    if not texts:
        dim = DEFAULT_FALLBACK_DIM
        model = backend_info.get("model")
        if hasattr(model, "get_sentence_embedding_dimension"):
            dim = int(model.get_sentence_embedding_dimension())
        elif isinstance(model, DeterministicUsdaEmbedder):
            dim = model.dim
        return np.empty((0, dim), dtype=np.float32)

    model = backend_info.get("model")
    backend = str(backend_info.get("backend") or "")
    if backend.startswith("sentence_transformers") and model is not None:
        vectors = model.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return np.asarray(vectors, dtype=np.float32)
    if isinstance(model, DeterministicUsdaEmbedder):
        return model.encode(texts)
    return np.vstack([_hash_vector(text, DEFAULT_FALLBACK_DIM) for text in texts]).astype(np.float32)



def _iter_usda_rows(source_path: Path, include_branded: bool) -> tuple[list[int], list[str]]:
    allowed_types = set(ALLOWED_DATA_TYPES)
    if include_branded:
        allowed_types.add("branded_food")

    fdc_ids: list[int] = []
    descriptions: list[str] = []
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            data_type = str(row.get("data_type", "")).strip()
            if data_type and data_type not in allowed_types:
                continue
            description = str(row.get("description", "")).strip()
            fdc_id = row.get("fdc_id")
            if not description or not fdc_id:
                continue
            fdc_ids.append(int(fdc_id))
            descriptions.append(description)
    return fdc_ids, descriptions



def load_usda_embeddings(
    path: str | Path = DEFAULT_EMBEDDINGS_PATH,
    expected_model_name: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    resolved_path = Path(path)
    if not resolved_path.exists():
        return _empty_payload(expected_model_name)
    with resolved_path.open("rb") as handle:
        payload = pickle.load(handle)
    if not isinstance(payload, dict):
        return _empty_payload(expected_model_name)
    if expected_model_name and payload.get("model_name") not in {None, expected_model_name}:
        return _empty_payload(expected_model_name)
    payload.setdefault("backend", "unknown")
    payload.setdefault("device", None)
    payload.setdefault("fallback_reason", None)
    payload.setdefault("fdc_ids", np.asarray([], dtype=np.int64))
    payload.setdefault("descriptions", np.asarray([], dtype=object))
    payload.setdefault("embeddings", np.empty((0, DEFAULT_FALLBACK_DIM), dtype=np.float32))
    fdc_ids = payload.get("fdc_ids")
    payload.setdefault("count", len(fdc_ids) if fdc_ids is not None else 0)
    embeddings = payload.get("embeddings")
    payload.setdefault(
        "dim",
        int(embeddings.shape[1]) if getattr(embeddings, "ndim", 0) == 2 else DEFAULT_FALLBACK_DIM,
    )
    return payload



def _write_payload(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    with temp_path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temp_path.replace(destination)



def build_or_load_usda_embeddings(
    source_path: str | Path,
    output_path: str | Path = DEFAULT_EMBEDDINGS_PATH,
    model_name: str = MODEL_NAME,
    local_files_only: bool = True,
    force_rebuild: bool = False,
    include_branded: bool = False,
) -> dict[str, Any]:
    resolved_source = Path(source_path)
    resolved_output = Path(output_path)
    existing = load_usda_embeddings(resolved_output)
    backend_info = load_usda_embedder(model_name=model_name, local_files_only=local_files_only)
    semantic_backend = str(backend_info.get("backend") or "").startswith("sentence_transformers")

    needs_rebuild = force_rebuild or not resolved_output.exists()
    if not needs_rebuild and existing.get("source_path") and Path(str(existing.get("source_path"))) != resolved_source:
        needs_rebuild = True
    if not needs_rebuild and semantic_backend and not str(existing.get("backend") or "").startswith("sentence_transformers"):
        needs_rebuild = True
    if not needs_rebuild and semantic_backend and existing.get("model_name") != model_name:
        needs_rebuild = True

    if not needs_rebuild:
        return existing

    fdc_ids, descriptions = _iter_usda_rows(resolved_source, include_branded=include_branded)
    embeddings = _encode_texts(descriptions, backend_info)
    payload = {
        "model_name": str(backend_info.get("model_name") or model_name),
        "backend": backend_info.get("backend"),
        "device": backend_info.get("device"),
        "source_path": str(resolved_source),
        "fdc_ids": np.asarray(fdc_ids, dtype=np.int64),
        "descriptions": np.asarray(descriptions, dtype=object),
        "embeddings": np.asarray(embeddings, dtype=np.float32),
        "count": len(fdc_ids),
        "dim": int(embeddings.shape[1]) if getattr(embeddings, "ndim", 0) == 2 and len(descriptions) > 0 else DEFAULT_FALLBACK_DIM,
        "fallback_reason": backend_info.get("fallback_reason"),
        "include_branded": include_branded,
    }
    _write_payload(payload, resolved_output)
    logger.info(
        "Built USDA embeddings: backend=%s count=%s output=%s",
        payload["backend"],
        payload["count"],
        resolved_output,
    )
    return payload



def cosine_similarity_search(
    query_text: str,
    payload: dict[str, Any],
    top_k: int = 3,
    local_files_only: bool = True,
) -> list[dict[str, Any]]:
    payload_embeddings = payload.get("embeddings")
    if payload_embeddings is None:
        payload_embeddings = np.empty((0, DEFAULT_FALLBACK_DIM), dtype=np.float32)
    embeddings = np.asarray(payload_embeddings, dtype=np.float32)
    if embeddings.size == 0:
        return []
    backend = str(payload.get("backend") or "")
    if backend.startswith("sentence_transformers"):
        backend_info = load_usda_embedder(model_name=str(payload.get("model_name") or MODEL_NAME), local_files_only=local_files_only)
        if not str(backend_info.get("backend") or "").startswith("sentence_transformers"):
            logger.warning(
                "USDA semantic search unavailable at runtime; payload backend=%s runtime backend=%s",
                backend,
                backend_info.get("backend"),
            )
            return []
        query_vector = _encode_texts([query_text], backend_info)[0]
    else:
        query_vector = _hash_vector(query_text, int(payload.get("dim") or DEFAULT_FALLBACK_DIM))

    scores = embeddings @ query_vector
    ranked_indices = np.argsort(scores)[::-1][: max(1, top_k)]
    fdc_ids = payload.get("fdc_ids")
    descriptions = payload.get("descriptions")
    if fdc_ids is None:
        fdc_ids = np.asarray([], dtype=np.int64)
    if descriptions is None:
        descriptions = np.asarray([], dtype=object)
    results: list[dict[str, Any]] = []
    for index in ranked_indices:
        results.append(
            {
                "fdc_id": int(fdc_ids[index]),
                "description": str(descriptions[index]),
                "score": round(float(scores[index]), 6),
                "backend": backend,
            }
        )
    return results

