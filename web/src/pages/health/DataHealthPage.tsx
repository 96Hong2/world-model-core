// 데이터 상태. 관리자 KPI 대시보드로 만들지 않는다.
//
// 목적은 하나다: **어디가 약한지 보고 그 자리로 바로 가는 것.** 그래서 숫자만 세지 않고
// 항목마다 해당 대상·자료로 가는 링크를 붙인다.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { HealthReport } from "../../api/browse";
import { Fold } from "../../panel/Fold";

export function DataHealthPage() {
  const { browse } = useWm();
  const [data, setData] = useState<HealthReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    browse
      .health(ctl.signal)
      .then(setData)
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse]);

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>데이터 상태</h1>
        <span className="lede">지식 월드에서 약한 곳</span>
        <span className="spacer" />
        {data && (
          <span className="meta">{data.checked_at.slice(0, 19)} 기준</span>
        )}
      </div>

      <div className="browse-page scroll-y">
        <div className="browse-inner" data-narrow="true">
          {error ? (
            <div className="notice" data-tone="error">
              <div className="notice-title">상태를 읽지 못했습니다</div>
              <div className="notice-body">{error}</div>
            </div>
          ) : !data ? (
            <div className="skeleton-rows" style={{ padding: 0 }}>
              {[0, 1, 2].map((i) => (
                <i key={i} />
              ))}
            </div>
          ) : (
            <>
              <p className="meta">
                여기 있는 것은 고장이 아니라 <b>아직 근거가 얇은 자리</b>입니다.
                답변의 근거 세기가 낮게 나오는 이유이기도 합니다. 항목을 누르면
                그 대상으로 갑니다.
              </p>

              {data.groups.map((g) => (
                <section className="health-group" key={g.key}>
                  <div className="health-group-head">
                    <h2>{g.title}</h2>
                    <span className="health-count">{g.count}건</span>
                  </div>
                  <p className="health-why">{g.why}</p>

                  {g.items.length === 0 ? (
                    <p className="meta" style={{ marginTop: "var(--sp-3)" }}>
                      해당하는 것이 없습니다. 이 항목은 지금 깨끗합니다.
                    </p>
                  ) : (
                    <div className="rows" style={{ marginTop: "var(--sp-3)" }}>
                      {g.items.slice(0, 12).map((it, i) =>
                        it.target_kind === "entity" && it.target_id ? (
                          <Link
                            key={`${it.target_id}-${i}`}
                            to={`/explore/${it.target_type ?? "account"}/${encodeURIComponent(it.target_id)}`}
                            className="rowitem"
                          >
                            <span className="rowitem-top">
                              <span className="rowitem-name">{it.label}</span>
                              <span className="rowitem-type">{it.detail}</span>
                            </span>
                          </Link>
                        ) : (
                          <div className="rowitem" key={`${it.label}-${i}`}>
                            <span className="rowitem-top">
                              <span className="rowitem-name">{it.label}</span>
                              <span className="rowitem-type">{it.detail}</span>
                            </span>
                          </div>
                        ),
                      )}
                    </div>
                  )}

                  {g.items.length > 12 && (
                    <Fold title="나머지 보기" count={g.items.length - 12}>
                      <div className="rows">
                        {g.items.slice(12).map((it, i) =>
                          it.target_kind === "entity" && it.target_id ? (
                            <Link
                              key={`${it.target_id}-${i}`}
                              to={`/explore/${it.target_type ?? "account"}/${encodeURIComponent(it.target_id)}`}
                              className="rowitem"
                            >
                              <span className="rowitem-top">
                                <span className="rowitem-name">{it.label}</span>
                                <span className="rowitem-type">
                                  {it.detail}
                                </span>
                              </span>
                            </Link>
                          ) : (
                            <div className="rowitem" key={`${it.label}-${i}`}>
                              <span className="rowitem-top">
                                <span className="rowitem-name">{it.label}</span>
                                <span className="rowitem-type">
                                  {it.detail}
                                </span>
                              </span>
                            </div>
                          ),
                        )}
                      </div>
                    </Fold>
                  )}

                  {g.count > g.items.length && (
                    <p className="meta" style={{ marginTop: "var(--sp-2)" }}>
                      전체 {g.count}건 중 {g.items.length}건만 가져왔습니다.
                    </p>
                  )}
                </section>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
