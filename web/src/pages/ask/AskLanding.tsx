// Ask 초기 화면. 질문 하나에만 집중한다.
//
// 개선 전에는 준비된 질문 20개가 첫 화면의 24% 를 먹고 본문을 아래로 밀어냈다.
// 여기서는 다섯 개만 보이고, 나머지는 접힌 블록 안에 둔다.

import { Link } from "react-router-dom";
import type { GoldenQuestion } from "../../api/client";
import { Fold } from "../../panel/Fold";
import { IconExplore, IconLibrary, IconChanges } from "../../shell/icons";
import { AskField } from "./AskField";
import { PROFILE } from "../../app/profile";

/** 초기 화면에 보이는 추천 질문 수. 나머지는 접어 둔다. */
const SHOWN = 5;

const KIND_KO: Record<string, string> = {
  "Q-E": "사실 조회",
  "Q-M": "관계 추적",
  "Q-S": "전략 판단",
};

interface Props {
  golden: GoldenQuestion[];
  onAsk: (q: string) => void;
  graphNodes?: number;
  graphEdges?: number;
}

function SuggestList({
  items,
  onAsk,
}: {
  items: GoldenQuestion[];
  onAsk: (q: string) => void;
}) {
  return (
    <div className="ask-suggest-list">
      {items.map((q) => (
        <button
          type="button"
          key={q.id}
          className="ask-suggest-item"
          onClick={() => onAsk(q.question)}
        >
          <span className="q">{q.question}</span>
          <span className="kind">
            {KIND_KO[q.expected_route] ?? q.expected_route}
          </span>
        </button>
      ))}
    </div>
  );
}

export function AskLanding({ golden, onAsk, graphNodes, graphEdges }: Props) {
  const shown = golden.slice(0, SHOWN);
  const rest = golden.slice(SHOWN);

  return (
    <div className="ask-landing scroll-y">
      <div className="ask-landing-inner">
        <p className="ask-hero-eyebrow">{PROFILE.modelName}</p>
        <h1 className="ask-hero-title">
          회사가 아는 것과 그 근거가
          <br />
          어떻게 연결되는지 봅니다
        </h1>
        <p className="ask-hero-lede">
          제품·고객·영업·시장 자료 267종을 하나의 지식 월드로 이어 두었습니다.
          물어보면 답과 함께 그 답을 만든 근거와 관계를 보여 줍니다. 근거가
          없으면 없다고 말합니다.
        </p>

        <AskField onAsk={onAsk} autoFocus />

        <p className="ask-field-hint">
          Enter 로 물어봅니다. Shift+Enter 는 줄바꿈입니다.
          {graphNodes != null && graphEdges != null && (
            <>
              {" "}
              지금 지식 월드에 노드 {graphNodes.toLocaleString()}개 · 연결{" "}
              {graphEdges.toLocaleString()}개가 있습니다.
            </>
          )}
        </p>

        {shown.length > 0 && (
          <div className="ask-suggest">
            <div className="ask-suggest-head">
              <h2>이런 것을 물어볼 수 있습니다</h2>
            </div>
            <SuggestList items={shown} onAsk={onAsk} />
            {rest.length > 0 && (
              <Fold title="준비된 질문 더 보기" count={rest.length}>
                <SuggestList items={rest} onAsk={onAsk} />
              </Fold>
            )}
          </div>
        )}

        <div className="ask-shortcuts">
          <Link to="/explore" className="ask-shortcut">
            <IconExplore size={15} />
            질문 없이 둘러보기
          </Link>
          <Link to="/library" className="ask-shortcut">
            <IconLibrary size={15} />
            원본 자료 찾기
          </Link>
          <Link to="/changes" className="ask-shortcut">
            <IconChanges size={15} />
            이번에 무엇이 들어왔나
          </Link>
        </div>
      </div>
    </div>
  );
}
