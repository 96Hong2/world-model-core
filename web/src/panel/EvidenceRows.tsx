// 둘러보기·서재·변경 화면이 함께 쓰는 근거 목록.
//
// Ask 의 여백 각주와 달리 여기는 각주 번호가 없다. 대신 자료 이름 · 위치 · 발췌를 보이고
// 누르면 원문으로 가는 길을 펼친다.

import { useState } from "react";
import type { EvidenceRow } from "../api/browse";
import { readLocator } from "../lib/locator";
import { OriginBlock } from "./OriginBlock";
import { sourceTypeKo } from "../lib/labels";
import { IconChevron } from "../shell/icons";

interface Props {
  rows: EvidenceRow[];
  onOpenViewer: (sourceId: string, locator: string) => void;
  /** 목록이 비었을 때 적을 말. "없음" 으로 끝내지 않는다. */
  emptyBody?: string;
}

export function EvidenceRows({ rows, onOpenViewer, emptyBody }: Props) {
  const [open, setOpen] = useState<string | null>(null);

  if (rows.length === 0) {
    return (
      <p className="meta">
        {emptyBody ??
          "이 대상을 뒷받침하는 발췌를 찾지 못했습니다. 자료에 이름은 나왔지만 근거가 안 붙은 상태입니다."}
      </p>
    );
  }

  return (
    <div className="ev-list">
      {rows.map((ev) => {
        const isOpen = open === ev.evidence_id;
        return (
          <div key={ev.evidence_id}>
            <button
              type="button"
              className="ev-row"
              data-active={isOpen}
              aria-expanded={isOpen}
              onClick={() => setOpen(isOpen ? null : ev.evidence_id)}
            >
              <span className="ev-row-top">
                <IconChevron size={12} />
                <span className="ev-source">{ev.source_title}</span>
                <span className="meta">{sourceTypeKo(ev.source_type)}</span>
              </span>
              <span className="ev-snippet">{ev.snippet}</span>
              <span className="ev-where">
                <code>{readLocator(ev.locator).human}</code>
                {ev.observed_at && (
                  <span className="meta">{ev.observed_at}</span>
                )}
              </span>
            </button>
            {isOpen && (
              <div style={{ padding: "0 var(--sp-2) var(--sp-3)" }}>
                <OriginBlock
                  sourceId={ev.source_id}
                  locator={ev.locator}
                  sourceType={ev.source_type}
                  onOpenViewer={onOpenViewer}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
