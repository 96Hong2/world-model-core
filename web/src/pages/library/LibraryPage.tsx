// 서재. 원본 자료 270종을 도서관처럼 훑는다.
//
// **왼쪽에 폴더 트리, 오른쪽에 자료 목록**이다. 예전에는 평평한 목록 하나에 칩 필터만
// 있었다. 칩은 "지금 무엇으로 걸렀나"는 보여 주지만 "안에 무엇이 있나"는 보여 주지 못해서,
// 엔지니어링 136종 안에 무슨 종류가 있는지 알려면 하나씩 눌러 봐야 했다.
//
// 자료 카드에 필요한 것만 적는다: 자료명 · 무엇의 정본인가 · 종류 · 관련 사업영역·고객사 ·
// 반영 시각. 발췌 수와 뽑아낸 지식 수는 "이 자료가 얼마나 쓰였나" 의 척도라 함께 둔다.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { SourceList } from "../../api/browse";
import { IconSearch } from "../../shell/icons";
import { SourceTree } from "./SourceTree";

export function LibraryPage() {
  const { browse } = useWm();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const group = params.get("group") ?? "";
  const sourceType = params.get("type") ?? "";
  const domain = params.get("domain") ?? "";
  const account = params.get("account") ?? "";

  const [data, setData] = useState<SourceList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(q);
  const [shown, setShown] = useState(40);
  const [openGroups, setOpenGroups] = useState<string[]>([]);

  useEffect(() => setDraft(q), [q]);
  useEffect(() => setShown(40), [q, group, sourceType, domain, account]);

  useEffect(() => {
    const ctl = new AbortController();
    browse
      .sources(
        { q, group, source_type: sourceType, domain, account, limit: 200 },
        ctl.signal,
      )
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse, q, group, sourceType, domain, account]);

  const setParam = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params);
      if (value) next.set(key, value);
      else next.delete(key);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const items = useMemo(() => data?.items.slice(0, shown) ?? [], [data, shown]);
  const filtered = !!(q || group || sourceType || domain || account);

  // 트리에서 고른 가지. 종류를 고르면 그 종류만, 그룹을 고르면 그룹 전체.
  const pickGroup = useCallback(
    (name: string) => {
      const next = new URLSearchParams(params);
      next.delete("type");
      if (group === name) next.delete("group");
      else next.set("group", name);
      setParams(next, { replace: true });
    },
    [group, params, setParams],
  );

  const pickType = useCallback(
    (name: string) => {
      const next = new URLSearchParams(params);
      next.delete("group");
      if (sourceType === name) next.delete("type");
      else next.set("type", name);
      setParams(next, { replace: true });
    },
    [sourceType, params, setParams],
  );

  const pickAll = useCallback(() => {
    const next = new URLSearchParams(params);
    next.delete("group");
    next.delete("type");
    setParams(next, { replace: true });
  }, [params, setParams]);

  // 고른 가지의 이름. 목록 위에 "지금 어디를 보고 있나"로 적는다.
  const whereLabel = useMemo(() => {
    if (sourceType) {
      const found = data?.facets.source_types.find((t) => t.name === sourceType);
      return found ? found.name_ko : sourceType;
    }
    return group || "전체 자료";
  }, [data, group, sourceType]);

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>서재</h1>
        <span className="lede">지식 월드가 읽은 원본 자료</span>
        <span className="spacer" />
        {data && (
          <span className="meta">
            {items.length} / {data.total}종
          </span>
        )}
      </div>

      <div className="toolbar">
        <label className="search">
          <IconSearch />
          <span className="sr-only">자료 이름이나 설명으로 찾기</span>
          <input
            value={draft}
            placeholder="자료 이름 · 무엇의 정본인가로 찾기"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setParam("q", draft.trim())}
            onBlur={() => setParam("q", draft.trim())}
          />
        </label>

        {data && (
          <>
            <span className="spacer" />

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

            <label className="row">
              <span className="meta">고객사</span>
              <select
                className="select"
                value={account}
                onChange={(e) => setParam("account", e.target.value)}
              >
                <option value="">전체</option>
                {data.facets.accounts.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
      </div>

      <div className="library">
        {/* 왼쪽 폴더 트리. 자료 목록이 아니라 "어디를 볼지" 고르는 자리다. */}
        {data && (
          <aside className="library-tree scroll-y">
            <SourceTree
              total={data.facets.groups.reduce((s, g) => s + g.count, 0)}
              groups={data.facets.groups}
              types={data.facets.source_types}
              group={group}
              sourceType={sourceType}
              openGroups={openGroups}
              onToggleGroup={(name) =>
                setOpenGroups((prev) =>
                  prev.includes(name)
                    ? prev.filter((x) => x !== name)
                    : [...prev, name],
                )
              }
              onPickGroup={pickGroup}
              onPickType={pickType}
              onPickAll={pickAll}
            />
          </aside>
        )}

      <div className="browse-page scroll-y">
        {data && (
          <p className="library-where">
            <b>{whereLabel}</b>
            <span className="meta">
              {data.total}종{q && ` · "${q}" 로 찾음`}
            </span>
          </p>
        )}
        {error ? (
          <div className="notice" data-tone="error">
            <div className="notice-title">자료 목록을 받지 못했습니다</div>
            <div className="notice-body">{error}</div>
          </div>
        ) : !data ? (
          <div className="skeleton-rows">
            {[0, 1, 2, 3, 4].map((i) => (
              <i key={i} />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="notice">
            <div className="notice-title">찾은 자료가 없습니다</div>
            <div className="notice-body">
              {q
                ? `"${q}" 로는 자료를 못 찾았습니다. 자료 이름 대신 무엇을 다루는 자료인지로 찾아 보세요.`
                : "이 조건에 맞는 자료가 없습니다."}
            </div>
            {filtered && (
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
              {items.map((s) => (
                <Link
                  key={s.source_id}
                  to={`/library/${encodeURIComponent(s.source_id)}`}
                  className="rowitem"
                >
                  <span className="rowitem-top">
                    <span className="rowitem-name">{s.title}</span>
                    <span className="rowitem-type">{s.group}</span>
                    {s.sensitivity === "restricted" && (
                      <span className="tag" data-tone="critical">
                        열람 제한
                      </span>
                    )}
                  </span>
                  {s.description && (
                    <span className="rowitem-sub">
                      이 자료가 정본인 것: {s.description}
                    </span>
                  )}
                  <span className="meta-row" style={{ marginTop: 4 }}>
                    <span>{s.source_type_ko}</span>
                    <span>발췌 {s.evidence_count.toLocaleString()}건</span>
                    <span>뽑아낸 지식 {s.entity_count.toLocaleString()}개</span>
                    {s.domains.length > 0 && (
                      <span>{s.domains.join(" · ")}</span>
                    )}
                    {s.ingested_at && (
                      <span>{s.ingested_at.slice(0, 10)} 반영</span>
                    )}
                  </span>
                </Link>
              ))}
            </div>

            {data.items.length > items.length && (
              <div style={{ padding: "var(--sp-4)" }}>
                <button
                  type="button"
                  className="btn"
                  data-variant="outline"
                  onClick={() => setShown((n) => n + 40)}
                >
                  {Math.min(40, data.items.length - items.length)}종 더 보기
                </button>
              </div>
            )}
          </>
        )}
      </div>
      </div>
    </div>
  );
}
