"""상시 하드 게이트 3종 (REVISED §8).

    1. PII·민감정보 노출 0        — 답변·evidence·subgraph 전체
    2. evidence traceability 100% — 인용이 전부 실제 Evidence 를 가리킨다
    3. 무근거 숫자·고유명사 0      — 답변의 숫자·고유명사가 인용 Evidence 안에 실재한다

세 게이트 모두 **위반을 조용히 통과시키지 않는다.** 위반 문장은 답변에서 빼고, 무엇을 왜
뺐는지 `GuardReport` 에 남긴다.

탐지기는 `config/pii-patterns.yaml` 을 읽는다(읽기 전용). 하드 게이트에 넣는 것은 결정적
4종 계열(휴대전화·유선·대표번호·이메일·주민번호)이다. 계좌번호는 맥락 없이는 오탐이 크고,
사업자번호는 맨 10자리라 금액·ID 와 구분되지 않아 게이트에서 뺐다 — 두 탐지기 모두 설정
파일이 needs_human_confirm 으로 표시해 둔 것들이다.

숫자·고유명사 판정에서 "근거"는 답변에 실린 Evidence 발췌와 질문 문장이다. 질문에 있던
말을 되쓰는 것은 새 사실을 만든 것이 아니다.

3번에는 판정이 하나 더 붙는다. 발췌를 이어 붙여 보면 "근거에 있는 값을 **다른 대상**에
붙이는" 오류가 통과한다(기능맵 172행의 버전을 174행 기능 이름에 붙이는 식). 그래서 버전
표기만은 발췌를 한 건씩 보고, 대상과 값이 같은 발췌 안에 함께 적혀 있는지 확인한다.
"""

from __future__ import annotations

import functools
import pathlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PII_CONFIG = REPO_ROOT / "config" / "pii-patterns.yaml"

#: 결정적으로 개인을 식별하는 값. 이 다섯은 노출 0 이 아니면 답변을 내보내지 않는다.
HARD_GATE_DETECTORS: tuple[str, ...] = (
    "PII-MOBILE",
    "PII-LANDLINE",
    "PII-SERVICE-NUMBER",
    "PII-EMAIL",
    "PII-RRN",
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).translate(_ZERO_WIDTH)
    return _WS.sub(" ", value).strip()


# ---------------------------------------------------------------------------
# 1. PII
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _pii_rules() -> tuple[tuple[tuple[str, re.Pattern[str]], ...], tuple[re.Pattern[str], ...], frozenset[str]]:
    cfg = yaml.safe_load(PII_CONFIG.read_text(encoding="utf-8"))
    detectors = tuple(
        (d["id"], re.compile(d["regex"]))
        for d in cfg["detectors"]
        if d["id"] in HARD_GATE_DETECTORS
    )
    guards = tuple(
        re.compile(g["pattern"])
        for g in (cfg.get("false_positive_guards") or [])
        if g.get("pattern")
    )
    allow = frozenset(
        value.lower()
        for d in cfg["detectors"]
        for value in ((d.get("allowlist") or {}).get("values") or [])
    )
    return detectors, guards, allow


def find_pii(text: str) -> list[tuple[str, str]]:
    """(탐지기 id, 매칭 문자열). 오탐 방지 규칙을 먼저 걷어낸 뒤 판정한다.

    Slack 메시지 ts(`1751610789.108349`)와 epoch ms 는 자릿수만 보면 대표번호로 잡힌다.
    설정 파일의 false_positive_guards 가 그 둘을 지목하고 있어 먼저 지운다. 다만 010 으로
    시작하는 긴 숫자열은 실제 전화번호 오입력 사례가 있어 지우지 않는다.
    """
    detectors, guards, allow = _pii_rules()
    scrubbed = text or ""
    for guard in guards:
        scrubbed = guard.sub(
            lambda m: m.group(0) if m.group(0).startswith("010") else "", scrubbed
        )

    hits: list[tuple[str, str]] = []
    for det_id, pattern in detectors:
        for match in pattern.finditer(scrubbed):
            value = match.group(0)
            if det_id == "PII-EMAIL" and value.lower() in allow:
                continue
            hits.append((det_id, value))
    return hits


