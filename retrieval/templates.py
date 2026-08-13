"""이름 붙은 Cypher 템플릿 (Q-M 골격).

LLM 이 Cypher 를 짓지 않는다. Text2Cypher 는 V2 다(REVISED §0 33행). 질문은 라우터가
템플릿 이름으로 바꾸고, 파라미터만 넘어온다.

DECISIONS A-1: 보조 엣지 3종이 없다. Event↔Account 는 Observation 경유, Feature↔Product 와
Competitor↔Capability 는 Claim-ABOUT 경유로 도달한다. 아래 템플릿은 그 경유 경로를 쓴다.
직접 엣지가 없다는 이유로 "관계 없음"을 반환하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j import Session

DEFAULT_ROW_LIMIT = 25
DEFAULT_EVIDENCE_PER_ROW = 6


@dataclass(frozen=True)
class CypherTemplate:
    name: str
    description: str
    cypher: str
    defaults: dict[str, Any] = field(default_factory=dict)
    #: 이 파라미터가 전부 비어 있으면 실행하지 않는다(그래프 전체를 쏟아내지 않게).
    requires_any: tuple[str, ...] = ()
    #: 행 하나가 들고 올 근거 수. 한 행이 여러 고객사를 묶는 템플릿은 이 값이 작으면
    #: 뒤쪽 고객사의 근거가 통째로 잘려 답이 그 고객사를 말하지 못한다.
    evidence_per_row: int | None = None

    def merged_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(self.defaults)
        merged.update({k: v for k, v in (params or {}).items() if v is not None})
        return merged

    def is_runnable(self, params: dict[str, Any]) -> bool:
        if not self.requires_any:
            return True
        return any(params.get(key) for key in self.requires_any)


# 여러 Observation 의 evidence_ids 를 평평하게 편다. null 은 collect 가 이미 걸러낸다.
_FLATTEN = "reduce(acc = [], l IN nested | acc + coalesce(l, []))"

# 고객사별 근거 목록을 **곳마다 하나씩 돌아가며** 평평하게 편다.
# 한 고객사의 근거를 몰아서 앞에 두면 뒤쪽 고객사가 행별 상한과 선별 감쇠에 걸려 통째로
# 잘린다(실측: 마바손보 3건이 앞을 차지하고 타하렌터카 1건이 24칸 밖으로 밀렸다).
# 라운드 수는 한 고객사가 들고 오는 근거 수의 현실적 상한으로 잡는다.
_ROUND_ROBIN = (
    "reduce(flat = [], i IN range(0, 5) | "
    "flat + reduce(inner = [], l IN ids_by_account | inner + coalesce(l[i..i+1], [])))"
)


# 사업영역 판정은 Deal 경로로 한다(딜이 어느 영역에 속하는지가 그 경로에만 있다).
# 고객사는 그 경로로 좁히지 않는다. 딜이 없는 고객사도 Need 를 가지고 있고,
# 좁히면 "여러 곳에서 같은 요구가 나온다"는 답의 근거가 조용히 줄어든다.
# 실측: need.channel_control_absent 는 고객사 4곳인데 딜 경로로는 2곳만 보였다.
_COMMON_NEEDS = f"""
MATCH (bd:BusinessDomain)<-[:IN_DOMAIN]-(:Deal)-[:WITH_ACCOUNT]->(:Account)-[:HAS_NEED]->(n:Need)
WHERE n.name IS NOT NULL
WITH n, bd.name AS domain
ORDER BY domain
WITH n, collect(DISTINCT domain) AS domains
WHERE size(domains) >= $min_domains
MATCH (holder:Account)-[:HAS_NEED]->(n)
OPTIONAL MATCH (o:Observation)-[:MENTIONS]->(n)
WHERE (o)-[:MENTIONS]->(holder)
WITH n, domains, holder.canonical_name AS account, collect(o.evidence_ids) AS nested
ORDER BY account
WITH n, domains, collect(account) AS accounts,
     collect({_FLATTEN}) AS ids_by_account
RETURN n.need_id AS need_id,
       n.name AS need,
       domains AS domains,
       size(domains) AS domain_count,
       accounts AS accounts,
       size(accounts) AS account_count,
       {_ROUND_ROBIN} AS evidence_ids,
       ids_by_account AS evidence_ids_by_target
ORDER BY domain_count DESC, account_count DESC, need
LIMIT $limit
"""


_ACCOUNTS_WITH_NEED = f"""
MATCH (a:Account)-[:HAS_NEED]->(n:Need)
WHERE n.name IS NOT NULL
  AND (size($need_terms) = 0
       OR any(t IN $need_terms WHERE toLower(n.name) CONTAINS t)
       OR any(t IN $need_terms WHERE toLower(coalesce(n.definition, '')) CONTAINS t))
