"""FastAPI 앱.

    POST /login          고정 계정 로그인 → 토큰
    POST /ask            질문 → Answer JSON
    POST /brief          Answer JSON → 읽는 순서로 정리한 Brief (결론·핵심 주장·할 일)
    GET  /graph/expand   노드 1-hop 확장
    GET  /health         그래프·LLM 준비 상태
    GET  /golden         데모 화면의 질문 칩 목록

인증은 선택이다. 토큰이 없으면 기본 계정으로 답한다. eval 하네스와 데모 화면이 로그인
없이도 붙어야 하고, 1A 의 접근 통제는 per-user RBAC 이 아니라 sensitivity 한 겹이기 때문이다
(REVISED §0 29행 · UAA 연동은 V2).

    .venv/bin/python -m uvicorn api.main:app --port 8099
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from graph.connection import read_only_graph

from .accounts import ACCOUNTS, TokenStore, authenticate, default_account
from .service import AnswerService

LLM_DISABLED_ENV = "BWM_LLM"
#: 누적 LLM 비용 상한을 이 실행에서만 올릴 때 쓴다. 기본값은 `llm.service.DEFAULT_BUDGET_USD`
#: 그대로다 — 상한을 코드에서 올리지 않는다. 상한에 닿으면 /ask 가 500 이 되므로, 검증을
#: 위해 한 번 더 태워야 할 때 이 환경변수로 그 사실을 밖에 드러낸 채 올린다.
LLM_BUDGET_ENV = "BWM_LLM_BUDGET_USD"


def _build_synthesizer():
    """LLM 합성기. 끄면(BWM_LLM=off) 결정적 합성기로 내려간다.

    LLMService 를 함께 돌려준다. 정리기(`/brief`)가 **같은 인스턴스**를 써야 비용 원장과
    예산 상한이 한 곳에 쌓인다. 정리기가 자기 서비스를 새로 만들면 상한이 두 배가 된다.
    """
    if os.getenv(LLM_DISABLED_ENV, "on").lower() in {"off", "0", "false"}:
        from .synthesis import DeterministicSynthesizer

        return DeterministicSynthesizer(), "deterministic", None

    from llm import LLMCache, LLMService
    from llm.providers import ClaudeCLIProvider
    from llm.service import DEFAULT_BUDGET_USD, DEFAULT_CACHE_DIR

    from .synthesis import LLMSynthesizer

    budget = float(os.getenv(LLM_BUDGET_ENV) or DEFAULT_BUDGET_USD)
    provider = ClaudeCLIProvider()
    service = LLMService(provider, cache=LLMCache(DEFAULT_CACHE_DIR), budget_usd=budget)
    return LLMSynthesizer(service), provider.id, service


def create_app() -> FastAPI:
    app = FastAPI(title="Business World Model — Answer API", version="1A")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 사내 데모 전용. 배포 시 좁힌다.
        allow_methods=["*"],
        allow_headers=["*"],
    )

    graph = read_only_graph()
    synthesizer, provider_id, llm_service = _build_synthesizer()
    service = AnswerService(graph, synthesizer=synthesizer)
    tokens = TokenStore()

    def resolve(authorization: str | None):
        if not authorization:
            return default_account()
        token = authorization.removeprefix("Bearer ").strip()
        account = tokens.resolve(token)
        if account is None:
            raise HTTPException(status_code=401, detail="알 수 없는 토큰입니다.")
        return account

    @app.get("/health")
    def health() -> dict[str, Any]:
        try:
            with graph.session() as session:
                counts = session.run(
                    "MATCH (n) WITH count(n) AS nodes "
                    "MATCH ()-[r]->() RETURN nodes, count(r) AS edges"
                ).single()
            graph_state = {"ok": True, "nodes": counts["nodes"], "edges": counts["edges"]}
        except Exception as exc:  # noqa: BLE001 - 상태 화면이므로 원인을 그대로 보여준다
            graph_state = {"ok": False, "error": str(exc)[:200]}
        return {
            "status": "ok" if graph_state.get("ok") else "degraded",
            "graph": graph_state,
            "synthesizer": provider_id,
            "accounts": sorted(ACCOUNTS),
        }

    @app.post("/login")
    def login(payload: dict = Body(...)) -> dict[str, Any]:
        account = authenticate(payload.get("username", ""), payload.get("password", ""))
        if account is None:
            raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 맞지 않습니다.")
        return {
            "token": tokens.issue(account),
            "username": account.username,
            "display_name": account.display_name,
        }

    @app.post("/ask")
    def ask(
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        question = (payload.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question 이 비어 있습니다.")
        account = resolve(authorization)
        return service.answer(question, account=account, scenario=payload.get("scenario"))

    @app.post("/brief")
    def brief(
        payload: dict = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Answer 를 읽는 순서대로 다시 배치한다(결론 → 핵심 주장 → 할 일).

        `/ask` 와 나눠 둔 이유는 `api/distill.py` 모듈 설명에 있다: Answer 계약이 루트에
        추가 필드를 금지하고, 계약 파일은 고치지 않는다.

        답변 payload 를 그대로 받는다. 서버가 상태를 들고 있지 않아도 되고, 근거 검사에 쓸
        발췌가 그 안에 이미 다 있다. 정리기는 그래프를 보지 않는다.
        """
        question = (payload.get("question") or "").strip()
        answer = payload.get("answer")
        if not isinstance(answer, dict):
            raise HTTPException(status_code=400, detail="answer 가 없습니다.")
        resolve(authorization)
        from .distill import distill as distill_answer

        return distill_answer(question, answer, llm_service=llm_service)

    @app.get("/graph/expand")
    def expand(
        node_id: str = Query(...),
        hops: int = Query(default=1),
        already: int = Query(default=0),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = resolve(authorization)
        try:
            return service.expand(node_id, account=account, hops=hops, already=already)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 둘러보기·서재·변경·상태 화면이 쓰는 조회 라우트. 답변 경로와 분리된 별도 모듈이다.
    from .browse import build_router as build_browse_router
    from .browse import warm as warm_browse
    from .browse import warm_lists as warm_browse_lists
    from .browse import warm_runs as warm_browse_runs

    browse_router = build_browse_router(graph, resolve)
    app.include_router(browse_router)

    @app.on_event("startup")
    def _warm_browse() -> None:
        """서재·변경 화면의 집계표를 미리 만든다.

        노드 3만 개를 한 번 훑어야 나오는 값이라 첫 요청이 40초대였다(실측).
        백그라운드 스레드에서 만들어 서버 기동을 막지 않는다.
        """
        import threading

        def prepare() -> None:
            warm_browse(graph)
            warm_browse_runs(browse_router.fetch_runs, browse_router.fetch_run)
            warm_browse_lists(browse_router.fetch_overview, browse_router.fetch_entities)

        threading.Thread(target=prepare, daemon=True).start()

    @app.get("/golden")
    def golden() -> dict[str, Any]:
        from eval.golden_loader import load_golden

        loaded = load_golden()
        return {
            "version": loaded.version,
            "questions": [
                {
                    "id": q["id"],
                    "question": q["question"],
                    "type": q.get("type", ""),
                    "expected_route": q.get("expected_route", ""),
                }
                for q in loaded.questions
            ],
        }

    return app


app = create_app()