# ---------------------------------------------------------------------------
# 3. 무근거 숫자·고유명사
# ---------------------------------------------------------------------------

# 버전·금액처럼 점과 쉼표가 섞인 숫자를 통째로 본다. "2.0.0" "3.77" "2,564"
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)*")

# 대문자로 시작하는 로마자 토큰. KB · DTS · OpenBuilder 같은 조직·제품명을 잡는다.
_ASCII_PROPER = re.compile(r"[A-Z][A-Za-z0-9+]{1,}")

# 한국어 고유명사는 정규식으로 일반적으로 잡을 수 없다. 이 코퍼스의 기관·법인명이 실제로
# 쓰는 접미사만 나열했다. 목적은 "자료에 없는 회사 이름을 지어내는 것"을 잡는 것이다.
#
# 괄호는 이름의 일부로 보지 않는다. 넣어 두면 토큰이 괄호를 넘어가 붙어 버려 실재하는 말을
# 없는 회사 이름으로 만든다. 실측(GQ-D5): "퇴직연금(은행·증권사·생명보험사·손해보험사)" 에서
# `퇴직연금(은행` 이 한 토큰으로 잡혔고, `퇴직연금`·`은행` 은 각각 근거에 있는데도 붙여 놓은
# 형태는 없으니 무근거로 판정돼 문장이 통째로 지워졌다(답변 419자 손실). 괄호를 뺀 뒤에도
# 지어낸 이름은 그대로 잡힌다 — `(가상)없는보험` 은 `없는보험` 으로 잡혀 근거에 없으므로 걸린다.
_KO_ORG = re.compile(
    r"[가-힣A-Za-z0-9]{2,12}"
    r"(?:손해보험|해상화재|생명보험|생명|은행|캐피탈|카드사|증권|화재|렌터카|전선|"
    r"텔레콤|시스템|물산|건설|코리아|그룹|공사|공단|중앙회|신용정보|저축은행|보험)"
)

_CITATION_MARKER = re.compile(r"\[\d{1,3}\]")


def _strip_commas(text: str) -> str:
    return text.replace(",", "")


def _number_in(token: str, haystack: str) -> bool:
    """숫자 토큰이 근거 문자열에 **온전한 수로** 있는지.

    단순 부분 문자열 판정('in')은 '3.7' ⊂ '3.77', '400' ⊂ '4,000'(콤마 제거 후
    '4000')을 근거 있음으로 봐서 반올림·절삭 환각이 게이트를 지나갔다.
    앞뒤가 숫자(또는 소수점으로 이어지는 숫자)가 아닌 자리의 일치만 인정한다.
    문장 끝 마침표('2.0.')는 뒤에 숫자가 없으므로 그대로 통과한다.
    """
    pattern = re.compile(
        r"(?<![0-9])(?<![0-9]\.)" + re.escape(token) + r"(?!\.?[0-9])"
    )
    return pattern.search(haystack) is not None


def unsupported_tokens(
    prose: str, support_text: str, question_text: str
) -> tuple[list[str], list[str]]:
    """(근거 없는 숫자, 근거 없는 고유명사)."""
    body = _CITATION_MARKER.sub(" ", prose or "")
    support = _strip_commas(normalize(support_text))
    question = _strip_commas(normalize(question_text))

    numbers = [
        token
        for token in _NUMBER.findall(body)
        if not _number_in(_strip_commas(token), support)
        and not _number_in(_strip_commas(token), question)
    ]
    terms = [
        token
        for token in sorted(set(_ASCII_PROPER.findall(body)) | set(_KO_ORG.findall(body)))
        if normalize(token) not in support and normalize(token) not in question
    ]
    return numbers, terms


# ---------------------------------------------------------------------------
# 3-1. 값을 엉뚱한 대상에 붙이는 오류
#
# 위의 판정은 발췌 전체를 한 덩어리로 본다. 그래서 "근거에 있는 값을 다른 대상에 붙이는"
# 오류는 통과한다. 기능맵처럼 한 행이 곧 한 사실인 자료에서 이게 그대로 드러난다 —
# 어느 행에 적힌 버전을 옆 행 기능 이름에 붙여도 숫자 자체는 근거에 있으니 잡히지 않았다.
#
# 그래서 버전만은 **한 발췌 안에서** 대상과 값이 함께 적혀 있는지 본다. 판정은 보수적이다.
# 대상이 뚜렷하지 않거나 어느 발췌도 그 대상을 다루지 않으면 판정을 미룬다.
# ---------------------------------------------------------------------------

