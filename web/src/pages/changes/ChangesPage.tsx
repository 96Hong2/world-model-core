// 변경. 자료가 언제 들어와서 지식이 어떻게 늘었는지 본다.
//
// **무엇에 쓰는 화면인지 먼저 적는다.** 예전에는 `ingest` 라는 내부 이름 아래 `+ 노드 30,174`
// 같은 숫자만 늘어놓았다. 숫자는 맞았지만 읽는 사람은 그게 많은 것인지, 자기가 무엇을 해야
// 하는지 알 수 없었다. 답이 이상할 때 "언제 들어온 자료 때문인가"를 되짚는 자리다.
//
// 세 단으로 읽힌다.
//   ① 지금 무엇이 들어 있나        누적 총계
//   ② 언제 무엇이 들어왔나         실행별로 한 문장씩. 세부 숫자는 접어 둔다
//   ③ 이 화면이 답하지 못하는 것    값이 바뀐 내역과 삭제는 기록이 없다
//
// 지금 데이터로 말할 수 있는 것과 말할 수 없는 것을 가른다. 실행 기록(pipeline_run_id)과
// 처음 들어온 시각(created_at)은 있고, **속성이 A→B 로 바뀐 내역과 삭제는 없다.**
// 없는 것을 있는 척 그리지 않는다.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWm } from "../../app/WmProvider";
import type { RunSummary } from "../../api/browse";
import { Fold } from "../../panel/Fold";

/**
 * "2026-08-07 21:01" 처럼. `T` 와 초·`Z` 만 걷어낸다.
 *
 * **시간대를 옮기지 않는다.** 적재가 적어 준 시각에 `Z` 가 붙어 있지만 그것이 정말 UTC 인지
 * 로컬 시각에 `Z` 를 붙인 것인지 알 수 없다. 확실하지 않은 값에 9시간을 더하면 틀린 시각을
 * 자신 있게 보여 주는 셈이 된다. 적힌 값을 그대로 읽는다.
 */
function when(at?: string | null): string {
  if (!at) return "시각 기록 없음";
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(at);
  return m ? `${m[1]} ${m[2]}` : at;
}

