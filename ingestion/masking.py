"""PII 마스킹.

규칙은 전부 `config/pii-patterns.yaml` 에서 읽는다. 이 모듈은 규칙을 새로 만들지 않는다.

설계 원칙 하나만 지킨다:
    **마스킹 전 문자열을 밖으로 내보내는 경로를 만들지 않는다.**
    `Masker.mask()` 는 원본을 인자로만 받고, 돌려주는 `MaskedText` 에는 마스킹된 값과
    길이·탐지 통계만 담는다. 원본을 필드로 보관하지 않으므로 실수로 직렬화할 수 없다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "pii-patterns.yaml"

# 전화번호 계열 — Gate 1A 가 검사하는 대상
PHONE_DETECTORS = ("PII-MOBILE", "PII-LANDLINE", "PII-SERVICE-NUMBER")
# 값 전체가 숫자인 셀(금액·수량)에는 적용하지 않는 탐지기.
# 하이픈 없는 10자리 사업자번호 패턴이 '6000000000'(60억) 같은 금액을 통째로 먹는다.
SKIP_ON_NUMERIC_CELL = ("PII-BIZ-NO", "PII-SERVICE-NUMBER")


@dataclass(frozen=True)
class PiiHit:
    kind: str
    count: int

    def as_dict(self) -> dict:
        return {"kind": self.kind, "count": self.count}


@dataclass(frozen=True)
class MaskedText:
    """마스킹이 끝난 값. 원본은 어디에도 들어 있지 않다."""

    text: str
    hits: tuple[PiiHit, ...] = ()
    raw_len: int = 0

    def hit_dicts(self) -> list[dict]:
        return [h.as_dict() for h in self.hits]

    def __bool__(self) -> bool:
        return bool(self.text)


@dataclass
class _Detector:
    id: str
    regex: re.Pattern
    replacement: str
    priority: int
    expand_digit_run: bool = False
    allowlist: tuple[str, ...] = ()
    context_keywords: tuple[str, ...] = ()
    context_window: int = 0


@dataclass
class _Span:
    start: int
    end: int
    kind: str
    replacement: str
    priority: int = 0


@dataclass
class _Rules:
    detectors: list[_Detector] = field(default_factory=list)
    guards: list[re.Pattern] = field(default_factory=list)
    deny_external: tuple[str, ...] = ()
    keep_internal: tuple[str, ...] = ()
    pseudonyms: tuple[str, ...] = ()
    column_labels: tuple[str, ...] = ()
    person_placeholder: str = "[이름]"
    role_placeholder: str = "[담당자]"
    title_suffix: re.Pattern | None = None


def _load_rules(config_path: Path) -> _Rules:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    notation = raw.get("masking_notation", {}).get("placeholders", {})

    detectors: list[_Detector] = []
    for d in raw.get("detectors", []):
        detectors.append(
            _Detector(
                id=d["id"],
                regex=re.compile(d["regex"]),
                replacement=d.get("mask_template", "***"),
                priority=int(d.get("priority", 99)),
                # 숫자만으로 이뤄진 탐지기는 앞뒤 숫자까지 삼켜서 자릿수 유추를 막는다.
                # 실측된 13자리 오입력(예: '0101234567890')이 11자리만 가려지는 것을 방지한다.
                expand_digit_run=d["id"]
                in ("PII-MOBILE", "PII-LANDLINE", "PII-SERVICE-NUMBER", "PII-RRN"),
                allowlist=tuple((d.get("allowlist") or {}).get("values", [])),
                context_keywords=tuple(d.get("context_keywords", [])),
                context_window=int(d.get("require_context_within", 0)),
            )
        )
    detectors.sort(key=lambda x: x.priority)

    guards = []
    for g in raw.get("false_positive_guards", []):
        if g.get("pattern"):
            guards.append(re.compile(g["pattern"]))
    # FP-VERSION-STRING 은 examples 만 있고 pattern 이 없다. 예시를 덮는 최소 패턴을 쓴다.
    guards.append(re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?:[.\-][\w\-]+)*"))

    ne = raw.get("named_entities", {})
    deny_external: tuple[str, ...] = ()
    keep_internal: tuple[str, ...] = ()
    pseudonyms: tuple[str, ...] = ()
    column_labels: list[str] = []
    title_suffix = None
    for s in ne.get("strategies", []):
        if s["id"] == "NE-DENY-LIST":
            deny_external = tuple(s.get("values_external", []))
            keep_internal = tuple(s.get("values_internal", []))
        elif s["id"] == "NE-PSEUDONYM-PASS":
            pseudonyms = tuple(s.get("values", []))
        elif s["id"] == "NE-COLUMN":
            for col in s.get("target_columns", []):
                m = re.search(r"\(([^)]+)\)\s*$", col)
                if m:
                    column_labels.append(m.group(1).strip())
        elif s["id"] == "NE-TITLE-SUFFIX":
            title_suffix = re.compile(s["regex"])

    return _Rules(
        detectors=detectors,
        guards=guards,
        deny_external=deny_external,
        keep_internal=keep_internal,
        pseudonyms=pseudonyms,
        column_labels=tuple(sorted(set(column_labels))),
        person_placeholder=notation.get("person_name", "[이름]"),
        role_placeholder=notation.get("person_role", "[담당자]"),
        title_suffix=title_suffix,
    )


class Masker:
    """config/pii-patterns.yaml 을 그대로 적용하는 마스커.

    `enable_title_suffix` 는 기본 꺼져 있다. DECISIONS A-3 이 사외 개인은 deny-list,
    사내 임직원 이름은 유지로 정했는데 직급 접미 휴리스틱은 '고객님' 같은 일반 명사와
    사내 인물을 함께 지운다.

    `_disable_all` 은 마스킹 게이트가 빈 검사가 아님을 증명하는 차단 케이스 전용이다.
    """

    def __init__(
        self,
        config_path: Path | str | None = None,
        *,
        enable_title_suffix: bool = False,
        _disable_all: bool = False,
    ) -> None:
        self._rules = _load_rules(Path(config_path) if config_path else CONFIG_PATH)
        self._enable_title_suffix = enable_title_suffix
        self._disabled = _disable_all

    # ------------------------------------------------------------------
    # 공개 API — 마스킹된 값만 돌려준다
    # ------------------------------------------------------------------
    def mask(
        self,
        raw,
        *,
        column_name: str | None = None,
        numeric_cell: bool = False,
    ) -> MaskedText:
        if raw is None:
            return MaskedText("", (), 0)
        text = raw if isinstance(raw, str) else str(raw)
        raw_len = len(text)

        if self._disabled:
            return MaskedText(text, (), raw_len)

        # 열 이름 기반 선제 마스킹(NE-COLUMN). 정규식에 안 걸리는 실명을 잡는 유일한 수단이다.
        if column_name and column_name.strip() in self._rules.column_labels:
            if text.strip():
                return MaskedText(
                    self._rules.role_placeholder,
                    (PiiHit("NE-COLUMN", 1),),
                    raw_len,
                )
            return MaskedText("", (), raw_len)

        text = self._preprocess(text)
        counter: dict[str, int] = {}
        for _ in range(3):  # placeholder 가 다시 매칭되지 않을 때까지 수렴시킨다
            text, changed = self._apply_detectors(text, counter, numeric_cell)
            if not changed:
                break
        text = self._apply_names(text, counter)

        hits = tuple(PiiHit(k, v) for k, v in sorted(counter.items()))
        return MaskedText(text, hits, raw_len)

    def mask_text(self, raw, **kw) -> str:
        """마스킹된 문자열만 필요할 때."""
        return self.mask(raw, **kw).text

    def scan(self, text: str) -> list[PiiHit]:
        """이미 마스킹된 문자열에 PII 가 남았는지 검사한다(self-check)."""
        counter: dict[str, int] = {}
        for span in self._collect_spans(text, numeric_cell=False):
            counter[span.kind] = counter.get(span.kind, 0) + 1
        return [PiiHit(k, v) for k, v in sorted(counter.items())]

    def phone_hits(self, text: str) -> int:
        """Gate 1A 항목 5 검사용."""
        return sum(h.count for h in self.scan(text) if h.kind in PHONE_DETECTORS)

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------
    def _preprocess(self, text: str) -> str:
        # PRE-SLACK-LINK: <tel:타깃|표시> 는 타깃 쪽이 표시값과 다를 수 있어 둘 다 남긴다.
        text = re.sub(r"<tel:([^|>]+)\|([^>]*)>", r"\1 \2", text)
        text = re.sub(r"<mailto:([^|>]+)\|([^>]*)>", r"\1 \2", text)
        # PRE-MENTION: uid 는 버리고 이름만 남긴다.
        text = re.sub(r"<@U[A-Z0-9]+\|([^>]*)>", r"\1", text)
        return text

    def _protected(self, text: str) -> list[tuple[int, int]]:
        spans = []
        for g in self._rules.guards:
            for m in g.finditer(text):
                spans.append((m.start(), m.end()))
        return spans

    def _collect_spans(self, text: str, numeric_cell: bool) -> list[_Span]:
        protected = self._protected(text)
        spans: list[_Span] = []
        for det in self._rules.detectors:
            if numeric_cell and det.id in SKIP_ON_NUMERIC_CELL:
                continue
            for m in det.regex.finditer(text):
                start, end = m.start(), m.end()
                if any(ps <= start and end <= pe for ps, pe in protected):
                    continue
                if det.allowlist and m.group(0) in det.allowlist:
                    continue
                if det.context_keywords:
                    lo = max(0, start - det.context_window)
                    hi = min(len(text), end + det.context_window)
                    window = text[lo:hi]
                    if not any(k in window for k in det.context_keywords):
                        continue
                if det.expand_digit_run:
                    while start > 0 and text[start - 1].isdigit():
                        start -= 1
                    while end < len(text) and text[end].isdigit():
                        end += 1
                    if any(ps <= start and end <= pe for ps, pe in protected):
                        continue
                spans.append(_Span(start, end, det.id, det.replacement, det.priority))
        return spans

    def _apply_detectors(
        self, text: str, counter: dict[str, int], numeric_cell: bool
    ) -> tuple[str, bool]:
        spans = self._collect_spans(text, numeric_cell)
        if not spans:
            return text, False
        # 우선순위(낮은 값 우선) → 위치 순으로 겹침을 해소한다
        spans.sort(key=lambda s: (s.priority, s.start))
        chosen: list[_Span] = []
        for s in spans:
            if any(not (s.end <= c.start or s.start >= c.end) for c in chosen):
                continue
            chosen.append(s)
        chosen.sort(key=lambda s: s.start)

        out = []
        cursor = 0
        for s in chosen:
            out.append(text[cursor : s.start])
            out.append(s.replacement)
            counter[s.kind] = counter.get(s.kind, 0) + 1
            cursor = s.end
        out.append(text[cursor:])
        return "".join(out), True

    def _apply_names(self, text: str, counter: dict[str, int]) -> str:
        # 사외 개인 실명은 deny-list 로 지운다(DECISIONS A-3). 사내 임직원 이름은 유지한다.
        for name in self._rules.deny_external:
            if name and name in text:
                counter["NE-DENY-LIST"] = counter.get("NE-DENY-LIST", 0) + text.count(name)
                text = text.replace(name, self._rules.person_placeholder)
        if self._enable_title_suffix and self._rules.title_suffix:
            keep = set(self._rules.keep_internal) | set(self._rules.pseudonyms)

            def _sub(m: re.Match) -> str:
                whole = m.group(0)
                if any(k in whole for k in keep):
                    return whole
                counter["NE-TITLE-SUFFIX"] = counter.get("NE-TITLE-SUFFIX", 0) + 1
                return f"{self._rules.person_placeholder} {m.group(1)}"

            text = self._rules.title_suffix.sub(_sub, text)
        return text


_DEFAULT: Masker | None = None


def default_masker() -> Masker:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Masker()
    return _DEFAULT


def merge_hits(chunks: Iterable[MaskedText]) -> list[dict]:
    """여러 셀의 탐지 통계를 Evidence 한 건 기준으로 합친다."""
    counter: dict[str, int] = {}
    for c in chunks:
        for h in c.hits:
            counter[h.kind] = counter.get(h.kind, 0) + h.count
    return [{"kind": k, "count": v} for k, v in sorted(counter.items())]