#: 세 자리 이상 점으로 이어진 번호. 기능맵의 제공 버전 표기(1.0.0 · 2.1.0)를 겨냥한다.
_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)+")

#: 대상으로 인정할 최소 낱말 수. 이보다 적으면 무엇에 대한 문장인지 정할 수 없다.
MIN_SUBJECT_TERMS = 2


def misattributed_in_sentence(sentence: str, rows: Sequence[str]) -> list[str]:
    """이 문장이 엉뚱한 대상에 붙인 버전들.

    문장이 가리키는 대상과 가장 잘 맞는 발췌를 고르고, 그 발췌가 다른 버전을 말하는데
    문장은 다른 발췌의 버전을 적었을 때만 위반으로 본다.
    """
    from retrieval.text import content_terms, term_overlap

    versions = sorted(set(_VERSION.findall(sentence or "")))
    if not versions or not rows:
        return []
    terms = content_terms(_VERSION.sub(" ", sentence))
    if len(terms) < MIN_SUBJECT_TERMS:
        return []  # 무엇에 대한 문장인지 정할 수 없다. 판정하지 않는다

    scores = [(len(term_overlap(terms, row)), row) for row in rows]
    best = max(score for score, _ in scores)
    if best < MIN_SUBJECT_TERMS:
        return []  # 어느 발췌도 이 대상을 다루지 않는다. 판정하지 않는다
    best_rows = [row for score, row in scores if score == best]

    wrong: list[str] = []
    for version in versions:
        own = max((score for score, row in scores if version in row), default=-1)
        if own < 0:
            continue  # 근거에 아예 없는 값은 위의 무근거 숫자 판정이 잡는다
        if own >= best:
            continue  # 그 값이 적힌 발췌가 곧 이 문장의 대상이다
        if any(_VERSION.search(row) and version not in row for row in best_rows):
            wrong.append(version)
    return wrong


def misattributed_versions(prose: str, snippets: Sequence[str]) -> list[tuple[str, list[str]]]:
    """(문장, 잘못 붙인 버전들) 목록. 진단·테스트용 전수 검사."""
    rows = [normalize(s) for s in snippets if normalize(s)]
    found: list[tuple[str, list[str]]] = []
    for line in (prose or "").splitlines():
        for sentence in split_sentences(line):
            wrong = misattributed_in_sentence(sentence, rows)
            if wrong:
                found.append((sentence.strip(), wrong))
    return found


# ---------------------------------------------------------------------------
# 문장 단위 제거
# ---------------------------------------------------------------------------

_SENTENCE_END = ".。!?"


def split_sentences(line: str) -> list[str]:
    """문장 경계는 마침표·물음표·느낌표로 본다.

    마침표는 **뒤가 공백이거나 줄 끝일 때만** 경계로 친다. 그러지 않으면 "2.1.0" 이나
    "v2.1!E15" 같은 표기가 문장 여럿으로 쪼개져 숫자가 반토막 난다.
    """
    parts: list[str] = []
    buffer = ""
    for index, char in enumerate(line):
        buffer += char
        if char not in _SENTENCE_END:
            continue
        nxt = line[index + 1] if index + 1 < len(line) else ""
        if char in ".!" and nxt and not nxt.isspace():
            continue
        parts.append(buffer)
        buffer = ""
    if buffer.strip():
        parts.append(buffer)
    return parts


def redact(text: str) -> str:
    """PII 매칭 부분만 별표로 덮는다. 로그·라벨처럼 문장을 통째로 뺄 수 없는 자리에 쓴다."""
    out = text or ""
    for _, value in find_pii(out):
        out = out.replace(value, re.sub(r"[0-9A-Za-z]", "*", value))
    return out