/** 걸린 시간. "4시간 46분" 처럼 사람 단위로. */
function took(from?: string | null, to?: string | null): string {
  if (!from || !to) return "";
  const ms = Date.parse(to) - Date.parse(from);
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins}분 걸렸습니다`;
  const days = Math.floor(mins / 1440);
  const hours = Math.floor((mins % 1440) / 60);
  const rest = mins % 60;
  if (days > 0) return `${days}일 ${hours}시간 걸렸습니다`;
  return `${hours}시간 ${rest}분 걸렸습니다`;
}

/**
 * 이 실행이 무슨 일을 했는지 한 문장.
 *
 * 새 자료를 읽은 실행과, 이미 읽은 자료에서 지식을 더 뽑아낸 실행은 다른 일이다.
 * `source_count` 가 그 둘을 가른다.
 */
function whatItDid(r: RunSummary): string {
  const nodes = r.node_count.toLocaleString();
  const edges = r.edge_count.toLocaleString();
  if (r.source_count > 0) {
    return `자료 ${r.source_count.toLocaleString()}종을 읽어 지식 ${nodes}개와 그 사이 관계 ${edges}개를 만들었습니다.`;
  }
  return `이미 읽은 자료에서 지식 ${nodes}개를 더 뽑아냈습니다(새 관계 ${edges}개).`;
}

export function ChangesPage() {
  const { browse } = useWm();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    browse
      .runs(ctl.signal)
      .then((res) => setRuns(res.runs))
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse]);

  // 누적 총계. 실행을 다 더한 값이라 "지금 들어 있는 것" 과 같다.
  const totals = runs
    ? runs.reduce(
        (a, r) => ({
          nodes: a.nodes + r.node_count,
          edges: a.edges + r.edge_count,
          sources: a.sources + r.source_count,
        }),
        { nodes: 0, edges: 0, sources: 0 },
      )
    : null;

  // 오래된 것이 위에 오게 한다. "언제부터 어떻게 늘었나" 는 시간 순서로 읽힌다.
  const ordered = runs
    ? [...runs].sort((a, b) =>
        String(a.started_at ?? "").localeCompare(String(b.started_at ?? "")),
      )
    : [];

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>변경</h1>
        <span className="lede">자료가 언제 들어와서 지식이 어떻게 늘었나</span>
        <span className="spacer" />
        {runs && <span className="meta">수집 실행 {runs.length}회</span>}
      </div>

      <div className="browse-page scroll-y">
        <div className="browse-inner" data-narrow="true">
          {error ? (
            <div className="notice" data-tone="error">
              <div className="notice-title">수집 기록을 받지 못했습니다</div>
              <div className="notice-body">{error}</div>
            </div>
          ) : !runs ? (
            <div className="skeleton-rows" style={{ padding: 0 }}>
              {[0, 1].map((i) => (
                <i key={i} />
              ))}
            </div>
          ) : runs.length === 0 ? (
            <div className="notice">
              <div className="notice-title">수집 기록이 없습니다</div>
              <div className="notice-body">
                아직 자료를 읽어들인 적이 없습니다. 자료를 넣으면 그 결과가
                여기에 쌓입니다.
              </div>
            </div>
          ) : (
            <>
              {/* 무엇에 쓰는 화면인가. 숫자보다 이게 먼저다. */}
              <p className="page-why">
                답이 이상할 때 <b>언제 들어온 자료 때문인지</b> 되짚는
                자리입니다. 실행 하나를 누르면 어느 자료가 무엇을 만들었는지
                자료별로 볼 수 있습니다.
              </p>

              {/* ① 지금 무엇이 들어 있나 */}
              {totals && (
                <section className="now">
                  <h2 className="now-title">지금 지식 월드에 들어 있는 것</h2>
                  <div className="now-nums">
                    <span>
                      <b>{totals.sources.toLocaleString()}</b>종의 자료를 읽어
                    </span>
                    <span>
                      <b>{totals.nodes.toLocaleString()}</b>개의 지식과
                    </span>
                    <span>
                      <b>{totals.edges.toLocaleString()}</b>개의 관계를
                      만들었습니다
                    </span>
                  </div>
                </section>
              )}

              {/* ② 언제 무엇이 들어왔나 */}
              <h2 className="now-title" style={{ marginTop: "var(--sp-6)" }}>
                언제 무엇이 들어왔나
              </h2>

              <ol className="runline">
                {ordered.map((r, i) => (
                  <li className="runstep" key={r.run_id}>
                    <span className="runstep-no" aria-hidden="true">
                      {i + 1}
                    </span>
                    <div className="runstep-body">
                      <p className="runstep-when">
                        {when(r.started_at)}
                        {r.ended_at && r.ended_at !== r.started_at && (
                          <span className="meta">
                            {" "}
                            · {took(r.started_at, r.ended_at)}
                          </span>
                        )}
                      </p>
                      <p className="runstep-what">{whatItDid(r)}</p>

                      {r.counts.length > 0 && (
                        <Fold
                          title="무엇이 몇 개 생겼나"
                          count={r.counts.length}
                        >
                          <div className="deltas">
                            {r.counts.map((c) => (
                              <span
                                className="delta"
                                data-kind="added"
                                key={c.label}
                              >
                                <span className="delta-sign">+</span>
                                {c.label_ko} <b>{c.count.toLocaleString()}</b>
                              </span>
                            ))}
                          </div>
                        </Fold>
                      )}

                      <div className="row">
                        <Link
                          to={`/changes/${encodeURIComponent(r.run_id)}`}
                          className="btn"
                          data-variant="outline"
                          data-size="sm"
                        >
                          어느 자료가 무엇을 만들었나
                        </Link>
                        <span className="meta run-id">{r.run_id}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ol>

              {/* ③ 못하는 것 */}
              <div className="limits">
                <h3>이 화면이 답하지 못하는 것</h3>
                <ul>
                  <li>
                    <b>값이 어떻게 바뀌었는지는 없습니다.</b> 어떤 값이 A 에서 B
                    로 변했는지 기록해 두지 않아서, 지금은 각 항목의 마지막
                    상태와 처음 들어온 시각만 알 수 있습니다.
                  </li>
                  <li>
                    <b>지워진 것은 남지 않습니다.</b> 이 화면은 늘어난 것만 보여
                    줍니다. 자료에서 사라진 내용이 지식에서도 사라졌는지는 알 수
                    없습니다.
                  </li>
                </ul>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
