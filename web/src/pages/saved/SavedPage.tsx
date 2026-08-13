// 저장함. 두 묶음이다.
//
//   최근 질문   물어본 것이 자동으로 남는다. 7일이 지나면 사라진다
//   저장한 것   직접 담은 질문·대상·자료. 지울 때까지 남는다
//
// 섞지 않는다. 자동으로 쌓이는 것과 일부러 담은 것을 한 목록에 두면, 담아 둔 것이 최근
// 질문에 밀려 내려간다.
//
// 서버에 저장 기능이 없으므로 이 브라우저에만 남는다. **그 사실을 화면에 적는다** —
// 팀과 공유되는 것처럼 보이면 나중에 사라졌을 때 신뢰를 잃는다.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IconAsk, IconClose, IconSaved } from "../../shell/icons";
import type { RecentQuestion, SavedItem, SavedKind } from "../../lib/saved";
import {
  RECENT_DAYS,
  clearRecent,
  forgetQuestion,
  questionTo,
  readRecent,
  readSaved,
  save,
  unsave,
} from "../../lib/saved";

const KIND_KO: Record<SavedKind, string> = {
  question: "질문",
  entity: "대상",
  source: "자료",
};

/** "3일 전"처럼. 날짜만 적으면 7일 규칙이 얼마나 남았는지 알 수 없다. */
function ago(at: string): string {
  const t = Date.parse(at);
  if (Number.isNaN(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60_000);
  if (mins < 1) return "방금";
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

export function SavedPage() {
  const [recent, setRecent] = useState<RecentQuestion[]>([]);
  const [items, setItems] = useState<SavedItem[]>([]);

  useEffect(() => {
    setRecent(readRecent());
    setItems(readSaved());
  }, []);

  const keep = useCallback((q: string) => {
    setItems(save({ kind: "question", label: q, to: questionTo(q) }));
  }, []);

  const drop = useCallback((to: string) => setItems(unsave(to)), []);

  const savedTo = new Set(items.map((i) => i.to));
  const empty = recent.length === 0 && items.length === 0;

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>저장함</h1>
        <span className="lede">내가 물어본 것과 담아 둔 것</span>
        <span className="spacer" />
        <span className="meta">이 브라우저에만 저장됩니다</span>
      </div>

      <div className="browse-page scroll-y">
        <div className="browse-inner" data-narrow="true">
          {empty ? (
            <div className="notice">
              <div className="notice-title">아직 아무것도 없습니다</div>
              <div className="notice-body">
                질문을 하면 최근 질문에 자동으로 남습니다. 오래 두고 볼 것은
                답변·대상·자료 화면의 [저장] 을 눌러 담아 두세요.
              </div>
              <div className="notice-hint">
                이 목록은 지금 쓰는 브라우저에만 남고 팀과 공유되지 않습니다.
              </div>
              <div className="row">
                <Link to="/ask" className="btn" data-variant="primary">
                  <IconAsk size={14} />
                  질문하러 가기
                </Link>
                <Link to="/explore" className="btn" data-variant="outline">
                  둘러보기
                </Link>
              </div>
            </div>
          ) : (
            <>
              {/* ① 자동으로 쌓이는 것 */}
              <section className="bin">
                <div className="bin-head">
                  <h2>최근 질문</h2>
                  <span className="meta">
                    {RECENT_DAYS}일 안에 물어본 것 {recent.length}건 · 지나면
                    저절로 사라집니다
                  </span>
                  <span className="spacer" />
                  {recent.length > 0 && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="quiet"
                      data-size="sm"
                      onClick={() => {
                        clearRecent();
                        setRecent([]);
                      }}
                    >
                      모두 지우기
                    </button>
                  )}
                </div>

                {recent.length === 0 ? (
                  <p className="bin-empty">
                    최근 {RECENT_DAYS}일 안에 물어본 질문이 없습니다.
                  </p>
                ) : (
                  <div className="rows">
                    {recent.map((r) => {
                      const to = questionTo(r.question);
                      const kept = savedTo.has(to);
                      return (
                        <div className="rowitem" key={r.question}>
                          <span className="rowitem-top">
                            <Link to={to} className="rowitem-name">
                              {r.question}
                            </Link>
                            <button
                              type="button"
                              className="btn save-btn"
                              data-variant={kept ? "quiet" : "outline"}
                              data-size="sm"
                              data-on={kept}
                              aria-pressed={kept}
                              title={
                                kept
                                  ? "이미 저장했습니다"
                                  : "오래 두고 보려면 저장함에 담습니다"
                              }
                              onClick={() =>
                                kept ? drop(to) : keep(r.question)
                              }
                            >
                              <IconSaved size={13} />
                              {kept ? "저장됨" : "저장"}
                            </button>
                            <button
                              type="button"
                              className="btn"
                              data-variant="quiet"
                              data-size="sm"
                              data-icon-only
                              aria-label={`${r.question} 최근 목록에서 지우기`}
                              onClick={() =>
                                setRecent(forgetQuestion(r.question))
                              }
                            >
                              <IconClose size={13} />
                            </button>
                          </span>
                          <span className="meta">{ago(r.at)}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>

              {/* ② 일부러 담은 것 */}
              <section className="bin">
                <div className="bin-head">
                  <h2>저장한 것</h2>
                  <span className="meta">
                    직접 담은 {items.length}건 · 지울 때까지 남습니다
                  </span>
                </div>

                {items.length === 0 ? (
                  <p className="bin-empty">
                    담아 둔 것이 없습니다. 위 최근 질문의 [저장] 이나
                    답변·대상·자료 화면의 저장 버튼으로 담습니다.
                  </p>
                ) : (
                  <div className="rows">
                    {items.map((it) => (
                      <div className="rowitem" key={it.to}>
                        <span className="rowitem-top">
                          <span className="rowitem-type">
                            {KIND_KO[it.kind]}
                          </span>
                          <Link to={it.to} className="rowitem-name">
                            {it.label}
                          </Link>
                          <button
                            type="button"
                            className="btn"
                            data-variant="quiet"
                            data-size="sm"
                            data-icon-only
                            aria-label={`${it.label} 저장함에서 빼기`}
                            onClick={() => drop(it.to)}
                          >
                            <IconClose size={14} />
                          </button>
                        </span>
                        <span className="meta">{ago(it.at)} 담음</span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <p className="meta" style={{ marginTop: "var(--sp-4)" }}>
                이 목록은 지금 쓰는 브라우저에만 남습니다. 다른 사람에게는
                보이지 않습니다.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
