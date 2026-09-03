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
EVENT_MAP_STORY_VERSION = "event_map_story_evidence_graph_v2"
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
    "proposal": ("propose", "proposed", "plan", "planned", "提议", "提議", "拟", "擬", "计划", "計劃"),
    "announcement": ("announce", "announced", "宣布", "公布", "发布", "發布"),
    "approval": ("approve", "approved", "批准", "通过", "通過", "核准", "获批", "獲批", "获准", "獲准"),
    "implementation": ("implement", "implemented", "begin", "began", "start", "started", "实施", "實施", "启动", "啟動", "开始", "開始"),
    "progress": ("progress", "expand", "expanded", "推进", "推進", "扩大", "擴大", "延续", "延續"),
    "completion": ("complete", "completed", "close", "closed", "完成", "交割", "結案", "结案"),
    "delay": ("delay", "delayed", "postpone", "延期", "推迟", "推遲"),
    "suspension": ("suspend", "suspended", "pause", "paused", "暂停", "暫停", "中止"),
    "cancellation": ("cancel", "cancelled", "canceled", "withdraw", "撤回", "取消", "终止", "終止"),
    "increase": ("increase", "raise", "raised", "上调", "上調", "提高", "加息", "增长", "增長", "上涨", "上漲"),
    "decrease": ("decrease", "cut", "lower", "下调", "下調", "降低", "降息", "下降", "下跌"),
}
_STORY_PHASE_TRANSITIONS = {
    ("proposal", "announcement"),
    ("proposal", "approval"),
    ("proposal", "implementation"),
    ("proposal", "cancellation"),
    ("announcement", "approval"),
    ("announcement", "implementation"),
    ("announcement", "delay"),
    ("announcement", "cancellation"),
    ("approval", "implementation"),
    ("approval", "progress"),
    ("approval", "cancellation"),
    ("implementation", "progress"),
    ("implementation", "completion"),
    ("implementation", "delay"),
    ("implementation", "suspension"),
    ("implementation", "cancellation"),
    ("progress", "completion"),
    ("progress", "delay"),
    ("progress", "suspension"),
    ("progress", "cancellation"),
    ("delay", "implementation"),
    ("delay", "progress"),
    ("delay", "completion"),
    ("suspension", "implementation"),
    ("suspension", "cancellation"),
}
_STORY_CORRECTION_CUES = (
    "correct", "corrected", "correction", "revise", "revised", "clarify", "clarified",
    "deny", "denied", "更正", "纠正", "糾正", "修正", "澄清", "否认", "否認",
)
_STORY_RESPONSE_CUES = (
    "respond", "responded", "response", "reaction", "reacted", "in response to", "after the",
    "回应", "回應", "响应", "響應", "反应", "反應", "应对", "應對", "随后", "隨後", "在此之后", "在此之後",
)
_STORY_GENERIC_WORDS = {
    "about", "after", "before", "company", "event", "market", "markets", "report", "reports", "said", "says",
    "the", "this", "will", "公司", "事件", "市场", "市場", "表示", "指出", "认为", "認為", "相关", "相關",
    "宣布", "公布", "发布", "發布", "预计", "預計", "影响", "影響", "最新", "消息",
}
_STORY_GENERIC_ENTITY_TYPES = {"country", "sector", "other"}
_STORY_ENTITY_PRIORITY = {
    "asset": 8,
    "company": 7,
    "institution": 6,
    "person": 5,
    "indicator": 4,
    "country": 3,
    "sector": 2,
    "other": 1,
}
_STORY_RELATION_LABELS = {
    "continuation": "阶段推进",
    "causes": "因果承接",
    "response": "事件响应",
    "corrects": "事实纠正",
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
    source_claim: str | None = None
    target_claim: str | None = None
    source_entity_key: str | None = None
    target_entity_key: str | None = None


@dataclass(frozen=True, slots=True)
class EventMapEvidenceRef:
    evidence_id: uuid.UUID
    video_id: uuid.UUID
    evidence_text: str
    confidence: float | None = None


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
    source_video_id: uuid.UUID | None = None
    entities: tuple[EventMapEntityRef, ...] = ()
    relations: tuple[EventMapRelationRef, ...] = ()
    evidence: tuple[EventMapEvidenceRef, ...] = ()

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
    anchor_key: str
    evidence: dict[str, object]


@dataclass(frozen=True, slots=True)
class EventMapStoryResult:
    story_index: int
    member_group_indices: tuple[int, ...]
    edges: tuple[EventMapStoryEdgeResult, ...]
    label: str
    summary: str
    anchor_key: str
    story_type: str
    maturity: str
    quality_score: float


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


@dataclass(frozen=True, slots=True)
class _StoryNodeProfile:
    group_index: int
    representative: EventMapRecord
    records: tuple[EventMapRecord, ...]
    start_day: int
    end_day: int
    entity_names: dict[str, str]
    core_entity_keys: frozenset[str]
    claim_terms: frozenset[str]
    phases: frozenset[str]
    source_video_ids: frozenset[uuid.UUID]
    evidence_revision_ids: tuple[uuid.UUID, ...]
    evidence_excerpts: tuple[str, ...]
    relations: tuple[EventMapRelationRef, ...]
    has_uncertainty: bool


def _story_text(record: EventMapRecord) -> str:
    return unicodedata.normalize("NFKC", f"{record.title} {record.summary}").casefold()


def _story_claim_terms_from_text(text: str, entity_names: Iterable[str] = ()) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    for name in sorted({str(value or "").strip().casefold() for value in entity_names}, key=len, reverse=True):
        if name:
            normalized = normalized.replace(name, " ")
    terms: set[str] = set()
    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", normalized):
        if token not in _STORY_GENERIC_WORDS:
            terms.add(f"w:{token}")
    for span in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
        for width in (2, 3):
            for offset in range(max(0, len(span) - width + 1)):
                token = span[offset : offset + width]
                if token not in _STORY_GENERIC_WORDS:
                    terms.add(f"c:{token}")
    return frozenset(terms)


def _story_claim_overlap(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _story_entity_type(entity_key: str) -> str:
    return entity_key.split(":", 1)[0] if ":" in entity_key else "other"


def _story_anchor_is_specific(anchor_key: str) -> bool:
    return _story_entity_type(anchor_key) not in _STORY_GENERIC_ENTITY_TYPES


def _story_best_anchor(left: _StoryNodeProfile, right: _StoryNodeProfile) -> str | None:
    shared = left.core_entity_keys & right.core_entity_keys
    if not shared:
        return None
    return min(
        shared,
        key=lambda key: (-_STORY_ENTITY_PRIORITY.get(_story_entity_type(key), 0), key),
    )


def _story_profile(
    group_index: int,
    group: EventMapCanonicalGroup,
    records: Sequence[EventMapRecord],
) -> _StoryNodeProfile:
    members = tuple(records[index] for index in group.member_indices)
    representative = records[group.representative_index]
    entity_counts: dict[str, tuple[str, int]] = {}
    entity_names: list[str] = []
    for record in members:
        for entity in record.entities:
            if not entity.is_core:
                continue
            key = entity.canonical_key
            name, count = entity_counts.get(key, (entity.name, 0))
            entity_counts[key] = (name, count + 1)
            entity_names.append(entity.name)

    evidence_revision_ids: list[uuid.UUID] = []
    evidence_excerpts: list[str] = []
    source_video_ids: set[uuid.UUID] = set()
    for record in members:
        if record.source_video_id is not None:
            source_video_ids.add(record.source_video_id)
        has_support = False
        for evidence in record.evidence:
            source_video_ids.add(evidence.video_id)
            excerpt = str(evidence.evidence_text or "").strip()
            if excerpt:
                has_support = True
                if excerpt not in evidence_excerpts:
                    evidence_excerpts.append(excerpt[:320])
        if any(
            str(value or "").strip()
            for relation in record.relations
            for value in (relation.evidence_text, relation.source_claim, relation.target_claim)
        ):
            has_support = True
        if has_support:
            evidence_revision_ids.append(record.revision_id)

    return _StoryNodeProfile(
        group_index=group_index,
        representative=representative,
        records=members,
        start_day=min(record.start_day for record in members),
        end_day=max(record.end_day for record in members),
        entity_names={key: name for key, (name, _count) in entity_counts.items()},
        core_entity_keys=frozenset(entity_counts),
        claim_terms=frozenset().union(
            *(
                _story_claim_terms_from_text(_story_text(record), entity_names)
                for record in members
            )
        ),
        phases=frozenset().union(*(_story_phases(record) for record in members)),
        source_video_ids=frozenset(source_video_ids),
        evidence_revision_ids=tuple(sorted(set(evidence_revision_ids), key=str)),
        evidence_excerpts=tuple(evidence_excerpts[:6]),
        relations=tuple(relation for record in members for relation in record.relations),
        has_uncertainty=bool(group.uncertainty_flags),
    )


def _story_phase_transition_strength(source: _StoryNodeProfile, target: _StoryNodeProfile) -> float:
    if any((left, right) in _STORY_PHASE_TRANSITIONS for left in source.phases for right in target.phases):
        return 1.0
    if source.phases & {"increase", "decrease"} and target.phases & {"increase", "decrease"}:
        return 0.65
    return 0.0


def _story_relation_bridge(source: _StoryNodeProfile, target: _StoryNodeProfile) -> tuple[float, dict[str, str]]:
    entity_names = tuple(source.entity_names.values()) + tuple(target.entity_names.values())
    best_score = 0.0
    best_evidence: dict[str, str] = {}
    for relation in source.relations + target.relations:
        if normalize_event_map_text(relation.relation_type) not in {"cause", "causes", "effect", "affects"}:
            continue
        source_claim = str(relation.source_claim or "").strip()
        target_claim = str(relation.target_claim or "").strip()
        if not source_claim or not target_claim:
            continue
        source_terms = _story_claim_terms_from_text(source_claim, entity_names)
        target_terms = _story_claim_terms_from_text(target_claim, entity_names)
        forward = min(
            _story_claim_overlap(source.claim_terms, source_terms),
            _story_claim_overlap(target.claim_terms, target_terms),
        )
        forward *= float(relation.confidence) if relation.confidence is not None else 0.75
        if forward > best_score:
            best_score = forward
            best_evidence = {
                "source_claim": source_claim[:320],
                "target_claim": target_claim[:320],
            }
    return best_score, best_evidence


def _story_relation_candidate(
    source: _StoryNodeProfile,
    target: _StoryNodeProfile,
    vectors: np.ndarray,
    vector_norms: np.ndarray,
    *,
    max_gap_days: int,
) -> EventMapStoryEdgeResult | None:
    gap_days = target.start_day - source.end_day
    if gap_days < 0 or gap_days > max_gap_days:
        return None
    if "year" in {source.representative.time_precision, target.representative.time_precision}:
        return None
    if not source.evidence_revision_ids or not target.evidence_revision_ids:
        return None

    anchor_key = _story_best_anchor(source, target)
    if anchor_key is None:
        return None
    anchor_specific = _story_anchor_is_specific(anchor_key)
    shared_entities = source.core_entity_keys & target.core_entity_keys
    entity_overlap = len(shared_entities) / max(1, min(len(source.core_entity_keys), len(target.core_entity_keys)))
    claim_overlap = _story_claim_overlap(source.claim_terms, target.claim_terms)
    phase_strength = _story_phase_transition_strength(source, target)
    bridge_strength, bridge_evidence = _story_relation_bridge(source, target)
    target_text = " ".join(_story_text(record) for record in target.records)
    correction_cue = next((cue for cue in _STORY_CORRECTION_CUES if cue in target_text), "")
    response_cue = next((cue for cue in _STORY_RESPONSE_CUES if cue in target_text), "")
    source_numbers = frozenset().union(*(record.numeric_signature for record in source.records))
    target_numbers = frozenset().union(*(record.numeric_signature for record in target.records))
    numeric_continuity = bool(source_numbers & target_numbers)
    source_family = event_map_semantic_family(source.representative.event_type)["code"]
    target_family = event_map_semantic_family(target.representative.event_type)["code"]

    relation_type = ""
    relation_strength = 0.0
    minimum_cosine = 1.0
    if correction_cue and (claim_overlap >= 0.06 or bridge_strength >= 0.12):
        relation_type = "corrects"
        relation_strength = 1.0
        minimum_cosine = 0.72
    elif bridge_strength >= 0.18:
        relation_type = "causes"
        relation_strength = min(1.0, bridge_strength / 0.35)
        minimum_cosine = 0.68
    elif response_cue and (claim_overlap >= 0.06 or bridge_strength >= 0.12):
        relation_type = "response"
        relation_strength = 1.0
        minimum_cosine = 0.72
    elif (
        gap_days > 0
        and source_family == target_family
        and phase_strength > 0
        and (claim_overlap >= 0.08 or numeric_continuity)
    ):
        relation_type = "continuation"
        relation_strength = phase_strength
        minimum_cosine = 0.80
    else:
        return None

    similarity = _cosine_similarity(
        vectors,
        source.representative.vector_index,
        target.representative.vector_index,
        vector_norms,
    )
    if similarity < minimum_cosine:
        return None
    if relation_type == "continuation" and not anchor_specific:
        if claim_overlap < 0.16 or similarity < 0.88:
            return None

    distinct_sources = bool(source.source_video_ids - target.source_video_ids) or bool(
        target.source_video_ids - source.source_video_ids
    )
    claim_strength = max(min(1.0, claim_overlap / 0.25), bridge_strength)
    anchor_strength = 1.0 if anchor_specific else 0.35
    uncertainty_factor = 0.85 if source.has_uncertainty or target.has_uncertainty else 1.0
    confidence = uncertainty_factor * (
        0.24 * similarity
        + 0.20 * claim_strength
        + 0.15 * entity_overlap
        + 0.12 * anchor_strength
        + 0.12 * relation_strength
        + 0.10
        + 0.07 * (1.0 if distinct_sources else 0.35)
    )
    threshold = 0.78 if relation_type == "causes" else 0.80
    if relation_type == "continuation":
        threshold = 0.82
    if confidence < threshold:
        return None

    revision_ids = tuple(
        sorted(set(source.evidence_revision_ids + target.evidence_revision_ids), key=str)
    )[:12]
    evidence: dict[str, object] = {
        "anchor_key": anchor_key,
        "shared_entities": sorted(shared_entities),
        "gap_days": gap_days,
        "cosine": round(similarity, 6),
        "claim_overlap": round(claim_overlap, 6),
        "source_phases": sorted(source.phases),
        "target_phases": sorted(target.phases),
        "numeric_continuity": numeric_continuity,
        "distinct_sources": distinct_sources,
        "supporting_revision_ids": [str(value) for value in revision_ids],
        "source_excerpts": list(source.evidence_excerpts[:2]),
        "target_excerpts": list(target.evidence_excerpts[:2]),
    }
    if correction_cue:
        evidence["correction_cue"] = correction_cue
    if response_cue:
        evidence["response_cue"] = response_cue
    if bridge_evidence:
        evidence["claim_bridge"] = bridge_evidence
    return EventMapStoryEdgeResult(
        source_group_index=source.group_index,
        target_group_index=target.group_index,
        relation_type=relation_type,
        confidence=round(confidence, 6),
        anchor_key=anchor_key,
        evidence=evidence,
    )


def _story_anchor_name(anchor_key: str, profiles: Sequence[_StoryNodeProfile]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for profile in profiles:
        name = profile.entity_names.get(anchor_key)
        if name:
            counts[name] += 1
    if counts:
        return min(counts, key=lambda name: (-counts[name], name))
    return anchor_key.split(":", 1)[-1] or "未命名主题"


def _story_title_part(title: str, anchor_name: str) -> str:
    value = unicodedata.normalize("NFKC", str(title or "")).strip()
    if anchor_name:
        value = value.replace(anchor_name, "")
    value = re.sub(r"^[\s·:：,，;；—-]+|[\s·:：,，;；—-]+$", "", value)
    return value or str(title or "事件").strip() or "事件"


def _story_label_summary(
    anchor_key: str,
    profiles: Sequence[_StoryNodeProfile],
    edges: Sequence[EventMapStoryEdgeResult],
) -> tuple[str, str, str]:
    anchor_name = _story_anchor_name(anchor_key, profiles)
    first = _story_title_part(profiles[0].representative.title, anchor_name)
    latest = _story_title_part(profiles[-1].representative.title, anchor_name)
    label = f"{anchor_name}：{first}" if first == latest else f"{anchor_name}：{first} → {latest}"
    relation_counts: dict[str, int] = defaultdict(int)
    for edge in edges:
        relation_counts[edge.relation_type] += 1
    relation_label = "、".join(
        f"{_STORY_RELATION_LABELS.get(kind, kind)} {count} 条"
        for kind, count in sorted(relation_counts.items())
    )
    source_count = len(set().union(*(profile.source_video_ids for profile in profiles)))
    summary = (
        f"围绕“{anchor_name}”的证据关系图，从“{profiles[0].representative.title}”发展至"
        f"“{profiles[-1].representative.title}”；包含 {len(profiles)} 个真实事件、{relation_label}，"
        f"证据来自 {source_count} 条来源记录。"
    )
    story_type = next(iter(relation_counts)) if len(relation_counts) == 1 else "trajectory"
    return label[:120], summary[:500], story_type


def build_event_map_stories(
    groups: Sequence[EventMapCanonicalGroup],
    records: Sequence[EventMapRecord],
    vectors: np.ndarray,
    *,
    max_gap_days: int = 365,
    max_members: int = 48,
    lookahead_per_entity: int = 8,
) -> list[EventMapStoryResult]:
    if not groups:
        return []
    if vectors.ndim != 2 or vectors.shape[0] != len(records):
        raise ValueError("story vector row count does not match records")

    vector_norms = _vector_norms(vectors)
    profiles = [_story_profile(index, group, records) for index, group in enumerate(groups)]
    by_entity: dict[str, list[int]] = defaultdict(list)
    for group_index, group in enumerate(groups):
        for entity_key in sorted(profiles[group_index].core_entity_keys):
            by_entity[entity_key].append(group_index)

    edge_by_pair: dict[tuple[int, int], EventMapStoryEdgeResult] = {}
    for _entity_key, group_indices in sorted(by_entity.items()):
        ordered = sorted(
            set(group_indices),
            key=lambda index: (
                profiles[index].start_day,
                str(profiles[index].representative.event_id),
            ),
        )
        for offset, source_index in enumerate(ordered):
            for target_index in ordered[offset + 1 : offset + 1 + lookahead_per_entity]:
                if profiles[target_index].start_day - profiles[source_index].end_day > max_gap_days:
                    break
                candidate = _story_relation_candidate(
                    profiles[source_index],
                    profiles[target_index],
                    vectors,
                    vector_norms,
                    max_gap_days=max_gap_days,
                )
                if candidate is None:
                    continue
                pair = (source_index, target_index)
                existing = edge_by_pair.get(pair)
                if existing is None or candidate.confidence > existing.confidence:
                    edge_by_pair[pair] = candidate

    edges_by_anchor: dict[str, list[EventMapStoryEdgeResult]] = defaultdict(list)
    for edge in edge_by_pair.values():
        edges_by_anchor[edge.anchor_key].append(edge)

    built: list[tuple[str, tuple[int, ...], tuple[EventMapStoryEdgeResult, ...], str, str, str, str, float]] = []
    for anchor_key, anchor_edges in sorted(edges_by_anchor.items()):
        outgoing: dict[int, list[EventMapStoryEdgeResult]] = defaultdict(list)
        incoming: dict[int, list[EventMapStoryEdgeResult]] = defaultdict(list)
        for edge in anchor_edges:
            outgoing[edge.source_group_index].append(edge)
            incoming[edge.target_group_index].append(edge)
        allowed_out = {
            (edge.source_group_index, edge.target_group_index)
            for edges in outgoing.values()
            for edge in sorted(edges, key=lambda item: (-item.confidence, item.target_group_index))[:3]
        }
        allowed_in = {
            (edge.source_group_index, edge.target_group_index)
            for edges in incoming.values()
            for edge in sorted(edges, key=lambda item: (-item.confidence, item.source_group_index))[:3]
        }
        selected_edges = [
            edge
            for edge in anchor_edges
            if (edge.source_group_index, edge.target_group_index) in allowed_out & allowed_in
        ]
        if not selected_edges:
            continue

        nodes = sorted(
            {edge.source_group_index for edge in selected_edges}
            | {edge.target_group_index for edge in selected_edges}
        )
        parent = {node: node for node in nodes}
        size = {node: 1 for node in nodes}

        def find(node: int) -> int:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        accepted_edges: list[EventMapStoryEdgeResult] = []
        for edge in sorted(
            selected_edges,
            key=lambda item: (-item.confidence, item.source_group_index, item.target_group_index),
        ):
            left = find(edge.source_group_index)
            right = find(edge.target_group_index)
            if left != right:
                if size[left] + size[right] > max_members:
                    continue
                if size[left] < size[right]:
                    left, right = right, left
                parent[right] = left
                size[left] += size[right]
            accepted_edges.append(edge)

        members_by_root: dict[int, set[int]] = defaultdict(set)
        for node in nodes:
            members_by_root[find(node)].add(node)
        for component_members in members_by_root.values():
            component_edges = [
                edge
                for edge in accepted_edges
                if edge.source_group_index in component_members
                and edge.target_group_index in component_members
            ]
            if not component_edges:
                continue
            ordered_members = tuple(
                sorted(
                    component_members,
                    key=lambda index: (
                        profiles[index].start_day,
                        str(profiles[index].representative.event_id),
                    ),
                )
            )
            ordered_profiles = [profiles[index] for index in ordered_members]
            ordered_edges = tuple(
                sorted(
                    component_edges,
                    key=lambda edge: (
                        profiles[edge.source_group_index].start_day,
                        profiles[edge.target_group_index].start_day,
                        edge.relation_type,
                    ),
                )
            )
            source_count = len(set().union(*(profile.source_video_ids for profile in ordered_profiles)))
            relation_types = {edge.relation_type for edge in ordered_edges}
            average_confidence = sum(edge.confidence for edge in ordered_edges) / len(ordered_edges)
            quality_score = min(
                1.0,
                0.80 * average_confidence
                + 0.10 * min(1.0, source_count / 3)
                + 0.10 * min(1.0, len(relation_types) / 2),
            )
            if len(ordered_members) == 2:
                edge = ordered_edges[0]
                if edge.relation_type == "continuation":
                    if edge.confidence < 0.92 or source_count < 2 or not _story_anchor_is_specific(anchor_key):
                        continue
                elif edge.confidence < 0.84 or source_count < 2:
                    continue
            elif quality_score < 0.76 or source_count < 2:
                continue

            maturity = (
                "established"
                if len(ordered_members) >= 4 and source_count >= 3 and quality_score >= 0.82
                else "emerging"
            )
            label, summary, story_type = _story_label_summary(
                anchor_key,
                ordered_profiles,
                ordered_edges,
            )
            built.append(
                (
                    anchor_key,
                    ordered_members,
                    ordered_edges,
                    label,
                    summary,
                    story_type,
                    maturity,
                    round(quality_score, 6),
                )
            )

    built.sort(
        key=lambda item: (
            profiles[item[1][0]].start_day,
            item[0],
            tuple(item[1]),
        )
    )
    return [
        EventMapStoryResult(
            story_index=index,
            member_group_indices=members,
            edges=edges,
            label=label,
            summary=summary,
            anchor_key=anchor_key,
            story_type=story_type,
            maturity=maturity,
            quality_score=quality_score,
        )
        for index, (anchor_key, members, edges, label, summary, story_type, maturity, quality_score) in enumerate(built)
    ]
