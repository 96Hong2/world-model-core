"""인용 라벨링 — authority_label 과 claim_domain (REVISED §5 · policy.schema x-1a-scope).

1A 가 하는 것은 **라벨을 붙여 보여주는 것까지**다. matrix 를 이용한 rerank, evidence_strength
상한, authority_caveat 자동 생성은 1B(B2) 범위이고 `config/authority-policy.yaml` 도 아직 없다.

그래서 tier 는 점수표가 아니라 계약 문서가 이미 적어 둔 사실 하나로 정한다.
`contracts/source_type.enum.json` 의 x-catalog 는 자료마다 "무엇의 정본인가"와
"VERIFIED Claim 을 허용하는가"를 못 박고 있다. 이 두 가지만 읽는다.

    T1  이 질문 유형(claim_domain)의 정본으로 선언된 자료
    T2  정본은 아니지만 VERIFIED Claim 이 허용되는 1차 기록
    T3  1차 기록이지만 검증 Claim 이 허용되지 않는 사내 자료(전언·계획·발화)
    T4  대외 서술 자료(제안서·브로슈어) — 제품 실동작에서 release_spec 을 이길 수 없다
    T5  분류할 수 없는 자료

claim_domain 은 질문의 모양으로 정한다. 못 정하면 중립으로 두고 그 사실을 응답에 남긴다
(policy.schema x-rules: "domain 분류 실패 시 중립 프로파일로 동작하고 캐비앗을 단다").

**중립은 특정 도메인이 아니다.** 예전에는 분류 실패 시 `product_behavior` 로 떨어뜨렸는데,
그러면 그 도메인의 정본 자료(release_spec·product_doc 등)가 아무 근거 없이 T1 대접을 받고
Evidence Strength 까지 HIGH 로 올라간다. 실측에서 딜 질문("어떤 Sales Point 를 잡을까")이
제품 동작 질문으로 취급됐고, 정작 그 질문의 정본인 sales_activity_log 는 T2 였다.
그래서 분류 실패는 `neutral=True` 로 다뤄 **어느 source_type 도 T1 을 받지 못하게** 한다.

계약(`policy.schema.json#/$defs/claimDomain`)이 claim_domain 을 5값으로 닫아 두었고
`answer.schema.json` 의 authority_label 이 이 필드를 필수로 요구한다. 그래서 "미분류"라는
여섯 번째 값을 만들 수 없다. 라벨에는 자리표시자를 넣되 tier 는 중립으로 계산하고 캐비앗을
달아, 값이 혼자 읽혔을 때 정본 판정으로 오해되지 않게 한다. route 쪽은 claim_domain 이
선택 필드라 아예 빼고 `domain_classification_failed` 만 남긴다.
"""

from __future__ import annotations

import re
from typing import Any

from .contracts import source_type_catalog

POLICY_VERSION = "1.0.0-labeling"

CLAIM_DOMAINS = (
    "product_behavior",
    "product_intent",
    "customer_need",
    "deal_fact",
    "market_fact",
)

#: 분류 실패 시 라벨에 넣는 자리표시자. 계약이 5값으로 닫혀 있어 값 자체는 골라야 하지만
#: tier 계산에는 쓰지 않는다(`neutral=True`). 이 값을 근거로 T1 을 주면 안 된다.
NEUTRAL_DOMAIN = "product_behavior"

NEUTRAL_CAVEAT = "질문 유형을 분류하지 못해 중립으로 판정했습니다. 어느 자료도 정본으로 보지 않았습니다."


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


#: BD 레지스트리 질문. `bd_registry` 가 "BD 의 산업 구분·타겟기업·역할·업무·성숙도"의 정본이고
#: 그것은 customer_need 에 묶여 있다. 이 규칙을 deal_fact 보다 앞에 두는 이유는 실측이다 —
#: "BD 들은 무엇이고 각각 어느 단계인가"가 `어느\s*단계` 에 걸려 딜 질문으로 분류됐고,
#: "자동차금융 BD의 타겟 기업과 시장 규모"는 `시장` 에 걸려 market_fact 로 갔다. 둘 다
#: BD 레지스트리가 정본인 질문인데 정본이 T1 을 못 받았다.
_BD_QUESTION = r"business\s*domain|비즈니스\s*도메인|사업\s*영역|(?<![A-Za-z])BD(?![A-Za-z])"

#: 영업 어휘. "어떻게 팔까/무엇으로 공략할까"는 딜 사실 질문이고 그 정본은 활동일지다.
_SALES_QUESTION = (
    r"sales\s*point|세일즈\s*포인트|영업\s*포인트|공략|제안\s*전략|어떻게\s*팔|"
    r"수주\s*전략|어떤\s*카드|셀링\s*포인트"
)

