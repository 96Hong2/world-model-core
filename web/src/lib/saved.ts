// 저장함이 쓰는 두 묶음.
//
//   최근 질문   내가 물어본 것이 자동으로 남는다. 7일이 지나면 사라진다
//   저장한 것   내가 직접 담은 것. 지우지 않으면 남는다
//
// 둘을 가르는 이유: 자동으로 쌓이는 것과 일부러 담은 것을 한 목록에 섞으면, 담아 둔 것이
// 최근 질문에 밀려 내려간다. "일주일 안에 뭘 물어봤지" 와 "이건 나중에 다시 보자" 는
// 서로 다른 요구다.
//
// 서버에 저장 기능이 없어서 **이 브라우저에만** 남는다. 화면이 그 사실을 밝힌다.

export type SavedKind = "question" | "entity" | "source";

export interface SavedItem {
  kind: SavedKind;
  label: string;
  /** 다시 돌아갈 자리. 같은 자리는 하나만 담긴다 */
  to: string;
  at: string;
}

export interface RecentQuestion {
  question: string;
  at: string;
}

const SAVED_KEY = "wm-saved";
const RECENT_KEY = "wm-recent";

/** 최근 질문을 며칠 남길지. 사용자 요구: 일주일. */
export const RECENT_DAYS = 7;
const MAX_RECENT = 60;
const MAX_SAVED = 200;

function read<T>(key: string): T[] {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

function write<T>(key: string, items: T[]): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(items));
  } catch {
    // 저장 공간이 막혀 있으면 조용히 넘긴다. 이 기능 때문에 화면이 죽어서는 안 된다.
  }
}

/** 며칠 지났나. 잘못된 시각은 아주 오래된 것으로 본다(그래서 걸러진다). */
function daysAgo(at: string): number {
  const t = Date.parse(at);
  if (Number.isNaN(t)) return Number.POSITIVE_INFINITY;
  return (Date.now() - t) / 86_400_000;
}

// ------------------------------------------------------------------ 최근 질문

/**
 * 최근 질문. **읽을 때 오래된 것을 버린다.**
 *
 * 지울 시점을 따로 잡지 않는다. 화면을 열 때마다 거르면 7일 규칙이 저절로 지켜진다.
 */
export function readRecent(): RecentQuestion[] {
  const all = read<RecentQuestion>(RECENT_KEY);
  const fresh = all.filter(
    (r) => r && typeof r.question === "string" && daysAgo(r.at) <= RECENT_DAYS,
  );
  if (fresh.length !== all.length) write(RECENT_KEY, fresh);
  return fresh;
}

/** 질문을 최근 목록에 올린다. 같은 질문은 새 줄을 만들지 않고 시각만 갱신한다. */
export function noteQuestion(question: string): void {
  const q = question.trim();
  if (!q) return;
  const rest = readRecent().filter((r) => r.question !== q);
  write(
    RECENT_KEY,
    [{ question: q, at: new Date().toISOString() }, ...rest].slice(
      0,
      MAX_RECENT,
    ),
  );
}

export function forgetQuestion(question: string): RecentQuestion[] {
  const next = readRecent().filter((r) => r.question !== question);
  write(RECENT_KEY, next);
  return next;
}

export function clearRecent(): void {
  write(RECENT_KEY, []);
}

// ------------------------------------------------------------------ 저장한 것

export function readSaved(): SavedItem[] {
  return read<SavedItem>(SAVED_KEY).filter(
    (i) => i && typeof i.to === "string",
  );
}

export function isSaved(to: string): boolean {
  return readSaved().some((i) => i.to === to);
}

/** 담는다. 같은 자리를 다시 담으면 맨 위로 올린다. */
export function save(item: Omit<SavedItem, "at">): SavedItem[] {
  const rest = readSaved().filter((i) => i.to !== item.to);
  const next = [{ ...item, at: new Date().toISOString() }, ...rest].slice(
    0,
    MAX_SAVED,
  );
  write(SAVED_KEY, next);
  return next;
}

export function unsave(to: string): SavedItem[] {
  const next = readSaved().filter((i) => i.to !== to);
  write(SAVED_KEY, next);
  return next;
}

/** 담기 ↔ 빼기. 담긴 상태를 돌려준다. */
export function toggleSaved(item: Omit<SavedItem, "at">): boolean {
  if (isSaved(item.to)) {
    unsave(item.to);
    return false;
  }
  save(item);
  return true;
}

/** 질문을 저장함에 담을 때 쓸 자리. 최근 질문과 저장한 것이 같은 키를 쓰게 한다. */
export function questionTo(question: string): string {
  return `/ask?q=${encodeURIComponent(question)}`;
}