OPTIONAL MATCH (a)-[:BELONGS_TO]->(ind:Industry)
OPTIONAL MATCH (o:Observation)-[:MENTIONS]->(n)
WITH n, a, collect(DISTINCT ind.name) AS industries, collect(o.evidence_ids) AS nested
RETURN n.need_id AS need_id,
       n.name AS need,
       a.canonical_name AS account,
       industries AS industries,
       {_FLATTEN} AS evidence_ids
ORDER BY need, account
LIMIT $limit
"""


_INDUSTRIES_USING_CAPABILITY = f"""
MATCH (n:Need)-[:ADDRESSED_BY]->(c:Capability)
WHERE size($capability_terms) = 0
   OR any(t IN $capability_terms WHERE toLower(c.name) CONTAINS t)
MATCH (a:Account)-[:HAS_NEED]->(n)
OPTIONAL MATCH (a)-[:BELONGS_TO]->(ind:Industry)
WITH c, ind.name AS industry, a.canonical_name AS account, n.name AS need
ORDER BY industry, account, need
WITH c,
     [x IN collect(DISTINCT industry) WHERE x IS NOT NULL] AS industries,
     collect(DISTINCT account) AS accounts,
     [x IN collect(DISTINCT need) WHERE x IS NOT NULL] AS needs
OPTIONAL MATCH (f:Feature)-[:IMPLEMENTS]->(c)
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(f)
WITH c, industries, accounts, needs,
     count(DISTINCT f) AS feature_count,
     collect(cl.evidence_ids) AS nested
RETURN c.capability_id AS capability_id,
       c.name AS capability,
       c.addon AS addon,
       industries AS industries,
       size(industries) AS industry_count,
       accounts AS accounts,
       size(accounts) AS account_count,
       needs AS needs,
       feature_count AS feature_count,
       {_FLATTEN} AS evidence_ids
ORDER BY industry_count DESC, account_count DESC, capability
LIMIT $limit
"""


_DEALS_BY_DOMAIN = f"""
MATCH (d:Deal)-[:IN_DOMAIN]->(bd:BusinessDomain)
WHERE size($domain_terms) = 0
   OR any(t IN $domain_terms WHERE toLower(bd.name) CONTAINS t)
OPTIONAL MATCH (d)-[:WITH_ACCOUNT]->(a:Account)
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(d)
WITH bd, d, a.canonical_name AS account, collect(cl.evidence_ids) AS nested
ORDER BY bd.name, account, d.natural_key
WITH bd,
     collect({{account: account, stage: d.stage_value, outcome: d.outcome,
               scope: d.deal_scope, observed_at: d.observed_at}}) AS deals,
     collect({_FLATTEN}) AS nested_evidence
RETURN bd.bd_id AS bd_id,
       bd.name AS domain,
       bd.bd_status AS bd_status,
       size(deals) AS deal_count,
       size([x IN deals WHERE x.outcome = 'won']) AS won_count,
       size([x IN deals WHERE x.outcome = 'lost']) AS lost_count,
       deals AS deals,
       reduce(acc = [], l IN nested_evidence | acc + coalesce(l, [])) AS evidence_ids
ORDER BY deal_count DESC, domain
LIMIT $limit
"""


# 사업영역을 상태로 훑는 질문("관리 중인 사업영역은?")용. 상태는 속성이므로 원문 검색이
# 아니라 속성으로 판정한다. 전문검색에 맡기면 '제외' 가 프로젝트 범위 제외로 잡혀 엉뚱한
# 답이 나온다(실측: "BD 중 제외로 분류된 것?" → "라이나생명 프로젝트에서 OCR 이 제외").
#
# 주의: 골든이 그 질문의 경로를 Q-E 로 못 박아 두었으므로 **라우터는 이 템플릿으로 보내지
# 않는다.** 대상을 이름 대신 상태로 특정하는 일은 `linking._link_domains_by_status` 가 한다.
# 이 템플릿은 상태별 개관을 한 번에 뽑아야 할 때 `run_template` 로 직접 부른다.
_DOMAINS_BY_STATUS = f"""
MATCH (bd:BusinessDomain)
WHERE size($statuses) = 0 OR bd.bd_status IN $statuses
OPTIONAL MATCH (bd)-[:TARGETS]->(ind:Industry)
WITH bd, [x IN collect(DISTINCT ind.name) WHERE x IS NOT NULL] AS industries
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(bd)
WITH bd, industries, collect(cl.evidence_ids) AS nested
RETURN bd.bd_id AS bd_id,
       bd.name AS domain,
       bd.bd_status AS bd_status,
       bd.industry_scope AS industry_scope,
       bd.target_company AS target_company,
       bd.market_size_note AS market_size_note,
       bd.needs_human_confirm AS needs_human_confirm,
       industries AS industries,
       {_FLATTEN} AS evidence_ids
