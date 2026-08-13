"""답변 합성 (REVISED §4 ⑦ · tiers.yaml stage_assignment).

Q-E·Q-M 은 S tier, Q-S 만 L tier 다. 입력은 A5 가 만든 축약본(`synthesis_input`)과 선별된
발췌뿐이고 원문 코퍼스는 들어가지 않는다.

**프롬프트에는 게이트가 근거로 인정하는 것만 넣는다.** 하드 게이트(§ guards)가 근거로 치는
것은 답변에 실린 발췌와 질문 문장뿐이다. 그런데 예전 프롬프트는 발췌마다 `id=ev_...`,
`위치=v2.1!F11` 같은 메타데이터를 함께 보여 줬다. 모델은 그것을 사실로 알고 문장에 옮겼고,
게이트는 그 문장을 통째로 지웠다. 실측(골든 20문항)에서 지워진 문장 49개 중 다수가 이
경로였다.

  · `위치=v2.1!F174` → 모델이 "v2.1 버전에서 제공됩니다" → 삭제 → **답변 전체가 사라짐**
  · `위치=Pain Point 목록!E2` → 모델이 "개별 Pain Point 들" → 삭제
  · `id=ev_dfc582e43098d503` → 모델이 괄호로 인용 → 숫자 43098·503·582 가 무근거로 잡힘

그래서 발췌는 **번호만** 붙여 보여 준다. 각주 번호가 곧 발췌 번호이고, evidence_id 는
서버가 되돌려 채운다. 모델이 옮겨 적을 일이 없어야 옮겨 적다 틀리는 일도 없다.

**답변 길이는 max_tokens 로 조여지지 않는다.** 현재 기본 provider(`claude_cli`)는 이 값을
CLI 에 전달할 자리가 없어 무시한다(실측 출력 824~11,881 토큰). 길이를 실제로 정하는 것은
프롬프트의 문장 수 상한이다. `max_tokens` 는 API provider 를 쓸 때를 위해 그대로 넘긴다.

**Claim 은 프롬프트에 넣지 않는다.** Claim 은 발췌에서 뽑아낸 2차 서술이라, 모델이 그것을
근거로 문장을 쓰면 발췌 어디에도 없는 값이 답변에 남는다(하드 게이트가 그 문장을 지운다).
실측에서 그렇게 새어 나온 것이 기능 버전 숫자였다. Claim 은 응답의 claims[] 로 따로 실어
사용자가 상태와 함께 본다 — 근본 해결은 파서가 그 값을 발췌에 넣는 것이다.

LLM 이 없거나 실패해도 API 는 답을 돌려준다. 그때는 결정적 합성기가 "자료에 이렇게 적혀
있다"까지만 말한다. 답이 없는 것보다 근거를 그대로 보여 주는 편이 낫고, 무엇보다 LLM 실패가
조용한 빈 응답으로 둔갑하지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from retrieval.types import EvidenceRef, RetrievalResult

SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "recommendation": {"type": "string"},
        "estimated_parts": {"type": "array", "items": {"type": "string"}},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                # evidence_id 는 서버가 marker 로 되돌려 채운다. 모델이 적어 오면 그대로 쓴다
                # (스크립트 합성기·픽스처가 그 형태를 쓴다).
                "required": ["marker"],
                "properties": {
                    "marker": {"type": "integer", "minimum": 1},
                    "evidence_id": {"type": "string"},
                    "quoted_span": {"type": "string"},
                },
            },
        },
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
    },
}

TIER_FOR_ROUTE = {"Q-E": "S", "Q-M": "S", "Q-S": "L"}
MAX_TOKENS_FOR_TIER = {"S": 1200, "L": 1500}

#: 문장 수 상한. Q-E 는 사실 한두 개를 정확히 답하면 되지만, Q-M·Q-S 는 "빠짐없이 나열"이
#: 채점 기준이라 같은 상한을 씌우면 항목이 잘린다(실측: 자동차금융 BD 문항에서 타겟·시장
#: 규모를 쓰다가 담당자 항목이 밀려났다).
MAX_SENTENCES_FOR_ROUTE = {"Q-E": 8, "Q-M": 12, "Q-S": 12}
DEFAULT_MAX_SENTENCES = 8

#: 발췌 한 건을 몇 자까지 보여 주는가. **게이트가 근거로 인정하는 길이와 같아야 한다.**
#: 게이트(`service._assemble` 의 `support_text`)는 발췌 전문을 인정하고, 그래프도 계약도
#: 500자까지 담는다(`contracts/answer.schema.json` 의 `snippet.maxLength`). 그런데 여기가
#: 300자였다. 그래서 모델은 뒤쪽에 적힌 값을 본 적이 없는데 화면은 그 값을 보여 주는 상태가
#: 됐다. 같은 근거를 놓고 답변과 화면이 다른 말을 한 것이다.
#:
#: 실측으로 확인한 사고: "딜리셔스에서는 상담이 몇 건 정도 들어와?" 의 4번 발췌는 정규화 후
#: 485자이고 `월4,000건` 이 **312번째 글자**에 있었다. 12자 차이로 잘렸고, 답변은 정직하게
#: "정확한 상담 건수는 확인된 자료가 없습니다"라고 썼다. 근거 서랍에는 그 숫자가 보였다.
#: 규모: 발췌 17,613건 중 6,026건(34.2%)이 300자를 넘고, 골든 20문항이 실제로 실은 발췌
#: 481건 중 250건(52.0%)이 잘려 29,260자가 모델 눈에서 사라져 있었다.
EVIDENCE_SNIPPET_CHARS = 500
#: 프롬프트에 넣는 발췌 수. 계약이 답변에 싣는 발췌가 24건인데 20건만 보여 주면 뒤 4건은
#: 화면에는 근거로 뜨는데 모델은 본 적이 없는 상태가 된다. 실측: `보안 정책상 제한`(23·24번),
#: `프랜차이즈`(24번)가 이 상한에 잘려 답에서 빠졌다. 계약 상한과 같게 맞춘다.
MAX_EVIDENCE_IN_PROMPT = 24
#: 추가 원문 근거는 **개수로 자르지 않는다.** 게이트는 화면에 실린 참고 신호를 전부 근거로
#: 인정하므로(§ `service._assemble` 의 `shown_raw`), 앞 몇 건만 보여 주면 발췌 길이와 똑같은
#: 비대칭이 생긴다. 실측: 골든 20문항 중 **13문항**이 12건을 넘었고 최대 27건이었다.
#: 그래프에 아직 정리되지 않은 사실이 여기로 올라오므로(AC-6) 잘라내면 그 설계가 무력해진다.
#:
#: 대신 글자 예산만 둔다. 실측 최대가 27건·약 6,000자라 이 예산은 사실상 걸리지 않고,
#: 걸리면 몇 건을 못 넣었는지 프롬프트에 적어 **없다는 뜻으로 읽히지 않게** 한다.
RAW_BUDGET_CHARS = 14_000
#: 집계 예산. 예전에는 JSON 문자열을 4,000자에서 그냥 잘랐다. 자른 자리가 문자열 중간이라
#: 모델은 닫히지 않은 조각을 받았고, 뒤쪽 컬렉션은 보이지도 않는데 없다는 표시도 없었다.
#: 실측: 골든·추가 22문항에서 **36,298자**가 그렇게 사라졌고 한 문항은 7,965자였다.
#: 지금은 **행 단위로** 줄이고 무엇을 몇 행 뺐는지 함께 적는다.
AGGREGATES_BUDGET_CHARS = 8_000


@dataclass
class Synthesis:
    text: str
    recommendation: str = ""
    estimated_parts: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    llm_used: bool = False
    cost_usd: float = 0.0
    cache_hit: bool = False
    error: str = ""


PROMPT = """너는 회사 지식 그래프의 답변 작성기다. 아래 근거만 가지고 한국어 존댓말로 답한다.

