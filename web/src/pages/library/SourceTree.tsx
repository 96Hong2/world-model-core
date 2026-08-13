// 서재 왼쪽의 폴더 트리.
//
// 자료가 270종이라 평평한 목록으로는 찾을 수 없다. 칩 필터가 있었지만 칩은 "무엇이 있는지"를
// 보여주지 못한다 — 엔지니어링 136종 안에 무슨 종류가 있는지 알려면 눌러 봐야 했다.
//
// 폴더는 두 단이다: **그룹 → 자료 종류**. 세 단으로 만들지 않는다. 자료 자체는 오른쪽
// 목록이 받고, 트리는 "어디를 볼지 고르는 곳"으로만 둔다.
//
// 개수를 모든 가지에 적는다. 빈 가지를 눌러 빈 목록을 보는 일이 없게.

import { IconChevron } from "../../shell/icons";

export interface TreeType {
  name: string;
  name_ko: string;
  count: number;
  group: string;
}

interface Props {
  total: number;
  groups: { name: string; count: number }[];
  types: TreeType[];
  /** 지금 고른 가지 */
  group: string;
  sourceType: string;
  /** 펼친 그룹 이름들 */
  openGroups: string[];
  onToggleGroup: (name: string) => void;
  onPickGroup: (name: string) => void;
  onPickType: (name: string) => void;
  onPickAll: () => void;
}

export function SourceTree({
  total,
  groups,
  types,
  group,
  sourceType,
  openGroups,
  onToggleGroup,
  onPickGroup,
  onPickType,
  onPickAll,
}: Props) {
  const all = !group && !sourceType;

  return (
    <nav className="tree" aria-label="자료 폴더">
      <button
        type="button"
        className="tree-row"
        data-depth="0"
        aria-current={all || undefined}
        onClick={onPickAll}
      >
        <span className="tree-caret" aria-hidden="true" />
        <span className="tree-name">전체 자료</span>
        <span className="tree-count">{total}</span>
      </button>

      {groups.map((g) => {
        const open = openGroups.includes(g.name);
        const kids = types.filter((t) => t.group === g.name);
        return (
          <div key={g.name}>
            <div className="tree-line">
              <button
                type="button"
                className="tree-caret-btn"
                aria-label={`${g.name} ${open ? "접기" : "펼치기"}`}
                aria-expanded={open}
                onClick={() => onToggleGroup(g.name)}
              >
                <span className="tree-caret" data-open={open}>
                  <IconChevron size={13} />
                </span>
              </button>
              <button
                type="button"
                className="tree-row"
                data-depth="1"
                aria-current={(group === g.name && !sourceType) || undefined}
                onClick={() => onPickGroup(g.name)}
              >
                <span className="tree-name">{g.name}</span>
                <span className="tree-count">{g.count}</span>
              </button>
            </div>

            {open &&
              kids.map((t) => (
                <button
                  key={t.name}
                  type="button"
                  className="tree-row"
                  data-depth="2"
                  aria-current={sourceType === t.name || undefined}
                  onClick={() => onPickType(t.name)}
                >
                  <span className="tree-caret" aria-hidden="true" />
                  <span className="tree-name">{t.name_ko}</span>
                  <span className="tree-count">{t.count}</span>
                </button>
              ))}
          </div>
        );
      })}
    </nav>
  );
}