@dataclass
class Removal:
    """지워진 문장 한 건과 그 이유.

    무엇을 뺐는지만 세면 "게이트가 답변을 빈약하게 만든다"는 신고가 들어와도 원인을 못 찾는다.
    어느 토큰이 걸렸는지 남겨야 표기 차이인지 진짜 날조인지 나중에 가릴 수 있다.
    """

    location: str
    sentence: str
    numbers: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    pii: list[str] = field(default_factory=list)
    #: 근거에는 있으나 **다른 발췌의 대상**에 적힌 값. 숫자 자체는 근거에 있으므로
    #: numbers 와 구분해 남긴다.
    misattributed: list[str] = field(default_factory=list)

    def as_log(self) -> dict:
        # 문장에 PII 가 들어 있어서 지워졌을 수 있다. 로그로 되살리지 않는다.
        return {
            "field": self.location,
            "sentence": redact(self.sentence)[:200],
            "numbers": self.numbers[:8],
            "terms": self.terms[:8],
            "pii": sorted(set(self.pii)),
            "misattributed": self.misattributed[:8],
        }


@dataclass
class GuardReport:
    pii_hits: list[tuple[str, str]] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    unsupported_terms: list[str] = field(default_factory=list)
    dropped_citations: list[dict] = field(default_factory=list)
    removed_sentences: list[str] = field(default_factory=list)
    dropped_evidence_ids: list[str] = field(default_factory=list)
    removals: list[Removal] = field(default_factory=list)
    #: 근거에는 있으나 다른 발췌의 대상에 붙인 값
    misattributed_values: list[str] = field(default_factory=list)
    #: 모델이 번호만 적고 본문에 달지 않아, 붙일 문장을 찾지 못해 버린 각주.
    #: 이 목록이 있어야 "각주가 0개인데 로그는 전부 0" 같은 조용한 실종이 안 생긴다.
    unanchored_citations: list[dict] = field(default_factory=list)
    #: 모델이 citations 에도 적지 않아, 문장의 숫자·회사명을 가진 발췌를 찾아 서버가 붙인 각주.
    #: 위반이 아니라 복구라서 `clean` 판정에는 넣지 않지만, 몇 건을 서버가 채웠는지는 남긴다.
    token_anchored_citations: list[dict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (
            self.pii_hits
            or self.unsupported_numbers
            or self.unsupported_terms
            or self.dropped_citations
            or self.dropped_evidence_ids
            or self.misattributed_values
            or self.unanchored_citations
        )

    def as_log(self) -> dict:
        return {
            "pii": len(self.pii_hits),
            "unsupported_numbers": len(self.unsupported_numbers),
            "unsupported_terms": len(self.unsupported_terms),
            "misattributed_values": len(self.misattributed_values),
            "dropped_citations": len(self.dropped_citations),
            "unanchored_citations": len(self.unanchored_citations),
            "token_anchored_citations": len(self.token_anchored_citations),
            "removed_sentences": len(self.removed_sentences),
            "dropped_evidence": len(self.dropped_evidence_ids),
        }

    def removal_log(self, limit: int = 12) -> list[dict]:
        return [r.as_log() for r in self.removals[:limit]]


def scrub_prose(
    text: str,
    support_text: str,
    question_text: str,
    report: GuardReport,
    *,
    location: str = "text",
    snippets: Sequence[str] = (),
) -> str:
    """PII·무근거 숫자·고유명사, 그리고 엉뚱한 대상에 붙인 값이 든 문장을 통째로 뺀다.

    토큰만 지우면 "도입 고객사는 곳입니다" 같은 문장이 남아 오히려 읽는 사람을 속인다.
    문장 단위로 빼는 이유다.

    `snippets` 는 발췌를 **한 건씩** 받는다. 이어 붙인 `support_text` 로는 "어느 행에 적힌
    값인가"를 볼 수 없어서, 근거에 있는 값을 다른 대상에 붙이는 오류가 통과한다.

    문장을 뺄 때 **무엇이 걸렸는지**를 `report.removals` 에 함께 남긴다. 개수만 세면
    답변이 빈약해졌을 때 원인이 표기 차이인지 진짜 날조인지 가릴 수 없다.
    """
    if not text:
        return ""

    rows = [normalize(s) for s in snippets if normalize(s)]
    kept_lines: list[str] = []
    for line in text.splitlines():
        kept: list[str] = []
        for sentence in split_sentences(line):
            pii = find_pii(sentence)
            numbers, terms = unsupported_tokens(sentence, support_text, question_text)
            misattributed = misattributed_in_sentence(sentence, rows)
            if pii or numbers or terms or misattributed:
                report.pii_hits.extend(pii)
                report.unsupported_numbers.extend(numbers)
                report.unsupported_terms.extend(terms)
                report.misattributed_values.extend(misattributed)
                report.removed_sentences.append(sentence.strip())
                report.removals.append(
                    Removal(
                        location=location,
                        sentence=sentence.strip(),
                        numbers=sorted(set(numbers)),
                        terms=sorted(set(terms)),
                        pii=[det for det, _ in pii],
                        misattributed=sorted(set(misattributed)),
                    )
                )
                continue
            kept.append(sentence)
        line_text = "".join(kept).strip()
        if line_text:
            kept_lines.append(line_text)
    return "\n".join(kept_lines).strip()


def scrub_list(
    items: Iterable[str],
    support_text: str,
    question_text: str,
    report: GuardReport,
    *,
    location: str = "list",
    snippets: Sequence[str] = (),
) -> list[str]:
    out: list[str] = []
    for item in items:
        cleaned = scrub_prose(
            item, support_text, question_text, report, location=location, snippets=snippets
        )
        if cleaned:
            out.append(cleaned)
    return out


def payload_text(answer: dict) -> str:
    """PII 검사 대상 표면. **사람이 읽는 텍스트**만 모은다.

    노드 id·evidence_id 같은 내부 식별자는 뺀다. 이것들은 hex 해시라 우연히 전화번호
    정규식에 걸리지만(실측: 64자 hex 안의 `19301910`) 사람에게 보이지 않고 개인을 식별하지도
    않는다. 여기에 넣으면 게이트가 매번 거짓으로 빨개져 진짜 노출을 못 보게 된다.
    """
    parts: list[str] = []
    body = answer.get("answer") or {}
    parts += [body.get("text") or "", body.get("recommendation") or ""]
    parts += body.get("estimated_parts") or []
    parts += answer.get("unknowns") or []
    parts += answer.get("next_actions") or []

    for item in answer.get("evidence") or []:
        parts += [item.get("snippet") or "", item.get("locator") or "", item.get("source_id") or ""]
    for item in answer.get("raw_signals") or []:
        parts += [item.get("snippet") or "", item.get("locator") or "", item.get("source_id") or ""]
    for gap in answer.get("gaps") or []:
        parts += [gap.get("subject") or "", (gap.get("basis") or {}).get("reason") or ""]
    for dispute in answer.get("disputes") or []:
        parts.append(dispute.get("subject") or "")
        for side in dispute.get("sides") or []:
            parts.append(side.get("statement") or "")
    for claim in answer.get("claims") or []:
        parts.append(claim.get("statement") or "")
    for node in (answer.get("subgraph") or {}).get("nodes") or []:
        parts.append(node.get("label_text") or "")
    return "\n".join(p for p in parts if p)


def final_sweep(answer: dict, report: GuardReport) -> dict:
    """마지막 방어선. 문장으로 뺄 수 없는 자리(위치 표기·노드 라벨)는 값만 덮는다."""
    hits = find_pii(payload_text(answer))
    if not hits:
        return answer
    report.pii_hits.extend(hits)

    for item in answer.get("evidence") or []:
        item["locator"] = redact(item.get("locator") or "")
        item["snippet"] = redact(item.get("snippet") or "")
    for item in answer.get("raw_signals") or []:
        item["locator"] = redact(item.get("locator") or "")
        item["snippet"] = redact(item.get("snippet") or "")
    for node in (answer.get("subgraph") or {}).get("nodes") or []:
        node["label_text"] = redact(node.get("label_text") or "")
    body = answer.get("answer") or {}
    body["text"] = redact(body.get("text") or "")
    if body.get("recommendation"):
        body["recommendation"] = redact(body["recommendation"])
    return answer


def surviving_markers(text: str) -> set[int]:
    return {int(m[1:-1]) for m in _CITATION_MARKER.findall(text or "")}


def drop_markers(text: str, markers: Sequence[int]) -> str:
    out = text or ""
    for marker in markers:
        out = out.replace(f"[{marker}]", "")
    return out
