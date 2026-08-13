// 둘러보기 첫 화면. 질문을 하지 않아도 회사 지식으로 들어갈 수 있어야 한다.
//
// KPI 대시보드로 만들지 않는다. 여기 있는 숫자는 "어디로 들어가면 볼 것이 많은지" 를
// 가늠하는 보조이고, 주역은 들어가는 문(사업영역·고객사·요구·역량·기능)이다.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { BrowseOverview } from "../../api/browse";
import { EntityIcon } from "../../graph/icons";
import type { EntityType } from "../../graph/adapter";
import { typeColorVar } from "../../graph/adapter";

/** 둘러보기 입구. 순서는 "회사가 무엇을 하려는가 → 누가 → 무엇을 원하나 → 우리가 뭘 할 수 있나". */
const GATES: {
  slug: string;
  label: string;
  type: EntityType;
  why: string;
  countLabel: string;
}[] = [
  {
    slug: "domain",
    label: "사업영역",
    type: "BusinessDomain",
    why: "회사가 어떤 영역을 개척하고 있고 각각 어디까지 왔는지 봅니다.",
    countLabel: "BusinessDomain",
  },
  {
    slug: "account",
    label: "고객사",
    type: "Account",
    why: "고객사·파트너마다 어떤 요구와 거래가 기록돼 있는지 봅니다.",
    countLabel: "Account",
  },
  {
    slug: "need",
    label: "요구",
    type: "Need",
    why: "여러 고객사에서 반복되는 요구를 찾습니다. 제품 우선순위의 근거입니다.",
    countLabel: "Need",
  },
  {
    slug: "capability",
    label: "역량",
    type: "Capability",
    why: "요구에 대응하는 우리 역량과, 그것을 구현하는 기능을 봅니다.",
    countLabel: "Capability",
  },
  {
    slug: "feature",
    label: "기능",
    type: "Feature",
    why: "제품 기능이 어떤 역량·요구와 이어져 있는지 봅니다.",
    countLabel: "Feature",
  },
  {
    slug: "deal",
    label: "거래",
    type: "Deal",
    why: "실제 거래가 어느 단계에 있고 무엇이 걸려 있는지 봅니다.",
    countLabel: "Deal",
  },
];

/** 성숙도 5축을 막대로. 값이 문자열이라 숫자를 뽑아 쓴다. */
function maturityLevel(raw?: string | null): number {
  if (!raw) return 0;
  const hit = /(\d)/.exec(raw);
  return hit ? Math.min(5, Number(hit[1])) : 0;
}

export function ExplorePage() {
  const { browse } = useWm();
  const [data, setData] = useState<BrowseOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    browse
      .overview(ctl.signal)
      .then(setData)
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse]);

  const countOf = (label: string) =>
    data?.counts.find((c) => c.label === label)?.count ?? null;

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>둘러보기</h1>
        <span className="lede">질문하지 않고 회사 지식을 훑습니다</span>
        <span className="spacer" />
        {data && (
          <span className="meta">
            지식 월드 노드 {data.total_nodes.toLocaleString()} · 연결{" "}
            {data.total_edges.toLocaleString()}
          </span>
        )}
      </div>

      <div className="browse-page scroll-y">
        <div className="browse-inner">
          {error && (
            <div className="notice" data-tone="error">
              <div className="notice-title">지식 월드를 읽지 못했습니다</div>
              <div className="notice-body">{error}</div>
              <div className="notice-hint">
                답변 API 가 떠 있는지 확인한 뒤 새로 고쳐 주세요.
              </div>
            </div>
          )}

          <section className="section-block">
            <h2>어디로 들어갈까요</h2>
            <div className="gates">
              {GATES.map((g) => (
                <Link
                  key={g.slug}
                  to={`/explore/${g.slug}`}
                  className="gate"
                  style={{ ["--type-color" as string]: typeColorVar(g.type) }}
                >
                  <span className="gate-top">
                    <EntityIcon type={g.type} size={16} />
                    <span className="gate-name">{g.label}</span>
                    <span className="spacer" />
                    {/* 아직 못 받아 온 숫자는 자리만 비운다. "…" 를 찍으면 그게 값처럼 보인다. */}
                    <span className="gate-count">
                      {countOf(g.countLabel)?.toLocaleString() ?? ""}
                    </span>
                  </span>
                  <span className="gate-why">{g.why}</span>
                </Link>
              ))}
            </div>
          </section>

          <section className="section-block">
            <h2>지금 개척 중인 사업영역</h2>
            <p className="meta">
              고객사·요구·거래가 많이 붙은 순서입니다. 카드를 누르면 그 영역의
              관계도와 근거로 들어갑니다.
            </p>
            {data ? (
              <div className="domain-cards">
                {data.domains.map((d) => (
                  <Link
                    key={d.id}
                    to={`/explore/domain/${encodeURIComponent(d.id)}`}
                    className="domain-card"
                  >
                    <span className="domain-card-name">{d.name}</span>
                    {d.industry_scope && (
                      <span className="meta">{d.industry_scope}</span>
                    )}
                    <span className="domain-card-nums">
                      <span>
                        <b>{d.account_count}</b>고객사
                      </span>
                      <span>
                        <b>{d.need_count}</b>요구
                      </span>
                      <span>
                        <b>{d.deal_count}</b>거래
                      </span>
                      <span>
                        <b>{d.evidence_count}</b>근거
                      </span>
                    </span>
                    {d.maturity && (
                      <span
                        className="maturity"
                        title={`성숙도 ${d.maturity}`}
                        aria-label={`성숙도 ${d.maturity}`}
                      >
                        {[1, 2, 3, 4, 5].map((i) => (
                          <i
                            key={i}
                            className={
                              i <= maturityLevel(d.maturity) ? "on" : ""
                            }
                          />
                        ))}
                      </span>
                    )}
                    {d.status && <span className="meta">단계 {d.status}</span>}
                  </Link>
                ))}
              </div>
            ) : (
              <div className="skeleton-rows">
                {[0, 1, 2, 3].map((i) => (
                  <i key={i} />
                ))}
              </div>
            )}
          </section>

          {data?.last_ingest_at && (
            <p className="meta" style={{ marginTop: "var(--sp-6)" }}>
              마지막으로 자료를 읽어들인 시각 {data.last_ingest_at}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
