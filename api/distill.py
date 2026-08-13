"""답변 정리기 (Answer Distiller).

    질문 → 검색·그래프 → 합성 → 하드 게이트 → Answer 계약 → **여기** → Brief → 화면

이 단계가 하는 일은 하나다. **이미 검증을 통과한 답변을 다시 배치하는 것.**
결론을 맨 위로 올리고, 주장과 근거를 짝지어 3~5개로 줄이고, 할 일을 뽑아낸다.
사실을 더하지 않는다. 그래서 검색·그래프·게이트에는 손대지 않는다.

**왜 응답을 나눴나.** Answer 계약(`contracts/answer.schema.json`)은 루트에
`additionalProperties: false` 다. 정리 결과를 그 안에 넣으면 계약 위반이 되고, 계약 파일은
고치지 않는다. 그래서 `POST /brief` 로 갈랐다. 화면은 `/ask` 뒤에 이어서 부른다.

**"새 사실을 만들지 마라"를 프롬프트로만 부탁하지 않는다.** 정리기 출력도 답변과 똑같은
하드 게이트를 다시 통과한다: PII · 무근거 숫자·고유명사 · 엉뚱한 대상에 붙인 값. 근거 집합도
같다(답변에 실린 발췌 + 참고 신호 + 질문 문장). 게이트가 문장을 지우면 그 핵심 포인트를
버린다. 정리 단계가 게이트의 구멍이 되면, 앞의 세 겹이 지키던 것이 마지막에 새어 나간다.

각주도 코드가 지킨다. 모델이 각주 번호를 적어 오게 하지 않고, 정리된 문장에 남아 있는
`[n]` 을 서버가 세어 `citation_markers` 를 만든다. 답변에 없던 번호는 문장에서 지운다.
모델이 옮겨 적을 일이 없어야 옮겨 적다 틀리는 일도 없다(§ synthesis 가 같은 이유로 발췌
메타데이터를 프롬프트에서 뺐다).

**번호가 빠진 문장에는 되붙인다(AC-4 근거 추적 100%).** 실측에서 이게 필요했다: 같은 답을
두 번 정리했을 때 본문 각주가 13개에서 5개로 줄었다. 값은 그대로 남았는데 번호만 사라진
것이라, 정리본만 읽는 사람은 그 값의 출처로 갈 수 없었다. 조립 단계와 **같은 되붙이기**
(`anchoring.anchor_orphan_tokens`)를 돌려 그 값을 담은 발췌의 **이미 있는** 번호를 붙인다.
새 인용을 만들지 않으므로 `citations` 목록은 답변 것 그대로다.

LLM 이 없거나 실패해도 화면은 답을 받는다. 그때는 `mode="raw"` 로 내려가 원문을 그대로
싣고, 왜 정리하지 못했는지 `note` 에 적는다. 정리 실패가 조용한 빈 화면으로 둔갑하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .anchoring import anchor_orphan_tokens, orphan_token_sentences
from .guards import (
    GuardReport,
    drop_markers,
    find_pii,
    scrub_list,
    scrub_prose,
    split_sentences,
    surviving_markers,
)
from .notation import restore_notation
from .synthesis import to_korean_labels

#: 화면이 한 번에 읽을 수 있는 핵심 주장 수. 넘으면 다시 훑어야 하는 목록이 된다.
MAX_KEY_POINTS = 5
#: 결론 요약 문장 수 상한. 2~4문장을 요구하고, 넘으면 뒤를 자른다.
MAX_CONCLUSION_SENTENCES = 4
MAX_ACTIONS = 6
MAX_CAVEATS = 3
#: raw 모드에서 결론 자리에 올릴 문장 수. 원문 첫머리가 곧 결론이라는 가정이 아니라,
#: "무엇이든 위에 두 문장은 보인다"를 보장하는 최소선이다.
RAW_CONCLUSION_SENTENCES = 2

TIER = "S"
MAX_TOKENS = 1200

DISTILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["conclusion", "key_points"],
    "properties": {
        "conclusion": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary"],
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string", "minLength": 1},
            },
        },
        "key_points": {
            "type": "array",
            "maxItems": MAX_KEY_POINTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "claim"],
                "properties": {
                    "title": {"type": "string"},
                    "claim": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
        "action_sequence": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
}


PROMPT = """너는 이미 검증을 통과한 답변을 **읽기 쉽게 다시 배치**하는 정리기다.
새 사실을 만들지 마라. 아래 [답변 원문]과 [권고]에 있는 내용만 쓴다.

