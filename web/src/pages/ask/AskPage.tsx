// Ask. 이 제품의 기본 화면이다.
//
// 세 상태가 있다: 초기 화면 → 기다리는 중 → 브리프 + 도면.
// 질문은 URL(`/ask?q=...`)에 남는다. 링크를 주고받을 수 있어야 하기 때문이다.
//
// 각주를 눌렀을 때(요구 §6):
//   1. 도면에서 그 근거가 걸린 노드·관계가 밝아진다
//   2. 여백 각주가 펼쳐져 자료 이름·위치·발췌·[원문 열기]가 보인다
//   3. 근거 서랍은 여백 각주나 근거 행을 한 번 더 누를 때 열린다
//
// 3번을 각주 클릭에 바로 걸지 않은 이유: 개선 전 실측에서 각주를 누르면 스크롤이 근거 카드로
// 옮겨 가 **읽던 문장이 화면에서 사라졌다**(본문 점유 48% → 0%). 서랍이 브리프를 덮으면 같은
// 일이 난다. 여백 각주가 이미 근거와 자료를 보여 주므로 요구의 뜻은 그 자리에서 지켜진다.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { AnswerPayload } from "../../types/answer";
import type { Brief } from "../../types/brief";
import { rawBrief } from "../../types/brief";
import { useWm } from "../../app/WmProvider";
import { noteQuestion } from "../../lib/saved";
import { GraphPane } from "../../graph/GraphPane";
import { AnswerBrief } from "../../panel/AnswerBrief";
import { EvidenceDrawer } from "../../panel/EvidenceDrawer";
import type { DrawerTarget } from "../../panel/EvidenceDrawer";
import { toRenderGraph } from "../../graph/adapter";
import { AskField } from "./AskField";
import { AskLanding } from "./AskLanding";
import { AskWorking } from "./AskWorking";

