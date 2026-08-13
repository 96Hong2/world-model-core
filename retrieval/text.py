"""질문·발췌를 다루는 문자열 도구.

여기의 규칙은 전부 deterministic 이다. 질문에서 무엇을 묻는지 알아내는 데 LLM 을 쓰지 않는다
(라우터 3단의 마지막 폴백만 예외).
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).translate(_ZERO_WIDTH)
    return _WS.sub(" ", value).strip()


def fold(text: str | None) -> str:
    """비교용 키. 공백까지 지우고 소문자로 만든다."""
    return _WS.sub("", normalize(text)).lower()


# ---------------------------------------------------------------------------
# gap subject — 무엇에 대한 gap 인지는 질문이 정한다
# ---------------------------------------------------------------------------

# 질문 끝의 의문 어미. 앞에서부터 긴 것을 먼저 벗긴다.
_TAIL_PATTERNS = (
    r"(?:는|은|이|가|을|를)?\s*무엇이고.*$",
    r"(?:는|은|이|가|을|를)?\s*무엇인가$",
    r"(?:는|은|이|가|을|를)?\s*무엇인지$",
    r"(?:는|은|이|가|을|를)?\s*어떻게\s*되(?:나|는가|나요)$",
    r"(?:는|은|이|가|을|를)?\s*어떻게\s*되어\s*있(?:나|는가)$",
    r"(?:는|은|이|가|을|를)?\s*제공(?:되|하)(?:나|나요|는가|합니까)$",
    r"(?:는|은|이|가|을|를)?\s*지원(?:되|하)(?:나|나요|는가|합니까)$",
    r"(?:는|은|이|가|을|를)?\s*있(?:나|나요|는가|습니까)$",
    r"(?:는|은|이|가|을|를)?\s*되(?:나|나요|는가|ㅂ니까)$",
    r"(?:는|은|이|가|을|를)?\s*인가$",
    r"(?:는|은|이|가|을|를)?\s*인지$",
    r"(?:는|은|이|가|을|를)?\s*알려\s*(?:줘|주세요|달라)$",
)
_TAIL_RE = [re.compile(p) for p in _TAIL_PATTERNS]

MAX_SUBJECT_CHARS = 120


def derive_gap_subject(question: str) -> str:
    """gap 의 subject 는 사용자가 물어본 것 그대로다.

    시스템이 새 고유명사를 지어내지 않게 하려는 것이기도 하다. 답변 본문 채점에서
    질문에 있던 말은 근거 있는 것으로 취급된다.
    """
    value = normalize(question).rstrip("?？.!。 ")
    for _ in range(3):  # 어미가 겹쳐 붙은 문장이 있어 몇 번 벗긴다
        before = value
        for pattern in _TAIL_RE:
            value = pattern.sub("", value).rstrip("?？.!。, ")
        if value == before:
            break
    value = value.strip(" ,·-")
    return value[:MAX_SUBJECT_CHARS] if value else normalize(question)[:MAX_SUBJECT_CHARS]


# ---------------------------------------------------------------------------
# 내용어 추출
# ---------------------------------------------------------------------------

# 어느 문장에나 나오는 말. 주제 겹침 판정에 쓰면 아무 근거나 걸린다.
GENERIC_TERMS = frozenset(
    {
        "우리",
        "회사",
        "제품",
        "기능",
        "문제",
        "영역",
        "무엇",
        "어떤",
        "어느",
        "여러",
        "현재",
        "지금",
        "경우",
        "관련",
        "부분",
        "사항",
        "내용",
        "대해",
        "대한",
        "위해",
        "있는",
        "없는",
        "하는",
        "되는",
        "가능",
        "필요",
        "확인",
        "사용",
        "제공",
        "지원",
        "요청",
        "다음",
        "이것",
        "그것",
        "때문",
        "통해",
        # 의문 어미의 잔재. 원문 검색어로 쓰면 아무 문서나 걸린다.
        "무엇인",
        "어떻게",
        "얼마나",
        "언제",
        "어디",
        "누구",
        "알려",
        "높은가",
        "인가",
        # 어절 하나가 통째로 종결어미인 것들. `_trim_verb_ending` 은 어미가 **낱말 뒤에**
        # 붙은 것만 뗄 수 있어서("통제하지" → "통제") 이 어절들은 그대로 남는다.
        # 실측(2026-08-13): 「소개해야 하나?」의 `하나` 가 검색어가 되어 focal 에
        # 상호가 `하나` 로 시작하는 고객사가 들어왔다. 골든 문항에도 `되나` · `있나` 가 새고 있었다.
        # `하나` 만 회사 이름·수사와 겹치는데, 겹침 판정은 어절 단위라 `하나○○보험` 처럼
        # 붙여 쓴 상호는 그대로 남는다(별개 낱말이다).
        "하나",
        "하나요",
        "되나",
        "되나요",
        "있나",
        "있나요",
        "없나",
        "없나요",
    }
)

# 조사·어미. 어절 끝에서만 벗긴다. 긴 것부터.
_PARTICLES = (
    "에서는",
    "으로는",
    "에게서",
    "이라는",
    "라는",
    "에서",
    "으로",
    "에게",
    "한테",
    "까지",
    "부터",
    "보다",
    "이나",
    "만큼",
    "처럼",
    "와의",
    "과의",
    "의",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "로",
    "와",
    "과",
    "도",
    "만",
    "랑",
    # 복수 접미사. "Domain들"·"고객사들"이 원문의 "Domain"·"고객사"와 안 맞는 것을 막는다.
    "들",
)

# 우리 온톨로지의 라벨 이름. 사용자는 이 말로 질문하지만, 자료는 한국어로 쓰여 있고 이
# 영어 낱말이 실제로 많이 적힌 곳은 스키마를 설명한 코드·설계 문서다. 검색어로 쓰면 그
# 문서들이 상위를 차지해 정작 답에 필요한 업무 자료를 밀어낸다.
# (실측: "Business Domain 들은 무엇이고 각각 어느 단계인가" 가 레포 구조 문서 3건을 먼저
#  물어 왔고, BD 20행 중 두 행만 답변 근거로 남았다.)
# 한글 표현(도메인·역량·니즈)은 자료에도 쓰이므로 여기 넣지 않는다.
ONTOLOGY_TERMS = frozenset(
    {
        "business",
        "domain",
        "domains",
        "capability",
        "capabilities",
        "need",
        "needs",
        "account",
        "accounts",
        "deal",
        "deals",
        "industry",
        "industries",
        "entity",
        "evidence",
        "observation",
        "claim",
    }
)

# 용언 어미. 조사를 뗀 뒤 한 번 더 뗀다. 이걸 안 하면 질문의 "통제하지 못하는"이 자료의
# "통제 불가"와 한 글자도 안 겹쳐서, 정작 그 사실을 적어 둔 발췌가 검색·선별에서 밀린다
# (실측: BD Overview Guest통제 행 · 하늘IT 메모).
_VERB_ENDINGS = (
    "하려면",
    "해야",
    "하려",
    "하는",
    "하지",
    "하고",
    "하면",
    "하며",
    "하기",
    "한다",
    "했다",
    "합니",
    "되는",
    "되지",
    "된다",
    "되며",
    "되면",
    "되어",
    "됐다",
    "시키",
    "할",
    "한",
    "해",
    "된",
    "될",
)

_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")
MIN_TERM_CHARS = 2


def _trim_particle(token: str) -> str:
    """조사를 뗀다. 두 번까지 돈다 — "Domain들은" 처럼 복수 접미사 위에 조사가 또 붙는다."""
    for _ in range(2):
        for particle in _PARTICLES:
            if len(token) > len(particle) + 1 and token.endswith(particle):
                token = token[: -len(particle)]
                break
        else:
            break
    return token


def _trim_verb_ending(token: str) -> str:
    for ending in _VERB_ENDINGS:
        if len(token) > len(ending) + 1 and token.endswith(ending):
            return token[: -len(ending)]
    return token


def content_terms(text: str) -> list[str]:
    """겹침 판정에 쓸 내용어. 순서를 보존하고 중복을 없앤다."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_SPLIT.split(normalize(text)):
        if not raw:
            continue
        token = _trim_verb_ending(_trim_particle(raw)).lower()
        if len(token) < MIN_TERM_CHARS or token in GENERIC_TERMS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def search_terms(terms: list[str]) -> list[str]:
    """원문 검색에 넣을 낱말만 남긴다.

    우리 온톨로지 이름은 뺀다. 자료는 한국어로 쓰여 있어서 이 영어 낱말이 실제로 많이 적힌
    곳은 스키마를 설명한 코드·설계 문서뿐이고, 검색어로 쓰면 그 문서들이 상위를 채워 정작
    답에 필요한 업무 자료를 밀어낸다. 겹침 점수에서는 그대로 쓴다 — 자료 안에 정말로 그
    낱말이 있으면 그건 유효한 신호다.
    """
    return [t for t in terms if t.lower() not in ONTOLOGY_TERMS]


