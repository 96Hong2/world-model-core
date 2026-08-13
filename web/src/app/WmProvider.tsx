// 앱 전역에서 한 벌만 있어야 하는 것들: 테마, 엔진, 그래프 상태, 준비된 질문 목록.
//
// 화면이 여러 개가 되면서 App.tsx 하나가 들고 있던 것을 여기로 옮겼다.
// 엔진은 모듈 로드 때 한 번 만든다(라우트가 바뀔 때마다 새로 만들면 fetch 가 중복된다).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { GoldenQuestion, HealthState } from "../api/client";
import { resolveApiBase, resolveDataSource } from "../api/client";
import { LiveEngine, MockEngine } from "../api/engine";
import type { Engine } from "../api/engine";
import { BrowseApi } from "../api/browse";

export type Theme = "light" | "dark";

const THEME_KEY = "wm-theme";

const ENV = import.meta.env as unknown as Record<string, string | undefined>;
export const DATA_SOURCE = resolveDataSource(ENV);
export const API_BASE = resolveApiBase(ENV);

const engine: Engine =
  DATA_SOURCE === "mock" ? new MockEngine() : new LiveEngine(API_BASE);
const browse = new BrowseApi(API_BASE);

function initialTheme(): Theme {
  const saved = window.localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

interface Wm {
  theme: Theme;
  toggleTheme: () => void;
  engine: Engine;
  browse: BrowseApi;
  dataSource: typeof DATA_SOURCE;
  apiBase: string;
  /** 그래프 규모. 못 받아 와도 화면은 돈다. */
  health: HealthState | null;
  /** 미리 준비된 질문. Ask 초기 화면과 Explore 의 "이 대상에 대해 묻기" 가 쓴다. */
  golden: GoldenQuestion[];
}

const Ctx = createContext<Wm | null>(null);

export function WmProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [health, setHealth] = useState<HealthState | null>(null);
  const [golden, setGolden] = useState<GoldenQuestion[]>([]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    const ctl = new AbortController();
    // 둘 다 실패해도 화면은 그대로 쓸 수 있다. 오류로 올리지 않는다.
    engine
      .health(ctl.signal)
      .then(setHealth)
      .catch(() => {});
    engine
      .golden(ctl.signal)
      .then(setGolden)
      .catch(() => {});
    return () => ctl.abort();
  }, []);

  const toggleTheme = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );

  const value = useMemo<Wm>(
    () => ({
      theme,
      toggleTheme,
      engine,
      browse,
      dataSource: DATA_SOURCE,
      apiBase: API_BASE,
      health,
      golden,
    }),
    [theme, toggleTheme, health, golden],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWm(): Wm {
  const value = useContext(Ctx);
  if (!value) throw new Error("useWm 은 WmProvider 안에서만 쓸 수 있습니다.");
  return value;
}
