// 브리프. 답변을 읽는 순서로 놓는다.
//
// 위계는 세 단이다. 화면을 열고 10초 안에 이 세 가지가 읽혀야 한다.
//
//   ① 결론      무엇을 말하고 싶은가
//   ② 핵심 포인트  왜 그렇게 판단했는가 (주장 + 그 근거, 3~5개)
//   ③ 할 일      그래서 무엇을 하면 되는가
//
// 나머지는 전부 접어 둔다. 근거 전체·출처·불확실한 것·모르는 것·추가 원문·정리 전 원문·
// 분석 정보가 그 아래에 한 줄씩 있고, 누를 때만 펼쳐진다.
//
// **왜 바꿨나.** 개선 전에는 결론·주장·근거·권고·추정·제품 공백·모르는 것·인용·추가 원문·
// 다음 확인 사항이 전부 같은 굵기로 한 화면에 펼쳐져 있었다. 정보가 부족한 게 아니라 위계가
// 없어서, "그래서 뭘 하면 되나"를 찾으려면 처음부터 다 읽어야 했다.
//
// **정보를 지우지 않았다.** 전부 상세 영역으로 내렸을 뿐이다. 결론을 뒤집을 만한 것만
// 결론 옆에 작은 배지로 남긴다.
//
// 그리는 근거는 `types/brief.ts` 의 구조다. LLM 이 쓴 문장 구조에 레이아웃이 기대지 않는다.
// 숫자 확률은 어디에도 쓰지 않는다(Master Plan §4: evidence_strength 는 밴드 + basis).

import { useMemo } from "react";
import type { AnswerPayload } from "../types/answer";
import type { Brief } from "../types/brief";
import { sourceTitle } from "../lib/sources";
import { readLocator } from "../lib/locator";
import {
  BAND_KO,
  BAND_WHY,
  CLAIM_DOMAIN_KO,
  CONTRADICTION_KO,
  GAP_KO,
  RECENCY_KO,
  RETRIEVER_KO,
  sourceTypeKo,
} from "../lib/labels";
import { RichLine, RichText, markersInOrder } from "./richtext";
import { Marginalia, buildNotes } from "./Marginalia";
import { Fold } from "./Fold";
import { SaveButton } from "./SaveButton";
import { questionTo } from "../lib/saved";
import type { DrawerTarget } from "./EvidenceDrawer";

interface Props {
  question: string;
  answer: AnswerPayload;
  brief: Brief;
  /** 정리기가 아직 도는 중. 원문을 먼저 보기로 한 사람에게만 이 상태가 보인다 */
  distilling: boolean;
  /** 원문을 보는 중에 정리가 끝났다. 화면을 저절로 바꾸지 않고 버튼으로 알린다 */
  briefReady?: boolean;
  onShowBrief?: () => void;
  activeMarker: number | null;
  onPickMarker: (marker: number | null) => void;
  onOpenDrawer: (target: DrawerTarget) => void;
}

/** 근거의 세기. 3칸 막대 + 왜 그렇게 봤는지. 점수·확률은 쓰지 않는다. */
function Strength({ answer }: { answer: AnswerPayload }) {
  const { band, basis } = answer.evidence_strength;
  const filled = band === "HIGH" ? 3 : band === "MEDIUM" ? 2 : 1;
  return (
    <>
      <div className="strength">
        <span className="strength-meter" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <i key={i} className={i < filled ? `on ${band}` : ""} />
          ))}
        </span>
        <span className="strength-band" data-band={band}>
          {BAND_KO[band]}
        </span>
        <span className="strength-why">{BAND_WHY[band]}</span>
      </div>
      <div className="strength-basis">
        <span>
          서로 독립된 근거 <b>{basis.independent_evidence}건</b>
        </span>
        <span>
          가장 권위 있는 자료 <b>{sourceTypeKo(basis.highest_authority)}</b>
        </span>
        <span>
          어긋나는 자료{" "}
          <b>{CONTRADICTION_KO[basis.contradiction] ?? basis.contradiction}</b>
        </span>
        <span>
          최신성 <b>{RECENCY_KO[basis.recency] ?? basis.recency}</b>
        </span>
        {basis.source_type_variety != null && (
          <span>
            자료 유형 <b>{basis.source_type_variety}종</b>
          </span>
        )}
      </div>
    </>
  );
}

