"""모델 응답에서 JSON 을 꺼내는 공통 계층.

claude CLI 는 응답을 ```json 펜스로 감싸서 돌려준다. provider 마다 따로 벗기면
provider 를 바꿀 때마다 같은 버그를 다시 만든다. 그래서 여기 한 곳에서만 벗긴다.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(
    r"```[ \t]*[A-Za-z0-9_+.\-]*[ \t]*\r?\n?(.*?)```",
    re.DOTALL,
)


def strip_code_fence(text: str) -> str:
    """펜스 안의 내용만 남긴다. 펜스가 없으면 앞뒤 공백만 털어 그대로 돌려준다.

    펜스 앞뒤에 설명 문장이 붙어 와도 첫 번째 블록을 꺼낸다.
    """
    if not text:
        return ""
    match = _FENCE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _slice_outermost(text: str) -> str | None:
    """중괄호/대괄호의 바깥 짝을 잘라낸다. 앞뒤에 잡소리가 붙은 응답 구제용."""
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return None
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        return None
    return text[start : end + 1]


def parse_json_payload(text: str) -> tuple[Any | None, str | None]:
    """(파싱 결과, 오류 메시지) 를 돌려준다. 실패해도 예외를 던지지 않는다."""
    candidate = strip_code_fence(text)
    if not candidate:
        return None, "응답이 비어 있다"

    try:
        return json.loads(candidate), None
    except json.JSONDecodeError as exc:
        first_error = f"JSON 파싱 실패: {exc.msg} (line {exc.lineno} col {exc.colno})"

    sliced = _slice_outermost(candidate)
    if sliced is not None and sliced != candidate:
        try:
            return json.loads(sliced), None
        except json.JSONDecodeError:
            pass

    return None, first_error
