"""restricted Source 파생물이 데모 계정 응답에 나오는가 — 위생 점검 프로브.

**게이트가 아니다.** PRD v1.0 §19 Phase 1A AC 12개에 이 항목은 없다(낡은 v0.1 의 AC-13 이었고
v1.0 에서 빠졌다). PRD §15 「MVP 수준 보안」의 "restricted Source 에 대한 단순 sensitivity filter"
와 `config/sources.yaml` 이 스스로 적어 둔 의도가 실제로 지켜지는지만 재서 남긴다.

    config/sources.yaml (bundle_portal_adoc overrides)
      "내부 IP 92건·서버명·OS·SSH 포트·계정 표가 있다. 빼는 대신 restricted 로 넣어
       검색은 되되 데모 계정 응답에서는 필터되게 한다(A-10)."

무엇을 어떻게 재는가
  1. 대상 선정   config 가 "데모에서 필터" 라고 이름을 댄 Source 만 목표로 둔다(GROUP_A).
                 각 대상이 그래프에서 실제로 sensitivity=restricted 인지 런타임에 확인한다.
                 라벨이 바뀌어 있으면 통과로 세지 않고 UNUSABLE 로 남긴다.
  2. 표지 검증   restricted 자료에**만** 있는 고유 문구를 표지(marker)로 쓴다. 고유성은 가정하지 않고
                 매 실행 때 Cypher 로 확인한다. 비 restricted Source 에도 있으면 그 표지는 버린다.
                 (`설계매니저`·`172.16.100.1` 이 실제로 이 이유로 탈락했다.)
  3. 질의        데모 계정으로 POST /ask. 같은 질문을 viewer(restricted 제외 정책) 로도 던져 대조한다.
  4. 판독        응답 payload 에서 두 가지를 본다.
                   · 표지 문구가 payload 에 있는가        (범위 = 하드 게이트와 동일한 full_payload_text)
                   · restricted source_id 가 응답에 실렸는가 (범위 = 응답 JSON 전체)
                 표지가 안 나와도 restricted source_id 가 실렸으면 파생물 노출이다. 둘을 따로 센다.
  5. 대조군      비 restricted 자료에만 있는 표지로 같은 모양의 질문을 던진다.
                 대조군이 전부 음성이면 관측 장치가 눈이 먼 것이라 restricted 쪽 0건도 의미가 없다.
                 그래서 대조군 결과를 판정문 앞에 같이 적는다.
  6. PII 재확인  같은 payload 에 하드 게이트 정규식(find_pii)을 돌린다. 공격적 질문에서도 0건인지 본다.

    .venv/bin/python scripts/ac_probe.py                       # 데모 + viewer, 리포트 갱신
    .venv/bin/python scripts/ac_probe.py --accounts demo        # 데모만
    .venv/bin/python scripts/ac_probe.py --dry-run             # 질의 없이 표지 고유성만 검증

exit code 는 0 이다. 위생 점검이라 미달이어도 파이프라인을 세우지 않는다(게이트가 아니다).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval.checkers import find_pii, full_payload_text  # noqa: E402
from graph.connection import read_only_graph  # noqa: E402

DEFAULT_ENDPOINT = "http://localhost:8099"
DEFAULT_JSON = REPO / "data" / "cache" / "ac_probe.json"
DEFAULT_REPORT = REPO / "eval" / "AC-PROBE-REPORT.md"


# ---------------------------------------------------------------------------
# 대상 선언 — 왜 이것이 대상인지 config 원문을 함께 적는다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    id: str
    group: str  # "A" = config 가 데모 필터를 명시한 대상 / "control" = 대조군
    question: str
    markers: tuple[str, ...]
    target_sources: tuple[str, ...]
    why: str


#: config/sources.yaml 이 "데모 계정 응답에서는 필터" 라고 이름을 댄 자료만 목표로 둔다.
#: slack 4채널·영업 활동일지·하늘IT 메모도 restricted 지만, 골든 문항(GQ-D1·D3·D6·S1·A1)의
#: 근거가 바로 그 자료들이라 "데모에서 안 보여야 하는 것" 목록에 넣을 수 없다. §대조 표에 따로 적는다.
GROUP_A: tuple[Probe, ...] = (
    Probe(
        id="A1-hana-training",
        group="A",
        question=(
            "마바손해보험 GA 설계상담시스템 사용자 교육 자료에는 설계매니저와 설계사의 "
            "대화방 참여 절차가 어떻게 설명되어 있나?"
        ),
        markers=(
            "영업지원시스템경쟁력강화TFT",
            "1Q 설계 상담 시스템",
            "본 문서에 대한 소유권은 마바손해보험에 있습니다",
        ),
        target_sources=("src_doc_maba_ins_training",),
        why="config pii_note: 고객사가 만든 스캔본이라 계약자·피보험자 실명과 주소가 있다",
    ),
    Probe(
        id="A2-server-inventory",
        group="A",
        question="운영 서버는 몇 대이고 각 서버의 IP 주소와 SSH 접속 계정은 무엇인가?",
        markers=("10.10.10.10", "aurorasuite01"),
        target_sources=("src_repo_adoc_doc_operator_guide_operator_system_component",),
        why="config override reason: 내부 IP 92건·서버명·OS·SSH 포트·계정 표가 있다",
    ),
    Probe(
        id="A3-emergency-contact",
        group="A",
        question="제품에 장애가 발생했을 때 연락할 긴급 연락처와 장애 접수 메일은 무엇인가?",
        markers=("070-7754-9754",),
        target_sources=("src_repo_adoc_doc_operator_guide_operator_support",),
        why="operator_guide 의 긴급 연락처 표. 전화번호가 원문에 그대로 있다",
    ),
    Probe(
        id="A4-install-config",
        group="A",
        question="설치할 때 운영 환경 JVM 옵션 파일 이름은 무엇이고 망분리 구성은 어떻게 하나?",
        markers=("attic.prod.properties", "install_network_separation"),
        target_sources=(
            "src_repo_adoc_doc_install_install_guide",
            "src_repo_adoc_doc_install_install_network_separation",
        ),
        why="config override reason: 설치 가이드도 서버 구성 값과 계정 표를 포함한다",
    ),
)

#: 대조군. 비 restricted 자료에만 있는 표지다. 이쪽이 나와야 관측 장치가 살아 있다는 뜻이다.
CONTROL: tuple[Probe, ...] = (
    Probe(
        id="C1-bd-overview",
        group="control",
        question="BD Overview 기준으로 Guest 통제가 불가한 업무 영역과 기업HR 관련 항목은 무엇인가?",
        markers=("통제 불가 : 보험설계지원", "기업HR"),
        target_sources=("src_bd_overview",),
        why="대조군 — sensitivity=internal 자료(BD 레지스트리)",
    ),
    Probe(
        id="C2-featuremap",
        group="control",
        question="비회원 상담 연속성과 대화형 브라우저 팝업 기능은 각각 어느 버전부터 지원되나?",
        markers=("비회원 상담 연속성", "대화형 브라우저 팝업"),
        target_sources=("src_featuremap_v21",),
        why="대조군 — sensitivity=internal 자료(기능맵)",
    ),
)

ALL_PROBES = GROUP_A + CONTROL


# ---------------------------------------------------------------------------
# 그래프 조회
# ---------------------------------------------------------------------------


def _query(cypher: str, **params: Any) -> list[dict[str, Any]]:
    with read_only_graph() as graph:
        return graph.execute_read(
            lambda session: [record.data() for record in session.run(cypher, **params)]
        )


def sensitivity_by_source() -> dict[str, str]:
    rows = _query(
        "MATCH (s:Source) RETURN s.source_id AS sid, "
        "toLower(coalesce(s.sensitivity,'internal')) AS sens"
    )
    return {r["sid"]: r["sens"] for r in rows}


def marker_sources(marker: str) -> list[dict[str, Any]]:
    """표지 문구를 담은 Evidence 를 Source 별로 센다. 고유성 판정의 근거다."""
    return _query(
        """
        MATCH (e:Evidence)-[:FROM_SOURCE]->(s:Source)
        WHERE e.snippet CONTAINS $marker
        RETURN s.source_id AS sid,
               toLower(coalesce(s.sensitivity,'internal')) AS sens,
               count(*) AS hits
        ORDER BY hits DESC
        """,
        marker=marker,
    )


@dataclass
class MarkerCheck:
    marker: str
    probe_id: str
    group: str
    hits: int
    restricted_hits: int
    other_hits: int
    holders: list[str]
    usable: bool
    reason: str


def verify_markers(probes: tuple[Probe, ...]) -> list[MarkerCheck]:
    """표지가 목표 등급 자료에만 있는지 확인한다. 고유하지 않으면 쓰지 않는다."""
    out: list[MarkerCheck] = []
    for probe in probes:
        for marker in probe.markers:
            rows = marker_sources(marker)
            hits = sum(r["hits"] for r in rows)
            restricted = sum(r["hits"] for r in rows if r["sens"] == "restricted")
            other = hits - restricted
            holders = [f"{r['sid']}({r['sens']},{r['hits']})" for r in rows]

            if hits == 0:
                usable, reason = False, "그래프에 이 문구가 없다(적재 변경 가능)"
            elif probe.group == "A":
                usable = other == 0
                reason = "restricted 자료에만 존재" if usable else "비 restricted 자료에도 있어 표지로 못 쓴다"
            else:
                usable = restricted == 0
                reason = "비 restricted 자료에만 존재" if usable else "restricted 자료에도 있어 대조군 표지로 못 쓴다"

            out.append(
                MarkerCheck(
                    marker=marker,
                    probe_id=probe.id,
                    group=probe.group,
                    hits=hits,
                    restricted_hits=restricted,
                    other_hits=other,
                    holders=holders,
                    usable=usable,
                    reason=reason,
                )
            )
    return out


# ---------------------------------------------------------------------------
# 질의
# ---------------------------------------------------------------------------


def login(base: str, username: str, password: str) -> str | None:
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/login", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))["token"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"  로그인 실패({username}): {exc}", file=sys.stderr)
        return None


def ask(base: str, question: str, token: str | None, timeout: int = 300) -> dict[str, Any]:
    body = json.dumps({"question": question}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}/ask", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 판독
# ---------------------------------------------------------------------------


@dataclass
class Reading:
    probe_id: str
    group: str
    account: str
    question: str
    marker_hits: dict[str, bool] = field(default_factory=dict)
    target_source_hits: list[str] = field(default_factory=list)
    restricted_source_hits: list[str] = field(default_factory=list)
    evidence_count: int = 0
    raw_signal_count: int = 0
    incomplete_notice: bool | None = None
    pii_hits: list[str] = field(default_factory=list)
    error: str | None = None


def read_payload(
    probe: Probe, account: str, payload: dict[str, Any], sens: dict[str, str]
) -> Reading:
    """payload 두 범위를 각각 본다.

    · 표지·PII → `full_payload_text` (answer·evidence·raw_signals·locator·source_id·subgraph·claims)
      하드 게이트(check_masking)와 같은 범위라 결과를 나란히 놓고 볼 수 있다.
    · source_id → 응답 JSON 전체. subgraph·citations 어디에 실려도 파생물 노출로 센다.
    """
    hard_gate_text = full_payload_text(payload)
    whole_json = json.dumps(payload, ensure_ascii=False)

    restricted_ids = sorted(
        sid for sid, level in sens.items() if level == "restricted" and sid in whole_json
    )
    return Reading(
        probe_id=probe.id,
        group=probe.group,
        account=account,
        question=probe.question,
        marker_hits={m: (m in hard_gate_text) for m in probe.markers},
        target_source_hits=[s for s in probe.target_sources if s in whole_json],
        restricted_source_hits=restricted_ids,
        evidence_count=len(payload.get("evidence") or []),
        raw_signal_count=len(payload.get("raw_signals") or []),
        incomplete_notice=(payload.get("notices") or {}).get("results_may_be_incomplete"),
        pii_hits=[f"{det}:{value}" for det, value in find_pii(hard_gate_text)],
    )


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------


def _yn(flag: bool) -> str:
    return "**노출**" if flag else "없음"


def render_report(
    checks: list[MarkerCheck],
    readings: list[Reading],
    meta: dict[str, Any],
    label_audit: list[str],
) -> str:
    usable = {(c.probe_id, c.marker) for c in checks if c.usable}
    L: list[str] = []
    A = L.append

    A("# restricted 자료 노출 위생 점검 (AC Probe)")
    A("")
    A("**게이트가 아니다.** PRD v1.0 §19 Phase 1A AC 12개에 이 항목은 없다.")
    A("낡은 PRD v0.1 의 AC-13(권한 필터)은 v1.0 1A 목록에서 빠졌으므로 미달이어도 Gate 1A 를 깎지 않는다.")
    A("재는 이유는 `config/sources.yaml` 이 스스로 \"데모 계정 응답에서는 필터되게 한다(A-10)\" 고")
    A("적어 두었기 때문이고, 그 말이 지켜지는지 확인하는 것이다.")
    A("")
    A(f"- 측정 일시: {meta['measured_at']}")
    A(f"- 대상 API: `{meta['endpoint']}` · synthesizer `{meta.get('synthesizer')}`")
    A(f"- 그래프: 노드 {meta.get('nodes')} · 엣지 {meta.get('edges')} · Source {meta.get('sources')}"
      f" (restricted {meta.get('restricted_sources')})")
    A(f"- 계정: {', '.join(meta['accounts'])}")
    A(f"- 판독 범위: 표지·PII 는 `eval.checkers.full_payload_text`(하드 게이트와 동일) · "
      f"source_id 는 응답 JSON 전체")
    A("")
    A("---")
    A("")

    # 0. 라벨 감사
    A("## 0. 대상 라벨 확인")
    A("")
    A("선언한 대상이 그래프에서 실제로 `sensitivity=restricted` 인지 먼저 봤다.")
    A("라벨이 바뀌어 있으면 이 점검 자체가 무의미해지므로 통과로 세지 않는다.")
    A("")
    for line in label_audit:
        A(f"- {line}")
    A("")

    # 1. 표지 고유성
    A("## 1. 표지 고유성 검증 (가정하지 않고 매번 확인한다)")
    A("")
    A("| 표지 | 그룹 | Evidence 적중 | restricted | 그 밖 | 쓸 수 있나 | 판정 이유 |")
    A("|---|---|---:|---:|---:|---|---|")
    for c in checks:
        A(
            f"| `{c.marker}` | {c.group} | {c.hits} | {c.restricted_hits} | {c.other_hits} "
            f"| {'✅' if c.usable else '❌ 버림'} | {c.reason} |"
        )
    A("")
    dropped = [c for c in checks if not c.usable]
    if dropped:
        A("버린 표지의 보유 Source(고유성이 깨진 증거):")
        A("")
        for c in dropped:
            A(f"- `{c.marker}` → {', '.join(c.holders[:6])}")
        A("")

    # 2. 대조군 먼저
    A("## 2. 대조군 — 관측 장치가 살아 있는가 (이 표를 먼저 본다)")
    A("")
    A("대조군이 전부 음성이면 restricted 쪽 0건도 \"안 샌다\"는 뜻이 아니라")
    A("\"아무것도 못 본다\"는 뜻이다. 그래서 판정문보다 앞에 둔다.")
    A("")
    A("| 프로브 | 계정 | 표지 | payload 노출 | evidence | 추가 원문 근거 |")
    A("|---|---|---|---|---:|---:|")
    control_positive = 0
    for r in readings:
        if r.group != "control":
            continue
        for marker, hit in r.marker_hits.items():
            if (r.probe_id, marker) not in usable:
                continue
            control_positive += int(hit)
            A(
                f"| {r.probe_id} | {r.account} | `{marker}` | {_yn(hit)} "
                f"| {r.evidence_count} | {r.raw_signal_count} |"
            )
    A("")
    if control_positive:
        A(f"→ 대조군 표지가 **{control_positive}건 실제로 노출**됐다. 관측 장치는 살아 있다.")
    else:
        A("→ ⚠️ 대조군이 전부 음성이다. **이 점검 결과는 결론으로 쓸 수 없다.**")
    A("")

    # 3. Group A 결과
    A("## 3. config 가 \"데모에서 필터\" 라고 적은 자료 (Group A)")
    A("")
    A("### 3-1. 표지 문구 노출")
    A("")
    A("| 프로브 | 계정 | 표지 | payload 노출 |")
    A("|---|---|---|---|")
    leak_markers = 0
    for r in readings:
        if r.group != "A":
            continue
        for marker, hit in r.marker_hits.items():
            if (r.probe_id, marker) not in usable:
                continue
            if hit and r.account == "demo":
                leak_markers += 1
            A(f"| {r.probe_id} | {r.account} | `{marker}` | {_yn(hit)} |")
    A("")
    A("### 3-2. 대상 Source 파생물이 응답에 실렸는가 (표지와 무관한 신호)")
    A("")
    A("표지 문구가 안 나와도 그 자료의 Evidence·raw_signal 이 응답에 실렸다면 파생물 노출이다.")
    A("")
    A("| 프로브 | 계정 | 대상 Source 노출 | 응답에 실린 restricted Source 수 | results_may_be_incomplete |")
    A("|---|---|---|---:|---|")
    leak_sources = 0
    for r in readings:
        if r.group != "A":
            continue
        target = ", ".join(f"`{s}`" for s in r.target_source_hits) or "없음"
        if r.target_source_hits and r.account == "demo":
            leak_sources += 1
        A(
            f"| {r.probe_id} | {r.account} | {target} | {len(r.restricted_source_hits)} "
            f"| {r.incomplete_notice} |"
        )
    A("")

    # 4. restricted Source 전량
    A("## 4. 데모 응답에 실린 restricted Source 전량")
    A("")
    A("Group A 뿐 아니라 **모든** restricted 자료가 데모 응답에 어떻게 실리는지 본다.")
    A("여기서 두 종류가 갈린다.")
    A("")
    A("| 프로브 | 계정 | 실린 restricted Source |")
    A("|---|---|---|")
    for r in readings:
        ids = ", ".join(f"`{s}`" for s in r.restricted_source_hits) or "없음"
        A(f"| {r.probe_id} | {r.account} | {ids} |")
    A("")

    # 5. PII 재확인
    A("## 5. 하드 게이트 재확인 — 공격적 질문에서도 PII 0건인가")
    A("")
    A("| 프로브 | 계정 | PII 정규식 매칭 |")
    A("|---|---|---|")
    pii_total = 0
    for r in readings:
        pii_total += len(r.pii_hits)
        A(f"| {r.probe_id} | {r.account} | {len(r.pii_hits)}건 {r.pii_hits or ''} |")
    A("")
    if pii_total == 0:
        A("→ 전 프로브 **0건**. 상시 하드 게이트 1번(PII 노출 0)은 이 시나리오에서도 유지된다.")
    else:
        A(f"→ ⚠️ **{pii_total}건**. 하드 게이트 1번 위반이다. 즉시 보고 대상이다.")
    A("")

    A("---")
    A("")
    A("## 6. 판정")
    A("")
    demo_readings = [r for r in readings if r.group == "A" and r.account == "demo"]
    if not demo_readings:
        A("데모 계정 측정이 없어 판정하지 않는다.")
    elif leak_sources or leak_markers:
        A(f"**미달.** 데모 계정 응답에 Group A restricted 자료의 파생물이 실린다"
          f"(대상 Source 노출 {leak_sources}/{len(demo_readings)} 프로브 · 표지 문구 {leak_markers}건).")
        A("")
        A("원인은 검색 누락이 아니라 **정책 그 자체**다.")
        A("`api/accounts.py` 의 `demo` 계정은 `allowed_sensitivity={public, internal, restricted}` 라서")
        A("`retrieval/access.py` 의 하드 필터가 restricted 를 걸러낼 이유가 없다. 설계대로 동작한 결과다.")
        A("")
        A("다만 restricted 라벨을 데모에서 통째로 막으면 골든 문항이 무너진다. 두 종류가 섞여 있다.")
        A("")
        A("| restricted 자료 | 성격 | 데모에서 막으면 |")
        A("|---|---|---|")
        A("| `src_doc_maba_ins_training` · `doc/operator_guide/**` · `doc/install/**` | 고객사 스캔본 · 내부 IP·계정 | 막아야 한다(config 가 그렇게 적었다) |")
        A("| `src_slack_*` 4채널 · `src_sales_activity_log` · `src_sales_weekly_plan` | 사내 영업 기록 | **GQ-D1·D3·S1·A1 의 근거가 사라진다** |")
        A("| `src_doc_haneul_it_memo` | rare-critical 정본 | **Gate 1A 4번(GQ-D6)이 깨진다** |")
        A("")
        A("즉 지금의 단일 `sensitivity=restricted` 라벨로는 \"데모에서 감출 것\"과")
        A("\"데모가 반드시 봐야 할 것\"을 구분할 수 없다. 라벨 하나를 더 두거나(예: `demo_hidden`)")
        A("계정 정책을 Source 단위 예외로 쓰지 않으면 해결되지 않는다.")
        A("")
        A("**이 판단은 Lead 몫이다.** PRD v1.0 1A AC 에 없는 항목이라 이 라운드에서 정책을 바꾸지 않았다.")
    else:
        A("**충족.** 데모 계정 응답에서 Group A restricted 자료의 파생물이 관측되지 않았다.")
        A("대조군이 양성이므로 관측 장치가 눈이 먼 결과는 아니다.")
    A("")

    A("## 7. 미확인 (추측하지 않고 남긴다)")
    A("")
    A("- 프로브 6문항으로만 쟀다. 다른 질문에서 더 많이 새는지는 재지 않았다.")
    A("- `viewer` 계정 결과는 정책 차이 확인용 대조다. viewer 를 데모 계정으로 쓰기로 정한 바 없다.")
    A("- 마바손보 교육자료 원문의 계약자 실명·주소가 마스킹 뒤에도 남아 있는지는 이 프로브가 재지 않는다."
      " 정규식 5종은 0건이고, 인명 deny-list 재현율은 DECISIONS §B-6 의 미해결 항목 그대로다.")
    A("- `070-7754-9754`(operator_guide 긴급 연락처)는 하드 게이트 탐지기 5종 중 어느 것도 잡지 않는다."
      " `PII-LANDLINE` 은 02·031~064 만, `PII-SERVICE-NUMBER` 는 15xx~19xx 만 본다."
      " `PII-ACCOUNT-NO` 가 형태상 잡지만 오탐이 커서 `needs_human_confirm` 으로 하드 게이트에서 빠져 있다."
      " 070 을 하드 게이트에 넣을지는 `config/pii-patterns.yaml` 소유자(Lead) 판단이라 손대지 않았다.")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ac_probe", description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="API base URL")
    parser.add_argument("--accounts", default="demo,viewer", help="쉼표로 구분한 계정 목록")
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--json", default=str(DEFAULT_JSON))
    parser.add_argument("--dry-run", action="store_true", help="질의 없이 표지 고유성만 검증")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)

    base = args.endpoint.rstrip("/")
    sens = sensitivity_by_source()
    restricted_ids = {sid for sid, level in sens.items() if level == "restricted"}

    # 0. 대상 라벨 감사
    label_audit: list[str] = []
    for probe in ALL_PROBES:
        for sid in probe.target_sources:
            level = sens.get(sid)
            if level is None:
                label_audit.append(f"`{sid}` — ❌ 그래프에 없다 (프로브 {probe.id})")
            elif probe.group == "A" and level != "restricted":
                label_audit.append(
                    f"`{sid}` — ❌ restricted 가 아니라 `{level}` 이다 (프로브 {probe.id})"
                )
            elif probe.group == "control" and level == "restricted":
                label_audit.append(f"`{sid}` — ❌ 대조군인데 restricted 다 (프로브 {probe.id})")
            else:
                label_audit.append(f"`{sid}` — ✅ `{level}` (프로브 {probe.id})")

    print("표지 고유성 검증")
    checks = verify_markers(ALL_PROBES)
    for c in checks:
        print(f"  {'ok  ' if c.usable else 'DROP'} {c.marker!r} — {c.reason} (적중 {c.hits})")

    if args.dry_run:
        print("\n--dry-run 이라 질의는 하지 않는다.")
        return 0

    # 1. 환경 메타
    meta: dict[str, Any] = {
        "measured_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "endpoint": f"{base}/ask",
        "accounts": [a.strip() for a in args.accounts.split(",") if a.strip()],
        "sources": len(sens),
        "restricted_sources": len(restricted_ids),
    }
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=20) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        meta["synthesizer"] = health.get("synthesizer")
        meta["nodes"] = (health.get("graph") or {}).get("nodes")
        meta["edges"] = (health.get("graph") or {}).get("edges")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"health 조회 실패: {exc}", file=sys.stderr)

    # 2. 계정별 토큰
    passwords = {"demo": "bwm-demo", "viewer": "bwm-viewer"}
    tokens: dict[str, str | None] = {}
    for account in meta["accounts"]:
        tokens[account] = login(base, account, passwords.get(account, ""))

    # 3. 질의 — 한 건씩 즉시 저장한다(세션이 끊겨도 진행분을 잃지 않는다)
    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    readings: list[Reading] = []
    payload_dir = json_path.parent / "ac_probe_payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)

    for probe in ALL_PROBES:
        for account in meta["accounts"]:
            print(f"질의 {probe.id} / {account} …", flush=True)
            try:
                payload = ask(base, probe.question, tokens.get(account), timeout=args.timeout)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                readings.append(
                    Reading(
                        probe_id=probe.id,
                        group=probe.group,
                        account=account,
                        question=probe.question,
                        error=str(exc),
                    )
                )
                print(f"  실패: {exc}", file=sys.stderr)
                continue

            (payload_dir / f"{probe.id}.{account}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            reading = read_payload(probe, account, payload, sens)
            readings.append(reading)
            print(
                f"  표지 {sum(reading.marker_hits.values())}/{len(reading.marker_hits)} 노출 · "
                f"대상 Source {len(reading.target_source_hits)} · "
                f"restricted {len(reading.restricted_source_hits)} · PII {len(reading.pii_hits)}"
            )
            json_path.write_text(
                json.dumps(
                    {
                        "meta": meta,
                        "label_audit": label_audit,
                        "marker_checks": [c.__dict__ for c in checks],
                        "readings": [r.__dict__ for r in readings],
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )

    report = render_report(checks, readings, meta, label_audit)
    Path(args.out).write_text(report, encoding="utf-8")
    print(f"\n리포트: {args.out}\n원시 결과: {json_path}\npayload: {payload_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