ORDER BY bd.bd_status, domain
LIMIT $limit
"""


_FEATURE_VERSION_HISTORY = f"""
MATCH (f:Feature)
WHERE ($name <> '' AND toLower(f.name) CONTAINS toLower($name))
   OR any(t IN $terms WHERE toLower(f.name) CONTAINS t)
   OR any(t IN $terms WHERE toLower(coalesce(f.description, '')) CONTAINS t)
OPTIONAL MATCH (f)-[:IMPLEMENTS]->(c:Capability)
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(f)
WITH f,
     [x IN collect(DISTINCT c.name) WHERE x IS NOT NULL] AS capabilities,
     collect(cl.evidence_ids) AS nested
RETURN f.natural_key AS feature_key,
       f.name AS feature,
       f.screen AS screen,
       f.area AS area,
       f.introduced_in AS introduced_in,
       f.available_versions AS available_versions,
       capabilities AS capabilities,
       {_FLATTEN} AS evidence_ids
ORDER BY feature
LIMIT $limit
"""


CYPHER_TEMPLATES: dict[str, CypherTemplate] = {
    t.name: t
    for t in (
        CypherTemplate(
            name="common_needs_across_domains",
            description="여러 BusinessDomain 에서 반복되는 Need. Deal 의 IN_DOMAIN 을 경유한다.",
            cypher=_COMMON_NEEDS,
            defaults={"min_domains": 2, "limit": DEFAULT_ROW_LIMIT},
            # 한 행이 고객사 여러 곳을 묶는다. 기본 6이면 네 곳 중 뒤쪽 한 곳의 근거가
            # 전부 잘려(실측: 카파전선 E2·E3) 답이 그 고객사를 말하지 못했다.
            evidence_per_row=12,
        ),
        CypherTemplate(
            name="accounts_with_need",
            description="한 Need 를 가진 고객사 목록과 그 산업.",
            cypher=_ACCOUNTS_WITH_NEED,
            defaults={"need_terms": [], "limit": DEFAULT_ROW_LIMIT},
        ),
        CypherTemplate(
            name="industries_using_capability",
            description="Capability 를 필요로 하는 산업. Need→Account→Industry 를 경유한다.",
            cypher=_INDUSTRIES_USING_CAPABILITY,
            defaults={"capability_terms": [], "limit": DEFAULT_ROW_LIMIT},
        ),
        CypherTemplate(
            name="deals_by_domain",
            description="BusinessDomain 별 딜 현황.",
            cypher=_DEALS_BY_DOMAIN,
            defaults={"domain_terms": [], "limit": DEFAULT_ROW_LIMIT},
        ),
        CypherTemplate(
            name="domains_by_status",
            description="사업영역을 상태(관리/후보/제외)로 조회한다. 상태는 속성이므로 원문 검색이 아니라 속성으로 판정한다.",
            cypher=_DOMAINS_BY_STATUS,
            defaults={"statuses": [], "limit": DEFAULT_ROW_LIMIT},
        ),
        CypherTemplate(
            name="feature_version_history",
            description="기능의 도입 버전·제공 버전 이력과 구현 Capability.",
            cypher=_FEATURE_VERSION_HISTORY,
            defaults={"name": "", "terms": [], "limit": DEFAULT_ROW_LIMIT},
            requires_any=("name", "terms"),
        ),
    )
}


class UnknownTemplateError(KeyError):
    pass


def run_template(
    session: Session,
    name: str,
    params: dict[str, Any] | None = None,
    *,
    evidence_per_row: int = DEFAULT_EVIDENCE_PER_ROW,
) -> list[dict[str, Any]]:
    """템플릿을 이름으로 실행한다. 결과는 결정적이다(정렬·상한이 Cypher 안에 있다)."""
    try:
        template = CYPHER_TEMPLATES[name]
    except KeyError as exc:  # 오탈자로 조용히 빈 결과가 되지 않게 한다
        raise UnknownTemplateError(
            f"없는 템플릿: {name!r} (있는 것: {', '.join(sorted(CYPHER_TEMPLATES))})"
        ) from exc

    merged = template.merged_params(params)
    if not template.is_runnable(merged):
        return []

    per_row = template.evidence_per_row or evidence_per_row
    rows = [dict(record) for record in session.run(template.cypher, **merged)]
    for row in rows:
        row["evidence_ids"] = _dedupe(row.get("evidence_ids") or [])[:per_row]
    return rows


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
