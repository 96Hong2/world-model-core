// 그래프 범례.
//
// 개선 전에는 3줄 12칩이 116px 을 먹어 도면 높이를 그만큼 잡아먹었다. 지금은 계열 여섯 개만
// 한 줄로 보이고, 자세한 뜻은 눌러서 펼친다.
//
// 계열이 여섯 개인 이유: 노드 종류 14개마다 색을 주면 무지개가 된다. 판은 중성색으로 두고
// 왼쪽 세로 띠로 계열만 구분한다. 정확한 종류는 노드에 글자로 적혀 있다.

import { useEffect, useRef, useState } from "react";
import { TYPE_KO } from "./adapter";
import type { EntityType } from "./adapter";

interface Family {
  key: string;
  name: string;
  cssVar: string;
  types: EntityType[];
}

const FAMILIES: Family[] = [
  {
    key: "market",
    name: "사업·시장",
    cssVar: "--fam-market",
    types: ["BusinessDomain", "Industry"],
  },
  {
    key: "customer",
    name: "고객·거래",
    cssVar: "--fam-customer",
    types: ["Account", "Deal", "Competitor"],
  },
  { key: "need", name: "요구", cssVar: "--fam-need", types: ["Need"] },
  {
    key: "build",
    name: "역량·기능",
    cssVar: "--fam-build",
    types: ["Capability", "Feature", "Product"],
  },
  {
    key: "knowledge",
    name: "근거·자료",
    cssVar: "--fam-knowledge",
    types: ["Claim", "Observation", "Source"],
  },
  {
    key: "actor",
    name: "사건·사람",
    cssVar: "--fam-actor",
    types: ["Event", "Persona"],
  },
];

export function Legend() {
  const [open, setOpen] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);

  // 바깥을 누르거나 Esc 로 닫는다. 닫는 방법을 하나만 두지 않는다.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!hostRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="legend" ref={hostRef}>
      <div className="legend-line">
        {FAMILIES.map((f) => (
          <span className="legend-chip" key={f.key} title={f.types.join(" · ")}>
            <i
              className="legend-swatch"
              style={{ background: `var(${f.cssVar})` }}
            />
            {f.name}
          </span>
        ))}
      </div>

      <button
        type="button"
        className="legend-more"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "범례 닫기" : "범례"}
      </button>

      {open && (
        <div className="legend-panel" role="dialog" aria-label="그래프 범례">
          <div className="legend-group">
            <h3>노드 계열</h3>
            <div className="legend-grid">
              {FAMILIES.map((f) => (
                <div className="legend-row" key={f.key}>
                  <i
                    className="legend-swatch"
                    style={{
                      background: `var(${f.cssVar})`,
                      width: 4,
                      height: 16,
                    }}
                  />
                  <span>
                    {f.name}
                    <span className="meta">
                      {" "}
                      {f.types.map((t) => TYPE_KO[t]).join(" · ")}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="legend-group">
            <h3>연결선</h3>
            <div className="legend-grid">
              <div className="legend-row">
                <i className="legend-sample" data-kind="solid" />
                실선: 정본 자료로 확인됨
              </div>
              <div className="legend-row">
                <i className="legend-sample" data-kind="dashed" />
                점선: 아직 후보·미검증
              </div>
              <div className="legend-row">
                <i className="legend-sample" data-kind="critical" />
                굵은 붉은 선: 놓치면 안 되는 항목
              </div>
              <div className="legend-row">
                <i className="legend-sample" data-kind="conflict" />
                이중선: 자료끼리 어긋남
              </div>
            </div>
            <p className="legend-note">
              관계 이름은 평소에 숨겨 둡니다. 답변이 인용한 관계와, 마우스를
              올리거나 고른 관계에만 나타납니다.
            </p>
          </div>

          <div className="legend-group">
            <h3>노드 크기와 표시</h3>
            <div className="legend-grid">
              <div className="legend-row">
                <i className="legend-box" style={{ width: 26, height: 16 }} />
                가장 큼: 질문의 중심
              </div>
              <div className="legend-row">
                <i className="legend-box" style={{ width: 22, height: 14 }} />
                왼쪽 위 번호: 답변이 인용한 근거
              </div>
              <div className="legend-row">
                <i
                  className="legend-box"
                  style={{ width: 18, height: 12, opacity: 0.6 }}
                />
                작고 흐림: 맥락을 잇는 보조
              </div>
            </div>
            <p className="legend-note">
              자료의 권위(Authority)는 노드 크기에 쓰지 않습니다. 근거를 눌러
              여는 상세 패널에서 등급으로 봅니다. 상태는 색과 함께 오른쪽 위
              표시 모양으로도 알립니다.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
