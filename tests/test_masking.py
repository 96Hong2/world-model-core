"""PII 마스킹 단위 테스트.

기대값의 근거는 config/pii-patterns.yaml 의 detector 정의다. 픽스처의 PII 는
전부 합성값이며, 값의 '형태'는 실측에서 확인된 유형(하이픈 유무·13자리 오입력·
대표번호·주민번호 하이픈 유무)을 그대로 따른다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.masking import MaskedText, Masker  # noqa: E402

# config/pii-patterns.yaml detectors 와 같은 정규식
PHONE_RES = [
    re.compile(r"01[016789][-. ]?\d{3,4}[-. ]?\d{4}"),
    re.compile(r"0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4])[-. ]?\d{3,4}[-. ]?\d{4}"),
    re.compile(r"1[5-9]\d{2}[-. ]?\d{4}"),
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RRN_RE = re.compile(r"\d{6}[-. ]?[1-4]\d{6}")


def phone_matches(text: str) -> list[str]:
    out = []
    for rx in PHONE_RES:
        out.extend(rx.findall(text))
    return out


# 일부러 PII 형태를 넣은 합성 픽스처.
PII_FIXTURE = [
    "담당자 연락처는 010-3456-7890 입니다",
    "하이픈 없는 번호 01087654321 도 있다",
    "13자리 오입력 0101234567890",
    "대표번호 02-123-4567 / 02-987-6543",
    "상담센터 1588-0000 으로 연락",
    "메일 hong.gildong@gamil.com 로 회신",
    "주민번호 900101-1111111 과 9001011234567",
    "사업자등록번호 123-45-67890",
    "계좌 이체는 110-987-654321 로",
]


def test_masking_removes_every_phone_number():
    m = Masker()
    for raw in PII_FIXTURE:
        out = m.mask(raw)
        assert isinstance(out, MaskedText)
        assert phone_matches(out.text) == [], f"{raw!r} -> {out.text!r}"


def test_masking_removes_email_and_rrn():
    m = Masker()
    assert EMAIL_RE.search(m.mask("메일 hong.gildong@gamil.com 로 회신").text) is None
    assert RRN_RE.search(m.mask("주민번호 900101-1111111").text) is None
    assert RRN_RE.search(m.mask("주민번호 9001011234567").text) is None


def test_masking_placeholders_never_rematch():
    """마스킹 결과가 어떤 detector 정규식에도 다시 매칭되면 안 된다(pii-patterns self_check)."""
    m = Masker()
    for raw in PII_FIXTURE:
        out = m.mask(raw)
        assert m.scan(out.text) == [], f"{out.text!r} 에 PII 가 남았다"


def test_masking_records_what_it_hid():
    m = Masker()
    out = m.mask("연락처 010-3456-7890, 메일 hong.gildong@gamil.com")
    kinds_hit = {h.kind for h in out.hits}
    assert "PII-MOBILE" in kinds_hit
    assert "PII-EMAIL" in kinds_hit
    assert sum(h.count for h in out.hits) >= 2


def test_masking_keeps_allowlisted_role_mailbox():
    """info@auroraworks.example 는 자사 공개 창구다(pii-patterns allowlist)."""
    m = Masker()
    out = m.mask("info@auroraworks.example 로 접수된 리드")
    assert "info@auroraworks.example" in out.text


def test_masking_guards_do_not_eat_slack_ts_or_epoch():
    m = Masker()
    assert "1743492135.982449" in m.mask("ts 1743492135.982449").text
    assert "1700642391152" in m.mask("epoch 1700642391152").text


def test_masking_keeps_internal_names_and_hides_external_ones():
    """사내 임직원 이름은 유지, 사외 개인은 deny-list 마스킹."""
    m = Masker()
    assert "홍길동" in m.mask("작성자 홍길동").text
    assert "남소리" not in m.mask("고객사 담당자 남소리 차장").text


def test_masking_by_column_name_hides_customer_contact_person():
    """정규식에 안 걸리는 실명은 열 이름으로 선제 마스킹한다(NE-COLUMN)."""
    m = Masker()
    out = m.mask("강새별 부장", column_name="고객사 담당자")
    assert "강새별" not in out.text
    assert out.text == "[담당자]"


def test_masking_disabled_is_actually_detected():
    """차단 케이스: 마스킹을 끄면 게이트 검사가 반드시 실패해야 한다.

    이 테스트가 통과한다는 것은 위의 '전화번호 0건' 단언이 빈 검사가 아니라는 뜻이다.
    """
    broken = Masker(_disable_all=True)
    leaked = [raw for raw in PII_FIXTURE if phone_matches(broken.mask(raw).text)]
    assert leaked, "마스킹을 껐는데도 전화번호가 안 잡힌다면 검사 자체가 무효다"


def test_masked_text_never_carries_raw_value():
    """마스킹 전 문자열을 내보내는 경로가 없어야 한다."""
    m = Masker()
    out = m.mask("연락처 010-3456-7890")
    assert "010-3456-7890" not in out.text
    for hit in out.hits:
        assert "010-3456-7890" not in repr(hit)
