"""Slack 4채널 1회 덤프 파서.

메시지 1건 = Evidence 1건이 기본이다. 500자를 넘으면 1단 불릿 경계에서 이어지는 조각을
`#2` 로 붙인다(원문은 한 글자도 버리지 않는다).

locator
    최상위 메시지  slack:<channel_id>/<ts>
    쓰레드 답글    slack:<channel_id>/<thread_ts>/<ts>   ← 부모를 좌표 안에 담는다

구조 분해는 실측한 서식에만 적용한다. 안 맞는 메시지는 structured 를 비워 두고
A4 의 추출 대상으로 넘긴다. 억지로 끼워 맞추면 없는 사실이 생긴다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ingestion.parsers.doc_common import (
    Unit,
    build_evidences,
    build_source_record,
    mask_text,
    normalize_text,
)

# --------------------------------------------------------------------------
# 미팅노트 서식 (#all-영업활동공유, slack-inventory §3)
# --------------------------------------------------------------------------
TITLE_RE = re.compile(r"^\*?\s*\[(?P<title>.+?)\]\s*\*?$")
SECTION_RE = re.compile(r"^\*?\s*\[(?P<section>[^\[\]]+)\]\s*\*?:?\s*$")
FIELD_RE = re.compile(r"^[•\-]\s*(?P<label>일시|참석자|대상자|장소|참석|일자)\s*:\s*(?P<value>.+)$")
BULLET_RE = re.compile(r"^[•]\s*(?P<text>.+)$")
SUBBULLET_RE = re.compile(r"^\s+[◦▪·•\-]\s*(?P<text>.+)$")
# 제목 구분자는 hyphen 과 en-dash 2종이다(slack-inventory T2)
TITLE_SPLIT_RE = re.compile(r"\s+[-–—]\s+")

SECTION_CONTENT = {
    "미팅내용",
    "cc내용",
    "내용",
    "주요내용",
    "진행내용",
    "진행상황",
}
SECTION_ACTION = {"actionitem", "actionitems", "acitonitem", "acitonitems"}

# --------------------------------------------------------------------------
# 리드 폼 서식 (#리드등록알림, slack-inventory §2.3)
# --------------------------------------------------------------------------
F1_MARK = "신규 리드 도착"
F2_MARK = "*접수일자:*"
F3_MARK = ":love_letter:"
F1_LINE_RE = re.compile(r"^:[a-z0-9_+\-]+:\s*(?P<label>[^:]{1,20}):\s*(?P<value>.*)$")
F2_LINE_RE = re.compile(r"\*(?P<label>[^*:]{1,20}):\*\s*(?P<value>[^\n]*)")
F3_LABEL_RE = re.compile(r"^\*(?P<label>[^*]{1,20})\*\s*$")
F5_LINE_RE = re.compile(r"^\s*[•\-]\s*\*?(?P<label>[^:*]{1,20}?)\*?\s*:\s*(?P<value>.*)$")
F5_LABELS = {
    "경로", "유입경로", "인입 채널", "접수일자", "기업명", "회사", "회사명", "문의처",
    "담당자", "문의자", "이름", "연락처", "전화", "이메일", "문의내용", "문의", "비고",
}
# F3 폼의 괄호 안내문은 값이 아니다(slack-inventory T6)
FORM_GUIDE_RE = re.compile(r"^\(.*\)$")


@dataclass(frozen=True)
class SlackMessage:
    channel_id: str
    ts: str
    thread_ts: str | None
    user_name: str | None
    text: str
    files: list[dict]


# ==========================================================================
# 읽기
# ==========================================================================
def load_records(path: Path) -> list[SlackMessage]:
    """ts 기준 dedup 후 시간순으로 돌려준다(slack-inventory T1: 페이지 경계 중복)."""
    seen: set[str] = set()
    out: list[SlackMessage] = []
    # splitlines() 는 U+2028 같은 유니코드 줄바꿈에서도 자른다. 본문에 그 문자가 든
    # 메시지가 실재해 JSON 한 줄이 두 조각으로 깨진다. 개행은 \n 만 인정한다.
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        rec = json.loads(line)
        ts = rec["ts"]
        if ts in seen:
            continue
        seen.add(ts)
        out.append(
            SlackMessage(
                channel_id=rec["channel_id"],
                ts=ts,
                thread_ts=rec.get("thread_ts"),
                user_name=rec.get("user_name"),
                text=rec.get("text") or "",
                files=rec.get("files") or [],
            )
        )
    out.sort(key=lambda m: float(m.ts))
    return out


def dedup_by_value_change(messages: list[SlackMessage]) -> tuple[list[SlackMessage], int]:
    """작성자별로 값이 바뀔 때만 남긴다.

    'B팀 세일즈 데일리미팅' 봇이 매일 같은 문구를 216건 올린다(slack-inventory T11).
    첫 건은 남기므로 사실은 보존되고, 뒤따르는 동일 문구만 사라진다.
    """
    last: dict[str | None, str] = {}
    kept: list[SlackMessage] = []
    dropped = 0
    for msg in messages:
        key = re.sub(r"\s+", " ", msg.text).strip()
        if key and last.get(msg.user_name) == key:
            dropped += 1
            continue
        last[msg.user_name] = key
        kept.append(msg)
    return kept, dropped


# ==========================================================================
# 미팅노트 분해
# ==========================================================================
def normalize_section(label: str) -> str | None:
    key = re.sub(r"\s+", "", label).lower()
    if key in SECTION_ACTION:
        return "action"
    if key in SECTION_CONTENT:
        return "content"
    return None


def split_title(title: str) -> tuple[str | None, str | None]:
    """제목을 '상대 - 주제' 로 나눈다. 구분자는 hyphen·en-dash 둘 다 받는다."""
    parts = TITLE_SPLIT_RE.split(title, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return title.strip(), None


def _split_when(raw: str) -> tuple[str, str | None]:
    for sep in (" / ", ", "):
        if sep in raw:
            head, tail = raw.rsplit(sep, 1)
            return head.strip(), tail.strip()
    return raw.strip(), None


def _parse_attendees(raw: str) -> list[dict]:
    out: list[dict] = []
    for chunk in raw.split(" / "):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            org, people = chunk.split(":", 1)
            names = [p.strip() for p in re.split(r"[,、]", people) if p.strip()]
            out.append({"org": org.strip(), "people": names})
        else:
            out.append({"org": None, "people": [chunk]})
    return out


def parse_meeting_note(text: str) -> dict | None:
    """`*[제목]*` + 불릿 일시/참석 + [섹션] 구조를 결정적으로 쪼갠다."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return None
    m = TITLE_RE.match(lines[idx].strip())
    if not m:
        return None

    title = m.group("title").strip()
    counterpart, subject = split_title(title)

    fields: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    section_order: list[str] = []
    current_section: str | None = None
    bullets: list[str] = []

    def push(bullet: str) -> None:
        if current_section is None:
            return
        sections.setdefault(current_section, []).append(bullet)

    # 첫 줄은 무조건 제목으로 소비한다(slack-inventory T4)
    for line in lines[idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue

        field = FIELD_RE.match(stripped)
        if field and current_section is None:
            fields[field.group("label")] = field.group("value").strip()
            continue

        section = SECTION_RE.match(stripped)
        if section and not BULLET_RE.match(stripped):
            if bullets:
                push(bullets.pop())
            raw_label = section.group("section").strip()
            current_section = raw_label
            if raw_label not in section_order:
                section_order.append(raw_label)
            sections.setdefault(raw_label, [])
            continue

        bullet = BULLET_RE.match(line)
        if bullet:
            if bullets:
                push(bullets.pop())
            bullets.append(bullet.group("text").strip())
            continue

        sub = SUBBULLET_RE.match(line)
        if bullets and (sub or line.startswith((" ", "\t"))):
            bullets[-1] = bullets[-1] + "\n" + stripped
            continue
        if bullets:
            bullets[-1] = bullets[-1] + "\n" + stripped
    if bullets:
        push(bullets.pop())

    when_raw = fields.get("일시") or fields.get("일자")
    has_section = any(normalize_section(name) for name in section_order)
    if not when_raw and not has_section:
        return None

    content: list[str] = []
    action: list[str] = []
    other: dict[str, list[str]] = {}
    for name in section_order:
        kind = normalize_section(name)
        if kind == "content":
            content.extend(sections.get(name, []))
        elif kind == "action":
            action.extend(sections.get(name, []))
        else:
            other[name] = sections.get(name, [])

    when_text, mode = _split_when(when_raw) if when_raw else (None, None)
    attendees_raw = fields.get("참석자") or fields.get("참석") or fields.get("대상자")

    return {
        "record_kind": "meeting_note",
        "title": title,
        "counterpart": counterpart,
        "subject": subject,
        "when_raw": when_raw,
        "when_text": when_text,
        "mode": mode,
        "attendees_raw": attendees_raw,
        "attendees": _parse_attendees(attendees_raw) if attendees_raw else [],
        "content_bullets": content,
        "action_items": action,
        "other_sections": other,
    }


# ==========================================================================
# 리드 폼 분해
# ==========================================================================
def parse_lead_form(text: str) -> dict | None:
    if F3_MARK in text and "신규 리드 등록" in text:
        return {"record_kind": "lead_alert", "form": "F3", "fields": _parse_f3(text)}
    if F2_MARK in text:
        return {"record_kind": "lead_alert", "form": "F2", "fields": _parse_f2(text)}
    if F1_MARK in text:
        return {"record_kind": "lead_alert", "form": "F1", "fields": _parse_f1(text)}
    fields = _parse_f5(text)
    if fields is not None:
        return {"record_kind": "lead_alert", "form": "F5", "fields": fields}
    return None


def _parse_f1(text: str) -> dict[str, str]:
    """값은 반드시 같은 줄에서만 취한다(slack-inventory T7)."""
    fields: dict[str, str] = {}
    for line in text.split("\n"):
        m = F1_LINE_RE.match(line.strip())
        if m:
            fields[m.group("label").strip()] = m.group("value").strip()
    return fields


def _parse_f2(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    marks = list(F2_LINE_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        value = text[m.end("label") + 2 : end].strip()
        fields[m.group("label").strip()] = value
    return fields


def _parse_f3(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    label: str | None = None
    buffer: list[str] = []
    for line in text.split("\n"):
        m = F3_LABEL_RE.match(line.strip())
        if m:
            if label:
                fields[label] = "\n".join(buffer).strip()
            label = m.group("label").strip()
            buffer = []
            continue
        if label and line.strip() and not FORM_GUIDE_RE.match(line.strip()):
            buffer.append(line.strip())
    if label:
        fields[label] = "\n".join(buffer).strip()
    return fields


def _parse_f5(text: str) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    for line in text.split("\n"):
        m = F5_LINE_RE.match(line)
        if m:
            label = m.group("label").strip()
            if label in F5_LABELS:
                fields[label] = m.group("value").strip()
    return fields if len(fields) >= 3 else None


# ==========================================================================
# 소스 파싱
# ==========================================================================
def _locator(msg: SlackMessage) -> str:
    if msg.thread_ts and msg.thread_ts != msg.ts:
        return f"slack:{msg.channel_id}/{msg.thread_ts}/{msg.ts}"
    return f"slack:{msg.channel_id}/{msg.ts}"


def _body(msg: SlackMessage) -> str:
    if msg.text.strip():
        return msg.text
    # Slackbot 메일 전달은 본문이 비고 첨부 제목만 남는다(slack-inventory F4)
    titles = [f.get("title", "") for f in msg.files if f.get("title")]
    return "\n".join(titles)


def parse_source(cfg: dict) -> tuple[dict, list[dict]]:
    path = Path(cfg["path"])
    messages = load_records(path)
    total = len(messages)
    messages, dropped = dedup_by_value_change(messages)

    is_lead_channel = cfg["source_type"] == "slack_thread" and "리드등록알림" in path.name

    units: list[Unit] = []
    empty = 0
    meeting_notes = 0
    lead_alerts = 0
    top_level = 0
    for msg in messages:
        raw = _body(msg)
        if not raw.strip():
            empty += 1
            continue
        # 구조 분해는 마스킹이 끝난 문자열에 대해서 한다.
        # 마스킹 전 값이 structured 로 새어 나가는 경로를 아예 만들지 않기 위해서다.
        masked, hits = mask_text(raw)
        masked = normalize_text(masked)

        structured: dict | None = None
        if is_lead_channel:
            structured = parse_lead_form(masked)
            if structured is not None:
                lead_alerts += 1
        if structured is None:
            structured = parse_meeting_note(masked)
            if structured is not None:
                meeting_notes += 1

        if not (msg.thread_ts and msg.thread_ts != msg.ts):
            top_level += 1

        if structured is not None:
            structured = {
                **structured,
                "thread_ts": msg.thread_ts,
                "is_thread_reply": bool(msg.thread_ts and msg.thread_ts != msg.ts),
            }

        units.append(
            Unit(
                locator=_locator(msg),
                unit="message",
                text=masked,
                structured=structured,
                authored_at=datetime.fromtimestamp(
                    float(msg.ts), tz=timezone.utc
                ).isoformat(),
                author_name=msg.user_name,
                pii_hits=tuple((h["kind"], h["count"]) for h in hits),
            )
        )

    evidences = build_evidences(cfg["id"], units)
    source = build_source_record(
        cfg,
        parser="slack_dump",
        extractor="slack_mcp",
        evidence_count=len(evidences),
        notes=(
            f"원문 {total}건(ts dedup 후) · 동일 문구 반복 제외 {dropped}건 · "
            f"본문·첨부 모두 빈 메시지 {empty}건 제외 · "
            f"meeting_note {meeting_notes}건 · lead_alert {lead_alerts}건 · "
            f"최상위 {top_level}건"
        ),
    )
    return source, evidences
