"""근거 선별 — 후보 수십 건에서 답변에 실을 것을 고른다.

A5 는 관대하게 모은다. Q-E 하나에 발췌가 50~60건 오는 일이 흔하고 그중 다수는 같은 고객사
이름이 스쳐 지나가는 Slack 회의록이다. 그대로 다 실으면 사람이 못 읽고, 무엇을 근거로
답했는지도 흐려진다. 그래서 A6 가 자른다.

자를 때 쓰는 신호는 셋이다.
  1. 질문 낱말과의 겹침 — 흔한 낱말은 덜, 드문 낱말은 더 (후보 안에서 계산한 역문서빈도)
  2. deterministic 집계가 이미 지목한 발췌인가 (Q-M·Q-S 의 집계 행이 인용한 것)
  3. 질문의 중심 엔티티 이름이 발췌 안에 있는가

**authority 는 여기에 쓰지 않는다.** 1A 의 authority 는 인용 라벨링까지이고 rerank 는
1B(B2) 범위다(policy.schema x-1a-scope). 실측해 보니 상한을 24 로 두면 authority 가중을
넣으나 빼나 골든 근거 적중이 같아서, 규정 범위를 넘길 이유가 없었다.

역문서빈도를 쓰는 이유는 실측이다. 단순 낱말 개수로 자르면 "하늘IT" 가 수십 번 나오는
Slack 회의록이 상위를 채우고, 정작 그 자료에 한 번 적힌 "멀티테넌트 구조 개발 필요"가
잘려 나갔다. 드문 낱말에 가중을 주자 그 발췌가 다시 올라왔다.

원문 검색으로 찾은 발췌도 같은 후보 풀에 넣는다. "그래프 순회로 닿았는가"는 그 발췌가
좋은 근거인지와 무관하기 때문이다. 다만 `raw_signals` 섹션은 A5 가 준 목록 그대로 남겨
"이건 원문 검색이 찾아 준 것"이라는 사실이 화면에서 사라지지 않게 한다.

**점수만으로 한 줄로 세워 자르지 않는다.** A5 가 후보를 공평하게 모아 줘도 여기서 한 줄로
세우면 같은 굶주림이 이 자리에서 되풀이된다. 실측이 그랬다 — BD 20행이 후보에 전부 들어와
있는데 최종 24건에는 두 행만 남았다. 전략 질문은 낱말이 자료의 한글 이름과 안 겹쳐서 점수가
거의 평평해지고(전원 4.00), 그러면 남는 기준이 evidence_id 문자열 순서뿐이라 제비뽑기가 된다.

그래서 후보가 **어디서 왔는지**(A5 의 `evidence_groups`)와 어느 자료인지를 함께 보고,
**점수가 같은 것들 사이에서** 곳과 자료를 돌려 가며 담는다. 점수가 크게 갈리는 질문에서는
좋은 근거가 한 곳에서 여러 개 실리고, 점수가 평평한 질문에서만 "곳마다 한 건씩"이 된다.

한때는 이것을 곱셈 감쇠(같은 곳의 n번째면 점수 ×0.75ⁿ)로 했는데, 그 방식은 몫 나누기가
점수 판정을 뒤집는다. 실측(2026-08-13 GQ-D1): 원점수 9.99 인 발췌가 같은 집계 행의 3번째라
5.62 로 깎여 5.99 짜리 14건에 밀려 잘렸다. 자세한 것은 `_fill` 주석.

원문 검색분(`raw#…`)에는 몫을 따로 떼어 준다. 상한이 없으면 흔한 낱말이 많은 코드·문서
자료가 상위를 채워 그래프 근거를 밀어내고, 하한이 없으면 딱 한 자료에만 적힌 사실이
매번 24칸 밖으로 밀린다(실측: 하늘IT 메모 · BD Overview Guest통제 행).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from retrieval.text import fold, term_overlap
from retrieval.types import EvidenceRef

#: 답변 하나에 실을 발췌 상한. 실측에서 20 → 24 로 올릴 때 골든 근거 적중이 늘었고
#: 그 위로는 더 늘지 않았다.
DEFAULT_LIMIT = 24

TERM_WEIGHT = 3.0
TERM_FLOOR = 0.3
AGGREGATE_WEIGHT = 4.0
FOCAL_WEIGHT = 1.0

#: 질문의 중심 엔티티가 발췌 본문이 아니라 **자료의 표기**에 있을 때 주는 가중.
#: 본문 겹침(FOCAL_WEIGHT)의 절반이다. 이 신호는 그 자료의 발췌 전부에 똑같이 걸리므로
#: 자료를 경쟁선에 올려 줄 수는 있어도 자료 안에서 무엇이 나은지는 가리지 못한다. 그리고
#: 그 회사를 실제로 말한 발췌를 이겨서는 안 된다 — 이기면 정작 답이 될 문장이 밀린다.
SOURCE_FOCAL_WEIGHT = 0.5

#: 집계 행의 순위가 한 칸 내려갈 때마다 그 행이 지목한 발췌의 가중에 곱해지는 값.
#: 집계는 이미 순서를 매겨 놨는데(사업영역 수·고객사 수 순) 균등 가중을 주면 그 순서가
#: 선별에서 사라진다. 0.85 면 1위 4.00 · 5위 2.09 · 9위 1.11 로 벌어진다.
AGGREGATE_RANK_DECAY = 0.85

#: 행 안 위치가 한 칸 밀릴 때 순위에 더해지는 값. 한 행이 근거 8건을 들고 와도 그 폭이
#: 다음 행의 1번째를 넘지 않게 작게 둔다(8번째 = 순위 0.42 < 다음 행 1.0).
POSITION_SHARE = 0.06

#: 원문 검색분(`raw#…`)에 떼어 주는 몫. 상한이자 하한이다.
RAW_GROUP_PREFIX = "raw#"
RAW_SHARE = 0.25
RAW_MIN_SLOTS = 3

#: 발췌가 몇 줄뿐인 자료(A5 가 `rare#…` 로 표시)에 떼어 주는 몫.
#: 그 자료를 자르면 코퍼스 어디에도 없는 사실이 사라지므로 여기가 먼저 자리를 받는다.
#: 다만 그 자리는 **원문 검색분 몫 안에서** 받는다. 둘 다 fulltext 가 찾아 준 것이고,
#: 그래프 순회 근거의 몫을 빼앗으면 다른 질문이 답을 잃는다(실측: 퇴직연금 Guest 질문에서
#: rare 가 그래프 몫을 6칸 잘라 'BM 정의!G7 기업HR' 이 답변 근거에서 빠졌다).
RARE_GROUP_PREFIX = "rare#"


def aggregate_evidence_ids(aggregates: Any) -> set[str]:
    """집계 결과가 인용한 evidence_id 를 전부 끌어모은다."""
    return set(aggregate_evidence_ranks(aggregates))


def aggregate_evidence_ranks(aggregates: Any) -> dict[str, float]:
    """집계가 인용한 evidence_id 를 **몇 번째 행이 인용했는지**와 함께 돌려준다.

    집계 행은 이미 정렬되어 있다(공통 Need 는 사업영역 수·고객사 수 순). 그런데 모든 행의
    근거에 같은 가중을 주면 그 정렬이 선별 단계에서 통째로 버려진다. 실측: 공통 Need 9행의
    근거 72건이 전원 같은 점수를 받아, 1위 행이 지목한 발췌가 9위 행 것과 섞여 24칸에서
    밀렸다(타하렌터카·사아캐피탈 근거가 빠져 답이 두 고객사를 말하지 못했다).

    행 **안에서의** 순서도 약하게 센다. 행이 근거를 대상별로 돌아가며 내보내 놓아도
    (공통 Need 는 고객사별로 하나씩) 선별이 그 순서를 무시하면 한 대상이 앞을 다 차지한다.
    행 안 가중은 행 간 순위를 뒤집지 못할 만큼만 준다.

    같은 발췌를 여러 행이 인용하면 가장 앞선 자리의 값을 쓴다.
    """
    ranks: dict[str, float] = {}

    def note(evidence_id: str, rank: float) -> None:
        current = ranks.get(evidence_id)
        if current is None or rank < current:
            ranks[evidence_id] = rank

    def walk(value: Any, rank: int) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence_ids" and isinstance(item, list):
                    for position, x in enumerate(item):
                        if isinstance(x, str):
                            note(x, rank + position * POSITION_SHARE)
                else:
                    walk(item, rank)
        elif isinstance(value, list):
            # 리스트는 집계 행 묶음으로 본다. 위치가 그 행의 순위다.
            for index, item in enumerate(value):
                walk(item, index)

    walk(aggregates, 0)
    return ranks


@dataclass(frozen=True)
class Scorer:
    terms: tuple[str, ...]
    focal_names: tuple[str, ...]
    aggregate_ids: frozenset[str]
    idf: dict[str, float]
    #: evidence_id → 그것을 인용한 가장 앞선 집계 행의 순위. 비어 있으면 순위 없이 균등 가중.
    aggregate_ranks: Mapping[str, int] = field(default_factory=dict)

    def score(self, ref: EvidenceRef) -> float:
        hits = term_overlap(list(self.terms), ref.snippet)
        value = TERM_WEIGHT * sum(self.idf.get(t, 1.0) + TERM_FLOOR for t in hits)
        if ref.evidence_id in self.aggregate_ids:
            rank = self.aggregate_ranks.get(ref.evidence_id, 0)
            value += AGGREGATE_WEIGHT * AGGREGATE_RANK_DECAY**rank
        folded = fold(ref.snippet)
        named = [name for name in self.focal_names if fold(name) and fold(name) in folded]
        value += FOCAL_WEIGHT * len(named)
        # 본문에 이름이 없어도 자료 표기가 그 회사 것이면 신호로 센다. 고객사 제안서는 본문이
        # 일반 솔루션 설명이고 회사 이름은 파일 이름에만 있다(실측: 발췌 32건 중 0건).
        # 표기 대조는 `term_overlap` 을 쓴다 — 파일 이름은 낱말 사이 구분자가 적어서 영문 짧은
        # 이름이 다른 낱말 속에 우연히 박히기 쉬운데, 그 함수가 영문에만 경계를 요구한다.
        rest = [name for name in self.focal_names if name not in set(named)]
        if rest and ref.source_label:
            value += SOURCE_FOCAL_WEIGHT * len(term_overlap(rest, ref.source_label))
        return value


def build_scorer(
    candidates: Sequence[EvidenceRef],
    *,
    terms: Sequence[str],
    focal_names: Sequence[str],
    aggregate_ids: Iterable[str],
    aggregate_ranks: Mapping[str, int] | None = None,
) -> Scorer:
    total = max(len(candidates), 1)
    idf: dict[str, float] = {}
    for term in terms:
        folded = fold(term)
        if not folded:
            continue
        seen = sum(1 for ref in candidates if folded in fold(ref.snippet))
        idf[term] = math.log((total + 1) / (seen + 1))
    return Scorer(
        terms=tuple(terms),
        focal_names=tuple(focal_names),
        aggregate_ids=frozenset(aggregate_ids),
        idf=idf,
        aggregate_ranks=dict(aggregate_ranks or {}),
    )


def select_evidence(
    candidates: Sequence[EvidenceRef],
    *,
    terms: Sequence[str],
    focal_names: Sequence[str],
    aggregate_ids: Iterable[str],
    aggregate_ranks: Mapping[str, int] | None = None,
    groups: Mapping[str, str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[EvidenceRef]:
    unique: dict[str, EvidenceRef] = {}
    for ref in candidates:
        if ref.evidence_id and ref.evidence_id not in unique:
            unique[ref.evidence_id] = ref
    pool = list(unique.values())
    if len(pool) <= limit:
        return pool

    scorer = build_scorer(
        pool,
        terms=terms,
        focal_names=focal_names,
        aggregate_ids=aggregate_ids,
        aggregate_ranks=aggregate_ranks,
    )
    score = {ref.evidence_id: scorer.score(ref) for ref in pool}
    groups = groups or {}

    rare = [r for r in pool if _has_prefix(groups.get(r.evidence_id), RARE_GROUP_PREFIX)]
    raw = [r for r in pool if _has_prefix(groups.get(r.evidence_id), RAW_GROUP_PREFIX)]
    graph = [
        r
        for r in pool
        if not _has_prefix(groups.get(r.evidence_id), RARE_GROUP_PREFIX)
        and not _has_prefix(groups.get(r.evidence_id), RAW_GROUP_PREFIX)
    ]

    # 원문 검색분에 떼어 주는 몫. 발췌가 몇 줄뿐인 자료가 그 안에서 먼저 자리를 받는다.
    # 자르면 코퍼스 어디에도 없는 사실이 사라지기 때문이다. 그래프 순회 근거의 몫은 rare 가
    # 있든 없든 같게 유지된다.
    outside_slots = max(RAW_MIN_SLOTS, int(limit * RAW_SHARE))
    chosen = _fill(rare, groups, score, min(len(rare), outside_slots), spread=False)
    raw_slots = min(len(raw), max(0, outside_slots - len(chosen)))
    chosen += _fill(graph, groups, score, limit - len(chosen) - raw_slots)
    chosen += _fill(raw, groups, score, limit - len(chosen), spread=False)
    if len(chosen) < limit:  # 한쪽이 몫을 다 못 쓰면 다른 쪽이 마저 쓴다
        taken = {r.evidence_id for r in chosen}
        chosen += _fill(
            [r for r in pool if r.evidence_id not in taken], groups, score, limit - len(chosen)
        )
    return chosen


def _has_prefix(group: str | None, prefix: str) -> bool:
    return bool(group) and group.startswith(prefix)


_LOCATOR_CHUNK = re.compile(r"(\d+)")


def _document_order(ref: EvidenceRef) -> tuple:
    """같은 점수끼리는 **원문에 적힌 순서**로 세운다.

    evidence_id 는 해시라 순서에 뜻이 없다. 그것으로 자르면 어느 발췌가 남는지가 제비뽑기가
    되고, 실제로 그렇게 잘렸다 — Pain 레지스트리에서 8건이 같은 점수였는데 남은 것은
    시트 51·17·27행이고 정작 첫 행(E2)이 떨어졌다. 시트·쪽 번호 순서로 세우면 적어도
    "자료 앞쪽부터"라는 설명이 서고, 사람이 원문을 열었을 때 순서가 같다.
    숫자는 자릿수가 아니라 값으로 비교한다(E2 가 E17 보다 앞).
    """
    parts = _LOCATOR_CHUNK.split(ref.locator or "")
    key = tuple((1, int(p)) if p.isdigit() else (0, p) for p in parts if p != "")
    return (key, ref.evidence_id)


def _fill(
    refs: Sequence[EvidenceRef],
    groups: Mapping[str, str],
    score: Mapping[str, float],
    slots: int,
    *,
    spread: bool = True,
) -> list[EvidenceRef]:
    """점수 순으로 담되, **점수가 같은 것들 사이에서만** 곳·자료를 돌려 가며 담는다.

    예전에는 같은 곳에서 거듭 꺼낼 때 점수에 감쇠(0.75)를 곱했다. 그 방식은 몫 나누기가
    점수 판정을 뒤집는다. 실측(2026-08-13 GQ-D1): 기대 발췌 `2.85억` 은 원점수 9.99 로
    후보 144건 중 5위인데 같은 집계 행의 3번째라 5.62 로 깎여, 5.99 짜리 14건에 밀려
    잘렸다. 점수는 "질문에 얼마나 맞는가"이고 차례는 "몫을 어떻게 나누는가"라서 한 저울에
    올릴 수 없다. 그래서 점수를 먼저 보고, 동률 안에서만 차례를 센다.

    동률 계층은 실재한다 — 후보의 서로 다른 점수는 GQ-D1 11개(167건) · GQ-D2 3개(165건)이고
    GQ-D2 는 147건이 4.00 한 덩어리다. 즉 전략 질문에서 배분을 정하는 것은 사실상 이 규칙이다.

    자료(`source_id`)까지 세는 이유는 계약이다. 곳(집계 행)만 세면 같은 자료가 서로 다른
    행으로 여러 번 들어와 목록을 채운다(실측 GQ-D1: 24건 중 13건이 한 슬랙 채널).
    자료 하나가 근거 목록을 독식하지 않는 것은 `test_a_single_source_never_takes_...` 가
    지키는 계약인데, 지금까지는 **조회 순서가 우연히 자료를 섞어 준 덕에** 지켜지고 있었다.

    `spread=False` 는 차례를 세지 않는다(희소 자료·원문 검색 몫). 그 몫은 자리가 3~6칸뿐이라
    돌리면 한 자료에서 두 번째로 필요한 발췌가 통째로 사라진다.

    정렬의 마지막 기준은 `_document_order`(위치 → evidence_id)라 **입력 순서와 무관하게
    같은 결과**가 나온다. 그래서 조회 계층이 순서를 고정해도 배분이 흔들리지 않는다.
    """
    if slots <= 0 or not refs:
        return []

    ordered = sorted(refs, key=lambda ref: (-score[ref.evidence_id], _document_order(ref)))
    if not spread:
        return ordered[:slots]

    picked: list[EvidenceRef] = []
    group_taken: dict[str, int] = {}
    source_taken: dict[str, int] = {}
    start = 0
    while start < len(ordered) and len(picked) < slots:
        tier_score = score[ordered[start].evidence_id]
        end = start
        while end < len(ordered) and score[ordered[end].evidence_id] == tier_score:
            end += 1
        tier = list(ordered[start:end])
        start = end
        while tier and len(picked) < slots:
            tier.sort(
                key=lambda ref: (
                    group_taken.get(groups.get(ref.evidence_id) or "", 0),
                    source_taken.get(ref.source_id, 0),
                    _document_order(ref),
                )
            )
            ref = tier.pop(0)
            group = groups.get(ref.evidence_id) or ""
            group_taken[group] = group_taken.get(group, 0) + 1
            source_taken[ref.source_id] = source_taken.get(ref.source_id, 0) + 1
            picked.append(ref)
    return picked


# 폭을 넓혀 자리를 못 받은 곳을 구제하려 했으나 **되돌렸다.** 실측이 두 가지를 알려 줬다.
#   1. 효과가 없다 — "개척하려는 사업영역" 질문은 그룹이 93개인데 자리가 24개다.
#      어떻게 나눠도 20개 사업영역을 덮지 못한다. 폭 확보로 풀리는 문제가 아니다.
#   2. 해가 있다 — 자리를 내주려면 같은 곳의 두 번째 이후를 빼야 하는데, 하늘IT 메모처럼
#      **한 자료에서 여러 건이 필요한 질문**이 바로 그것을 잃는다(GQ-D6 회귀).
# 폭과 깊이는 24칸 안에서 정면으로 부딪힌다. 근본 해결은 선별이 아니라 다른 층에 있다
# (LEAD-FINDINGS D-5 「남은 것」 참고). 여기서는 점수·묶음 공평 분배만 유지한다.
#
# 2026-08-13 에 그 분배가 점수를 뒤집던 것을 고쳤다(`_fill`). 그래도 GQ-D2·GQ-D5 는 여전히
# 여기서 풀리지 않는다. 그 문항들의 기대 발췌는 4.00 동률 덩어리에 묻혀 있어 **무엇을 고를
# 근거 자체가 없다.** 남은 일은 점수 층(질문이 묻는 축에 가중)이다 → LEAD-FINDINGS D-5 후속.
