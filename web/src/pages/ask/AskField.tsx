// 질문 입력. 초기 화면에서는 크게, 답변 뒤에는 한 줄로 얇게 쓴다.
//
// 라벨은 화면에 보이는 문구가 겸하지 않게 sr-only 로 따로 둔다(placeholder 만으로
// 라벨을 대신하지 않는다). Enter 로 보내고 Shift+Enter 로 줄을 바꾼다.

import { useEffect, useRef, useState } from "react";

interface Props {
  onAsk: (q: string) => void;
  /** 답변 뒤 얇은 바 모양 */
  slim?: boolean;
  /** 이미 물은 질문을 입력창에 채워 둔다 */
  initial?: string;
  busy?: boolean;
  onStop?: () => void;
  autoFocus?: boolean;
}

const PLACEHOLDER =
  "예) 하늘IT와 구독 사업을 하려면 무엇을 먼저 풀어야 하는가?";

export function AskField({
  onAsk,
  slim = false,
  initial = "",
  busy = false,
  onStop,
  autoFocus = false,
}: Props) {
  const [text, setText] = useState(initial);
  const ref = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => setText(initial), [initial]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  // 여러 줄 질문에 맞춰 높이를 늘린다(얇은 바에서는 한 줄로 고정).
  useEffect(() => {
    const el = ref.current;
    if (!el || slim) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  }, [text, slim]);

  const submit = () => {
    const q = text.trim();
    if (!q || busy) return;
    onAsk(q);
  };

  return (
    <div className={slim ? "ask-bar-field" : "ask-field"}>
      <label className="sr-only" htmlFor="ask-input">
        사내 자료에 물어볼 질문
      </label>
      <textarea
        id="ask-input"
        ref={ref}
        className="ask-input"
        rows={1}
        placeholder={slim ? "다른 것을 물어보세요" : PLACEHOLDER}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      {busy && onStop ? (
        <button
          type="button"
          className="btn ask-submit"
          data-variant="outline"
          data-size={slim ? "sm" : undefined}
          onClick={onStop}
        >
          중단
        </button>
      ) : (
        <button
          type="button"
          className="btn ask-submit"
          data-variant="primary"
          data-size={slim ? "sm" : undefined}
          disabled={!text.trim()}
          onClick={submit}
        >
          물어보기
        </button>
      )}
    </div>
  );
}
