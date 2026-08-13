// 원문 보기. 브라우저가 로컬 파일을 못 여는 자료의 "원문 열기" 를 여기가 받는다.
//
// 가짜가 아니다. 수집 때 자료를 위치(locator)까지 쪼개 발췌로 저장해 뒀고
// (`data/parsed/<source_id>.jsonl` → Neo4j Evidence), 그것을 위치 순서로 보여주면서
// 찾던 자리를 강조한다. 엑셀이면 시트!셀, PDF 면 쪽 번호, 슬랙이면 메시지 시각이다.
//
// 못 보여주는 것은 원본 서식(표 모양·이미지·색)이다. 그 사실을 화면에 적는다.

import { useEffect, useMemo, useRef, useState } from "react";
import { useWm } from "../app/WmProvider";
import type { SourceDetail } from "../api/browse";
import { readLocator } from "../lib/locator";
import { copyText } from "../lib/open-source";
import { IconClose, IconCopy, IconSearch } from "../shell/icons";

interface Props {
  sourceId: string;
  /** 처음에 강조하고 그 자리로 옮겨 갈 위치 */
  locator?: string;
  onClose: () => void;
}

export function SourceViewer({ sourceId, locator = "", onClose }: Props) {
  const { browse } = useWm();
  const [data, setData] = useState<SourceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [find, setFind] = useState("");
  const hitRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    browse
      .source(sourceId, ctl.signal)
      .then(setData)
      .catch((err) => {
        if (!ctl.signal.aborted) setError((err as Error).message);
      });
    return () => ctl.abort();
  }, [browse, sourceId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 찾던 위치로 옮겨 준다. 발췌가 수백 건이라 손으로 찾게 두면 못 찾는다.
  useEffect(() => {
    if (!data) return;
    const t = window.setTimeout(
      () => hitRef.current?.scrollIntoView({ block: "center" }),
      60,
    );
    return () => window.clearTimeout(t);
  }, [data]);

  const rows = useMemo(() => {
    const all = data?.all_excerpts ?? [];
    if (!find.trim()) return all;
    const needle = find.trim().toLowerCase();
    return all.filter(
      (r) =>
        r.text.toLowerCase().includes(needle) ||
        r.locator.toLowerCase().includes(needle),
    );
  }, [data, find]);

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside
        className="drawer"
        data-wide="true"
        role="dialog"
        aria-modal="true"
        aria-label="원문 보기"
      >
        <div className="drawer-head">
          <div style={{ minWidth: 0, flex: 1 }}>
            <div className="drawer-kind">원문 보기</div>
            <div className="drawer-title">{data?.title ?? sourceId}</div>
            {data && (
              <div className="meta-row" style={{ marginTop: 4 }}>
                <span>{data.source_type_ko}</span>
                <span>발췌 {data.preview_total ?? rows.length}건</span>
                {data.ingested_at && <span>{data.ingested_at} 수집</span>}
              </div>
            )}
          </div>
          <button
            type="button"
            className="drawer-close"
            onClick={onClose}
            aria-label="원문 보기 닫기 (Esc)"
            title="닫기 (Esc · 바깥 클릭도 됩니다)"
          >
            <IconClose size={16} />
          </button>
        </div>

        <div className="drawer-body">
          {error ? (
            <div
              className="notice"
              data-tone="error"
              style={{ margin: 0, padding: 0 }}
            >
              <div className="notice-title">원문을 읽지 못했습니다</div>
              <div className="notice-body">{error}</div>
            </div>
          ) : !data ? (
            <div className="skeleton-rows" style={{ padding: 0 }}>
              {[0, 1, 2, 3, 4].map((i) => (
                <i key={i} />
              ))}
            </div>
          ) : (
            <>
              <div className="viewer-head">
                <label className="search">
                  <IconSearch />
                  <span className="sr-only">원문 안에서 찾기</span>
                  <input
                    value={find}
                    placeholder="이 자료 안에서 찾기"
                    onChange={(e) => setFind(e.target.value)}
                  />
                </label>
                {locator && (
                  <span className="meta">
                    강조된 자리 {readLocator(locator).human}
                  </span>
                )}
                <span className="spacer" />
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  data-size="sm"
                  onClick={() => copyText(data.open.path)}
                >
                  <IconCopy size={13} />
                  경로 복사
                </button>
              </div>

              <p className="meta" style={{ marginBottom: "var(--sp-3)" }}>
                WM 이 수집할 때 쪼개 둔 발췌를 위치 순서로 보여 줍니다. 원본의
                서식(표 모양·이미지·색)은 여기에 나오지 않습니다.
              </p>

              {rows.length === 0 ? (
                <div className="notice" style={{ margin: 0, padding: 0 }}>
                  <div className="notice-title">찾은 자리가 없습니다</div>
                  <div className="notice-body">
                    다른 말로 찾아 보거나 검색어를 지워 전체를 보세요.
                  </div>
                </div>
              ) : (
                <div className="viewer-rows">
                  {rows.map((r, i) => {
                    const hit = !!locator && r.locator === locator;
                    return (
                      <div
                        key={`${r.locator}-${i}`}
                        className="viewer-row"
                        data-hit={hit || undefined}
                        ref={hit ? hitRef : undefined}
                      >
                        <span className="preview-where">
                          {readLocator(r.locator).human}
                        </span>
                        <span className="preview-text">{r.text}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        <div className="drawer-foot">
          <span className="drawer-hint">
            닫기: 오른쪽 위 × · Esc · 바깥 아무 곳
          </span>
        </div>
      </aside>
    </>
  );
}