# 질문의 모양 → claim_domain. 위에서부터 먼저 맞는 것을 쓴다.
#
# deal_fact 를 둘로 쪼개 BD 규칙을 사이에 끼웠다. `어느 단계`·`담당자`·`시장` 은 혼자서는
# 딜 질문의 표시가 못 된다 — BD 레지스트리 질문에도 그대로 나온다. 그래서 딜임이 분명한
# 어휘(딜·수주·제안가·영업 어휘)만 BD 규칙보다 앞에 두고, 약한 어휘는 뒤로 보냈다.
DOMAIN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deal_fact", _rx(r"딜|수주|제안가|파이프라인|접촉|미팅|영업\s*활동|" + _SALES_QUESTION)),
    ("customer_need", _rx(_BD_QUESTION)),
    ("deal_fact", _rx(r"어느\s*단계|담당자")),
    ("market_fact", _rx(r"시장|벌금|규제|경쟁사|업계|시장\s*규모|매출\s*전망")),
    (
        "customer_need",
        _rx(
            r"pain|페인|니즈|need|요구|불편|통제|문제(는|가|를)?\s*무엇|공통적으로|"
            r"부족한|미충족|해결할\s*수\s*있는\s*문제"
        ),
    ),
    ("product_intent", _rx(r"전제|로드맵|검토\s*중|개발\s*필요|추진|방향|계획|확장될")),
    (
        "product_behavior",
        _rx(r"기능|버전|지원(하|되)|제공(하|되)|동작|화면|되나|연동|다운로드"),
    ),
)

#: 각 claim_domain 의 정본 자료. 근거는 x-catalog 의 source_of_record_for 문장이다.
#:   release_spec  "제품 기능의 존재·이름·화면 소속·도입 버전"
#:   product_doc   "구축·설정 가이드의 설정 키 이름과 기본값, 라이선스 게이팅"
#:   code/test/production_signal  "실제 구현 사실" / "검증된 동작" / "운영 환경의 실제 동작"
#:   internal_memo "그 협업 건의 현재 진행 상태와 내부 의사결정 안건·미해결 전제"
#:   bd_openbook   "적합/부적합 판단 기준, 그리고 '핵심 목적이 아닌 것'의 명시"
#:   pain_registry "도입 전 Pain 원문과 대응 기능·제공구분, 명시적 기능 부재 12건"
#:   bd_registry   "BD 의 산업 구분·타겟기업·BR/Guest 역할·업무·성숙도"
#:   sales_activity_log "딜 단계·예상매출·성공확률·현재 이슈의 1차 기록"
#:   slack_thread  "그 시점에 사내에서 오간 발화 자체와 리드 인입 사실"
#:   web_official  "외부 1차 출처의 시장 사실"
SOURCE_OF_RECORD: dict[str, frozenset[str]] = {
    "product_behavior": frozenset(
        {"release_spec", "product_doc", "code", "test", "production_signal", "user_manual"}
    ),
    "product_intent": frozenset({"internal_memo", "bd_openbook", "architecture_spec"}),
    "customer_need": frozenset(
        {"pain_registry", "bd_registry", "bd_registry_aux", "customer_internal_report"}
    ),
    "deal_fact": frozenset({"sales_activity_log", "slack_thread"}),
    "market_fact": frozenset({"web_official", "compliance_checklist"}),
}

#: 대외 서술 자료. 제품 실동작 축에서 release_spec 을 이겨서는 안 된다(v1.0 §F-1 실패사례 1).
OUTWARD_FACING: frozenset[str] = frozenset({"proposal", "product_brochure", "internal_deck"})


def classify_claim_domain(question: str) -> tuple[str, bool]:
    """(claim_domain, 분류 실패 여부)."""
    for domain, pattern in DOMAIN_RULES:
        if pattern.search(question or ""):
            return domain, False
    return NEUTRAL_DOMAIN, True


def tier_for(source_type: str, claim_domain: str, *, neutral: bool = False) -> str:
    """`neutral=True` 면 T1 을 주지 않는다 — 질문 유형을 모르면 정본도 정할 수 없다."""
    if not source_type:
        return "T5"
    if not neutral and source_type in SOURCE_OF_RECORD.get(claim_domain, frozenset()):
        return "T1"
    catalog = source_type_catalog().get(source_type) or {}
    if source_type in OUTWARD_FACING:
        return "T4"
    if catalog.get("verified_claim_allowed"):
        return "T2"
    if catalog:
        return "T3"
    return "T5"


TIER_ORDER = {"T1": 0, "T2": 1, "T3": 2, "T4": 3, "T5": 4}


def best_tier(tiers: list[str]) -> str:
    if not tiers:
        return "T5"
    return min(tiers, key=lambda t: TIER_ORDER.get(t, 9))


def authority_label(
    *,
    source_type: str,
    claim_domain: str,
    source_of_record_for: str = "",
    neutral: bool = False,
) -> dict[str, Any]:
    label: dict[str, Any] = {
        "tier": tier_for(source_type, claim_domain, neutral=neutral),
        "claim_domain": claim_domain,
        "source_type": source_type or "internal_memo",
        "policy_version": POLICY_VERSION,
    }
    if source_of_record_for:
        label["source_of_record_for"] = source_of_record_for

    catalog = source_type_catalog().get(source_type) or {}
    not_record = catalog.get("not_source_of_record_for")
    if neutral:
        # 자리표시자 claim_domain 이 정본 판정으로 오해되지 않게 한다(policy.schema x-rules).
        label["caveat"] = NEUTRAL_CAVEAT
    elif label["tier"] in {"T4", "T5"} and not_record:
        label["caveat"] = f"이 자료가 정본이 아닌 축: {not_record[:160]}"
    return label
