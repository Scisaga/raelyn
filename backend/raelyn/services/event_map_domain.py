from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
import math
import re
import unicodedata
import uuid
from typing import Callable, Iterable, Sequence

import numpy as np
from sklearn.cluster import MiniBatchKMeans


EVENT_MAP_CANONICAL_VERSION = "event_map_canonical_v2"
EVENT_MAP_TOPIC_VERSION = "event_map_topic_hierarchical_kmeans_v2"
EVENT_MAP_STORY_VERSION = "event_map_story_continuation_v1"
EVENT_MAP_RANDOM_SEED = 42

EVENT_MAP_SEMANTIC_FAMILIES = (
    {"code": "macro_policy", "label": "宏观与政策", "color": "#a78bfa"},
    {"code": "market_assets", "label": "市场与资产", "color": "#38bdf8"},
    {"code": "corporate", "label": "公司与资本动作", "color": "#fbbf24"},
    {"code": "industry", "label": "产业与供给链", "color": "#34d399"},
    {"code": "technology", "label": "科技与网络安全", "color": "#e879f9"},
    {"code": "geopolitical", "label": "地缘、法律与监管", "color": "#fb7185"},
    {"code": "consumer_social", "label": "消费与社会", "color": "#fb923c"},
    {"code": "other", "label": "其他", "color": "#94a3b8"},
)
_EVENT_MAP_SEMANTIC_FAMILY_BY_CODE = {
    family["code"]: family for family in EVENT_MAP_SEMANTIC_FAMILIES
}
_EVENT_MAP_EVENT_TYPE_FAMILY = {
    "accounting": "corporate",
    "analyst_rating": "market_assets",
    "banking": "market_assets",
    "bond": "market_assets",
    "capital_expenditure": "corporate",
    "clinical_trial": "industry",
    "commodities": "market_assets",
    "consumer_behavior": "consumer_social",
    "corporate": "corporate",
    "corporate_action": "corporate",
    "corporate_debt": "corporate",
    "corporate_expansion": "corporate",
    "corporate_governance": "corporate",
    "corporate_strategy": "corporate",
    "credit": "market_assets",
    "crypto": "market_assets",
    "cyber": "technology",
    "cybersecurity": "technology",
    "data_blackout": "technology",
    "debt": "corporate",
    "defense": "geopolitical",
    "earnings": "corporate",
    "education": "consumer_social",
    "employment": "macro_policy",
    "energy": "industry",
    "equity": "market_assets",
    "fx": "market_assets",
    "geopolitical": "geopolitical",
    "government_bond": "market_assets",
    "guidance": "corporate",
    "health": "industry",
    "healthcare": "industry",
    "housing": "industry",
    "housing_market": "industry",
    "indicator": "macro_policy",
    "inflation": "macro_policy",
    "infrastructure": "industry",
    "insurance": "industry",
    "investment": "market_assets",
    "ipo": "corporate",
    "labor": "macro_policy",
    "legal": "geopolitical",
    "liquidity": "market_assets",
    "litigation": "geopolitical",
    "logistics": "industry",
    "m_a": "corporate",
    "macro": "macro_policy",
    "market": "market_assets",
    "market_structure": "market_assets",
    "media": "consumer_social",
    "merger": "corporate",
    "merger_acquisition": "corporate",
    "mergers": "corporate",
    "mergers_acquisitions": "corporate",
    "monetary_policy": "macro_policy",
    "mortgage": "industry",
    "muni": "market_assets",
    "muni_bond": "market_assets",
    "operations": "corporate",
    "partnership": "corporate",
    "policy": "macro_policy",
    "political": "geopolitical",
    "product": "corporate",
    "product_launch": "corporate",
    "rates": "macro_policy",
    "real_estate": "industry",
    "regulatory": "geopolitical",
    "risk": "geopolitical",
    "sector": "industry",
    "sectors": "industry",
    "social": "consumer_social",
    "sovereign_debt": "market_assets",
    "strategy": "corporate",
    "supply_chain": "industry",
    "technology": "technology",
    "trade": "geopolitical",
}

