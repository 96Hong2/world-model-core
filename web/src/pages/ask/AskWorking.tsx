// 기다리는 동안 무엇을 하고 있는지 말한다.
//
// 스피너 하나와 빈 그래프를 보여주지 않는다. 여섯 단계를 차례로 지나가고, 지금 어느 단계인지
// 표시한다. Q-S(전략 질문)는 자료를 집계한 뒤 문장을 만들어서 1~3분까지 걸리므로,
// 왜 오래 걸리는지도 함께 적는다.
//
// 단계는 실제 처리 순서를 따른다(retrieval → evidence → subgraph → synthesis → 정리).
// 서버가 진행률을 흘려 주지 않으므로 경과 시간으로 추정하고, 그 사실을 화면에 숨기지 않는다.
//
// **마지막 정리 단계를 여기서 기다리는 이유.** 답변과 정리본은 서로 다른 두 호출이고 정리에
// 1~2분이 더 걸린다. 예전에는 답변이 오는 즉시 원문을 띄우고 정리본이 오면 바꿔 끼웠는데,
// 읽던 화면이 한참 뒤에 통째로 다른 구조로 바뀌었다. 대기 화면을 한 단계 더 두면 좌측은
// 한 번만 그려진다. 기다리기 싫은 사람은 [답변 원문 보기]로 빠져나간다.

export type WorkPhase = "ask" | "distill";

interface Stage {
  key: string;
  label: string;
  note: string;
  /** 이 초를 넘기면 이 단계에 들어섰다고 본다 */
  at: number;
}

const STAGES: Stage[] = [
  {
    key: "parse",
    label: "질문 이해 중",
    note: "무엇을 묻는지 갈래를 가립니다",
    at: 0,
  },
  {
    key: "focal",
    label: "관련 대상 찾는 중",
    note: "질문에 나온 회사·사업영역·요구를 지식 월드에서 찾습니다",
    at: 2,
  },
  {
    key: "evidence",
    label: "근거 모으는 중",
    note: "자료에서 뽑아 둔 발췌를 후보로 모아 걸러냅니다",
    at: 5,
  },
  {
    key: "graph",
    label: "관계 확인 중",
    note: "근거가 어느 관계로 이어지는지 맞춰 봅니다",
    at: 9,
  },
  {
    key: "synth",
    label: "답 만드는 중",
    note: "모은 근거만으로 문장을 만들고 각주를 답니다",
    at: 14,
  },
  // 두 번째 호출(`POST /brief`)이다. 경과 시간으로는 알 수 없어 phase 로만 켠다.
  {
    key: "distill",
    label: "읽는 순서로 배치 중",
    note: "결론과 핵심 포인트를 가려 뽑고 각주를 다시 맞춥니다",
    at: Number.POSITIVE_INFINITY,
  },
];

function stageState(index: number, elapsed: number, phase: WorkPhase) {
  const isLast = STAGES[index].key === "distill";
  if (phase === "distill") return isLast ? "now" : "done";
  if (isLast) return "wait";
  const next = STAGES[index + 1];
  if (next && elapsed >= next.at) return "done";
  return elapsed >= STAGES[index].at ? "now" : "wait";
}

export function AskWorking({
  question,
  elapsed,
  phase,
  onShowRaw,
}: {
  question: string;
  elapsed: number;
  phase: WorkPhase;
  onShowRaw?: () => void;
}) {
  const slow = elapsed >= 30;

  return (
    <div className="brief scroll-y">
      <div className="brief-inner">
        <h1 className="brief-question">{question}</h1>

        <ol className="stages" style={{ marginTop: "var(--sp-5)" }}>
          {STAGES.map((s, i) => (
            <li
              key={s.key}
              className="stage"
              data-state={stageState(i, elapsed, phase)}
            >
              <span className="stage-dot" aria-hidden="true" />
              <span>
                {s.label}
                <span className="stage-note">{s.note}</span>
              </span>
            </li>
          ))}
        </ol>

        <p className="meta" style={{ marginTop: "var(--sp-4)" }}>
          {elapsed}초 경과
          {phase === "distill"
            ? " · 답은 나왔습니다. 결론과 핵심 포인트를 가려 뽑는 데 보통 1~2분 걸립니다"
            : slow &&
              " · 전략 질문은 자료를 집계한 뒤 문장을 만들어서 1~3분까지 걸립니다"}
        </p>

        {/* 기다리기 싫은 사람의 출구. 원문을 감추지 않는다. */}
        {phase === "distill" && onShowRaw && (
          <p className="work-escape">
            <button
              type="button"
              className="btn"
              data-variant="outline"
              data-size="sm"
              onClick={onShowRaw}
            >
              기다리지 않고 답변 원문 보기
            </button>
          </p>
        )}

        {/* 답이 들어올 자리를 미리 잡아 둔다. 답이 오는 순간 화면이 튀지 않는다. */}
        <div className="load-skeleton" aria-hidden="true">
          <i style={{ width: "96%" }} />
          <i style={{ width: "88%" }} />
          <i style={{ width: "92%" }} />
          <i style={{ width: "64%" }} />
        </div>
      </div>
    </div>
  );
}
