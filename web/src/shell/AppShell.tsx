// 앱 골격. 좌측 레일 + 지금 화면.
//
// 레일에는 네 개의 1차 메뉴와 두 개의 보조 메뉴만 둔다. Graph 는 메뉴가 아니다 —
// 네 화면을 관통하는 상호작용 층이라, 각 화면 안에서 그려진다.

import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useWm } from "../app/WmProvider";
import { PROFILE } from "../app/profile";
import {
  IconAsk,
  IconChanges,
  IconExplore,
  IconHealth,
  IconLibrary,
  IconMoon,
  IconSaved,
  IconSun,
} from "./icons";

interface NavEntry {
  to: string;
  label: string;
  /** 스크린리더와 툴팁에 쓰는 한 줄 설명 */
  title: string;
  Icon: (p: { size?: number }) => JSX.Element;
}

const PRIMARY: NavEntry[] = [
  {
    to: "/ask",
    label: "묻기",
    title: "묻기 — 사내 자료에서 답과 근거를 찾습니다",
    Icon: IconAsk,
  },
  {
    to: "/explore",
    label: "둘러보기",
    title: "둘러보기 — 사업영역·고객사·요구·역량을 관계로 훑습니다",
    Icon: IconExplore,
  },
  {
    to: "/library",
    label: "서재",
    title: "서재 — 원본 자료를 찾고 열어 봅니다",
    Icon: IconLibrary,
  },
  {
    to: "/changes",
    label: "변경",
    title: "변경 — 이번 수집으로 지식이 어떻게 바뀌었는지 봅니다",
    Icon: IconChanges,
  },
];

const SECONDARY: NavEntry[] = [
  {
    to: "/saved",
    label: "저장함",
    title: "저장함 — 담아 둔 질문과 대상",
    Icon: IconSaved,
  },
  {
    to: "/health",
    label: "상태",
    title: "데이터 상태 — 근거가 부족한 곳과 어긋나는 곳",
    Icon: IconHealth,
  },
];

function RailLink({ entry }: { entry: NavEntry }) {
  const { Icon } = entry;
  return (
    <NavLink to={entry.to} className="rail-item" title={entry.title}>
      <Icon />
      <span className="rail-label">{entry.label}</span>
    </NavLink>
  );
}

export function AppShell() {
  const { theme, toggleTheme } = useWm();
  const location = useLocation();
  const mainRef = useRef<HTMLElement | null>(null);

  // 화면이 바뀌면 본문으로 초점을 옮긴다. 그러지 않으면 스크린리더를 쓰는 사람은
  // 링크를 눌러도 읽는 자리가 레일에 그대로 남아 무엇이 바뀌었는지 알 수 없다.
  useEffect(() => {
    mainRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  return (
    <div className="shell">
      {/* 키보드로 들어온 사람이 레일 여섯 칸을 지나지 않고 본문으로 바로 가게 한다. */}
      <a className="skip-link" href="#wm-main">
        본문으로 건너뛰기
      </a>
      <nav className="rail" aria-label="주요 메뉴">
        <NavLink to="/ask" className="rail-mark" title={PROFILE.modelName}>
          {PROFILE.modelAbbr}
        </NavLink>

        {PRIMARY.map((e) => (
          <RailLink key={e.to} entry={e} />
        ))}

        <span className="rail-split" aria-hidden="true" />

        {SECONDARY.map((e) => (
          <RailLink key={e.to} entry={e} />
        ))}

        <div className="rail-foot">
          <button
            type="button"
            className="rail-btn"
            onClick={toggleTheme}
            aria-label={
              theme === "dark" ? "라이트 모드로 바꾸기" : "다크 모드로 바꾸기"
            }
            title={
              theme === "dark" ? "라이트 모드로 바꾸기" : "다크 모드로 바꾸기"
            }
          >
            {theme === "dark" ? <IconSun /> : <IconMoon />}
          </button>
        </div>
      </nav>

      <main className="screen-host" id="wm-main" ref={mainRef} tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
