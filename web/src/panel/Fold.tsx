// 접히는 블록. 기본은 접혀 있다.
//
// 개발 정보와 긴 목록을 처음부터 펼쳐 놓으면 답변이 뒤로 밀린다. 그래서 제목과 건수만
// 보이고, 필요할 때 펼친다(progressive disclosure). <details> 를 쓰지 않는 이유는
// 화살표 회전과 접힘 상태를 다른 컴포넌트와 같은 방식으로 다루려는 것이다.

import { useId, useState } from "react";
import type { ReactNode } from "react";
import { IconChevron } from "../shell/icons";

interface Props {
  title: string;
  /** 제목 옆에 조용히 붙는 건수 */
  count?: number;
  hint?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

export function Fold({
  title,
  count,
  hint,
  defaultOpen = false,
  children,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();

  return (
    <section className="fold" data-open={open}>
      <button
        type="button"
        className="fold-summary"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
      >
        <IconChevron size={14} />
        <span>{title}</span>
        {count != null && <span className="fold-count">{count}건</span>}
        {hint && <span className="spacer" />}
        {hint && <span className="meta">{hint}</span>}
      </button>
      {open && (
        <div className="fold-body" id={bodyId}>
          {children}
        </div>
      )}
    </section>
  );
}
