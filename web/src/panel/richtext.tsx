// 답변 본문을 읽을 수 있는 글로 그린다.
//
// LLM 합성기는 `**굵게**` 와 줄바꿈을 쓴다. 지금까지 이 문자열을 그대로 찍어서
// 화면에 `**멀티테넌트 구조 개발**이 필요합니다` 처럼 별표가 보였다. 여기서 처리한다.
//
// 마크다운 전체를 지원하지 않는다. 합성기가 실제로 내보내는 것만 다룬다:
//   `**굵게**` · 줄바꿈으로 나뉜 문단 · `[1]` 각주 · `- ` 로 시작하는 항목

import { Fragment } from "react";
import type { ReactNode } from "react";

export interface CiteHandlers {
  activeMarker: number | null;
  onPick: (marker: number) => void;
}

/** `**굵게**` 와 `[1]` 각주를 섞어 처리한다. */
function inline(text: string, h: CiteHandlers, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  // 굵게와 각주를 한 번에 쪼갠다. 순서가 섞여 있어도 원문 순서를 지킨다.
  const parts = text.split(/(\*\*[^*]+\*\*|\[\d+\])/g);

  parts.forEach((part, i) => {
    if (!part) return;
    const key = `${keyBase}-${i}`;

    const bold = /^\*\*([^*]+)\*\*$/.exec(part);
    if (bold) {
      out.push(<strong key={key}>{bold[1]}</strong>);
      return;
    }

    const cite = /^\[(\d+)\]$/.exec(part);
    if (cite) {
      const marker = Number(cite[1]);
      out.push(
        <button
          type="button"
          key={key}
          className="cite"
          /* 여백 각주가 이 값으로 줄 위치를 찾는다. 같은 번호가 본문에 여러 번 나올 수
             있으므로 ref 맵(번호 하나에 요소 하나)이 아니라 속성으로 둔다 —
             맵으로 하면 마지막에 등장한 자리에 붙는다(실측: 각주 6 이 세 번 나와서
             여백 노트가 마지막 자리로 밀렸다). */
          data-marker={marker}
          aria-pressed={h.activeMarker === marker}
          aria-label={`각주 ${marker} 의 근거 보기`}
          title={`각주 ${marker} · 관계도에서 이 근거가 걸린 곳을 밝힙니다`}
          onClick={() => h.onPick(marker)}
        >
          {marker}
        </button>,
      );
      return;
    }

    out.push(<Fragment key={key}>{part}</Fragment>);
  });

  return out;
}

/**
 * 여러 줄 글을 문단으로 나눈다.
 *
 * 합성기는 항목마다 줄을 바꾼다(`**A**가 필요합니다.\n**B**도 필요합니다.`). 그래서
 * 줄 하나를 문단 하나로 본다. 빈 줄은 문단 사이 간격이 되고 따로 요소를 만들지 않는다.
 */
export function RichText({
  text,
  handlers,
  className,
}: {
  text: string;
  handlers: CiteHandlers;
  className?: string;
}) {
  const lines = text
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);

  return (
    <div className={className}>
      {lines.map((line, i) => {
        const bullet = /^[-•*]\s+(.*)$/.exec(line);
        const body = bullet ? bullet[1] : line;
        return (
          <p key={i} data-bullet={bullet ? "true" : undefined}>
            {inline(body, handlers, `p${i}`)}
          </p>
        );
      })}
    </div>
  );
}

/** 한 줄짜리(권고·추정)용. 문단으로 쪼개지 않는다. */
export function RichLine({
  text,
  handlers,
}: {
  text: string;
  handlers: CiteHandlers;
}) {
  return <>{inline(text, handlers, "line")}</>;
}

/** 답변 본문에 실제로 등장한 각주 번호를 원문 순서대로 뽑는다. */
export function markersInOrder(...texts: (string | undefined)[]): number[] {
  const seen: number[] = [];
  for (const text of texts) {
    if (!text) continue;
    for (const m of text.matchAll(/\[(\d+)\]/g)) {
      const n = Number(m[1]);
      if (!seen.includes(n)) seen.push(n);
    }
  }
  return seen;
}
