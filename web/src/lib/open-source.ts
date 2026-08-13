// 근거에서 **원본으로 가는 길**을 정한다.
//
// 위치는 두 가지가 있고 둘은 다른 것이다. 예전 화면은 이 둘을 한 덩어리로 보여 줘서
// "지금 보고 있는 게 어디인가"가 흐렸다.
//
//   지식 월드 안의 위치   이 근거가 어느 노드에 붙어 무엇을 만들었나 (WM 쪽 이야기)
//   자료 안의 위치        원본 파일의 몇 번째 시트·칸·쪽·메시지인가 (원본 쪽 이야기)
//
// 이 파일은 **자료 쪽**만 다룬다. 지식 쪽 위치는 답변 payload 의 subgraph 에서 찾아
// `OriginBlock` 이 함께 그린다.
//
// 원본을 여는 길은 세 갈래다. 원칙은 그대로다: **깨진 링크를 만드는 것이 링크가 없는 것보다
// 나쁘다.** 눌러도 아무 일이 없으면 사람은 제품이 고장 났다고 읽는다.
//
//   slack  채널·시각으로 슬랙 permalink 를 만들 수 있다 → 새 탭
//   file   서버가 도는 컴퓨터에서 기본 앱으로 연다 → 실제 엑셀·PDF 가 열린다
//   none   둘 다 안 되면 "원본 링크 없음"을 밝히고 경로만 준다
//
// 어느 갈래든 WM 안에서 발췌를 펼쳐 보는 길(`canPreview`)은 따로 항상 열어 둔다.

import { findSource } from "./sources";
import { readLocator } from "./locator";
import {
  SLACK_CHANNELS,
  SLACK_WORKSPACE as BUILT_WORKSPACE,
} from "./source-registry";

const ENV = import.meta.env as unknown as Record<string, string | undefined>;

/**
 * 슬랙 워크스페이스 주소.
 *
 * 기본값은 슬랙 덤프의 permalink 에서 읽어 구워 둔 값이다(짐작한 값이 아니다).
 * 다른 워크스페이스를 쓰면 `VITE_SLACK_WORKSPACE` 로 덮어쓴다.
 */
const SLACK_WORKSPACE = (
  ENV.VITE_SLACK_WORKSPACE ??
  BUILT_WORKSPACE ??
  ""
).trim();

const SLACK_LOCATOR =
  /^(?:slack:)?([A-Za-z0-9_\-가-힣]+)\/(\d{10})\.(\d{1,6})$/;

/** WM 이 발췌를 위치까지 들고 있는 자료 형식. 이 형식이면 안에서 펼쳐 볼 수 있다. */
const VIEWABLE_TYPES = new Set([
  "bd_registry",
  "bd_registry_aux",
  "bd_openbook",
  "pain_registry",
  "proposal",
  "product_brochure",
  "user_manual",
  "internal_memo",
  "internal_analysis",
  "internal_deck",
  "customer_internal_report",
  "sales_activity_log",
  "sales_weekly_plan",
  "release_spec",
  "ai_eval_dataset",
  "compliance_checklist",
  "architecture_spec",
  "glossary",
  "repo_doc",
  "product_doc",
  "slack_thread",
  "code",
  "test",
]);

export type OpenHow = "slack" | "file" | "none";

export interface OriginPlan {
  /** 자료 파일의 경로. 등록돼 있지 않으면 빈 문자열 */
  path: string;
  /** 자료 안의 위치를 사람 말로. 예: 시트 「기대효과」 · I7 칸 */
  where: string;
  /** 위치 종류. 예: 엑셀 시트 / 슬랙 쓰레드 */
  whereKind: string;
  /** 파서가 적어 준 위치 원문 */
  whereRaw: string;
  /** 원본을 여는 길 */
  how: OpenHow;
  /** how === "slack" 일 때만 */
  url?: string;
  /** 버튼에 적을 말 */
  openLabel: string;
  /** 어디가 열리는지 한 줄. 누르기 전에 알 수 있게 */
  openHint: string;
  /** how === "none" 일 때 왜 못 여는지 */
  noneReason?: string;
  /** WM 안에서 발췌를 펼쳐 볼 수 있는가 */
  canPreview: boolean;
}

export function planOrigin(sourceId: string, locator = ""): OriginPlan {
  const info = findSource(sourceId);
  const path = info?.canonical_location ?? "";
  const read = locator ? readLocator(locator) : null;
  const canPreview = !!info && VIEWABLE_TYPES.has(info.source_type);

  const base = {
    path,
    where: read?.human ?? "",
    whereKind: read?.kind ?? "",
    whereRaw: read?.raw ?? "",
    canPreview,
  };

  const slack = SLACK_LOCATOR.exec(locator.trim());
  if (slack && SLACK_WORKSPACE) {
    const [, channelId, sec, frac] = slack;
    // 슬랙 permalink 는 소수점을 뺀 타임스탬프에 p 를 붙인다.
    const ts = `p${sec}${frac.padEnd(6, "0")}`;
    const channel = SLACK_CHANNELS[channelId] ?? channelId;
    return {
      ...base,
      how: "slack",
      url: `https://${SLACK_WORKSPACE}.slack.com/archives/${channelId}/${ts}`,
      openLabel: "슬랙에서 이 대화 열기",
      openHint: `${SLACK_WORKSPACE}.slack.com 의 #${channel} 채널이 새 탭에서 열립니다`,
    };
  }

  if (slack) {
    return {
      ...base,
      how: "none",
      openLabel: "",
      openHint: "",
      noneReason:
        "슬랙 워크스페이스 주소를 몰라 링크를 만들 수 없습니다. 지어낸 주소로 링크를 만들지는 않습니다.",
    };
  }

  if (path) {
    const name = path.split("/").pop() || path;
    return {
      ...base,
      how: "file",
      openLabel: "이 파일 열기",
      openHint: `이 컴퓨터의 기본 앱으로 ${name} 을 엽니다`,
    };
  }

  return {
    ...base,
    how: "none",
    openLabel: "",
    openHint: "",
    noneReason: "이 자료는 원본 위치가 기록돼 있지 않습니다.",
  };
}

/** 클립보드 복사. 실패하면 false 를 돌려주고 화면이 그 사실을 말한다. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