[질문]
{question}

[집계 결과 — 답의 뼈대를 잡는 용도. 값을 옮겨 적는 출처가 아니다]
{aggregates}

[근거 발췌 — 답변에 쓸 수 있는 유일한 출처. 앞의 번호가 각주 번호다]
{evidence}

[참고 신호 — 원문 검색이 찾은 발췌. 내용은 써도 되지만 각주 번호는 없다]
{raw_signals}

[판정]
{gaps}

[값을 옮기는 규칙 — 이걸 어기면 그 문장은 통째로 삭제된다]
- 발췌에 있는 값은 요약하지 말고 **원문 표기 그대로** 옮겨라. 금액·버전·기간·회사명·기능 이름·직무 이름을 다른 말로 바꾸거나 반올림하지 마라. 띄어쓰기와 물결표(~)까지 그대로 쓴다.
- 기능·항목·역할을 가리킬 때는 **발췌에 적힌 그 이름을 그대로 쓴다.** 설명으로 풀어 바꿔 부르지 마라(발췌가 "대화형 브라우저 팝업"이라 부르면 답도 그 이름으로 부른다).
- **질문이 그 이름을 조금 다르게 불러도 답에는 발췌의 이름을 쓴다.** 질문의 표현을 그대로 되풀이하지 마라. 발췌가 "카카오 OpenBuilder 봇 연동"이면 질문이 "챗봇 연동"이라 물어도 답은 "카카오 OpenBuilder 봇 연동"이라고 쓴다.
- **날짜·기간은 발췌의 표기 형식을 바꾸지 마라.** 발췌가 `2026-06-01` 이면 답에도 `2026-06-01` 로 쓴다. "2026년 6월 1일" 로 고쳐 쓰면 그 값을 찾을 수 없게 된다.
- 발췌와 참고 신호 어디에도 없는 숫자·회사명·제품명·영문 약어를 쓰지 마라. 없으면 unknowns 에 적어라.
- **질문과 관련된 금액·규모·건수·기간이 발췌에 적혀 있으면 그 값을 빠뜨리지 말고 적어라.** 값을 빼고 정성적으로만 쓰면 답이 약해진다.
- 발췌에 "제공 버전" 처럼 버전이 적혀 있으면 그 값을 그대로 옮겨라. 다만 **자료 이름이나 표 이름에 붙은 번호를 그 기능의 버전으로 바꿔 적지 마라.** 어느 발췌에도 버전이 없을 때만 "언제부터인지는 확인된 자료가 없습니다"라고 적어라.
- 발췌 하나가 곧 한 기능의 한 행이다. **기능 이름과 그 버전은 같은 번호의 발췌 안에서만 짝지어라.** 옆 번호 발췌에 적힌 버전을 이 기능에 붙이면 그 문장은 통째로 삭제된다.
- 집계 결과에만 있고 발췌에 없는 숫자·이름은 문장에 쓰지 마라. 집계는 무엇을 다룰지 정하는 데만 쓴다.
- **숫자 값은** 한 문장에 하나만 담아라. 금액·버전·건수·기간 여러 개를 한 문장에 몰면 그중 하나가 근거와 어긋날 때 나머지 값까지 같이 사라진다. 반면 **이름만 늘어놓는 열거는 한 문장에 여러 개를 담아도 된다**(이름은 발췌에 있는 그대로 쓰면 지워지지 않는다).

