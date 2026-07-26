from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import calendar
import multiprocessing
import os
from pathlib import Path
import queue
import traceback
import uuid
from typing import Any, Callable, Iterator, Sequence

import numpy as np
from sklearn.decomposition import IncrementalPCA

from raelyn.services.event_map_domain import enrich_event_map_type_categories


EVENT_MAP_PROJECTION_METHOD = "incremental_pca50_umap3_cosine"
EVENT_MAP_PROJECTION_VERSION = "event_map_projection_v2"
EVENT_MAP_PROJECTION_SEED = 42
EVENT_MAP_PRECISION_CODES = {"unknown": 0, "year": 1, "month": 2, "day": 3, "second": 4}
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


@dataclass(frozen=True)
class EventMapProjectionStaging:
    directory: Path
    vectors_path: Path
    reduced_path: Path
    coordinates_path: Path
    count: int
    embedding_dim: int
    event_ids: Sequence[uuid.UUID]
    event_start_days: np.ndarray
    event_end_days: np.ndarray
    event_type_codes: np.ndarray
    time_precision_codes: np.ndarray
    categories: list[dict[str, Any]]


@dataclass(frozen=True)
class EventMapProjectionResult:
    coordinates_path: Path
    count: int
    bounds: dict[str, float]
    peak_rss_bytes: int


@dataclass(frozen=True)
class EventMapReductionResult:
    reduced_path: Path
    count: int
    dimension: int
    peak_rss_bytes: int


@dataclass(frozen=True)
class EventMapNeighborResult:
    indices_path: Path
    distances_path: Path
    count: int
    neighbor_count: int
    peak_rss_bytes: int


def _event_time_days(start: datetime, end: datetime | None, precision: str) -> tuple[int, int]:
    start_date = start.date()
    normalized = str(precision or "unknown").strip().lower()
    if normalized == "year":
        start_date = date(start_date.year, 1, 1)
        end_date = date(start_date.year, 12, 31)
    elif normalized == "month":
        start_date = date(start_date.year, start_date.month, 1)
        end_date = date(start_date.year, start_date.month, calendar.monthrange(start_date.year, start_date.month)[1])
    elif end is not None and end.date() >= start_date:
        end_date = end.date()
    else:
        end_date = start_date
    return start_date.toordinal() - _EPOCH_ORDINAL, end_date.toordinal() - _EPOCH_ORDINAL


def stage_event_map_projection(
    rows: Iterator[Any],
    *,
    directory: Path,
    expected_count: int,
    embedding_dim: int,
    batch_size: int,
    checkpoint: Callable[[int], None],
) -> EventMapProjectionStaging:
    count = max(0, int(expected_count))
    vectors_path = directory / "event-vectors.float32"
    reduced_path = directory / "event-vectors-pca.float32"
    coordinates_path = directory / "event-map-coordinates.float32"
    vectors: np.memmap | None = None
    if count:
        vectors = np.memmap(vectors_path, dtype=np.float32, mode="w+", shape=(count, embedding_dim))
    else:
        vectors_path.touch()
    event_ids: list[uuid.UUID] = []
    event_start_days = np.empty(count, dtype=np.int32)
    event_end_days = np.empty(count, dtype=np.int32)
    time_precision_codes = np.empty(count, dtype=np.uint8)
    event_types: list[str] = []

    processed = 0
    for row in rows:
        if processed >= count:
            raise RuntimeError(f"event map staging exceeded expected count {count}")
        vector = row.vector
        if not isinstance(vector, list) or len(vector) != embedding_dim:
            actual_dim = len(vector) if isinstance(vector, list) else 0
            raise RuntimeError(
                f"event map staging found vector dimension {actual_dim} for event {row.event_id}; expected {embedding_dim}"
            )
        event_time_start = row.event_time_start
        if not isinstance(event_time_start, datetime):
            raise RuntimeError(f"event map staging found no event_time_start for event {row.event_id}")
        precision = str(row.time_precision or "unknown").strip().lower()
        start_day, end_day = _event_time_days(event_time_start, row.event_time_end, precision)
        assert vectors is not None
        vectors[processed] = vector
        event_ids.append(row.event_id)
        event_start_days[processed] = start_day
        event_end_days[processed] = end_day
        time_precision_codes[processed] = EVENT_MAP_PRECISION_CODES.get(precision, 0)
        event_types.append(str(row.event_type or "other").strip().lower() or "other")
        processed += 1
        if processed % batch_size == 0:
            vectors.flush()
            checkpoint(processed)

    if vectors is not None:
        vectors.flush()
    checkpoint(processed)
    if processed != count:
        raise RuntimeError(f"event map staging expected {count} events, got {processed}")

    category_values = sorted(set(event_types))
    if len(category_values) > 255:
        category_values = category_values[:254] + ["other"]
    category_by_value = {value: index for index, value in enumerate(category_values)}
    fallback_code = category_by_value.get("other", 0)
    event_type_codes = np.asarray(
        [category_by_value.get(value, fallback_code) for value in event_types],
        dtype=np.uint8,
    )
    categories = enrich_event_map_type_categories(
        [{"code": index, "value": value, "label": value} for index, value in enumerate(category_values)]
    )
    return EventMapProjectionStaging(
        directory=directory,
        vectors_path=vectors_path,
        reduced_path=reduced_path,
        coordinates_path=coordinates_path,
        count=count,
        embedding_dim=embedding_dim,
        event_ids=event_ids,
        event_start_days=event_start_days,
        event_end_days=event_end_days,
        event_type_codes=event_type_codes,
        time_precision_codes=time_precision_codes,
        categories=categories,
    )


