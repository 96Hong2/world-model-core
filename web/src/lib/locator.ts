// Evidence.locator 를 사람이 읽을 수 있는 문장으로 바꾼다.
// 형식 정의는 contracts/ontology.schema.json 의 Evidence.locator 설명이 정본이다:
//   pdf 'p.n' / xlsx '시트!셀' / md·adoc '파일#앵커' / adoc '§문서>헤딩#n' / slack 'channel/ts'
// 여기서는 그 표기를 한국어로 풀어 쓰기만 한다. 원문 문자열은 항상 함께 보여준다.

import { SLACK_CHANNELS } from "./source-registry";

export interface ReadableLocator {
  /** 무슨 종류의 위치인가 (예: "엑셀 시트") */
  kind: string;
  /** 사람이 읽는 위치 (예: "시트 「Pain Point 목록」 · 셀 E2") */
  human: string;
  /** 파서가 적어 준 원문 표기 */
  raw: string;
}

const SHEET_CELL = /^(.+)!(\$?[A-Z]{1,3}\$?\d{1,7})(?:#(\d+))?$/;
const PDF_PAGE = /^p\.(\d+)(?:#(\d+))?$/i;
const SLIDE = /^(?:slide|s)\.?\s*(\d+)$/i;
const FILE_ANCHOR =
  /^(.+\.(?:md|adoc|txt|json|ya?ml|java|ts|tsx|py|cypher))#(.+)$/i;
const ADOC_PATH = /^§(.+?)>(.+?)(?:#(\d+))?$/;
// 문서 이름 없이 헤딩만 적힌 표기(`§머리말#9`). 실 데이터에 이 형태가 있다.
const ADOC_HEADING = /^§([^>]+?)(?:#(\d+))?$/;
// 슬랙 파서는 `slack:` 접두를 붙여 내보낸다(실 API 응답: slack:C087J33P9PT/1772062465.171759).
// 접두 없는 표기도 받아 둔다 — 목 시나리오가 그 형태로 적혀 있다.
const SLACK = /^(?:slack:)?([A-Za-z0-9_\-가-힣]+)\/(\d{10}\.\d{1,6})$/;
const LABELLED = /^([A-Za-z가-힣]{1,6})[\-_ ]?(\d{1,3})$/;

export function readLocator(locator: string): ReadableLocator {
  const raw = locator.trim();

  const adoc = ADOC_PATH.exec(raw);
  if (adoc) {
    const [, docTitle, heading, block] = adoc;
    const tail = block ? ` · ${block}번째 문단` : "";
    return {
      kind: "문서 안 위치",
      human: `문서 「${docTitle}」 · 「${heading}」 항목${tail}`,
      raw,
    };
  }

  const heading = ADOC_HEADING.exec(raw);
  if (heading) {
    const [, title, block] = heading;
    return {
      kind: "문서 안 위치",
      human: `「${title}」 항목${block ? ` · ${block}번째 문단` : ""}`,
      raw,
    };
  }

  const pdf = PDF_PAGE.exec(raw);
  if (pdf) {
    const [, page, block] = pdf;
    return {
      kind: "PDF 쪽 번호",
      human: block ? `${page}쪽 · ${block}번째 블록` : `${page}쪽`,
      raw,
    };
  }

  const slide = SLIDE.exec(raw);
  if (slide) {
    return { kind: "슬라이드 번호", human: `${slide[1]}번째 슬라이드`, raw };
  }

  const file = FILE_ANCHOR.exec(raw);
  if (file) {
    const [, path, anchor] = file;
    return {
      kind: "파일 안 위치",
      human: `파일 「${path}」 · 「${anchor}」 항목`,
      raw,
    };
  }

  const cell = SHEET_CELL.exec(raw);
  if (cell) {
    const [, sheet, ref, part] = cell;
    // 한 칸이 500자를 넘으면 적재가 조각으로 나눠 담고 `#2`, `#3` 을 붙인다. 원본을 버리지
    // 않으려는 것이므로, 화면도 "그 칸의 몇 번째 조각"이라고 말해 준다.
    const where = `시트 「${sheet}」 · ${ref.replace(/\$/g, "")} 칸`;
    return {
      kind: "엑셀 시트",
      human: part ? `${where} (이어지는 ${part}번째 조각)` : where,
      raw,
    };
  }

  const slack = SLACK.exec(raw);
  if (slack) {
    const [, channelId, ts] = slack;
    // 채널 ID 를 그대로 보여주면 어느 대화인지 알 수 없다. 이름을 알면 이름을 쓴다.
    const channel = SLACK_CHANNELS[channelId] ?? channelId;
    const at = new Date(Number(ts.split(".")[0]) * 1000);
    const when = Number.isNaN(at.getTime())
      ? ts
      : `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(
          at.getDate(),
        ).padStart(2, "0")} ${String(at.getHours()).padStart(2, "0")}:${String(
          at.getMinutes(),
        ).padStart(2, "0")}`;
    return {
      kind: "슬랙 쓰레드",
      human: `#${channel} 채널 · ${when} 메시지`,
      raw,
    };
  }

  const labelled = LABELLED.exec(raw);
  if (labelled) {
    return {
      kind: "자체 번호 라벨",
      human: `${labelled[1]} ${labelled[2]}번 항목`,
      raw,
    };
  }

  return { kind: "원문 표기", human: raw, raw };
}

/** Source 제목·경로와 locator 를 한 줄로 합친 "원본 위치" 문장. */
export function fullOrigin(sourceTitle: string, locator: string): string {
  return `${sourceTitle} · ${readLocator(locator).human}`;
}