[발췌를 고르는 순서]
- **번호는 질문과 얼마나 맞는지의 순서다.** 앞 번호부터 읽고, 앞 번호가 질문이 묻는 것을 직접 말하고 있으면 그것을 답으로 써라. 뒤 번호가 앞 번호를 대신하지 못한다.
- **발췌가 길다고 더 중요한 것이 아니다.** 답이 짧은 발췌 여러 건에 나뉘어 담겨 있으면 그 발췌들을 다 써라. 긴 발췌 하나로 갈음하면 정작 질문이 묻는 대상이 빠진다.
- **어떤 규칙을 그렇게 하기로 정해 둔 자료**(설정·명세·프롬프트·코드)와 **실제 업무 기록**이 같은 것을 말하면, 업무 기록을 답으로 쓰고 규칙 쪽은 덧붙여라. 규칙은 그렇게 하기로 한 약속이지 지금 그렇다는 사실이 아니다.

[빠뜨리지 않기 — 가장 자주 어기는 규칙]
- **먼저 세고 그다음 쓴다.** 질문이 목록·복수를 요구하면(무엇들인가 / 어느 것들인가 / 무엇이고 어디서 나왔나 / 어떤 것이 있나) 발췌와 참고 신호를 훑어 **해당하는 항목이 몇 개인지 먼저 세고, 그 개수만큼 다 적어라.** 하나만 답하고 멈추지 마라.
- 항목이 많아 {max_sentences}문장에 안 들어가면 **문장 수를 줄이려고 항목을 버리지 말고**, 한 문장에 이름을 여러 개 담아 전부 적어라. 항목을 버리는 것이 문장이 긴 것보다 나쁘다.
- 참고 신호(추가 원문 근거)에만 있는 항목도 **빠뜨리지 마라.** 각주 번호가 없을 뿐 근거는 근거다. 각주 없이 문장을 쓰고, 그 항목이 어느 고객사·어느 상황의 것인지 함께 적어라.
- "주요한 것은 …입니다" 처럼 대표 하나만 골라 쓰는 것은 목록 질문의 답이 아니다.

