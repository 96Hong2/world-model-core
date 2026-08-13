// 종류별 대상 목록. 검색 + 사업영역 필터 + 무한 더보기.
//
// 항목마다 상자를 두르지 않고 구분선 한 줄로 잇는다. 406개 고객사를 카드로 그리면
// 화면이 카드밭이 되고 무엇이 중요한지 사라진다.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { EntityList, EntityRow } from "../../api/browse";
import { EntityIcon } from "../../graph/icons";
import { TYPE_KO, typeColorVar } from "../../graph/adapter";
import type { EntityType } from "../../graph/adapter";
import { IconSearch } from "../../shell/icons";

const TITLE: Record<string, { label: string; type: EntityType; lede: string }> =
  {
    domain: {
      label: "사업영역",
      type: "BusinessDomain",
      lede: "회사가 개척하려는 영역과 그 단계",
    },
    account: {
      label: "고객사",
      type: "Account",
      lede: "고객사·파트너와 그 요구",
    },
    need: {
      label: "요구",
      type: "Need",
      lede: "고객이 필요로 한다고 기록된 것",
    },
    capability: {
      label: "역량",
      type: "Capability",
      lede: "요구에 대응하는 우리 역량",
    },
    feature: {
      label: "기능",
      type: "Feature",
      lede: "제품이 실제로 가진 기능",
    },
    product: { label: "제품", type: "Product", lede: "제품 단위" },
    industry: { label: "산업", type: "Industry", lede: "겨냥하는 산업" },
    deal: { label: "거래", type: "Deal", lede: "실제 거래와 그 단계" },
    competitor: {
      label: "경쟁사",
      type: "Competitor",
      lede: "자료에 등장한 경쟁 상대",
    },
  };

const PAGE = 50;

export function EntityListPage() {
  const { type = "account" } = useParams();
  const [params, setParams] = useSearchParams();
  const { browse } = useWm();

  const meta = TITLE[type] ?? TITLE.account;
  const q = params.get("q") ?? "";
  const domain = params.get("domain") ?? "";

  const [data, setData] = useState<EntityList | null>(null);
  const [items, setItems] = useState<EntityRow[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(q);

  useEffect(() => setDraft(q), [q]);

  // 검색·필터가 바뀌면 처음부터 다시 받는다.
  useEffect(() => {
    setOffset(0);
    setItems([]);
  }, [type, q, domain]);

  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    browse
      .entities({ type, q, domain, limit: PAGE, offset }, ctl.signal)
      .then((res) => {
        setData(res);
        setItems((prev) =>
          offset === 0 ? res.items : [...prev, ...res.items],
        );
        setError(null);
      })
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      })
      .finally(() => {
        if (!ctl.signal.aborted) setLoading(false);
      });
    return () => ctl.abort();
  }, [browse, type, q, domain, offset]);

  const setParam = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params);
      if (value) next.set(key, value);
      else next.delete(key);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const hasMore = useMemo(
    () => !!data && items.length < data.total,
    [data, items.length],
  );

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>{meta.label}</h1>
        <span className="lede">{meta.lede}</span>
        <span className="spacer" />
        {data && (
          <span className="meta">
            {items.length.toLocaleString()} / {data.total.toLocaleString()}개
          </span>
        )}
      </div>

      <div className="toolbar">
        <label className="search">
          <IconSearch />
          <span className="sr-only">{meta.label} 이름으로 찾기</span>
          <input
            value={draft}
            placeholder={`${meta.label} 이름으로 찾기`}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setParam("q", draft.trim())}
            onBlur={() => setParam("q", draft.trim())}
          />
        </label>

        {data && data.facets.domains.length > 0 && (
          <label className="row">
            <span className="meta">사업영역</span>
            <select
              className="select"
              value={domain}
              onChange={(e) => setParam("domain", e.target.value)}
            >
              <option value="">전체</option>
              {data.facets.domains.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
        )}

        <span className="spacer" />
        <div className="filters">
          {Object.entries(TITLE).map(([slug, t]) => (
            <Link
              key={slug}
              to={`/explore/${slug}`}
              className="filter"
              aria-pressed={slug === type}
            >
              {t.label}
            </Link>
          ))}
        </div>
      </div>

      <div className="browse-page scroll-y">
        {error ? (
          <div className="notice" data-tone="error">
            <div className="notice-title">목록을 받지 못했습니다</div>
            <div className="notice-body">{error}</div>
          </div>
        ) : items.length === 0 && !loading ? (
          <div className="notice">
            <div className="notice-title">찾은 것이 없습니다</div>
            <div className="notice-body">
              {q
                ? `"${q}" 로는 ${meta.label}을 못 찾았습니다. 다른 말로 찾아 보거나 검색어를 지워 전체를 보세요.`
                : `이 조건에 맞는 ${meta.label}이 지식 월드에 없습니다.`}
            </div>
            {(q || domain) && (
              <button
                type="button"
                className="btn"
                data-variant="outline"
                onClick={() => setParams({}, { replace: true })}
              >
                조건 지우고 전체 보기
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="rows">
              {items.map((row) => (
                <Link
                  key={row.id}
                  to={`/explore/${type}/${encodeURIComponent(row.id)}`}
                  className="rowitem"
                  style={{
                    ["--type-color" as string]: typeColorVar(
                      row.type as EntityType,
                    ),
                  }}
                >
                  <span className="rowitem-top">
                    <span className="rowitem-type">
                      <i />
                      <EntityIcon type={row.type as EntityType} size={12} />
                      {TYPE_KO[row.type as EntityType] ?? row.type}
                    </span>
                    <span className="rowitem-name">{row.name}</span>
                    {row.status && <span className="meta">{row.status}</span>}
                  </span>
                  {row.subtitle && (
                    <span className="rowitem-sub">{row.subtitle}</span>
                  )}
                  <span className="meta-row" style={{ marginTop: 4 }}>
                    <span>근거 {row.evidence_count}건</span>
                    <span>관계 {row.degree}개</span>
                    {row.domains.length > 0 && (
                      <span>{row.domains.join(" · ")}</span>
                    )}
                  </span>
                </Link>
              ))}
            </div>

            {loading && (
              <div className="skeleton-rows">
                {[0, 1, 2].map((i) => (
                  <i key={i} />
                ))}
              </div>
            )}

            {hasMore && !loading && (
              <div style={{ padding: "var(--sp-4)" }}>
                <button
                  type="button"
                  className="btn"
                  data-variant="outline"
                  onClick={() => setOffset(items.length)}
                >
                  {Math.min(PAGE, (data?.total ?? 0) - items.length)}개 더 보기
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