_PRECISION_RANK = {"unknown": 0, "year": 1, "month": 2, "day": 3, "second": 4}
_FUZZY_TYPE_PAIRS = {
    frozenset(("policy", "monetary_policy")),
    frozenset(("rates", "monetary_policy")),
}
_IGNORED_ENTITY_ROLES = {"source", "other"}
_ENTITY_ALIASES = {
    "中国": "china",
    "中华人民共和国": "china",
    "prc": "china",
    "美国": "unitedstates",
    "美國": "unitedstates",
    "usa": "unitedstates",
    "us": "unitedstates",
    "u.s": "unitedstates",
    "台湾": "taiwan",
    "台灣": "taiwan",
    "美联储": "federalreserve",
    "美聯儲": "federalreserve",
    "fed": "federalreserve",
    "联准会": "federalreserve",
    "聯準會": "federalreserve",
    "欧洲央行": "europeancentralbank",
    "歐洲央行": "europeancentralbank",
    "ecb": "europeancentralbank",
}
_NUMBER_RE = re.compile(
    r"(?<![a-z0-9_])(?:q[1-4]|[12]\d{3}(?:q[1-4])?|\d+(?:\.\d+)?\s*(?:%|％|bp|bps|亿美元|億美元|万元|萬元|万|萬|亿|億|trillion|billion|million))(?![a-z0-9_])",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+", re.IGNORECASE)
_STORY_PHASES = {
    "announce": ("announce", "announced", "宣布", "公布", "发布", "發布"),
    "approve": ("approve", "approved", "批准", "通过", "通過", "核准"),
    "begin": ("begin", "began", "start", "started", "启动", "啟動", "开始", "開始"),
    "complete": ("complete", "completed", "close", "closed", "完成", "交割", "結案", "结案"),
    "delay": ("delay", "delayed", "postpone", "延期", "推迟", "推遲"),
    "cancel": ("cancel", "cancelled", "canceled", "撤回", "取消", "终止", "終止"),
    "increase": ("increase", "raise", "raised", "上调", "上調", "提高", "加息"),
    "decrease": ("decrease", "cut", "lower", "下调", "下調", "降低", "降息"),
}


def normalize_event_map_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def event_map_semantic_family(event_type: str | None) -> dict[str, str]:
    normalized = re.sub(
        r"[^0-9a-z]+",
        "_",
        unicodedata.normalize("NFKC", str(event_type or "")).casefold().strip(),
    ).strip("_")
    family_code = _EVENT_MAP_EVENT_TYPE_FAMILY.get(normalized, "other")
    return dict(_EVENT_MAP_SEMANTIC_FAMILY_BY_CODE[family_code])


def enrich_event_map_type_categories(
    categories: Iterable[dict[str, object]] | None,
) -> list[dict[str, object]]:
    enriched: list[dict[str, object]] = []
    for category in categories or ():
        item = dict(category)
        family = event_map_semantic_family(str(item.get("value") or item.get("label") or "other"))
        item.update(
            {
                "semantic_family": family["code"],
                "semantic_family_label": family["label"],
                "semantic_color": family["color"],
            }
        )
        enriched.append(item)
    return enriched


def normalize_event_map_entity(entity_type: str, name: str, normalized_key: str | None = None) -> str:
    entity_kind = normalize_event_map_text(entity_type) or "other"
    raw_key = normalize_event_map_text(normalized_key) or normalize_event_map_text(name)
    alias = _ENTITY_ALIASES.get(raw_key, raw_key)
    return f"{entity_kind}:{alias}" if alias else ""


@dataclass(frozen=True, slots=True)
class EventMapEntityRef:
    entity_type: str
    key: str
    name: str
    role: str = "other"
    confidence: float | None = None

    @property
    def canonical_key(self) -> str:
        return normalize_event_map_entity(self.entity_type, self.name, self.key)

    @property
    def is_core(self) -> bool:
        return bool(self.canonical_key) and normalize_event_map_text(self.role) not in _IGNORED_ENTITY_ROLES


@dataclass(frozen=True, slots=True)
class EventMapRelationRef:
    relation_type: str
    confidence: float | None = None
    evidence_text: str | None = None


@dataclass(frozen=True, slots=True)
class EventMapRecord:
    event_id: uuid.UUID
    revision_id: uuid.UUID
    vector_index: int
    title: str
    summary: str
    event_type: str
    direction: str
    start_day: int
    end_day: int
    time_precision: str
    entities: tuple[EventMapEntityRef, ...] = ()
    relations: tuple[EventMapRelationRef, ...] = ()

    @property
    def core_entity_keys(self) -> frozenset[str]:
        return frozenset(entity.canonical_key for entity in self.entities if entity.is_core)

    @property
    def numeric_signature(self) -> frozenset[str]:
        text = unicodedata.normalize("NFKC", f"{self.title} {self.summary}").casefold()
        return frozenset(re.sub(r"\s+", "", match.group(0)) for match in _NUMBER_RE.finditer(text))

    @property
    def exact_key(self) -> tuple[object, ...]:
        return (
            self.start_day,
            self.end_day,
            normalize_event_map_text(self.time_precision),
            normalize_event_map_text(self.event_type),
            normalize_event_map_text(self.direction),
            tuple(sorted(self.core_entity_keys)),
            normalize_event_map_text(self.title),
            tuple(sorted(self.numeric_signature)),
        )


@dataclass(frozen=True, slots=True)
class EventMapMemberDecision:
    record_index: int
    rule: str
    score: float | None
    runner_up_score: float | None
    reasons: tuple[str, ...]


@dataclass(slots=True)
class EventMapCanonicalGroup:
    member_indices: list[int]
    representative_index: int
    decisions: dict[int, EventMapMemberDecision]
    uncertainty_flags: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class EventMapTopicResult:
    topic_index: int
    parent_topic_index: int | None
    member_group_indices: tuple[int, ...]
    label: str
    center: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EventMapStoryEdgeResult:
    source_group_index: int
    target_group_index: int
    relation_type: str
    confidence: float
    evidence: dict[str, object]


@dataclass(frozen=True, slots=True)
class EventMapStoryResult:
    story_index: int
    member_group_indices: tuple[int, ...]
    edges: tuple[EventMapStoryEdgeResult, ...]
    label: str


def _event_types_compatible(left: str, right: str) -> bool:
    a = normalize_event_map_text(left) or "other"
    b = normalize_event_map_text(right) or "other"
    return a == b or frozenset((a, b)) in _FUZZY_TYPE_PAIRS


def _direction_conflicts(left: str, right: str) -> bool:
    neutral = {"", "unknown", "neutral", "mixed"}
    a = normalize_event_map_text(left)
    b = normalize_event_map_text(right)
    return a not in neutral and b not in neutral and a != b


def _entity_overlap(left: EventMapRecord, right: EventMapRecord) -> float:
    a = left.core_entity_keys
    b = right.core_entity_keys
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _time_intersects(left: EventMapRecord, right: EventMapRecord) -> bool:
    return max(left.start_day, right.start_day) <= min(left.end_day, right.end_day)


def _numeric_conflicts(left: EventMapRecord, right: EventMapRecord) -> bool:
    a = left.numeric_signature
    b = right.numeric_signature
    if not a or not b:
        return False

    def by_kind(values: frozenset[str]) -> dict[str, frozenset[str]]:
        buckets: dict[str, set[str]] = defaultdict(set)
        for value in values:
            if re.fullmatch(r"q[1-4]|[12]\d{3}(?:q[1-4])?", value, re.IGNORECASE):
                kind = "period"
            elif value.endswith(("%", "％")):
                kind = "percent"
            elif value.endswith(("bp", "bps")):
                kind = "basis_points"
            else:
                kind = "amount"
            buckets[kind].add(value)
        return {kind: frozenset(items) for kind, items in buckets.items()}

    left_by_kind = by_kind(a)
    right_by_kind = by_kind(b)
    return any(
        left_by_kind[kind] != right_by_kind[kind]
        for kind in left_by_kind.keys() & right_by_kind.keys()
    )


def _fuzzy_pair_constraints(
    left: EventMapRecord,
    right: EventMapRecord,
) -> tuple[bool, tuple[str, ...], float]:
    reasons: list[str] = []
    if not _time_intersects(left, right):
        return False, ("time_disjoint",), 1.0
    if left.time_precision == "year" or right.time_precision == "year":
        return False, ("year_precision_exact_only",), 1.0
    if not _event_types_compatible(left.event_type, right.event_type):
        return False, ("event_type_conflict",), 1.0
    if _direction_conflicts(left.direction, right.direction):
        return False, ("direction_conflict",), 1.0
    overlap = _entity_overlap(left, right)
    if overlap < 0.5:
        return False, ("core_entity_overlap_below_0.5",), 1.0
    if _numeric_conflicts(left, right):
        return False, ("numeric_or_period_conflict",), 1.0
    threshold = 0.90 if "month" in {left.time_precision, right.time_precision} else 0.85
    reasons.extend(("time_overlap", "compatible_event_type", "core_entity_overlap"))
    return True, tuple(reasons), threshold


def _fuzzy_pair_allowed(left: EventMapRecord, right: EventMapRecord, similarity: float) -> tuple[bool, tuple[str, ...]]:
    allowed, reasons, threshold = _fuzzy_pair_constraints(left, right)
    if not allowed:
        return False, reasons
    if similarity < threshold:
        return False, (f"cosine_below_{threshold:.2f}",)
    return True, reasons + (f"cosine_{similarity:.6f}",)


def _normalized_vector(vectors: np.ndarray, index: int) -> np.ndarray:
    vector = np.asarray(vectors[index], dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector if norm <= 0.0 else vector / norm


def _canonical_centroid_for_members(
    member_indices: Sequence[int],
    records: Sequence[EventMapRecord],
    vectors: np.ndarray,
) -> np.ndarray:
    if not member_indices:
        raise ValueError("event map canonical group has no members")
    dimension = int(vectors.shape[1]) if vectors.ndim == 2 else 0
    if dimension <= 0:
        raise ValueError("event map embedding matrix has no vector dimension")
    centroid = np.zeros(dimension, dtype=np.float32)
    ordered_members = sorted(
        member_indices,
        key=lambda index: str(records[index].event_id),
    )
    for record_index in ordered_members:
        vector = _normalized_vector(vectors, records[record_index].vector_index)
        centroid += np.asarray(vector, dtype=np.float32)
    centroid /= np.float32(len(ordered_members))
    norm = np.float32(np.linalg.norm(centroid))
    if norm > 0:
        centroid /= norm
    return np.ascontiguousarray(centroid, dtype=np.float32)


def event_map_canonical_centroid(
    group: EventMapCanonicalGroup,
    records: Sequence[EventMapRecord],
    vectors: np.ndarray,
) -> np.ndarray:
    """返回成员 embedding 的确定性 float32 归一化均值。"""

    return _canonical_centroid_for_members(group.member_indices, records, vectors)


def _vector_norms(vectors: np.ndarray, *, batch_size: int = 4096) -> np.ndarray:
    norms = np.empty(len(vectors), dtype=np.float32)
    for start in range(0, len(vectors), batch_size):
        end = min(len(vectors), start + batch_size)
        norms[start:end] = np.linalg.norm(np.asarray(vectors[start:end], dtype=np.float32), axis=1)
    norms[norms == 0] = 1.0
    return norms


def _cosine_similarity(
    vectors: np.ndarray,
    left: int,
    right: int,
    norms: np.ndarray | None = None,
) -> float:
    denominator = float(norms[left] * norms[right]) if norms is not None else 0.0
    if denominator <= 0.0:
        return float(np.dot(_normalized_vector(vectors, left), _normalized_vector(vectors, right)))
    return float(np.dot(vectors[left], vectors[right]) / denominator)


def _representative_index(member_indices: Sequence[int], vectors: np.ndarray, records: Sequence[EventMapRecord]) -> int:
    if len(member_indices) == 1:
        return int(member_indices[0])
    normalized = np.stack([_normalized_vector(vectors, records[index].vector_index) for index in member_indices])
    center = _canonical_centroid_for_members(member_indices, records, vectors)
    scores = normalized @ center
    best_score = float(np.max(scores))
    candidates = [
        member_indices[offset]
        for offset, score in enumerate(scores)
        if math.isclose(float(score), best_score, rel_tol=0.0, abs_tol=1e-8)
    ]
    return min(candidates, key=lambda index: str(records[index].event_id))


def canonicalize_event_map_records(
    records: Sequence[EventMapRecord],
    vectors: np.ndarray,
    neighbor_indices: np.ndarray | None = None,
    *,
    checkpoint: Callable[[int, int], None] | None = None,
    checkpoint_interval: int = 2048,
) -> list[EventMapCanonicalGroup]:
    """执行确定性的保守归并；不确定候选不会通过传递闭包扩大。"""

    if not records:
        if checkpoint is not None:
            checkpoint(0, 0)
        return []
    record_count = len(records)
    total_work = record_count * 6
    report_interval = max(1, int(checkpoint_interval))
    last_reported = -report_interval

    def report(processed: int, *, force: bool = False) -> None:
        nonlocal last_reported
        if checkpoint is None:
            return
        current = max(0, min(total_work, int(processed)))
        if force or current - last_reported >= report_interval:
            checkpoint(current, total_work)
            last_reported = current

    vector_norms = _vector_norms(vectors)
    exact_members: dict[tuple[object, ...], list[int]] = defaultdict(list)
    for record_index, record in enumerate(records):
        exact_members[record.exact_key].append(record_index)
        report(record_index + 1)
    report(record_count, force=True)

    groups: dict[int, EventMapCanonicalGroup] = {}
    group_by_record: dict[int, int] = {}
    next_group_id = 0
    ordered_exact_keys = sorted(exact_members, key=lambda item: repr(item))
    report(record_count, force=True)
    for key_index, key in enumerate(ordered_exact_keys):
        members = sorted(exact_members[key], key=lambda index: str(records[index].event_id))
        representative = _representative_index(members, vectors, records)
        decisions = {
            index: EventMapMemberDecision(
                record_index=index,
                rule="singleton" if len(members) == 1 else "exact",
                score=1.0 if len(members) > 1 else None,
                runner_up_score=None,
                reasons=("exact_fingerprint",) if len(members) > 1 else ("no_unambiguous_match",),
            )
            for index in members
        }
        groups[next_group_id] = EventMapCanonicalGroup(members, representative, decisions)
        for index in members:
            group_by_record[index] = next_group_id
        next_group_id += 1
        report(record_count + key_index + 1)
    report(record_count * 2, force=True)
    del ordered_exact_keys, exact_members

    if neighbor_indices is None or len(records) < 2:
        report(total_work, force=True)
        return _ordered_groups(groups.values(), vectors, records)
    if neighbor_indices.shape[0] != len(records):
        raise ValueError("neighbor index row count does not match event records")

    # 每条记录只需要最佳候选和 runner-up 才能判断互为最近邻及 0.05 歧义阈值。
    # 使用紧凑数组保留这两个候选，避免最坏 N×neighbor_count 个 Python tuple。
    best_scores = np.full(record_count, -np.inf, dtype=np.float64)
    best_targets = np.full(record_count, -1, dtype=np.int64)
    runner_scores = np.full(record_count, -np.inf, dtype=np.float64)
    runner_targets = np.full(record_count, -1, dtype=np.int64)

    def candidate_precedes(
        score: float,
        target: int,
        incumbent_score: float,
        incumbent_target: int,
    ) -> bool:
        if incumbent_target < 0:
            return True
        if score != incumbent_score:
            return score > incumbent_score
        return records[target].event_id.int < records[incumbent_target].event_id.int

    for left_index, neighbors in enumerate(neighbor_indices):
        for raw_right_index in neighbors:
            right_index = int(raw_right_index)
            if right_index < 0 or right_index >= len(records) or right_index == left_index:
                continue
            if group_by_record[left_index] == group_by_record[right_index]:
                continue
            constraints_ok, _constraint_reasons, threshold = _fuzzy_pair_constraints(
                records[left_index],
                records[right_index],
            )
            if not constraints_ok:
                continue
            similarity = _cosine_similarity(
                vectors,
                records[left_index].vector_index,
                records[right_index].vector_index,
                vector_norms,
            )
            if similarity >= threshold:
                if candidate_precedes(
                    similarity,
                    right_index,
                    float(best_scores[left_index]),
                    int(best_targets[left_index]),
                ):
                    runner_scores[left_index] = best_scores[left_index]
                    runner_targets[left_index] = best_targets[left_index]
                    best_scores[left_index] = similarity
                    best_targets[left_index] = right_index
                elif candidate_precedes(
                    similarity,
                    right_index,
                    float(runner_scores[left_index]),
                    int(runner_targets[left_index]),
                ):
                    runner_scores[left_index] = similarity
                    runner_targets[left_index] = right_index
        report(record_count * 2 + left_index + 1)
    report(record_count * 3, force=True)

    has_runner = runner_targets >= 0
    ambiguous_mask = np.zeros(record_count, dtype=np.bool_)
    runner_indices = np.flatnonzero(has_runner)
    ambiguous_mask[runner_indices] = (
        best_scores[runner_indices] - runner_scores[runner_indices]
    ) < 0.05
    accepted_mask = (best_targets >= 0) & ~ambiguous_mask
    del has_runner, runner_indices

    # 互为最佳候选的 pair 最多 N/2。预分配数值数组并用 UUID 数值顺序排序，
    # 与原先 (-score, str(left_uuid), str(right_uuid)) 的确定性顺序相同。
    pair_scores = np.empty(record_count, dtype=np.float64)
    pair_left = np.empty(record_count, dtype=np.int64)
    pair_right = np.empty(record_count, dtype=np.int64)
    pair_count = 0
    for left in range(record_count):
        report(record_count * 3 + left + 1)
        if not bool(accepted_mask[left]):
            continue
        right = int(best_targets[left])
        if not bool(accepted_mask[right]) or int(best_targets[right]) != left:
            continue
        if records[left].event_id.int > records[right].event_id.int:
            continue
        pair_scores[pair_count] = min(float(best_scores[left]), float(best_scores[right]))
        pair_left[pair_count] = left
        pair_right[pair_count] = right
        pair_count += 1
    report(record_count * 4, force=True)
    del accepted_mask

    if pair_count:
        active_left = pair_left[:pair_count]
        active_right = pair_right[:pair_count]
        left_high = np.fromiter(
            (records[int(index)].event_id.int >> 64 for index in active_left),
            dtype=np.uint64,
            count=pair_count,
        )
        left_low = np.fromiter(
            (records[int(index)].event_id.int & ((1 << 64) - 1) for index in active_left),
            dtype=np.uint64,
            count=pair_count,
        )
        right_high = np.fromiter(
            (records[int(index)].event_id.int >> 64 for index in active_right),
            dtype=np.uint64,
            count=pair_count,
        )
        right_low = np.fromiter(
            (records[int(index)].event_id.int & ((1 << 64) - 1) for index in active_right),
            dtype=np.uint64,
            count=pair_count,
        )
        pair_order = np.lexsort(
            (
                right_low,
                right_high,
                left_low,
                left_high,
                -pair_scores[:pair_count],
            )
        )
        del left_high, left_low, right_high, right_low
    else:
        pair_order = np.empty(0, dtype=np.int64)

    fuzzy_merged_groups: set[int] = set()
    for pair_position, raw_pair_index in enumerate(pair_order):
        report(record_count * 4 + pair_position + 1)
        pair_index = int(raw_pair_index)
        score = float(pair_scores[pair_index])
        left = int(pair_left[pair_index])
        right = int(pair_right[pair_index])
        left_runner = float(runner_scores[left]) if runner_targets[left] >= 0 else None
        right_runner = float(runner_scores[right]) if runner_targets[right] >= 0 else None
        _constraints_ok, reasons, _threshold = _fuzzy_pair_constraints(records[left], records[right])
        reasons = reasons + (f"cosine_{float(best_scores[left]):.6f}",)
        left_group_id = group_by_record[left]
        right_group_id = group_by_record[right]
        if left_group_id == right_group_id:
            continue
        # 一个自动组最多执行一次 fuzzy 合并，禁止 A~B~C 的传递扩张。
        if left_group_id in fuzzy_merged_groups or right_group_id in fuzzy_merged_groups:
            ambiguous_mask[left] = True
            ambiguous_mask[right] = True
            continue
        left_group = groups[left_group_id]
        right_group = groups[right_group_id]
        if len(left_group.member_indices) + len(right_group.member_indices) > 100:
            left_group.uncertainty_flags.add("fuzzy_cluster_size_limit")
            right_group.uncertainty_flags.add("fuzzy_cluster_size_limit")
            continue
        left_rep = records[left_group.representative_index]
        right_rep = records[right_group.representative_index]
        rep_similarity = _cosine_similarity(
            vectors,
            left_rep.vector_index,
            right_rep.vector_index,
            vector_norms,
        )
        compatible, rep_reasons = _fuzzy_pair_allowed(left_rep, right_rep, rep_similarity)
        if not compatible:
            continue
        merged_members = sorted(
            left_group.member_indices + right_group.member_indices,
            key=lambda index: str(records[index].event_id),
        )
        merged_decisions = dict(left_group.decisions)
        merged_decisions.update(right_group.decisions)
        for index in merged_members:
            previous = merged_decisions[index]
            if previous.rule == "singleton":
                merged_decisions[index] = EventMapMemberDecision(
                    index,
                    "fuzzy",
                    score,
                    left_runner if index in left_group.member_indices else right_runner,
                    tuple(sorted(set(reasons + rep_reasons))),
                )
        left_group.member_indices = merged_members
        left_group.decisions = merged_decisions
        left_group.representative_index = _representative_index(merged_members, vectors, records)
        left_group.uncertainty_flags.update(right_group.uncertainty_flags)
        del groups[right_group_id]
        for index in merged_members:
            group_by_record[index] = left_group_id
        fuzzy_merged_groups.add(left_group_id)
    report(record_count * 5, force=True)
    del pair_order, pair_scores, pair_left, pair_right
    del best_scores, best_targets, runner_scores, runner_targets
    del vector_norms

    for ambiguous_position, raw_record_index in enumerate(np.flatnonzero(ambiguous_mask)):
        record_index = int(raw_record_index)
        group = groups.get(group_by_record[record_index])
        if group is not None:
            group.uncertainty_flags.add("ambiguous_fuzzy_candidates")
        report(record_count * 5 + ambiguous_position + 1)
    report(total_work, force=True)
    return _ordered_groups(groups.values(), vectors, records)


def _ordered_groups(
    groups: Iterable[EventMapCanonicalGroup],
    vectors: np.ndarray,
    records: Sequence[EventMapRecord],
) -> list[EventMapCanonicalGroup]:
    result = list(groups)
    for group in result:
        group.representative_index = _representative_index(group.member_indices, vectors, records)
    result.sort(
        key=lambda group: (
            records[group.representative_index].start_day,
            str(records[group.representative_index].event_id),
        )
    )
    return result


def _topic_label(
    group_indices: Sequence[int],
    groups: Sequence[EventMapCanonicalGroup],
    records: Sequence[EventMapRecord],
) -> str:
    entity_counts: dict[str, tuple[str, int]] = {}
    type_counts: dict[str, int] = defaultdict(int)
    for group_index in group_indices:
        record = records[groups[group_index].representative_index]
        type_counts[record.event_type or "other"] += 1
        for entity in record.entities:
            if not entity.is_core:
                continue
            key = entity.canonical_key
            name, count = entity_counts.get(key, (entity.name, 0))
            entity_counts[key] = (name, count + 1)
    entities = [
        value[0]
        for _key, value in sorted(entity_counts.items(), key=lambda item: (-item[1][1], item[0]))[:2]
    ]
    event_type = min(type_counts, key=lambda key: (-type_counts[key], key)) if type_counts else "other"
    parts = entities + [event_type]
    return " · ".join(part for part in parts if part)[:120] or "其他事件"


def build_event_map_topics(
    groups: Sequence[EventMapCanonicalGroup],
    records: Sequence[EventMapRecord],
    canonical_vectors: np.ndarray,
) -> tuple[list[EventMapTopicResult], list[int]]:
    if not groups:
        return [], []
    if canonical_vectors.ndim != 2 or canonical_vectors.shape[0] != len(groups):
        raise ValueError("canonical topic vector row count does not match canonical groups")
    matrix = np.asarray(canonical_vectors, dtype=np.float32)
    count = len(groups)
    cluster_count = min(count, max(1, min(32, max(16, round(math.sqrt(count) / 16)))))
    if cluster_count == count:
        labels = np.arange(count, dtype=np.int32)
        centers = matrix.copy()
    else:
        model = MiniBatchKMeans(
            n_clusters=cluster_count,
            random_state=EVENT_MAP_RANDOM_SEED,
            n_init=1,
            batch_size=min(4096, max(256, cluster_count * 8)),
            reassignment_ratio=0.0,
        )
        labels = model.fit_predict(matrix)
        centers = np.asarray(model.cluster_centers_, dtype=np.float32)

    primary_topics: list[EventMapTopicResult] = []
    topic_by_group = [-1] * count
    topic_members: dict[int, list[int]] = defaultdict(list)
    for group_index, label in enumerate(labels):
        topic_members[int(label)].append(group_index)
    for label in sorted(topic_members):
        topic_index = len(primary_topics)
        members = tuple(sorted(topic_members[label]))
        for group_index in members:
            topic_by_group[group_index] = topic_index
        primary_topics.append(
            EventMapTopicResult(
                topic_index=topic_index,
                parent_topic_index=None,
                member_group_indices=members,
                label=_topic_label(members, groups, records),
                center=tuple(float(value) for value in centers[label]),
            )
        )

    # 每个一级星域都生成二级主题团，使远景和中景拥有完整且确定的标签层级。
    topics = list(primary_topics)
    for parent in primary_topics:
        parent_members = list(parent.member_group_indices)
        sub_count = min(16, max(1, math.ceil(len(parent_members) / 800)))
        sub_matrix = matrix[parent_members]
        if sub_count == 1:
            sub_labels = np.zeros(len(parent_members), dtype=np.int32)
            sub_centers = np.mean(sub_matrix, axis=0, keepdims=True)
        else:
            sub_model = MiniBatchKMeans(
                n_clusters=sub_count,
                random_state=EVENT_MAP_RANDOM_SEED,
                n_init=1,
                batch_size=min(4096, max(256, sub_count * 32)),
                reassignment_ratio=0.0,
            )
            sub_labels = sub_model.fit_predict(sub_matrix)
            sub_centers = np.asarray(sub_model.cluster_centers_, dtype=np.float32)
        for sub_label in range(sub_count):
            members = tuple(
                parent_members[offset] for offset, value in enumerate(sub_labels) if int(value) == sub_label
            )
            if not members:
                continue
            topic_index = len(topics)
            for group_index in members:
                topic_by_group[group_index] = topic_index
            topics.append(
                EventMapTopicResult(
                    topic_index=topic_index,
                    parent_topic_index=parent.topic_index,
                    member_group_indices=tuple(sorted(members)),
                    label=_topic_label(members, groups, records),
                    center=tuple(float(value) for value in sub_centers[sub_label]),
                )
            )
    return topics, topic_by_group


def _story_phases(record: EventMapRecord) -> frozenset[str]:
    text = unicodedata.normalize("NFKC", f"{record.title} {record.summary}").casefold()
    return frozenset(
        phase
        for phase, tokens in _STORY_PHASES.items()
        if any(token.casefold() in text for token in tokens)
    )


def build_event_map_stories(
    groups: Sequence[EventMapCanonicalGroup],
    records: Sequence[EventMapRecord],
    vectors: np.ndarray,
    *,
    max_gap_days: int = 180,
    max_members: int = 24,
) -> list[EventMapStoryResult]:
    vector_norms = _vector_norms(vectors)
    by_entity_type: dict[tuple[str, str], list[int]] = defaultdict(list)
    for group_index, group in enumerate(groups):
        record = records[group.representative_index]
        for entity_key in sorted(record.core_entity_keys):
            by_entity_type[(entity_key, normalize_event_map_text(record.event_type))].append(group_index)

    edge_by_pair: dict[tuple[int, int], EventMapStoryEdgeResult] = {}
    for (entity_key, _event_type), group_indices in sorted(by_entity_type.items()):
        ordered = sorted(
            set(group_indices),
            key=lambda index: (
                records[groups[index].representative_index].start_day,
                str(records[groups[index].representative_index].event_id),
            ),
        )
        for source_index, target_index in zip(ordered, ordered[1:]):
            source = records[groups[source_index].representative_index]
            target = records[groups[target_index].representative_index]
            gap = target.start_day - source.end_day
            if gap <= 0 or gap > max_gap_days:
                continue
            source_phases = _story_phases(source)
            target_phases = _story_phases(target)
            if not source_phases or not target_phases or source_phases == target_phases:
                continue
            similarity = _cosine_similarity(
                vectors,
                source.vector_index,
                target.vector_index,
                vector_norms,
            )
            if similarity < 0.80:
                continue
            pair = (source_index, target_index)
            evidence = {
                "shared_entity": entity_key,
                "source_phases": sorted(source_phases),
                "target_phases": sorted(target_phases),
                "gap_days": gap,
                "cosine": round(similarity, 6),
            }
            candidate = EventMapStoryEdgeResult(source_index, target_index, "continuation", similarity, evidence)
            existing = edge_by_pair.get(pair)
            if existing is None or candidate.confidence > existing.confidence:
                edge_by_pair[pair] = candidate

    outgoing: dict[int, list[EventMapStoryEdgeResult]] = defaultdict(list)
    incoming: dict[int, int] = defaultdict(int)
    for edge in edge_by_pair.values():
        outgoing[edge.source_group_index].append(edge)
        incoming[edge.target_group_index] += 1
    for edges in outgoing.values():
        edges.sort(key=lambda edge: (-edge.confidence, edge.target_group_index))

    stories: list[EventMapStoryResult] = []
    visited_edges: set[tuple[int, int]] = set()
    starts = sorted(set(outgoing) | set(incoming), key=lambda index: (incoming[index] > 0, index))
    for start in starts:
        for first_edge in outgoing.get(start, []):
            first_key = (first_edge.source_group_index, first_edge.target_group_index)
            if first_key in visited_edges:
                continue
            members = [first_edge.source_group_index]
            edges: list[EventMapStoryEdgeResult] = []
            current = first_edge
            while len(members) < max_members:
                key = (current.source_group_index, current.target_group_index)
                if key in visited_edges or current.target_group_index in members:
                    break
                visited_edges.add(key)
                edges.append(current)
                members.append(current.target_group_index)
                next_edges = [
                    edge
                    for edge in outgoing.get(current.target_group_index, [])
                    if (edge.source_group_index, edge.target_group_index) not in visited_edges
                ]
                if len(next_edges) != 1:
                    break
                current = next_edges[0]
            if len(members) < 2:
                continue
            first_record = records[groups[members[0]].representative_index]
            entity_names = [entity.name for entity in first_record.entities if entity.is_core]
            stories.append(
                EventMapStoryResult(
                    story_index=len(stories),
                    member_group_indices=tuple(members),
                    edges=tuple(edges),
                    label=f"{entity_names[0] if entity_names else first_record.event_type} · 事件进展"[:120],
                )
            )
    return stories
