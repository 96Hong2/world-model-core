// 저장함에 담는 버튼. 질문·대상·자료가 같은 버튼을 쓴다.
//
// 예전에는 저장함 화면만 있고 **담는 길이 어디에도 없었다.** 빈 저장함이 "질문의 답이나
// 둘러보기의 대상에서 담아 두면"이라고 안내했지만 그 버튼이 없었다.

import { useEffect, useState } from "react";
import type { SavedKind } from "../lib/saved";
import { isSaved, toggleSaved } from "../lib/saved";
import { IconSaved } from "../shell/icons";

interface Props {
  kind: SavedKind;
  label: string;
  to: string;
  /** 글자 없이 아이콘만 */
  compact?: boolean;
}

const KIND_KO: Record<SavedKind, string> = {
  question: "질문",
  entity: "대상",
  source: "자료",
};

export function SaveButton({ kind, label, to, compact }: Props) {
  const [on, setOn] = useState(false);

  // 다른 화면에서 담았다 뺐을 수 있으니 자리(to)가 바뀌면 다시 읽는다.
  useEffect(() => setOn(isSaved(to)), [to]);

  const text = on ? "저장함에 있음" : `이 ${KIND_KO[kind]} 저장`;

  return (
    <button
      type="button"
      className="btn save-btn"
      data-variant={on ? "quiet" : "outline"}
      data-size="sm"
      data-on={on}
      data-icon-only={compact || undefined}
      aria-pressed={on}
      title={on ? "저장함에서 빼기" : "저장함에 담기"}
      onClick={() => setOn(toggleSaved({ kind, label, to }))}
    >
      <IconSaved size={14} />
      {!compact && text}
    </button>
  );
}