[질문]
{question}

[답변 원문 — 여기 없는 내용은 쓸 수 없다]
{answer_text}

[권고]
{recommendation}

[다음에 확인할 것]
{next_actions}

[아직 모르는 것]
{unknowns}

[판정 — 공백과 어긋남]
{findings}

[만들 것]
1. conclusion.summary: 질문에 대한 답을 **2~4문장**으로. 배경 설명으로 시작하지 마라. 결론부터 말한다.
2. conclusion.title: 그 결론을 한 줄로 줄인 제목. 20자 이내.
3. key_points: 결론을 받치는 핵심 주장 3~5개. 원문에 그만큼의 주장이 없으면 있는 만큼만 적어라.
   - title: 12자 이내의 짧은 명사구
   - claim: 무슨 일인지 한 문장
   - reason: 그래서 무엇을 뜻하는지 한두 문장. 원문에 그 판단이 없으면 빈 문자열로 둬라
4. action_sequence: 원문이 제안 순서나 우선순위를 말하고 있으면 그 순서를 항목당 **15자 이내**로. 없으면 빈 배열
5. next_actions: 사용자가 다음에 할 일. 항목당 **한 줄(30자 안팎)** 로 짧게 끊어라. 한 항목에 두 가지 일을 담지 마라. [다음에 확인할 것] 을 쓰거나 원문에서 뽑아라
6. caveats: 결론을 뒤집을 만한 불확실성만 한두 개. 없으면 빈 배열

[규칙 — 어기면 그 문장은 통째로 삭제된다]
- 원문에 없는 숫자·회사명·제품명·영문 약어를 쓰지 마라. 정리는 골라내는 일이고 채우는 일이 아니다.
- 금액·날짜·기간·버전·이름은 **원문 표기 그대로** 옮겨라. 반올림·형식 변경·다른 말로 바꿔 부르기 금지.
- 각주 번호 `[3]` 은 원문에서 그 문장에 붙어 있던 번호를 그대로 유지해라. **번호를 새로 만들거나 다른 문장으로 옮겨 붙이지 마라.**
- 한 문장에 논리를 여러 개 담지 마라. 짧게 끊어라.
- 같은 내용을 conclusion 과 key_points 에 두 번 쓰지 마라. conclusion 은 결론이고 key_points 는 그 근거다.
- 원문이 말하지 않은 판단·추천을 만들지 마라. 없으면 비워 둔다.
- Capability·Need·Gap·CONFIRMED 같은 영어 분류어를 쓰지 마라.

