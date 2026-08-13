// 내비게이션·도구 아이콘. graph/icons.tsx 와 같은 규격을 지킨다:
// 24 격자, 선 하나, stroke 1.6, 색은 currentColor. 이모지를 아이콘으로 쓰지 않는다.

interface Props {
  size?: number;
}

function Glyph({ d, size = 20 }: Props & { d: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={d} />
    </svg>
  );
}

/** 묻기: 말풍선 안에 물음표 */
export const IconAsk = (p: Props) => (
  <Glyph
    {...p}
    d="M4 5h16v11H10l-4 3.5V16H4zM12 8.4a1.9 1.9 0 0 1 1.9 1.9c0 1.3-1.9 1.3-1.9 2.5M12 14.1v.4"
  />
);

/** 둘러보기: 이어진 점 세 개 (관계를 따라간다) */
export const IconExplore = (p: Props) => (
  <Glyph
    {...p}
    d="M6 6.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8M18 3.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8M14 15.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8M8.2 8.2l7.6-2M7.4 11.4l5.4 4.6"
  />
);

/** 서재: 꽂혀 있는 책 */
export const IconLibrary = (p: Props) => (
  <Glyph {...p} d="M4 4h4v16H4zM10 4h4v16h-4zM16.4 4.6l3.4.9-3.8 14.1-3.4-.9" />
);

/** 변경: 시간축에 찍힌 표시 */
export const IconChanges = (p: Props) => (
  <Glyph
    {...p}
    d="M5 4v16M5 7.5h10M5 12h6M5 16.5h12M18.5 5.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 0 0 0-3.8"
  />
);

/** 저장함: 갈피표 */
export const IconSaved = (p: Props) => (
  <Glyph {...p} d="M6.5 3.5h11v17l-5.5-4-5.5 4z" />
);

/** 데이터 상태: 맥박 */
export const IconHealth = (p: Props) => (
  <Glyph {...p} d="M3 12h3.6l2-5.4 3 11L14.4 12H21" />
);

/** 라이트 모드로 (해) */
export const IconSun = (p: Props) => (
  <Glyph
    {...p}
    d="M12 7.4a4.6 4.6 0 1 0 0 9.2 4.6 4.6 0 0 0 0-9.2M12 2.4v2M12 19.6v2M2.4 12h2M19.6 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4"
  />
);

/** 다크 모드로 (달) */
export const IconMoon = (p: Props) => (
  <Glyph {...p} d="M20 14.4A8.4 8.4 0 0 1 9.6 4a8.4 8.4 0 1 0 10.4 10.4" />
);

/** 전체화면으로 펼치기 */
export const IconExpand = (p: Props) => (
  <Glyph {...p} d="M9 4H4v5M15 4h5v5M15 20h5v-5M9 20H4v-5" />
);

/** 전체화면 접기 */
export const IconCollapse = (p: Props) => (
  <Glyph {...p} d="M4 9h5V4M20 9h-5V4M20 15h-5v5M4 15h5v5" />
);

/** 새 탭에서 원문 열기 */
export const IconExternal = (p: Props) => (
  <Glyph
    {...p}
    d="M13.5 4.5H20v6.5M20 4.5 12 12.5M17 14.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4.5"
  />
);

/** 경로 복사 */
export const IconCopy = (p: Props) => (
  <Glyph
    {...p}
    d="M9 9V5.5a1 1 0 0 1 1-1h8.5a1 1 0 0 1 1 1V14a1 1 0 0 1-1 1H15M5.5 9h8.5a1 1 0 0 1 1 1v8.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1"
  />
);

/** 검색 */
export const IconSearch = (p: Props) => (
  <Glyph
    {...p}
    d="M10.8 4a6.8 6.8 0 1 0 0 13.6 6.8 6.8 0 0 0 0-13.6M15.8 15.8 20 20"
  />
);

/** 닫기 */
export const IconClose = (p: Props) => (
  <Glyph {...p} d="M6 6l12 12M18 6 6 18" />
);

/** 뒤로 */
export const IconBack = (p: Props) => <Glyph {...p} d="M14 6l-6 6 6 6" />;

/** 펼침 표시 (접힌 상태) */
export const IconChevron = (p: Props) => <Glyph {...p} d="M9 6l6 6-6 6" />;

/** 다시 보기 */
export const IconReplay = (p: Props) => (
  <Glyph {...p} d="M4 12a8 8 0 1 0 2.6-5.9M4 4.5V10h5.5" />
);