[내용 규칙]
- 질문이 대상(타겟 기업·담당자·사용자·외부 참여자)을 물으면 발췌에 적힌 그 대상의 이름을 그대로 답에 넣어라.
- 발췌에 딱 한 번 나오는 이름·역할도 질문과 맞으면 적어라. **한 번만 등장한다는 것이 덜 중요하다는 뜻이 아니다.**
- 근거가 서로 다른 값을 말하면 한쪽을 고르지 말고 양쪽을 다 적어라.
- 비슷한 항목이 여러 발췌에 있으면 **질문의 표현과 가장 잘 맞는 항목을 먼저 답하고**, 나머지는 이름을 밝혀 구분해 덧붙여라. 항목 이름과 값을 항상 짝지어 적는다. 고르기 어렵다는 이유로 답을 비우지 마라.
- 다만 질문이 기능 하나가 **어느 버전부터** 제공되는지 물으면, 질문 문장과 가장 잘 맞는 발췌 하나의 기능 이름과 버전만 답하라. 이름이 비슷한 다른 기능의 버전은 적지 마라 — 무엇이 답인지 알 수 없게 된다.
- 발췌에 값이 있는데 "확인된 자료가 없습니다"라고 적지 마라. 그 말은 어느 발췌에도 값이 없을 때만 쓴다.
- Capability·Need·BD·Domain·Gap·Pain Point·CONFIRMED·POSSIBLE 같은 영어 분류어를 답변 문장에 쓰지 마라. 발췌에 적힌 한국어 표현을 그대로 써라.
- 자료 이름·표 이름·칸 위치·내부 식별자를 답변 문장에 적지 마라. 출처는 각주로만 밝힌다.
  **예외: 자료끼리 서로 다른 말을 해서 그 차이가 곧 답인 질문**(이것인가 저것인가를 묻는 질문)에서는 어느 자료가 어느 쪽을 말하는지 **자료 이름을 적어 구분해라.** 누가 무엇을 말했는지 빼면 충돌을 설명할 수 없다. 칸 위치와 내부 식별자는 이때도 적지 않는다.
- text 는 {max_sentences}문장 이내로 쓴다.
{extra_rules}
[각주]
- 문장 끝에 [1] [2] 처럼 근거 발췌의 번호를 그대로 달아라. 없는 번호를 지어내지 마라.
- 같은 번호를 citations 에 marker 로 적어라. evidence_id 는 적지 않아도 된다.
- **citations 에 적은 번호는 본문 문장 끝에도 반드시 적어라.** 본문에 번호가 없으면 화면에서 그 근거를 누를 수 없다.

