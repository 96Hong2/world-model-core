// 근거 하나가 "어디서 왔고 어디에 쓰였나"를 보여 준다.
//
// 위치를 두 칸으로 나눈다. 예전에는 한 덩어리였고, 그래서 화면에 뜬 경로가 지식 월드 쪽
// 위치인지 원본 파일 쪽 위치인지 알 수 없었다.
//
//   ① 지식 월드 안의 위치   각주 몇 번으로 쓰였고 어느 노드에 붙었나
//   ② 자료 안의 위치        원본 파일의 몇 번째 시트·칸·쪽·메시지인가
//   ③ 원본 열기            슬랙 링크 · 파일 열기 · 링크 없음
//
// ③ 은 링크를 지어내지 않는다. 열 수 없으면 "원본 링크 없음"이라고 적고 경로만 준다.

import { useState } from "react";
import { useWm } from "../app/WmProvider";
import { copyText, planOrigin } from "../lib/open-source";
import { sourceTitle } from "../lib/sources";
import { sourceTypeKo } from "../lib/labels";
import { IconCopy, IconExternal, IconExplore } from "../shell/icons";

export interface KnowledgeSpot {
  /** 답변에서 이 근거가 달린 각주 번호. 답변 밖(원문 신호)이면 없다 */
  marker?: number;
  /** 이 근거가 붙어 있는 노드 */
  nodes: { id: string; text: string; label: string }[];
}

interface Props {
  sourceId: string;
  locator?: string;
  sourceType?: string;
  /** 원문 보기를 열어 줄 콜백. 없으면 발췌 보기 버튼을 달지 않는다. */
  onOpenViewer?: (sourceId: string, locator: string) => void;
  /** 지식 월드 안의 위치. 모르면 그 칸을 그리지 않는다. */
  knowledge?: KnowledgeSpot;
  /** 관계도에서 그 노드를 비추는 콜백 */
  onFocusNode?: (id: string) => void;
}

export function OriginBlock({
  sourceId,
  locator = "",
  sourceType,
  onOpenViewer,
  knowledge,
  onFocusNode,
}: Props) {
  const { browse } = useWm();
  const plan = planOrigin(sourceId, locator);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [opening, setOpening] = useState(false);

  const say = (ok: boolean, text: string) => {
    setSaid({ ok, text });
    window.setTimeout(() => setSaid(null), 5000);
  };

  const copy = async () => {
    const ok = await copyText(plan.path);
    say(
      ok,
      ok
        ? "경로를 복사했습니다"
        : "복사하지 못했습니다. 경로를 직접 선택해 주세요.",
    );
  };

  const openFile = async () => {
    setOpening(true);
    try {
      await browse.openSource(sourceId);
      say(true, "이 컴퓨터에서 파일을 열었습니다");
    } catch (err) {
      say(false, (err as Error).message);
    } finally {
      setOpening(false);
    }
  };

  const hasKnowledge =
    !!knowledge && (knowledge.marker != null || knowledge.nodes.length > 0);

  return (
    <div className="origin">
      {/* ① 지식 월드 쪽 */}
      {hasKnowledge && (
        <section className="origin-part">
          <h4 className="origin-part-title">지식 월드 안의 위치</h4>
          <p className="origin-part-line">
            {knowledge!.marker != null ? (
              <>
                답변의 각주 <b>[{knowledge!.marker}]</b> 로 쓰였습니다.
              </>
            ) : (
              <>답변에는 인용되지 않았습니다.</>
            )}
          </p>
          {knowledge!.nodes.length > 0 && (
            <ul className="origin-nodes">
              {knowledge!.nodes.map((n) => (
                <li key={n.id}>
                  <span className="origin-node-label">{n.label}</span>
                  <span className="origin-node-text">{n.text}</span>
                  {onFocusNode && (
                    <button
                      type="button"
                      className="btn"
                      data-variant="quiet"
                      data-size="sm"
                      onClick={() => onFocusNode(n.id)}
                    >
                      <IconExplore size={13} />
                      관계도에서 보기
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* ② 원본 자료 쪽 */}
      <section className="origin-part">
        <h4 className="origin-part-title">자료 안의 위치</h4>
        <p className="origin-part-line">
          <b>{sourceTitle(sourceId)}</b>
          {sourceType && (
            <span className="meta"> · {sourceTypeKo(sourceType)}</span>
          )}
        </p>
        {plan.where ? (
          <p className="origin-part-line">
            {plan.whereKind && (
              <span className="meta">{plan.whereKind} · </span>
            )}
            {plan.where}
          </p>
        ) : (
          <p className="origin-part-line meta">
            자료 안의 정확한 자리는 기록돼 있지 않습니다.
          </p>
        )}
        {plan.canPreview && onOpenViewer && (
          <button
            type="button"
            className="btn"
            data-variant="outline"
            data-size="sm"
            onClick={() => onOpenViewer(sourceId, locator)}
          >
            이 자리를 WM 에서 펼쳐 보기
          </button>
        )}
      </section>

      {/* ③ 원본 열기 */}
      <section className="origin-part">
        <h4 className="origin-part-title">원본 열기</h4>

        {plan.how === "slack" && (
          <>
            <a
              className="btn"
              data-variant="primary"
              data-size="sm"
              href={plan.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              <IconExternal size={14} />
              {plan.openLabel}
            </a>
            <p className="origin-part-line meta">{plan.openHint}</p>
          </>
        )}

        {plan.how === "file" && (
          <>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              data-size="sm"
              disabled={opening}
              onClick={openFile}
            >
              <IconExternal size={14} />
              {opening ? "여는 중입니다" : plan.openLabel}
            </button>
            <p className="origin-part-line meta">{plan.openHint}</p>
          </>
        )}

        {plan.how === "none" && (
          <p className="origin-none">
            <b>원본 링크 없음</b> {plan.noneReason}
          </p>
        )}

        {plan.path && (
          <div className="origin-pathrow">
            <code className="origin-path">{plan.path}</code>
            <button
              type="button"
              className="btn"
              data-variant="quiet"
              data-size="sm"
              onClick={copy}
            >
              <IconCopy size={13} />
              경로 복사
            </button>
          </div>
        )}

        {said && (
          <span className="copy-said" data-ok={said.ok} role="status">
            {said.text}
          </span>
        )}
      </section>
    </div>
  );
}
