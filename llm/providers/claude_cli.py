"""로컬 Claude CLI 를 subprocess 로 호출하는 provider.

이 환경에는 API 키가 없어서 기본 provider 다. 실측한 호출 형태:

    claude -p --model <model> --strict-mcp-config --setting-sources '' --output-format json

프롬프트는 argv 가 아니라 stdin 으로 넣는다. 길이 제한과 셸 인용 문제를 피하고
한국어 같은 비 ASCII 를 안전하게 넘기려는 것이다(둘 다 실측으로 확인했다).

중립 cwd 에서 돌린다. 프로젝트 폴더에서 부르면 그 프로젝트 컨텍스트가 프롬프트에 딸려와
비용이 늘고 결과가 오염된다.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from typing import Any

from ..errors import LLMConfigError, LLMTimeoutError, LLMTransientError
from ..provider import LLMProvider
from ..tiers import TierConfig
from ..types import LLMResult

DEFAULT_TIMEOUT_SECONDS = 180.0


class ClaudeCLIProvider(LLMProvider):
    id = "claude_cli"

    def __init__(
        self,
        tiers: TierConfig | None = None,
        *,
        binary: str = "claude",
        cwd: str | None = None,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._tiers = tiers or TierConfig.load()
        self.binary = binary
        self.cwd = cwd or tempfile.gettempdir()
        self.default_timeout = float(default_timeout)

    def build_argv(self, *, tier: str) -> list[str]:
        return [
            self.binary,
            "-p",
            "--model",
            self._tiers.model_for(tier),
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--output-format",
            "json",
        ]

    def complete(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        tier: str = "S",
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        argv = self.build_argv(tier=tier)
        limit = float(timeout) if timeout else self.default_timeout

        stdout, stderr, returncode = self._run(argv, prompt, limit)

        if returncode != 0:
            raise LLMTransientError(
                f"claude CLI 종료코드 {returncode}: {stderr.strip()[:500]}"
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise LLMTransientError(
                f"claude CLI 출력이 JSON 이 아니다: {exc.msg} / 앞부분={stdout[:300]!r}"
            ) from exc

        if payload.get("is_error"):
            raise LLMTransientError(
                f"claude CLI 오류 응답: subtype={payload.get('subtype')} "
                f"api_error_status={payload.get('api_error_status')}"
            )

        usage = payload.get("usage") or {}
        return LLMResult(
            text=payload.get("result") or "",
            parsed=None,
            model=self._resolve_model(payload, tier),
            cost_usd=float(payload.get("total_cost_usd") or 0.0),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_hit=False,
            attempts=1,
            provider=self.id,
            latency_ms=int(payload.get("duration_ms") or 0),
        )

    # ------------------------------------------------------------------
    def _run(self, argv: list[str], prompt: str, limit: float) -> tuple[str, str, int]:
        """타임아웃이면 프로세스 그룹째 정리한다. 손자 프로세스가 남으면 CPU 를 계속 먹는다."""
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise LLMConfigError(
                f"claude 실행 파일을 찾을 수 없다: {self.binary!r}. PATH 를 확인해라."
            ) from exc

        try:
            out, err = process.communicate(prompt.encode("utf-8"), timeout=limit)
        except subprocess.TimeoutExpired:
            self._kill_group(process)
            try:
                out, err = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
            raise LLMTimeoutError(f"claude CLI 가 {limit:.0f}초 안에 끝나지 않았다")

        return (
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
            process.returncode,
        )

    @staticmethod
    def _kill_group(process: subprocess.Popen) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()

    def _resolve_model(self, payload: dict[str, Any], tier: str) -> str:
        model_usage = payload.get("modelUsage") or {}
        if len(model_usage) == 1:
            return next(iter(model_usage))
        return self._tiers.model_for(tier)