JSON 만 출력해라. 형식:
{{"text": "...", "recommendation": "...", "estimated_parts": [], "citations": [{{"marker": 1}}], "unknowns": [], "next_actions": []}}"""

ESTIMATION_RULE = (
    "- 집계 위에 올린 판단은 estimated_parts 에 따로 적어라. 사실과 섞지 마라.\n"
)

#: 화면에 나가는 문장에서 걷어낼 온톨로지 라벨. 자료에 적힌 말이 아니라 우리가 붙인
#: 분류어라, 그대로 내보내면 읽는 사람에게도 낯설고 traceability 채점에도 무근거 고유명사로
#: 잡힌다(실측: 골든 5문항이 gaps.basis.reason 의 'Capability'·'Need' 때문에 실패).
ONTOLOGY_LABELS_KO: tuple[tuple[str, str], ...] = (
    ("BusinessDomain", "사업 영역"),
    ("Capability", "역량"),
    ("Competitor", "경쟁사"),
    ("Need", "요구"),
    ("Account", "고객사"),
    ("Industry", "산업"),
    ("Feature", "기능"),
    ("Gap", "공백"),
)


def to_korean_labels(text: str) -> str:
    """온톨로지 라벨을 한국어로 바꾼다. 값이 아니라 분류어만 건드린다."""
    out = text or ""
    for english, korean in ONTOLOGY_LABELS_KO:
        out = out.replace(english, korean)
    return out


class LLMSynthesizer:
    """A3 의 LLMService 를 답변 합성에 연결한다."""

    def __init__(self, service, *, max_sentences: int | None = None):
        self._service = service
        self._max_sentences = max_sentences

    def _sentence_budget(self, route: str) -> int:
        if self._max_sentences:
            return self._max_sentences
        return MAX_SENTENCES_FOR_ROUTE.get(route, DEFAULT_MAX_SENTENCES)

    def synthesize(
        self,
        *,
        result: RetrievalResult,
        evidence: Sequence[EvidenceRef],
        raw_signals: Sequence[Any] = (),
        claims: Sequence[dict[str, Any]] = (),
    ) -> Synthesis:
        route = result.route.retriever
        tier = TIER_FOR_ROUTE.get(route, "S")
        prompt = self.build_prompt(result, evidence, raw_signals)

        outcome = self._service.complete(
            prompt,
            schema=SYNTHESIS_SCHEMA,
            tier=tier,
            purpose=f"answer_synthesis:{route}",
            max_tokens=MAX_TOKENS_FOR_TIER[tier],
        )
        if not outcome.ok or not isinstance(outcome.parsed, dict):
            fallback = DeterministicSynthesizer().synthesize(
                result=result, evidence=evidence, raw_signals=raw_signals, claims=claims
            )
            fallback.error = outcome.error or "합성 실패"
            fallback.cost_usd = outcome.cost_usd
            return fallback

        parsed = outcome.parsed
        return Synthesis(
            text=str(parsed.get("text") or ""),
            recommendation=str(parsed.get("recommendation") or ""),
            estimated_parts=[str(x) for x in (parsed.get("estimated_parts") or [])],
            citations=resolve_markers(parsed.get("citations") or [], evidence),
            unknowns=[str(x) for x in (parsed.get("unknowns") or [])],
            next_actions=[str(x) for x in (parsed.get("next_actions") or [])],
            llm_used=True,
            cost_usd=outcome.cost_usd,
            cache_hit=outcome.cache_hit,
        )

    def build_prompt(
        self,
        result: RetrievalResult,
        evidence: Sequence[EvidenceRef],
        raw_signals: Sequence[Any] = (),
    ) -> str:
        route = result.route.retriever
        strategic = route == "Q-S"
        aggregates = result.synthesis_input.get("aggregates") or {}
        cited_ids = {ref.evidence_id for ref in evidence}
        extra_raw = [s for s in raw_signals if getattr(s, "evidence_id", "") not in cited_ids]
        return PROMPT.format(
            question=result.question,
            aggregates=_render_aggregates(aggregates),
            evidence=_render_evidence(evidence),
            raw_signals=_render_raw(extra_raw),
            gaps=_render_gaps(result.gaps),
            max_sentences=self._sentence_budget(route),
            extra_rules=ESTIMATION_RULE if strategic else "",
        )


def resolve_markers(
    citations: Sequence[Any], evidence: Sequence[EvidenceRef]
) -> list[dict[str, Any]]:
    """각주 번호를 evidence_id 로 되돌린다.

    번호는 `_render_evidence` 가 붙인 순서 그대로다. 모델이 evidence_id 를 직접 적어 왔으면
    그것을 존중한다(스크립트 합성기·픽스처가 그렇게 쓴다). 목록 밖 번호는 버린다 —
    버려진 각주는 `verify_citations` 가 그 문장까지 걷어낸다(traceability 100%).
    """
    ids = [ref.evidence_id for ref in evidence]
    out: list[dict[str, Any]] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        item = dict(citation)
        marker = item.get("marker")
        if not item.get("evidence_id"):
            if not isinstance(marker, int) or not 1 <= marker <= len(ids):
                # 존재하지 않는 번호. 지어낸 각주로 보고 그대로 흘려보내 검증에서 걸리게 한다.
                item["evidence_id"] = f"marker:{marker}"
            else:
                item["evidence_id"] = ids[marker - 1]
        out.append(item)
    return out


class DeterministicSynthesizer:
    """LLM 없이 도는 대체 합성기.

    새 문장을 지어내지 않는다. "어느 자료에 이렇게 적혀 있다"를 그대로 옮기고 각주를 단다.
    """

    def __init__(self, max_items: int = 4, snippet_chars: int = 200):
        self._max_items = max_items
        self._snippet_chars = snippet_chars

    def synthesize(
        self,
        *,
        result: RetrievalResult,
        evidence: Sequence[EvidenceRef],
        raw_signals: Sequence[Any] = (),
        claims: Sequence[dict[str, Any]] = (),
    ) -> Synthesis:
        picked = list(evidence)[: self._max_items]
        if not picked:
            return Synthesis(
                text="확인된 자료가 없습니다.",
                unknowns=["질문에 답할 근거를 찾지 못했습니다."],
            )

        lines = ["확인된 자료에 적힌 내용은 다음과 같습니다."]
        citations: list[dict[str, Any]] = []
        for index, ref in enumerate(picked, start=1):
            snippet = " ".join(ref.snippet.split())[: self._snippet_chars]
            lines.append(f"- {snippet}[{index}]")
            citations.append({"marker": index, "evidence_id": ref.evidence_id})

        return Synthesis(text="\n".join(lines), citations=citations)


def _render_evidence(evidence: Sequence[EvidenceRef]) -> str:
    """번호 + 자료 종류 + 발췌. **위치 표기와 evidence_id 는 넣지 않는다.**

    둘 다 게이트가 근거로 인정하지 않는 문자열이라(모듈 docstring 참고), 보여 주는 순간
    모델이 옮겨 적고 그 문장이 삭제된다. 자료 종류는 소문자 snake_case 라 무근거 고유명사
    정규식(대문자 시작)에 걸리지 않아 그대로 둔다 — 모델이 "어느 자료에 적혀 있다"를
    말할 수 있어야 답변이 읽을 만해진다.
    """
    rows = []
    for index, ref in enumerate(list(evidence)[:MAX_EVIDENCE_IN_PROMPT], start=1):
        snippet = " ".join(ref.snippet.split())[:EVIDENCE_SNIPPET_CHARS]
        source_type = ref.source_type or "unknown"
        rows.append(f"[{index}] ({source_type}) {snippet}")
    return "\n".join(rows) or "(없음)"


def _render_raw(raw_signals: Sequence[Any]) -> str:
    """각주를 달 수 없는 참고 발췌. 번호를 주지 않아 모델이 없는 각주를 만들지 않게 한다.

    이 발췌들도 게이트의 근거 풀에는 들어간다(`service._assemble` 의 support_text). 그래서
    내용을 문장에 옮기는 것은 안전하고, 인용만 못 한다.

    개수로 자르지 않는다(§ `RAW_BUDGET_CHARS`). 예산에 걸려 못 넣은 것이 있으면 그 사실을
    적는다 — 안 보이는 것을 없는 것으로 읽으면 "확인된 자료가 없습니다"가 또 나온다.
    """
    rows: list[str] = []
    used = 0
    omitted = 0
    for signal in raw_signals:
        snippet = " ".join(getattr(signal, "snippet", "").split())[:EVIDENCE_SNIPPET_CHARS]
        if not snippet:
            continue
        if used + len(snippet) > RAW_BUDGET_CHARS and rows:
            omitted += 1
            continue
        rows.append(f"- {snippet}")
        used += len(snippet)
    if omitted:
        rows.append(f"- (참고 신호 {omitted}건을 자리가 없어 넣지 못했다. 없다는 뜻이 아니다.)")
    return "\n".join(rows) or "(없음)"


def _render_aggregates(aggregates: Any) -> str:
    """집계를 예산 안에서 **행 단위로** 줄인다. 문자열 중간에서 자르지 않는다.

    집계는 답의 뼈대를 잡는 용도라 구조가 온전해야 읽힌다. 예전에는 완성된 JSON 문자열을
    4,000자에서 그냥 잘라, 모델이 닫히지 않은 조각을 받았다(§ `AGGREGATES_BUDGET_CHARS`).
    지금은 컬렉션마다 앞에서부터 행을 넣다가 예산이 차면 멈추고, 어느 컬렉션에서 몇 행을
    뺐는지 뒤에 한국어로 적는다.

    행의 **순서는 건드리지 않는다.** 어느 행이 앞에 오는지는 A5 의 순위 결정이고, 여기서
    다시 세우면 그 판단을 덮어쓰게 된다.
    """
    if not isinstance(aggregates, dict) or not aggregates:
        return "(없음)"

    kept: dict[str, Any] = {}
    dropped: list[str] = []
    used = 0
    for key, value in aggregates.items():
        if not isinstance(value, list):
            rendered = json.dumps({key: value}, ensure_ascii=False)
            if used + len(rendered) > AGGREGATES_BUDGET_CHARS and kept:
                dropped.append(key)
                continue
            kept[key] = value
            used += len(rendered)
            continue

        rows: list[Any] = []
        for row in value:
            rendered = json.dumps(row, ensure_ascii=False)
            if used + len(rendered) > AGGREGATES_BUDGET_CHARS and kept:
                break
            rows.append(row)
            used += len(rendered)
        kept[key] = rows
        if len(rows) < len(value):
            dropped.append(f"{key} {len(value) - len(rows)}행")

    text = to_korean_labels(json.dumps(kept, ensure_ascii=False))
    if dropped:
        text += "\n(자리가 없어 넣지 못한 집계: " + " · ".join(dropped) + ". 없다는 뜻이 아니다.)"
    return text


#: Gap 3값을 사람 말로. 프롬프트에 영어 enum 을 보여 주면 모델이 그대로 답변에 옮긴다
#: (실측: 'POSSIBLE' 이 들어간 문장이 무근거 고유명사로 삭제됐다).
GAP_VERDICT_KO = {
    "CONFIRMED": "확인된 부재",
    "POSSIBLE": "가능성 있는 공백",
    "UNKNOWN": "판단할 자료 부족",
}


def _render_gaps(gaps: Sequence[Any]) -> str:
    rows = []
    for gap in gaps:
        contract = gap.to_contract() if hasattr(gap, "to_contract") else dict(gap)
        verdict = GAP_VERDICT_KO.get(contract.get("verdict", ""), contract.get("verdict", ""))
        subject = to_korean_labels(contract.get("subject") or "")
        reason = to_korean_labels((contract.get("basis") or {}).get("reason") or "")
        rows.append(f"- {subject}: {verdict} — {reason}")
    return ("\n".join(rows) or "(없음)")[:1200]
