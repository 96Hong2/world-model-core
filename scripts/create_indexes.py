"""조회 인덱스를 만든다. 한 번만 돌리면 된다.

    .venv/bin/python scripts/create_indexes.py          만든다
    .venv/bin/python scripts/create_indexes.py --show   지금 있는 것을 본다
    .venv/bin/python scripts/create_indexes.py --drop    지운다(되돌리기)

왜 필요한가. 이 그래프에는 **인덱스가 하나도 없었다.** 그래서 화면이 쓰는 조회가 전부
전체 스캔이었다. 실측:

    고객사 50개 한 페이지          44초
    자료 한 건 상세                11초
    수집 실행 상세                 43초

`natural_key` 로 노드를 찾는 것이 조회의 대부분인데 인덱스가 없어 노드 3만 개를 매번
훑었다. 인덱스는 **데이터를 바꾸지 않는다** — 같은 결과를 더 빨리 찾게만 한다.
그래서 답변 경로의 결과도 달라지지 않는다(발췌 조회만 빨라진다).

되돌리려면 `--drop` 이다. 인덱스를 지워도 데이터는 그대로다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 저장소 루트를 경로에 넣는다. `python scripts/...` 로 바로 돌릴 수 있게.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graph.connection import writable_graph  # noqa: E402

#: `natural_key` 로 찾는 라벨. 화면이 대상 하나를 열 때마다 쓰는 값이다.
ENTITY_LABELS = (
    "BusinessDomain",
    "Account",
    "Need",
    "Capability",
    "Feature",
    "Product",
    "Industry",
    "Deal",
    "Competitor",
    "Event",
    "Claim",
    "Observation",
    "Source",
)

#: (인덱스 이름, 라벨, 속성)
def _plan() -> list[tuple[str, str, str]]:
    out = [(f"idx_{label.lower()}_natural_key", label, "natural_key") for label in ENTITY_LABELS]
    out += [
        ("idx_source_source_id", "Source", "source_id"),
        ("idx_evidence_evidence_id", "Evidence", "evidence_id"),
        ("idx_claim_claim_id", "Claim", "claim_id"),
        ("idx_source_pipeline_run", "Source", "pipeline_run_id"),
        ("idx_claim_pipeline_run", "Claim", "pipeline_run_id"),
        ("idx_account_canonical_name", "Account", "canonical_name"),
        ("idx_businessdomain_name", "BusinessDomain", "name"),
        ("idx_need_name", "Need", "name"),
    ]
    return out


def show() -> int:
    graph = writable_graph()
    try:
        with graph.read_session() as session:
            rows = list(
                session.run(
                    "SHOW INDEXES YIELD name, labelsOrTypes, properties, type, state "
                    "RETURN name, labelsOrTypes, properties, type, state ORDER BY name"
                )
            )
        if not rows:
            print("인덱스가 없습니다.")
            return 0
        for r in rows:
            print(
                f"  {r['name']:<36} {str(r['labelsOrTypes']):<20} "
                f"{str(r['properties']):<22} {r['type']} {r['state']}"
            )
        print(f"\n모두 {len(rows)}개")
        return 0
    finally:
        graph.close()


def create() -> int:
    graph = writable_graph()
    plan = _plan()
    try:
        with graph.write_session() as session:
            for name, label, prop in plan:
                session.run(
                    f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
                )
            print(f"인덱스 {len(plan)}개 생성 요청. 채워지는 것을 기다립니다...")

        # ONLINE 이 될 때까지 기다린다. 만들자마자 조회하면 아직 POPULATING 이라 느리다.
        deadline = time.time() + 180
        while time.time() < deadline:
            with graph.read_session() as session:
                pending = [
                    r["name"]
                    for r in session.run(
                        "SHOW INDEXES YIELD name, state WHERE state <> 'ONLINE' RETURN name"
                    )
                ]
            if not pending:
                print("전부 ONLINE 이 됐습니다.")
                return 0
            time.sleep(2)
        print(f"아직 채워지는 중인 인덱스가 있습니다: {pending}", file=sys.stderr)
        return 1
    finally:
        graph.close()


def drop() -> int:
    graph = writable_graph()
    try:
        with graph.write_session() as session:
            for name, _, _ in _plan():
                session.run(f"DROP INDEX {name} IF EXISTS")
        print("이 스크립트가 만든 인덱스를 지웠습니다. 데이터는 그대로입니다.")
        return 0
    finally:
        graph.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/create_indexes.py")
    parser.add_argument("--show", action="store_true", help="지금 있는 인덱스를 본다")
    parser.add_argument("--drop", action="store_true", help="이 스크립트가 만든 것을 지운다")
    args = parser.parse_args(argv)

    if args.show:
        return show()
    if args.drop:
        return drop()
    return create()


if __name__ == "__main__":
    raise SystemExit(main())
