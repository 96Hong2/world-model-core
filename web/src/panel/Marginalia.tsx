// 여백 각주. 이 화면의 시그니처다.
//
// 답변을 읽는 중에 "이 문장이 어디서 왔는지" 를 **눈을 옮기지 않고** 보게 한다.
// 그래서 각주가 달린 줄 옆에 그 근거의 자료 이름과 위치를 붙인다.
//
// 왜 이걸 만들었나: 개선 전 화면은 각주를 누르면 근거 카드로 스크롤이 옮겨 가서
// 읽던 문장이 화면에서 사라졌다(실측: 답변 본문 점유 48% → 0%). 근거를 본문 옆에 두면
// 그 문제가 구조적으로 없어진다.
//
// 자리 잡는 방법: 본문의 각주 버튼 위치를 재서 같은 높이에 붙이고, 서로 겹치면 아래로 밀어낸다.
// 글꼴이 늦게 로드되거나 창 크기가 바뀌면 다시 잰다.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { Evidence } from "../types/answer";
import { sourceTitle } from "../lib/sources";
import { readLocator } from "../lib/locator";
import { planOrigin } from "../lib/open-source";
import { IconExternal } from "../shell/icons";

/** 각주 하나가 여백에 적을 내용 */
export interface MarginNote {
  marker: number;
  evidenceId: string;
  sourceLabel: string;
  where: string;
  snippet: string;
  actionLabel: string;
}

export function buildNotes(
  markers: number[],
  citations: { marker: number; evidence_id: string }[],
  evidence: Evidence[],
): MarginNote[] {
  const byId = new Map(evidence.map((e) => [e.evidence_id, e]));
  const byMarker = new Map(citations.map((c) => [c.marker, c.evidence_id]));
  const notes: MarginNote[] = [];

  for (const marker of markers) {
    const id = byMarker.get(marker);
    if (!id) continue;
    const ev = byId.get(id);
    if (!ev) continue;
    notes.push({
      marker,
      evidenceId: id,
      sourceLabel: sourceTitle(ev.source_id),
      where: ev.locator ? readLocator(ev.locator).human : "",
      snippet: ev.snippet ?? "",
      actionLabel: marginLabel(ev.source_id, ev.locator),
    });
  }
  return notes;
}

/** 여백 각주 버튼에 적을 말. 원본을 여는 갈래에 따라 갈린다. */
function marginLabel(sourceId: string, locator: string): string {
  const plan = planOrigin(sourceId, locator);
  if (plan.how === "slack") return plan.openLabel;
  if (plan.canPreview) return "이 자리 펼쳐 보기";
  if (plan.how === "file") return plan.openLabel;
  return "근거 자세히 보기";
}

/** 각주 사이 최소 간격. 이보다 가까우면 아래로 밀어낸다. */
const MIN_GAP = 8;

interface Props {
  notes: MarginNote[];
  /** 위치를 다시 재야 하는 시점을 알리는 값(답변 내용·창 크기 등) */
  layoutKey: string;
  activeMarker: number | null;
  onPick: (marker: number) => void;
  onOpen: (evidenceId: string) => void;
}

export function Marginalia({
  notes,
  layoutKey,
  activeMarker,
  onPick,
  onOpen,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const itemRefs = useRef(new Map<number, HTMLButtonElement>());
  const [tops, setTops] = useState<Record<number, number>>({});

  const place = useCallback(() => {
    const host = hostRef.current;
    const body = host?.parentElement;
    if (!host || !body) return;
    const hostTop = host.getBoundingClientRect().top;

    // 각주 하나가 본문에 여러 번 나올 수 있다. 여백 노트는 **처음 나온 자리**에 붙인다 —
    // 그게 읽는 사람이 그 근거를 처음 만나는 지점이다.
    const wanted: { marker: number; top: number; height: number }[] = [];
    for (const note of notes) {
      const hits = body.querySelectorAll<HTMLElement>(
        `.cite[data-marker="${note.marker}"]`,
      );
      if (hits.length === 0) continue;
      let top = Infinity;
      for (const hit of hits) {
        top = Math.min(top, hit.getBoundingClientRect().top - hostTop);
      }
      if (!Number.isFinite(top)) continue;
      wanted.push({
        marker: note.marker,
        top,
        height: itemRefs.current.get(note.marker)?.offsetHeight ?? 34,
      });
    }
    wanted.sort((a, b) => a.top - b.top);

    const next: Record<number, number> = {};
    let floor = 0;
    for (const w of wanted) {
      const top = Math.max(w.top, floor);
      next[w.marker] = Math.round(top);
      floor = top + w.height + MIN_GAP;
    }
    setTops(next);
  }, [notes]);

  // 내용이 바뀌면 바로 다시 잰다(그리기 전에 자리를 잡아야 깜빡이지 않는다).
  useLayoutEffect(place, [place, layoutKey]);

  // 고른 각주가 펼쳐지면 높이가 달라진다. 그때도 다시 잰다.
  useLayoutEffect(place, [place, activeMarker]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    // 창 크기·글꼴 로드·본문 줄바꿈 변화까지 한 번에 잡는다.
    // 본문 열(형제)을 함께 본다 — 실제로 줄바꿈이 달라지는 것은 그쪽이고, 여백 열은
    // 높이가 1px 로 고정돼 있어 혼자 보면 아무 변화도 감지되지 않는다.
    const observer = new ResizeObserver(() => place());
    observer.observe(host);
    const parent = host.parentElement;
    if (parent) observer.observe(parent);
    const col = parent?.querySelector(".brief-col");
    if (col) observer.observe(col);

    // 글꼴 하위 집합(subset)은 나중에 더 내려올 수 있어서 loadingdone 도 듣는다.
    const onFonts = () => place();
    document.fonts?.addEventListener?.("loadingdone", onFonts);
    document.fonts?.ready.then(onFonts).catch(() => {});

    return () => {
      observer.disconnect();
      document.fonts?.removeEventListener?.("loadingdone", onFonts);
    };
  }, [place]);

  return (
    <div className="brief-margin" ref={hostRef} aria-label="근거 여백">
      {notes.map((note) => {
        const top = tops[note.marker];
        return (
          <button
            type="button"
            key={note.marker}
            className="mnote"
            data-active={activeMarker === note.marker}
            ref={(el) => {
              if (el) itemRefs.current.set(note.marker, el);
              else itemRefs.current.delete(note.marker);
            }}
            style={{
              top: top ?? 0,
              // 아직 자리를 못 잰 각주는 그리지 않는다(0 에 뭉쳐 보이는 것을 막는다).
              opacity: top == null ? 0 : 1,
            }}
            aria-label={`각주 ${note.marker} · ${note.sourceLabel} · 눌러서 근거 자세히 보기`}
            onClick={() => {
              if (activeMarker === note.marker) onOpen(note.evidenceId);
              else onPick(note.marker);
            }}
          >
            <span className="mnote-top">
              <span className="mnote-marker">{note.marker}</span>
              <span className="mnote-source">{note.sourceLabel}</span>
            </span>
            {note.where && <span className="mnote-where">{note.where}</span>}
            {note.snippet && (
              <span className="mnote-snippet">{note.snippet}</span>
            )}
            <span className="mnote-open">
              <IconExternal size={12} />
              {note.actionLabel}
            </span>
          </button>
        );
      })}
    </div>
  );
}