export function AnswerBrief({
  question,
  answer,
  brief,
  distilling,
  briefReady = false,
  onShowBrief,
  activeMarker,
  onPickMarker,
  onOpenDrawer,
}: Props) {
  const rawSignals = answer.raw_signals ?? [];
  const disputes = answer.disputes ?? [];
  const estimated = answer.answer.estimated_parts ?? [];
  const points = brief.key_points;
  const sequence = brief.actions.sequence;
  const nextActions = brief.actions.next;
  const band = answer.evidence_strength.band;

  // raw 모드에서는 원문을 자르지 않고 전부 결론 자리에 올린다. 앞 두 문장만 보여 주면
  // 나머지가 화면 어디에도 없게 된다.
  const leadText =
    brief.mode === "raw" ? brief.raw_text : brief.conclusion.summary;

  const evidenceById = useMemo(
    () => new Map(answer.evidence.map((e) => [e.evidence_id, e])),
    [answer.evidence],
  );

  const handlers = useMemo(
    () => ({
      activeMarker,
      onPick: (marker: number) =>
        onPickMarker(activeMarker === marker ? null : marker),
    }),
    [activeMarker, onPickMarker],
  );

  // 여백 각주는 본문에 **실제로 그려진** 각주만 따라간다. 정리본은 원문보다 문장이 적어
  // 각주도 적다. citations 배열을 그대로 쓰면 화면에 없는 번호까지 여백에 붙는다.
  const markers = useMemo(
    () =>
      markersInOrder(
        leadText,
        brief.mode === "raw" ? brief.raw_recommendation : undefined,
        ...points.flatMap((p) => [p.claim, p.reason]),
      ),
    [leadText, brief.mode, brief.raw_recommendation, points],
  );

  const notes = useMemo(
    () => buildNotes(markers, answer.citations, answer.evidence),
    [markers, answer.citations, answer.evidence],
  );

  const openEvidence = (id: string) => onOpenDrawer({ kind: "evidence", id });

  const uncertainCount =
    brief.caveats.length +
    estimated.length +
    answer.gaps.length +
    disputes.length;

  return (
    <div className="brief scroll-y">
      <div className="brief-inner">
        <div className="brief-qrow">
          <h1 className="brief-question">{question}</h1>
          <SaveButton
            kind="question"
            label={question}
            to={questionTo(question)}
          />
        </div>

        {/* 정리가 끝났다는 알림. 읽는 중에 화면을 저절로 바꾸지 않고 누를 때 바꾼다. */}
        {briefReady && onShowBrief && (
          <p className="brief-ready" role="status">
            <span>정리가 끝났습니다. 결론과 핵심 포인트로 볼 수 있습니다.</span>
            <button
              type="button"
              className="btn"
              data-variant="outline"
              data-size="sm"
              onClick={onShowBrief}
            >
              정리본 보기
            </button>
          </p>
        )}

        {/* 결론을 뒤집을 만한 것만 배지로. 분류·개수는 상세로 내렸다. */}
        <div className="brief-flags">
          {band === "LOW" && (
            <span className="tag" data-tone="critical">
              ⚠ 핵심 근거 부족
            </span>
          )}
          {(brief.caveats.length > 0 || estimated.length > 0) && (
            <span className="tag" data-tone="conflict">
              ⚠ 일부 추론 포함
            </span>
          )}
          {answer.notices.critical_unverified_included && (
            <span className="tag" data-tone="critical">
              미검증 중요 항목 포함
            </span>
          )}
          {answer.notices.results_may_be_incomplete && (
            <span className="tag" data-tone="unknown">
              결과가 완전하지 않을 수 있음
            </span>
          )}
        </div>

        <div className="brief-body">
          <div className="brief-col">
            {/* ① 결론 */}
            <section className="lead">
              <div className="lead-head">
                <span className="col-label">결론</span>
                {distilling && (
                  <span className="lead-working" role="status">
                    답을 정리하는 중입니다
                  </span>
                )}
                {!distilling && brief.mode === "raw" && (
                  <span className="meta">
                    {brief.note || "정리 전 답변 원문"}
                  </span>
                )}
              </div>

              {brief.conclusion.title && (
                <h2 className="lead-title">{brief.conclusion.title}</h2>
              )}

              <RichText
                text={leadText}
                handlers={handlers}
                className="brief-text lead-summary answer-text"
              />

              {/* 왜 그렇게 봤는지 한 줄. 색·칩을 쓰지 않는다. 자세한 것은 분석 세부정보. */}
              <p className="lead-basis">
                근거 {BAND_KO[band]} · 서로 독립된 근거{" "}
                {answer.evidence_strength.basis.independent_evidence}건 · 인용{" "}
                {answer.citations.length}건
              </p>
            </section>

            {/* ② 핵심 포인트 */}
            {points.length > 0 && (
              <section className="points">
                <span className="col-label">핵심 포인트</span>
                <ol className="point-list">
                  {points.map((point, i) => (
                    <li className="point" key={i}>
                      <span className="point-no" aria-hidden="true">
                        {i + 1}
                      </span>
                      <div className="point-body">
                        {point.title && (
                          <h3 className="point-title">{point.title}</h3>
                        )}
                        <p className="point-claim">
                          <RichLine text={point.claim} handlers={handlers} />
                        </p>
                        {point.reason && (
                          <p className="point-reason">
                            <RichLine text={point.reason} handlers={handlers} />
                          </p>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              </section>
            )}

            {/* ③ 그래서 어떻게 할까 */}
            {(sequence.length > 0 ||
              nextActions.length > 0 ||
              (brief.mode === "raw" && brief.raw_recommendation)) && (
              <section className="doing">
                <span className="col-label">그래서 어떻게 할까</span>

                {/* raw 모드에서는 정리기가 순서를 못 뽑았으니 권고 원문을 그대로 둔다. */}
                {brief.mode === "raw" && brief.raw_recommendation && (
                  <div className="brief-reco">
                    <span className="brief-reco-label">권고</span>
                    <RichLine
                      text={brief.raw_recommendation}
                      handlers={handlers}
                    />
                  </div>
                )}

                {sequence.length > 0 && (
                  <div className="seq">
                    <span className="seq-label">추천 순서</span>
                    <ol className="seq-chain">
                      {sequence.map((step, i) => (
                        <li key={i}>{step}</li>
                      ))}
                    </ol>
                  </div>
                )}

                {nextActions.length > 0 && (
                  <>
                    {/* 순서가 함께 있을 때만 이름을 붙인다. 목록 하나뿐이면
                        「그래서 어떻게 할까」 라벨과 겹쳐 라벨만 두 줄이 된다. */}
                    {sequence.length > 0 && (
                      <span className="seq-label">다음 액션</span>
                    )}
                    <ul className="plain-list next-list">
                      {nextActions.map((a, i) => (
                        <li key={i}>{a}</li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
            )}

            {/* ─────────────────────────────── 상세. 전부 접혀 있다 */}
            <div className="details">
              <span className="col-label">더 볼 것</span>

              <Fold
                title="인용한 근거"
                count={answer.citations.length}
                hint="누르면 원본까지 갑니다"
              >
                <div className="ev-list">
                  {answer.citations.map((c) => {
                    const ev = evidenceById.get(c.evidence_id);
                    if (!ev) return null;
                    const read = readLocator(ev.locator);
                    return (
                      <button
                        type="button"
                        key={c.evidence_id}
                        className="ev-row ev-card"
                        data-active={activeMarker === c.marker}
                        onClick={() => {
                          onPickMarker(c.marker);
                          openEvidence(ev.evidence_id);
                        }}
                      >
                        <span className="ev-row-top">
                          <span className="ev-marker">{c.marker}</span>
                          <span className="ev-source">
                            {sourceTitle(ev.source_id)}
                          </span>
                          <span className="meta">
                            {sourceTypeKo(ev.authority_label.source_type)}
                          </span>
                        </span>
                        <span className="ev-snippet">{ev.snippet}</span>
                        <span className="ev-where">
                          <code>{read.human}</code>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </Fold>

              {uncertainCount > 0 && (
                <Fold
                  title="불확실한 정보"
                  count={uncertainCount}
                  hint="추정 · 제품 공백 · 자료끼리 어긋나는 것"
                >
                  {brief.caveats.map((c, i) => (
                    <p className="finding-reason" key={`cav-${i}`}>
                      {c}
                    </p>
                  ))}

                  {estimated.map((part, i) => (
                    <div className="brief-estimate" key={`est-${i}`}>
                      <span className="brief-estimate-label">
                        추정 · 자료로 확인된 것이 아닙니다
                      </span>
                      {part}
                    </div>
                  ))}

                  {answer.gaps.map((gap, i) => {
                    const meta = GAP_KO[gap.verdict];
                    const tone =
                      gap.verdict === "CONFIRMED"
                        ? "critical"
                        : gap.verdict === "POSSIBLE"
                          ? "conflict"
                          : "unknown";
                    return (
                      <div className="finding" key={`gap-${i}`}>
                        <div className="finding-head">
                          <span className="tag" data-tone={tone}>
                            {meta.title}
                          </span>
                          <span className="finding-subject">{gap.subject}</span>
                        </div>
                        <p className="finding-reason">{gap.basis.reason}</p>
                        <p className="finding-hint">{meta.hint}</p>
                        {gap.basis.explicit_absence_evidence_ids?.length ? (
                          <div className="finding-links">
                            <span className="meta">부재를 적은 근거</span>
                            {gap.basis.explicit_absence_evidence_ids.map(
                              (id) => (
                                <button
                                  type="button"
                                  className="linkish"
                                  key={id}
                                  onClick={() => openEvidence(id)}
                                >
                                  {sourceTitle(
                                    evidenceById.get(id)?.source_id ?? id,
                                  )}
                                </button>
                              ),
                            )}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}

                  {disputes.map((d, i) => (
                    <div className="finding" key={`dis-${i}`}>
                      <div className="finding-head">
                        <span className="tag" data-tone="conflict">
                          어긋남
                        </span>
                        <span className="finding-subject">{d.subject}</span>
                      </div>
                      <p className="finding-hint">
                        한쪽을 지우지 않고 둘 다 둡니다
                      </p>
                      {d.sides.map((side, j) => (
                        <div className="finding-side" key={j}>
                          {side.statement}
                          <div className="finding-links">
                            {side.evidence_ids.map((id) => (
                              <button
                                type="button"
                                className="linkish"
                                key={id}
                                onClick={() => openEvidence(id)}
                              >
                                {sourceTitle(
                                  evidenceById.get(id)?.source_id ?? id,
                                )}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ))}
                </Fold>
              )}

              {answer.unknowns.length > 0 && (
                <Fold
                  title="아직 모르는 것"
                  count={answer.unknowns.length}
                  hint="자료에 없어서 답하지 못한 부분"
                >
                  <ul className="plain-list">
                    {answer.unknowns.map((u, i) => (
                      <li key={i}>{u}</li>
                    ))}
                  </ul>
                </Fold>
              )}

              {rawSignals.length > 0 && (
                <Fold
                  title="추가 원문 근거"
                  count={rawSignals.length}
                  hint="답변의 근거로 쓰지 않았습니다"
                >
                  <p
                    className="finding-hint"
                    style={{ marginBottom: "var(--sp-2)" }}
                  >
                    구조화된 지식에는 아직 반영되지 않았지만, 원본 자료를 그대로
                    훑어 찾은 관련 신호입니다.
                  </p>
                  <div className="ev-list">
                    {rawSignals.map((s) => {
                      const read = readLocator(s.locator);
                      return (
                        <button
                          type="button"
                          className="ev-row"
                          key={s.evidence_id}
                          onClick={() =>
                            onOpenDrawer({ kind: "raw", id: s.evidence_id })
                          }
                        >
                          <span className="ev-row-top">
                            <span className="ev-source">
                              {sourceTitle(s.source_id)}
                            </span>
                            <span className="meta">
                              {s.in_graph ? "그래프 반영됨" : "그래프 미반영"}
                            </span>
                          </span>
                          <span className="ev-snippet">{s.snippet}</span>
                          <span className="ev-where">
                            <code>{read.human}</code>
                            {s.match_terms?.length ? (
                              <span className="meta">
                                검색어 {s.match_terms.join(" · ")}
                              </span>
                            ) : null}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </Fold>
              )}

              {/* 정리본을 못 믿을 때 대조할 자리. 정리기가 문장을 바꿨는지 여기서 본다. */}
              {brief.mode === "distilled" && brief.raw_text && (
                <Fold
                  title="정리 전 답변 원문"
                  hint="정리본과 대조해 볼 수 있습니다"
                >
                  <RichText
                    text={brief.raw_text}
                    handlers={handlers}
                    className="brief-text"
                  />
                  {brief.raw_recommendation && (
                    <div className="brief-reco">
                      <span className="brief-reco-label">권고</span>
                      <RichLine
                        text={brief.raw_recommendation}
                        handlers={handlers}
                      />
                    </div>
                  )}
                </Fold>
              )}

              <Fold title="분석 세부정보" hint="이 답을 어떻게 만들었는지">
                <div className="detail-block">
                  <span className="detail-block-title">근거의 세기</span>
                  <Strength answer={answer} />
                </div>
                <dl className="tech-grid">
                  <dt>검색 경로</dt>
                  <dd>
                    {RETRIEVER_KO[answer.route.retriever] ??
                      answer.route.retriever}
                  </dd>
                  {answer.route.claim_domain && (
                    <>
                      <dt>주장 유형</dt>
                      <dd>{CLAIM_DOMAIN_KO[answer.route.claim_domain]}</dd>
                    </>
                  )}
                  {answer.route.matched_rule && (
                    <>
                      <dt>맞은 규칙</dt>
                      <dd>{answer.route.matched_rule}</dd>
                    </>
                  )}
                  <dt>근거</dt>
                  <dd>
                    인용 {answer.citations.length}건 · 검토{" "}
                    {answer.evidence.length}건
                  </dd>
                  <dt>관계도</dt>
                  <dd>
                    노드 {answer.subgraph.nodes.length} · 연결{" "}
                    {answer.subgraph.edges.length}
                    {answer.subgraph.truncated ? " · 상한에서 잘림" : ""}
                  </dd>
                  <dt>답변 정리</dt>
                  <dd>
                    {brief.mode === "distilled"
                      ? `정리함 · 핵심 주장 ${points.length}개${
                          brief.meta.dropped > 0
                            ? ` · 근거 검사에서 버린 주장 ${brief.meta.dropped}개`
                            : ""
                        }`
                      : `정리하지 않음${brief.note ? ` · ${brief.note}` : ""}`}
                  </dd>
                  {brief.mode === "distilled" && (
                    <>
                      <dt>각주 없는 문장</dt>
                      {/* 0 이어야 한다. 0 이 아니면 그게 다음에 고칠 자리다. 숨기지 않는다. */}
                      <dd>
                        {brief.meta.untraced === 0
                          ? "0건 · 값이 있는 모든 문장에 각주가 붙어 있습니다"
                          : `${brief.meta.untraced}건 · 이 문장은 출처로 갈 수 없습니다`}
                      </dd>
                    </>
                  )}
                  {answer.answered_at && (
                    <>
                      <dt>답한 시각</dt>
                      <dd>{answer.answered_at}</dd>
                    </>
                  )}
                  {answer.policy_version && (
                    <>
                      <dt>정책 버전</dt>
                      <dd>{answer.policy_version}</dd>
                    </>
                  )}
                  {answer.query_id && (
                    <>
                      <dt>질의 번호</dt>
                      <dd>{answer.query_id}</dd>
                    </>
                  )}
                </dl>
              </Fold>
            </div>
          </div>

          <Marginalia
            notes={notes}
            layoutKey={`${answer.query_id ?? question}:${brief.mode}:${notes.length}`}
            activeMarker={activeMarker}
            onPick={(m) => onPickMarker(activeMarker === m ? null : m)}
            onOpen={openEvidence}
          />
        </div>
      </div>
    </div>
  );
}