def _is_ascii_alnum(ch: str) -> bool:
    return bool(ch) and ch.isascii() and ch.isalnum()


def _needs_word_boundary(key: str) -> bool:
    """영문·숫자로 시작하고 끝나는 낱말인가.

    한글은 붙여 쓰므로 부분 문자열 비교가 맞다("통제"는 "통제·모니터링" 안에서 유효한 신호다).
    영문은 다르다. 낱말 경계를 안 보면 다른 낱말 속에 우연히 박힌 글자열이 신호로 잡힌다.
    """
    return _is_ascii_alnum(key[:1]) and _is_ascii_alnum(key[-1:])


def _boundary_match(needle: str, haystack: str) -> bool:
    start = 0
    width = len(needle)
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        before = haystack[index - 1] if index else ""
        after = haystack[index + width] if index + width < len(haystack) else ""
        if not _is_ascii_alnum(before) and not _is_ascii_alnum(after):
            return True
        start = index + 1


def term_overlap(terms: list[str], text: str) -> list[str]:
    """`terms` 중 실제로 `text` 안에 나타난 것.

    영문 낱말은 **낱말 경계**에서만 인정한다. 이것을 안 보면 질문의 "Sales"가 제품 이름
    "SalesHub" 같은 복합어 안에 박힌 글자열에 걸려, 질문과 아무 상관 없는 발췌가 그 자료의
    대표 근거로 올라간다. 실측이 그랬다: B2B 유통 문항에서 타하렌터카 pain 행이 그런 복합어
    때문에 다른 행보다 10점 앞서, 정작 그 질문이 지목한 카파전선 행을 최종 24건 밖으로 밀어냈다.

    한글은 그대로 부분 문자열로 본다. 붙여 쓰는 말이라 경계를 요구하면 "통제"가
    "통제·모니터링"과 못 만나고, 그러면 겹침 신호가 통째로 죽는다.
    """
    folded = fold(text)
    out: list[str] = []
    for term in terms:
        key = fold(term)
        if not key:
            continue
        if _boundary_match(key, folded) if _needs_word_boundary(key) else key in folded:
            out.append(term)
    return out
