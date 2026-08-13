"""표기 되돌리기 — 발췌의 표기로만 되돌리고, 없던 값은 만들지 않는다.

실측 두 건에서 나왔다(LEAD-FINDINGS D-5): 모델이 `한서버` 를 `한 서버` 로,
`2026-06-01` 을 `2026년 6월 1일` 로 고쳐 써서 그 값을 원문에서 찾을 수 없었다.
"""

from api.notation import restore_notation

MEMO = "현 구조 (교보+오로라소프트 한서버에서 멀티 고객 대응) 운영 조직 필요"
LOG = "최초인입일: 2025-12-03 | 최종활동일: 2026-06-01 | 영업대표: 홍길동"


def test_spacing_is_restored_to_the_source_form():
    out = restore_notation("교보와 오로라소프트가 한 서버에서 대응하는 구조다.", [MEMO])
    assert "한서버" in out


def test_korean_date_becomes_the_iso_form_that_the_source_uses():
    out = restore_notation("2026년 6월 1일을 최종활동일로 한 기록이다.", [LOG])
    assert "2026-06-01" in out


def test_a_date_absent_from_the_source_is_left_alone():
    """발췌에 없는 날짜를 바꾸면 근거 없는 값을 만든 것이 된다."""
    out = restore_notation("2030년 1월 2일 예정이다.", [LOG])
    assert "2030년 1월 2일" in out
    assert "2030-01-02" not in out


def test_a_word_absent_from_the_source_is_not_invented():
    out = restore_notation("우리 회사 서버 상태를 본다.", [MEMO])
    assert out == "우리 회사 서버 상태를 본다."