export function AskPage() {
  const { engine, golden, health } = useWm();
  const [params, setParams] = useSearchParams();
  const urlQuestion = params.get("q") ?? "";

  const [question, setQuestion] = useState(urlQuestion);
  const [answer, setAnswer] = useState<AnswerPayload | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [distilling, setDistilling] = useState(false);
  /** 정리를 기다리지 않고 원문을 먼저 보기로 한 상태. 정리본이 와도 자동으로 바꾸지 않는다 */
  const [rawFirst, setRawFirst] = useState(false);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [graphOpen, setGraphOpen] = useState(false);

  const abort = useRef<AbortController | null>(null);
  const asked = useRef<string>("");
  const startedAt = useRef(0);

  // 답변과 정리는 두 호출이지만 사용자에게는 한 번의 기다림이다. 그래서 경과 시간을
  // 단계 사이에서 0 으로 되돌리지 않는다.
  const waiting = busy || distilling;

  useEffect(() => {
    if (!waiting) return;
    const id = window.setInterval(
      () => setElapsed(Math.round((Date.now() - startedAt.current) / 1000)),
      250,
    );
    return () => window.clearInterval(id);
  }, [waiting]);

  const run = useCallback(
    (q: string) => {
      abort.current?.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      asked.current = q;

      setQuestion(q);
      // 물어본 것은 저장함의 「최근 질문」에 자동으로 남는다(7일).
      noteQuestion(q);
      startedAt.current = Date.now();
      setElapsed(0);
      setBusy(true);
      setError(null);
      setAnswer(null);
      setBrief(null);
      setDistilling(false);
      setRawFirst(false);
      setActiveMarker(null);
      setSelectedNodeId(null);
      setDrawer(null);

      engine
        .ask(q, ctl.signal)
        .then((payload) => {
          if (ctl.signal.aborted) return;
          setAnswer(payload);
          setBusy(false);

          // 정리는 답변이 온 뒤에 이어서 도는 두 번째 호출이다. 그동안 좌측은 대기 화면을
          // 그대로 두고 정리 단계를 켠다. 원문을 먼저 띄우면 1~2분 뒤에 화면 구조가 통째로
          // 바뀌어, 읽고 있던 사람이 읽던 자리를 잃는다. 기다리기 싫으면 원문으로 빠진다.
          setDistilling(true);
          engine
            .brief(q, payload, ctl.signal)
            .then((got) => {
              if (ctl.signal.aborted) return;
              setBrief(got);
              setDistilling(false);
            })
            .catch((err) => {
              if (ctl.signal.aborted) return;
              // 정리 실패가 답변까지 가리면 안 된다. 왜 못 했는지 적고 원문을 그대로 둔다.
              setBrief(
                rawBrief(
                  payload,
                  `정리하지 못했습니다: ${(err as Error).message}`,
                ),
              );
              setDistilling(false);
            });
        })
        .catch((err) => {
          if (ctl.signal.aborted) return;
          setError((err as Error).message);
          setBusy(false);
        });
    },
    [engine],
  );

  const ask = useCallback(
    (q: string) => {
      setParams({ q }, { replace: false });
      run(q);
    },
    [run, setParams],
  );

  // 링크로 들어오거나 뒤로 가기로 질문이 바뀌면 그 질문으로 답한다.
  useEffect(() => {
    if (!urlQuestion) {
      // 뒤로 가기로 초기 화면까지 돌아온 경우
      if (!busy) {
        setAnswer(null);
        setQuestion("");
        asked.current = "";
      }
      return;
    }
    if (urlQuestion === asked.current) return;
    run(urlQuestion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlQuestion]);

  const stop = useCallback(() => {
    abort.current?.abort();
    setBusy(false);
    setError("질문을 중단했습니다.");
  }, []);

  const citedIds = useMemo(
    () =>
      (answer?.subgraph.nodes ?? [])
        .filter((n) => (n.citation_markers ?? []).length > 0)
        .map((n) => n.id),
    [answer],
  );

  // 근거 서랍이 노드를 찾아볼 때 쓰는 화면용 그래프(서버가 준 전부를 기준으로 한다).
  const fullGraph = useMemo(
    () =>
      toRenderGraph(answer?.subgraph ?? { nodes: [], edges: [] }, { citedIds }),
    [answer, citedIds],
  );

  // 정리본이 없거나, 사용자가 원문을 먼저 보기로 했으면 정리 전 상태를 그린다.
  const shownBrief = useMemo(
    () =>
      answer
        ? rawFirst
          ? rawBrief(answer, "")
          : (brief ?? rawBrief(answer, ""))
        : null,
    [answer, brief, rawFirst],
  );

  const pickMarker = useCallback((marker: number | null) => {
    setActiveMarker(marker);
    setSelectedNodeId(null);
  }, []);

  const selectNode = useCallback((id: string) => {
    setSelectedNodeId(id);
    setDrawer({ kind: "node", id });
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedNodeId(null);
    setActiveMarker(null);
  }, []);

  /* --------------------------------------------------------- 초기 화면 */

  if (!busy && !answer && !error) {
    return (
      <div className="screen">
        <div className="ask">
          <AskLanding
            golden={golden}
            onAsk={ask}
            graphNodes={health?.graph.nodes}
            graphEdges={health?.graph.edges}
          />
        </div>
      </div>
    );
  }

  /* --------------------------------------------------------- 질문 뒤 */

  return (
    <div className="screen">
      <div className="ask">
        <div className="ask-bar">
          {/* 정리를 기다리는 동안에도 다른 질문을 던질 수 있어야 한다. 그래서 여기는
              답변 단계만 잠근다. 정리 대기의 출구는 대기 화면의 버튼이다. */}
          <AskField
            slim
            initial={question}
            busy={busy}
            onAsk={ask}
            onStop={stop}
          />
          <button
            type="button"
            className="btn"
            data-variant="quiet"
            data-size="sm"
            onClick={() => setParams({}, { replace: false })}
          >
            새 질문
          </button>
        </div>

        <div className="ask-work" data-graph={graphOpen ? "open" : undefined}>
          {waiting && !rawFirst ? (
            <AskWorking
              question={question}
              elapsed={elapsed}
              phase={busy ? "ask" : "distill"}
              onShowRaw={() => setRawFirst(true)}
            />
          ) : error ? (
            <div className="brief scroll-y">
              <div className="notice" data-tone="error">
                <div className="notice-title">답을 받지 못했습니다</div>
                <div className="notice-body">{error}</div>
                <div className="notice-hint">
                  답변 API 가 떠 있는지 확인하고 다시 물어보세요. 같은 질문을
                  그대로 다시 보낼 수 있습니다.
                </div>
                <div className="row">
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    onClick={() => run(question)}
                  >
                    다시 물어보기
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-variant="outline"
                    onClick={() => setParams({}, { replace: false })}
                  >
                    다른 것 물어보기
                  </button>
                </div>
              </div>
            </div>
          ) : answer && shownBrief ? (
            <AnswerBrief
              question={question}
              answer={answer}
              brief={shownBrief}
              distilling={distilling}
              briefReady={rawFirst && brief !== null}
              onShowBrief={() => setRawFirst(false)}
              activeMarker={activeMarker}
              onPickMarker={pickMarker}
              onOpenDrawer={setDrawer}
            />
          ) : null}

          <GraphPane
            title="이 답을 만든 관계"
            subgraph={answer?.subgraph ?? { nodes: [], edges: [] }}
            citedIds={citedIds}
            activeMarker={activeMarker}
            selectedNodeId={selectedNodeId}
            onSelectNode={selectNode}
            onClearSelection={clearSelection}
            emptyTitle={
              busy ? "관계를 모으는 중입니다" : "그릴 관계가 없습니다"
            }
            emptyBody={
              busy
                ? "근거가 모이면 그 근거가 이어지는 관계를 여기에 펼칩니다."
                : "이 질문은 지식 월드의 관계를 쓰지 않고 답했습니다."
            }
          />
        </div>

        {/* 좁은 화면에서 도면을 여닫는 버튼 */}
        <button
          type="button"
          className="btn narrow-graph-toggle"
          data-variant="outline"
          onClick={() => setGraphOpen((v) => !v)}
        >
          {graphOpen ? "관계 닫기" : "관계 보기"}
        </button>
      </div>

      {drawer && answer && (
        <EvidenceDrawer
          target={drawer}
          answer={answer}
          graph={fullGraph}
          onClose={() => setDrawer(null)}
          onOpen={setDrawer}
          onFocusMarker={setActiveMarker}
        />
      )}
    </div>
  );
}
