"""Router — 템플릿 → 신호 규칙 → S tier enum 폴백 (REVISED §4 ⑦ · §6 A5).

세 단계를 거치고, 어느 단계에서 정해졌는지를 결과에 남긴다. 나중에 "왜 이 질문이 Q-S 로
갔나"를 되짚을 수 있어야 하기 때문이다.

  1단 템플릿   질문 모양이 이름 붙은 Cypher 템플릿과 맞아떨어지는가
  2단 신호 규칙 전략 신호(권고·전망·부족) / 사실 조회 신호
  3단 폴백     S tier 에 enum 하나만 물어본다. 분류기가 없으면 Q-E 로 둔다

규칙은 **질문의 모양**으로 쓴다. 특정 골든 문항의 문자열을 그대로 넣지 않는다.
어휘의 출처는 `eval/golden.yaml` 이 요구하는 질문 계열(전략/멀티홉/사실)이다.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Sequence

from .templates import CYPHER_TEMPLATES
from .text import normalize
from .types import RouteDecision

RETRIEVERS = ("Q-E", "Q-M", "Q-S")


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------------------------------------------------------
# 1단 — 템플릿
# ---------------------------------------------------------------------------
# (템플릿 이름, 라우트, 반드시 다 맞아야 하는 패턴들)
TEMPLATE_RULES: tuple[tuple[str, str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "common_needs_across_domains",
        "Q-M",
        (
            _rx(r"(여러|다수|복수|공통|여러\s*개의)"),
            _rx(r"(business\s*domain|\bBD\b|도메인|사업\s*영역)"),
            _rx(r"(need|니즈|요구|문제|pain|페인)"),
        ),
    ),
    (
        "accounts_with_need",
        "Q-M",
        (_rx(r"(어느|어떤|무슨)\s*(고객사|고객|계정|account)"),),
    ),
    (
        "industries_using_capability",
        "Q-M",
        (
            _rx(r"(어느|어떤|무슨)\s*(산업|업종|industry)"),
            _rx(r"(capability|역량|기능)"),
        ),
    ),
    (
        "deals_by_domain",
        "Q-M",
        (
            _rx(r"(BD|도메인|사업\s*영역)\s*별"),
            _rx(r"(딜|deal|기회|파이프라인|수주)"),
        ),
    ),
    (
        "feature_version_history",
        "Q-E",
        (_rx(r"(어느|어떤|몇)\s*버전(부터|에서|이후)|언제부터|버전\s*이력"),),
    ),
)

#: 사업영역의 상태를 이름 대신 **속성으로** 가리키는 질문의 표지.
#: '제외' 는 사업영역 상태이자 프로젝트 범위 제외이기도 해서, 원문 검색에 맡기면
#: 엉뚱한 것을 물어온다(실측: "BD 중 제외로 분류된 것?" 에 "라이나생명 프로젝트에서
#: OCR 이 제외" 를 답했다). 라우트는 Q-E 그대로 두고 **엔티티 연결 단계**에서 해결한다.
#: 이름이 아니라 속성으로 대상을 특정하는 것이므로 Q-E 의 정의(엔티티 연결 → 확장)에 맞는다.
DOMAIN_STATUS_QUESTION: re.Pattern[str] = _rx(
    r"(business\s*domain|\bBD\b|도메인|사업\s*영역)[^?]*"
    r"((제외|후보|관리)\s*(로|으로|에)?\s*(분류|지정|구분)|상태(는|가))"
)

#: 질문이 가리키는 사업영역 상태. 질문에 나온 말만 넘긴다(없으면 전체).
DOMAIN_STATUS_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("제외", _rx(r"제외")),
    ("후보", _rx(r"후보")),
    ("관리", _rx(r"관리")),
)


def asks_domain_status(question: str) -> bool:
    """사업영역을 상태로 특정하는 질문인가."""
    return bool(DOMAIN_STATUS_QUESTION.search(normalize(question)))


def domain_statuses_in(question: str) -> list[str]:
    """질문에서 사업영역 상태 낱말을 뽑는다. 없으면 빈 목록(=전체)."""
    text = normalize(question)
    return [value for value, pattern in DOMAIN_STATUS_WORDS if pattern.search(text)]


# ---------------------------------------------------------------------------
# 2단 — 신호 규칙
# ---------------------------------------------------------------------------
# 전략 질문의 표지: 권고를 요구하거나(무엇이 좋은가), 전망을 요구하거나(가능성),
# 부족·공백을 묻거나, 전사 단위 개관을 요구한다.
STRATEGIC_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sales_point", _rx(r"sales\s*point|세일즈\s*포인트|영업\s*포인트")),
    ("advice", _rx(r"좋은가|좋을까|좋겠는가|바람직|추천|권고|공략|어떻게\s*접근")),
    ("outlook", _rx(r"가능성이\s*(높|큰)|전망|유망|확장(될|할|성|\s*가능)")),
    ("shortfall", _rx(r"부족한|미흡한|취약한|빈틈|보완이\s*필요")),
    ("portfolio", _rx(r"개척|각각\s*어느\s*단계|성숙도|우선순위|전략")),
    ("capability_response", _rx(r"(대응할|대응\s*가능한|메울)\s*\S{0,6}(capability|역량|기능)")),
)

# 사실 조회 질문의 표지. 여기까지 오면 Q-E 로 본다(엔티티 중심 1~2hop).
FACT_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("what_is", _rx(r"무엇인가|무엇인지|뭐인가|뭐야")),
    ("yes_no", _rx(r"(되나|되나요|하나\?|하나$|되는가|있나|있었나|있는가|맞나|맞습니까)")),
    ("how_much", _rx(r"얼마|규모|몇\s*건|어떻게\s*되")),
    ("which_stage", _rx(r"어느\s*단계|어느\s*상태|진행\s*상황")),
    ("tell_me", _rx(r"알려\s*(줘|주세요|달라)|설명해")),
    ("classification", _rx(r"인가,|아니면|분류된|해당하나")),
)


def _all_match(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return all(p.search(text) for p in patterns)


RouteClassifier = Callable[[str], str | None]


class Router:
    """질문 하나를 세 갈래 중 하나로 보낸다.

    `classifier` 는 3단 폴백이다. 주입하지 않으면 폴백 없이 Q-E 로 둔다(LLM 없이도 돌아야 한다).
    """

    def __init__(self, classifier: RouteClassifier | None = None):
        self._classifier = classifier

    # -- 공개 API ---------------------------------------------------------
    def route(self, question: str, linked_entities: Sequence[object] = ()) -> RouteDecision:
        text = normalize(question)

        decision = self._match_template(text)
        if decision is not None:
            return decision

        decision = self._match_signals(text, linked_entities)
        if decision is not None:
            return decision

        return self._fallback(text)

    # -- 1단 --------------------------------------------------------------
    def _match_template(self, text: str) -> RouteDecision | None:
        for name, retriever, patterns in TEMPLATE_RULES:
            if not _all_match(patterns, text):
                continue
            if name not in CYPHER_TEMPLATES:  # 이름만 있고 구현이 없는 템플릿을 막는다
                continue
            return RouteDecision(
                retriever=retriever,
                stage="template",
                matched_rule=f"template:{name}",
                template=name,
                signals=(name,),
            )
        return None

    # -- 2단 --------------------------------------------------------------
    def _match_signals(
        self, text: str, linked_entities: Sequence[object]
    ) -> RouteDecision | None:
        hits = [name for name, pattern in STRATEGIC_SIGNALS if pattern.search(text)]
        if hits:
            return RouteDecision(
                retriever="Q-S",
                stage="signal",
                matched_rule=f"signal:strategic:{hits[0]}",
                signals=tuple(hits),
            )

        hits = [name for name, pattern in FACT_SIGNALS if pattern.search(text)]
        if hits or linked_entities:
            rule = f"signal:fact:{hits[0]}" if hits else "signal:entity_linked"
            return RouteDecision(
                retriever="Q-E",
                stage="signal",
                matched_rule=rule,
                signals=tuple(hits) or ("entity_linked",),
            )
        return None

    # -- 3단 --------------------------------------------------------------
    def _fallback(self, text: str) -> RouteDecision:
        if self._classifier is not None:
            answer = self._classifier(text)
            if answer in RETRIEVERS:
                return RouteDecision(
                    retriever=answer,
                    stage="llm_fallback",
                    matched_rule="llm_enum_fallback",
                    llm_classifier_used=True,
                )
            return RouteDecision(
                retriever="Q-E",
                stage="default",
                matched_rule="llm_enum_rejected",
                llm_classifier_used=True,
            )
        return RouteDecision(
            retriever="Q-E",
            stage="default",
            matched_rule="no_rule_matched",
        )


# ---------------------------------------------------------------------------
# S tier 폴백 분류기
# ---------------------------------------------------------------------------

ROUTE_ENUM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["retriever"],
    "properties": {"retriever": {"enum": list(RETRIEVERS)}},
}

_FALLBACK_PROMPT = """다음 질문을 세 유형 중 하나로 분류해라.

Q-E  하나의 대상(고객사·기능·BD·딜)에 대한 사실 조회
Q-M  여러 대상을 가로질러 세는 질문(공통 항목·목록 집계)
Q-S  판단·권고·전망·부족한 것을 묻는 전략 질문

질문: {question}

{{"retriever": "..."}} 형태의 JSON 만 출력해라."""

FALLBACK_MAX_TOKENS = 64


def llm_route_classifier(service, *, max_tokens: int = FALLBACK_MAX_TOKENS) -> RouteClassifier:
    """LLMService 를 3단 폴백으로 감싼다.

    실측 단가가 호출당 수 센트·100초대라 폴백은 출력 토큰을 최소로 묶는다. 실패하면
    None 을 돌려 Router 가 기본값으로 내려가게 한다(질의가 예외로 죽지 않게).
    """

    def classify(question: str) -> str | None:
        result = service.complete(
            _FALLBACK_PROMPT.format(question=question),
            schema=ROUTE_ENUM_SCHEMA,
            tier="S",
            purpose="router_fallback",
            max_tokens=max_tokens,
        )
        if not result.ok or not isinstance(result.parsed, dict):
            return None
        return result.parsed.get("retriever")

    return classify