def _fit_batch_ranges(count: int, batch_size: int, component_count: int) -> Iterator[tuple[int, int]]:
    start = 0
    while start < count:
        end = min(count, start + batch_size)
        if count - end < component_count:
            end = count
        yield start, end
        start = end


def _proc_rss_bytes(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _process_tree_pids(root_pid: int) -> set[int]:
    pending = [int(root_pid)] if root_pid > 0 else []
    result: set[int] = set()
    while pending:
        pid = pending.pop()
        if pid in result:
            continue
        result.add(pid)
        try:
            children_text = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="utf-8")
        except OSError:
            continue
        for value in children_text.split():
            try:
                child_pid = int(value)
            except ValueError:
                continue
            if child_pid > 0 and child_pid not in result:
                pending.append(child_pid)
    return result


def _process_tree_rss_bytes(root_pid: int) -> int:
    return sum(_proc_rss_bytes(pid) for pid in _process_tree_pids(root_pid))


def _available_memory_bytes() -> int:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _run_umap_child(
    reduced_path: str,
    coordinates_path: str,
    count: int,
    reduced_dim: int,
    neighbor_count: int,
    result_queue: Any,
) -> None:
    try:
        os.environ["NUMBA_NUM_THREADS"] = "1"
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        from umap import UMAP

        reduced_memmap = np.memmap(reduced_path, dtype=np.float32, mode="r", shape=(count, reduced_dim))
        # pynndescent 的 numba RP-tree 内核要求可写 C-contiguous 数组；只复制 PCA50，
        # 不复制原始 1024 维 embedding，峰值仍受父进程树 RSS 门控。
        reduced = np.asarray(reduced_memmap, dtype=np.float32).copy(order="C")
        coordinates = UMAP(
            n_components=3,
            n_neighbors=neighbor_count,
            min_dist=0.1,
            metric="cosine",
            random_state=EVENT_MAP_PROJECTION_SEED,
            transform_seed=EVENT_MAP_PROJECTION_SEED,
            n_jobs=1,
            low_memory=True,
        ).fit_transform(reduced)
        output = np.memmap(coordinates_path, dtype=np.float32, mode="w+", shape=(count, 3))
        output[:] = np.asarray(coordinates, dtype=np.float32)
        output.flush()
        result_queue.put({"ok": True})
    except Exception as exc:
        result_queue.put(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )


def _run_neighbors_child(
    reduced_path: str,
    indices_path: str,
    distances_path: str,
    count: int,
    reduced_dim: int,
    neighbor_count: int,
    result_queue: Any,
) -> None:
    try:
        os.environ["NUMBA_NUM_THREADS"] = "1"
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        from pynndescent import NNDescent

        reduced_memmap = np.memmap(reduced_path, dtype=np.float32, mode="r", shape=(count, reduced_dim))
        reduced = np.asarray(reduced_memmap, dtype=np.float32).copy(order="C")
        index = NNDescent(
            reduced,
            n_neighbors=neighbor_count,
            metric="cosine",
            random_state=EVENT_MAP_PROJECTION_SEED,
            n_jobs=1,
            low_memory=True,
            compressed=True,
        )
        raw_indices, raw_distances = index.neighbor_graph
        indices = np.memmap(indices_path, dtype=np.int32, mode="w+", shape=(count, neighbor_count))
        distances = np.memmap(distances_path, dtype=np.float32, mode="w+", shape=(count, neighbor_count))
        indices[:] = np.asarray(raw_indices, dtype=np.int32)
        distances[:] = np.asarray(raw_distances, dtype=np.float32)
        indices.flush()
        distances.flush()
        result_queue.put({"ok": True})
    except Exception as exc:
        result_queue.put(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )


def _resource_limit_error(total_rss: int, max_rss_bytes: int, available: int, min_available: int) -> str | None:
    # 在公开 12 GiB 上限之前主动终止重型子进程，为异常收尾保留约 1 GiB。
    controlled_max = max(1, int(max_rss_bytes * 11 / 12)) if max_rss_bytes > 0 else 0
    if controlled_max > 0 and total_rss > controlled_max:
        return f"event map process-tree RSS {total_rss} exceeds configured maximum {max_rss_bytes}"
    if min_available > 0 and available > 0 and available < min_available:
        return f"event map available memory {available} is below configured minimum {min_available}"
    return None


def _wait_for_child(
    child: multiprocessing.Process,
    result_queue: Any,
    *,
    cancel_check: Callable[[], None],
    max_rss_bytes: int,
    min_available_memory_bytes: int,
) -> tuple[dict[str, Any], int]:
    peak_rss = _process_tree_rss_bytes(os.getpid())
    try:
        while child.is_alive():
            child.join(timeout=0.5)
            cancel_check()
            total_rss = _process_tree_rss_bytes(os.getpid())
            peak_rss = max(peak_rss, total_rss)
            error = _resource_limit_error(
                total_rss,
                max_rss_bytes,
                _available_memory_bytes(),
                min_available_memory_bytes,
            )
            if error:
                raise RuntimeError(error)
        child.join()
        try:
            result = result_queue.get(timeout=1)
        except queue.Empty as exc:
            raise RuntimeError(f"event map child process exited with code {child.exitcode} without a result") from exc
        if child.exitcode != 0 or not result.get("ok"):
            raise RuntimeError(str(result.get("error") or f"event map child process exited with code {child.exitcode}"))
        return result, max(peak_rss, _process_tree_rss_bytes(os.getpid()))
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()