JSON 만 출력해라. 형식:
{{"conclusion": {{"title": "...", "summary": "..."}}, "key_points": [{{"title": "...", "claim": "...", "reason": "..."}}], "action_sequence": [], "next_actions": [], "caveats": []}}"""


class AnswerDistiller:
    """정리기. LLM 이 없으면 원문을 그대로 싣는 raw 모드로 내려간다."""

    def __init__(self, llm_service: Any | None = None):
        self._llm = llm_service

    # ------------------------------------------------------------------
    def distill(self, question: str, answer: dict[str, Any]) -> dict[str, Any]:
        body = answer.get("answer") or {}
        raw_text = str(body.get("text") or "")
        raw_reco = str(body.get("recommendation") or "")

        if self._llm is None:
            return self._raw_brief(answer, note="정리기가 꺼져 있어 답변 원문을 그대로 싣습니다.")

        prompt = self.build_prompt(question, answer)
        try:
            outcome = self._llm.complete(
                prompt,
                schema=DISTILL_SCHEMA,
                tier=TIER,
                purpose="answer_distill",
                max_tokens=MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 - 정리 실패가 답변까지 막으면 안 된다
            return self._raw_brief(answer, note=f"정리하지 못했습니다: {str(exc)[:160]}")

        if not outcome.ok or not isinstance(outcome.parsed, dict):
            return self._raw_brief(
                answer, note=f"정리하지 못했습니다: {(outcome.error or '형식 오류')[:160]}"
            )

        brief = self._verify(question, answer, outcome.parsed)
        brief["meta"] = {
            "llm_used": True,
            "cost_usd": round(getattr(outcome, "cost_usd", 0.0) or 0.0, 6),
            "cache_hit": bool(getattr(outcome, "cache_hit", False)),
            "dropped": brief["meta"]["dropped"],
            "untraced": brief["meta"]["untraced"],
        }
        # 결론이 게이트에서 통째로 지워졌으면 정리본을 내보내지 않는다. 빈 결론은
        # 원문보다 나쁘다 — 사용자가 답이 없다고 읽는다.
        if not brief["conclusion"]["summary"].strip():
            fallback = self._raw_brief(
                answer, note="정리한 결론이 근거 검사를 통과하지 못해 원문을 싣습니다."
            )
            fallback["meta"] = brief["meta"]
            return fallback

        brief["raw_text"] = raw_text
        brief["raw_recommendation"] = raw_reco
        return brief

    # ------------------------------------------------------------------
    def build_prompt(self, question: str, answer: dict[str, Any]) -> str:
        body = answer.get("answer") or {}
        return PROMPT.format(
            question=question or "(없음)",
            answer_text=str(body.get("text") or "(없음)"),
            recommendation=str(body.get("recommendation") or "(없음)"),
            next_actions=_bullets(answer.get("next_actions")),
            unknowns=_bullets(answer.get("unknowns")),
            findings=_findings(answer),
        )

    # ------------------------------------------------------------------
    # 검사 — 답변과 같은 게이트를 다시 통과시킨다
    # ------------------------------------------------------------------
    def _verify(
        self, question: str, answer: dict[str, Any], parsed: dict[str, Any]
    ) -> dict[str, Any]:
        rows = _support_rows(answer)
        support_text = "\n".join(rows)
        cited = _cited_refs(answer)
        valid = {ref.marker for ref in cited}
        report = GuardReport()

        def clean(text: Any) -> str:
            out = restore_notation(str(text or ""), rows)
            out = _keep_only(out, valid)
            return scrub_prose(
                out, support_text, question, report, location="brief", snippets=rows
            ).strip()

        def clean_prose(text: Any) -> str:
            """읽는 문장용. 검사를 통과시킨 뒤 빠진 각주를 되붙인다.

            제목에는 쓰지 않는다. 12자 짧은 명사구에 각주 번호가 붙으면 라벨이 아니라
            문장처럼 보이고, 그 번호는 화면의 각주 목록에도 들어가지 않는다.
            """
            return _reanchor(clean(text), question, cited)

        raw_conclusion = parsed.get("conclusion") or {}
        summary = _limit_sentences(
            clean_prose(raw_conclusion.get("summary")), MAX_CONCLUSION_SENTENCES
        )
        title = clean(raw_conclusion.get("title"))

        points: list[dict[str, Any]] = []
        dropped = 0
        for item in parsed.get("key_points") or []:
            if not isinstance(item, dict) or len(points) >= MAX_KEY_POINTS:
                continue
            claim = clean_prose(item.get("claim"))
            if not claim:
                # 게이트가 주장을 지웠다. 근거 없는 주장이라 화면에 올리지 않는다.
                dropped += 1
                continue
            reason = clean_prose(item.get("reason"))
            markers = sorted(surviving_markers(claim) | surviving_markers(reason))
            points.append(
                {
                    "title": clean(item.get("title")),
                    "claim": claim,
                    "reason": reason,
                    "citation_markers": markers,
                }
            )

        sequence = scrub_list(
            [_keep_only(restore_notation(str(x), rows), valid) for x in _texts(parsed.get("action_sequence"))],
            support_text,
            question,
            report,
            location="brief.action_sequence",
            snippets=rows,
        )[:MAX_ACTIONS]
        next_actions = scrub_list(
            [_keep_only(restore_notation(str(x), rows), valid) for x in _texts(parsed.get("next_actions"))],
            support_text,
            question,
            report,
            location="brief.next_actions",
            snippets=rows,
        )[:MAX_ACTIONS]
        caveats = scrub_list(
            [_keep_only(restore_notation(str(x), rows), valid) for x in _texts(parsed.get("caveats"))],
            support_text,
            question,
            report,
            location="brief.caveats",
            snippets=rows,
        )[:MAX_CAVEATS]

        # 되붙인 뒤에도 번호가 없는 문장 수. 0 이어야 한다(AC-4). 숨기지 않고 세어
        # 화면의 「분석 세부정보」로 내보낸다 — 0 이 아니면 그게 다음에 고칠 자리다.
        surface = "\n".join(
            [summary] + [f"{p['claim']}\n{p['reason']}" for p in points]
        )
        untraced = len(orphan_token_sentences(surface, question))

        return {
            "mode": "distilled",
            "conclusion": {"title": title, "summary": summary},
            "key_points": points,
            "actions": {"sequence": sequence, "next": next_actions},
            "caveats": caveats,
            "raw_text": "",
            "raw_recommendation": "",
            "note": "",
            "meta": {
                "llm_used": True,
                "cost_usd": 0.0,
                "cache_hit": False,
                "dropped": dropped,
                "untraced": untraced,
            },
        }

    # ------------------------------------------------------------------
    def _raw_brief(self, answer: dict[str, Any], *, note: str) -> dict[str, Any]:
        """정리하지 못했을 때. 원문을 싣고 앞 두 문장만 결론 자리에 올린다.

        문장을 새로 쓰지 않는다. 화면은 `mode="raw"` 를 보고 "정리 전 원문"이라고 밝힌다.
        """
        body = answer.get("answer") or {}
        text = str(body.get("text") or "")
        lead = " ".join(split_sentences(text.replace("\n", " "))[:RAW_CONCLUSION_SENTENCES])
        return {
            "mode": "raw",
            "conclusion": {"title": "", "summary": _tidy(lead)},
            "key_points": [],
            "actions": {
                "sequence": [],
                "next": [str(x) for x in _texts(answer.get("next_actions"))][:MAX_ACTIONS],
            },
            "caveats": [],
            "raw_text": text,
            "raw_recommendation": str(body.get("recommendation") or ""),
            "note": note,
            "meta": {
                "llm_used": False,
                "cost_usd": 0.0,
                "cache_hit": False,
                "dropped": 0,
                "untraced": 0,
            },
        }


# ----------------------------------------------------------------------
# 도우미
# ----------------------------------------------------------------------
def _texts(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def _bullets(value: Any) -> str:
    items = _texts(value)
    return "\n".join(f"- {x}" for x in items) or "(없음)"


def _findings(answer: dict[str, Any]) -> str:
    """공백·어긋남을 사람 말로 넘긴다. 영어 enum 을 보여 주면 모델이 답에 옮겨 적는다."""
    rows: list[str] = []
    for gap in answer.get("gaps") or []:
        if not isinstance(gap, dict):
            continue
        subject = to_korean_labels(str(gap.get("subject") or ""))
        reason = to_korean_labels(str((gap.get("basis") or {}).get("reason") or ""))
        rows.append(f"- 공백 {subject}: {reason}")
    for dispute in answer.get("disputes") or []:
        if not isinstance(dispute, dict):
            continue
        rows.append(f"- 어긋남 {to_korean_labels(str(dispute.get('subject') or ''))}")
    return ("\n".join(rows) or "(없음)")[:1200]


def _support_rows(answer: dict[str, Any]) -> list[str]:
    """게이트가 근거로 인정하는 발췌. 답변에 실제로 실린 것과 정확히 같아야 한다.

    한 줄에 한 발췌다. 이어 붙인 문자열로는 "어느 발췌에 적힌 값인가"를 볼 수 없어서,
    근거에 있는 값을 다른 대상에 붙이는 오류가 통과한다(§ guards 3-1 과 같은 이유).
    """
    rows: list[str] = []
    for item in answer.get("evidence") or []:
        if isinstance(item, dict) and item.get("snippet"):
            rows.append(str(item["snippet"]))
    for item in answer.get("raw_signals") or []:
        if isinstance(item, dict) and item.get("snippet"):
            rows.append(str(item["snippet"]))
    return rows


@dataclass(frozen=True)
class _CitedRef:
    """되붙이기가 필요한 최소 정보. `anchoring` 은 `.snippet` 만 읽는다."""

    marker: int
    evidence_id: str
    snippet: str


def _cited_refs(answer: dict[str, Any]) -> list[_CitedRef]:
    """답변이 실제로 인용한 발췌. 번호 순서를 그대로 지킨다."""
    by_id = {
        str(e.get("evidence_id")): str(e.get("snippet") or "")
        for e in (answer.get("evidence") or [])
        if isinstance(e, dict)
    }
    refs: list[_CitedRef] = []
    for citation in answer.get("citations") or []:
        if not isinstance(citation, dict):
            continue
        marker = citation.get("marker")
        eid = str(citation.get("evidence_id") or "")
        if not isinstance(marker, int) or eid not in by_id:
            continue
        refs.append(_CitedRef(marker=marker, evidence_id=eid, snippet=by_id[eid]))
    return refs


def _reanchor(text: str, question: str, cited: list[_CitedRef]) -> str:
    """번호가 빠진 문장에 그 값을 담은 발췌의 **이미 있는** 번호를 붙인다.

    새 인용을 만들지 않는다. 어느 발췌도 그 값을 갖고 있지 않으면 문장을 그대로 둔다
    (그 경우는 게이트가 이미 걸렀어야 하는 자리다).
    """
    if not text or not cited or not orphan_token_sentences(text, question):
        return text
    return anchor_orphan_tokens(
        text, cited, question, marker_for=lambda index: cited[index].marker
    )


def _keep_only(text: str, valid: set[int]) -> str:
    """답변에 없는 각주 번호를 지운다. 지어낸 각주는 누를 수 없는 번호가 된다.

    번호를 떼면 `없습니다 .` 처럼 공백과 마침표가 남는다. 화면에 그대로 나가면 오타로
    보이므로 여기서 붙여 준다.
    """
    bad = surviving_markers(text) - valid
    if not bad:
        return text
    return _tidy(drop_markers(text, sorted(bad)))


_LOOSE_PUNCT = re.compile(r"\s+([.,!?)\]}])")


def _tidy(text: str) -> str:
    """빈 자리에 남은 공백을 정리한다. 낱말은 건드리지 않는다."""
    return _LOOSE_PUNCT.sub(r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _limit_sentences(text: str, limit: int) -> str:
    sentences = split_sentences(text.replace("\n", " "))
    if len(sentences) <= limit:
        return text.strip()
    return " ".join(s.strip() for s in sentences[:limit]).strip()


def has_pii(brief: dict[str, Any]) -> list[tuple[str, str]]:
    """정리본 표면 전체를 다시 훑는다. 하드 게이트 1은 노출 0이 아니면 통과가 아니다."""
    surface: list[str] = [
        str(brief.get("conclusion", {}).get("title") or ""),
        str(brief.get("conclusion", {}).get("summary") or ""),
        str(brief.get("raw_text") or ""),
        str(brief.get("raw_recommendation") or ""),
    ]
    for point in brief.get("key_points") or []:
        surface += [str(point.get(k) or "") for k in ("title", "claim", "reason")]
    actions = brief.get("actions") or {}
    surface += _texts(actions.get("sequence")) + _texts(actions.get("next"))
    surface += _texts(brief.get("caveats"))
    hits: list[tuple[str, str]] = []
    for text in surface:
        hits.extend(find_pii(text))
    return hits


def sanitize(brief: dict[str, Any]) -> dict[str, Any]:
    """PII 가 남아 있으면 정리본을 내보내지 않는다.

    답변 쪽 게이트는 문장을 빼는 방식이지만, 여기서 걸리는 것은 곧 **정리기가 원문에 없던
    값을 만들었다**는 뜻이다. 그런 정리본은 부분 수정하지 않고 통째로 버린다.
    """
    hits = has_pii(brief)
    if not hits:
        return brief
    kinds = sorted({kind for kind, _ in hits})
    return {
        "mode": "raw",
        "conclusion": {"title": "", "summary": ""},
        "key_points": [],
        "actions": {"sequence": [], "next": []},
        "caveats": [],
        "raw_text": "",
        "raw_recommendation": "",
        "note": f"정리본에서 민감정보 패턴({', '.join(kinds)})이 검출되어 폐기했습니다.",
        "meta": {
                "llm_used": False,
                "cost_usd": 0.0,
                "cache_hit": False,
                "dropped": 0,
                "untraced": 0,
            },
    }


def distill(
    question: str, answer: dict[str, Any], *, llm_service: Any | None = None
) -> dict[str, Any]:
    """한 번 쓰고 버리는 진입점. 라우트가 이걸 부른다."""
    return sanitize(AnswerDistiller(llm_service).distill(question, answer))


__all__ = [
    "AnswerDistiller",
    "DISTILL_SCHEMA",
    "MAX_KEY_POINTS",
    "distill",
    "has_pii",
    "sanitize",
]