def compute_event_map_reduction(
    staging: EventMapProjectionStaging,
    *,
    batch_size: int,
    checkpoint: Callable[[int], None],
    cancel_check: Callable[[], None],
    max_rss_bytes: int,
    min_available_memory_bytes: int,
) -> EventMapReductionResult:
    count = staging.count
    peak_rss = _process_tree_rss_bytes(os.getpid())
    if count == 0:
        staging.reduced_path.touch()
        return EventMapReductionResult(staging.reduced_path, 0, 0, peak_rss)

    vectors = np.memmap(
        staging.vectors_path,
        dtype=np.float32,
        mode="r",
        shape=(count, staging.embedding_dim),
    )
    if count <= 2:
        component_count = min(50, staging.embedding_dim)
        reduced = np.memmap(
            staging.reduced_path,
            dtype=np.float32,
            mode="w+",
            shape=(count, component_count),
        )
        batch = np.asarray(vectors[:, :component_count], dtype=np.float32)
        norms = np.linalg.norm(batch, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        reduced[:] = batch / norms
        reduced.flush()
        checkpoint(count)
        cancel_check()
        return EventMapReductionResult(staging.reduced_path, count, component_count, peak_rss)

    component_count = min(50, staging.embedding_dim, count - 1)
    effective_batch_size = max(component_count, int(batch_size))
    reducer = IncrementalPCA(n_components=component_count, batch_size=effective_batch_size)
    processed = 0
    for start, end in _fit_batch_ranges(count, effective_batch_size, component_count):
        reducer.partial_fit(vectors[start:end])
        processed = end
        checkpoint(processed)
        cancel_check()
        total_rss = _process_tree_rss_bytes(os.getpid())
        peak_rss = max(peak_rss, total_rss)
        error = _resource_limit_error(
            total_rss,
            max_rss_bytes,
            _available_memory_bytes(),
            min_available_memory_bytes,
        )
        if error:
            raise RuntimeError(error)

    reduced = np.memmap(
        staging.reduced_path,
        dtype=np.float32,
        mode="w+",
        shape=(count, component_count),
    )
    for start in range(0, count, effective_batch_size):
        end = min(count, start + effective_batch_size)
        batch = np.asarray(reducer.transform(vectors[start:end]), dtype=np.float32)
        norms = np.linalg.norm(batch, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        reduced[start:end] = batch / norms
        reduced.flush()
        checkpoint(count + end)
        cancel_check()
        total_rss = _process_tree_rss_bytes(os.getpid())
        peak_rss = max(peak_rss, total_rss)
        error = _resource_limit_error(
            total_rss,
            max_rss_bytes,
            _available_memory_bytes(),
            min_available_memory_bytes,
        )
        if error:
            raise RuntimeError(error)
    return EventMapReductionResult(staging.reduced_path, count, component_count, peak_rss)


def compute_event_map_neighbors(
    reduction: EventMapReductionResult,
    *,
    directory: Path,
    neighbor_count: int,
    cancel_check: Callable[[], None],
    max_rss_bytes: int,
    min_available_memory_bytes: int,
) -> EventMapNeighborResult:
    count = reduction.count
    effective_neighbors = min(max(1, int(neighbor_count)), max(1, count))
    indices_path = directory / "event-map-neighbor-indices.int32"
    distances_path = directory / "event-map-neighbor-distances.float32"
    if count == 0:
        indices_path.touch()
        distances_path.touch()
        return EventMapNeighborResult(indices_path, distances_path, 0, 0, reduction.peak_rss_bytes)
    if count <= 2:
        indices = np.memmap(indices_path, dtype=np.int32, mode="w+", shape=(count, effective_neighbors))
        distances = np.memmap(distances_path, dtype=np.float32, mode="w+", shape=(count, effective_neighbors))
        reduced = np.memmap(
            reduction.reduced_path,
            dtype=np.float32,
            mode="r",
            shape=(count, reduction.dimension),
        )
        for row in range(count):
            ordered = sorted(
                range(count),
                key=lambda other: (1.0 - float(np.dot(reduced[row], reduced[other])), other),
            )[:effective_neighbors]
            indices[row] = ordered
            distances[row] = [1.0 - float(np.dot(reduced[row], reduced[other])) for other in ordered]
        indices.flush()
        distances.flush()
        return EventMapNeighborResult(
            indices_path,
            distances_path,
            count,
            effective_neighbors,
            max(reduction.peak_rss_bytes, _process_tree_rss_bytes(os.getpid())),
        )

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    child = context.Process(
        target=_run_neighbors_child,
        args=(
            str(reduction.reduced_path),
            str(indices_path),
            str(distances_path),
            count,
            reduction.dimension,
            effective_neighbors,
            result_queue,
        ),
        name="event-map-neighbors",
    )
    child.start()
    _result, peak_rss = _wait_for_child(
        child,
        result_queue,
        cancel_check=cancel_check,
        max_rss_bytes=max_rss_bytes,
        min_available_memory_bytes=min_available_memory_bytes,
    )
    return EventMapNeighborResult(
        indices_path,
        distances_path,
        count,
        effective_neighbors,
        max(reduction.peak_rss_bytes, peak_rss),
    )


def compute_event_map_layout(
    *,
    reduced_path: Path,
    count: int,
    reduced_dim: int,
    coordinates_path: Path,
    cancel_check: Callable[[], None],
    max_rss_bytes: int,
    min_available_memory_bytes: int,
) -> EventMapProjectionResult:
    if count == 0:
        coordinates_path.touch()
        return EventMapProjectionResult(
            coordinates_path,
            0,
            {
                "min_x": 0.0,
                "max_x": 0.0,
                "min_y": 0.0,
                "max_y": 0.0,
                "min_z": 0.0,
                "max_z": 0.0,
            },
            _process_tree_rss_bytes(os.getpid()),
        )
    if count <= 2:
        coordinates = np.memmap(coordinates_path, dtype=np.float32, mode="w+", shape=(count, 3))
        coordinates[0] = (0.0, 0.0, 0.0) if count == 1 else (-1.0, 0.0, 0.0)
        if count == 2:
            coordinates[1] = (1.0, 0.0, 0.0)
        coordinates.flush()
        return EventMapProjectionResult(
            coordinates_path,
            count,
            _coordinate_bounds(coordinates),
            _process_tree_rss_bytes(os.getpid()),
        )

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    child = context.Process(
        target=_run_umap_child,
        args=(
            str(reduced_path),
            str(coordinates_path),
            count,
            reduced_dim,
            min(15, count - 1),
            result_queue,
        ),
        name="event-map-umap",
    )
    child.start()
    _result, peak_rss = _wait_for_child(
        child,
        result_queue,
        cancel_check=cancel_check,
        max_rss_bytes=max_rss_bytes,
        min_available_memory_bytes=min_available_memory_bytes,
    )
    coordinates = np.memmap(coordinates_path, dtype=np.float32, mode="r", shape=(count, 3))
    return EventMapProjectionResult(coordinates_path, count, _coordinate_bounds(coordinates), peak_rss)


def compute_event_map_projection(
    staging: EventMapProjectionStaging,
    *,
    batch_size: int,
    checkpoint: Callable[[int], None],
    cancel_check: Callable[[], None],
    max_rss_bytes: int,
    min_available_memory_bytes: int,
) -> EventMapProjectionResult:
    reduction = compute_event_map_reduction(
        staging,
        batch_size=batch_size,
        checkpoint=checkpoint,
        cancel_check=cancel_check,
        max_rss_bytes=max_rss_bytes,
        min_available_memory_bytes=min_available_memory_bytes,
    )
    result = compute_event_map_layout(
        reduced_path=reduction.reduced_path,
        count=reduction.count,
        reduced_dim=reduction.dimension,
        coordinates_path=staging.coordinates_path,
        cancel_check=cancel_check,
        max_rss_bytes=max_rss_bytes,
        min_available_memory_bytes=min_available_memory_bytes,
    )
    checkpoint(staging.count * 3)
    return EventMapProjectionResult(
        result.coordinates_path,
        result.count,
        result.bounds,
        max(result.peak_rss_bytes, reduction.peak_rss_bytes),
    )


def _coordinate_bounds(coordinates: np.ndarray) -> dict[str, float]:
    if len(coordinates) == 0:
        return {
            "min_x": 0.0,
            "max_x": 0.0,
            "min_y": 0.0,
            "max_y": 0.0,
            "min_z": 0.0,
            "max_z": 0.0,
        }
    return {
        "min_x": float(np.min(coordinates[:, 0])),
        "max_x": float(np.max(coordinates[:, 0])),
        "min_y": float(np.min(coordinates[:, 1])),
        "max_y": float(np.max(coordinates[:, 1])),
        "min_z": float(np.min(coordinates[:, 2])),
        "max_z": float(np.max(coordinates[:, 2])),
    }
