"""한국 개인정보 탐지 코어(L1) — 규칙 + 체크섬.

표준 라이브러리만 사용한다. 개인정보가 지나가는 경로에 서드파티 코드를 두지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 주민등록번호 뒷자리 검증에 쓰는 가중치.
# 앞 12자리 × 가중치의 합을 11로 나눈 나머지로 13번째 자리를 결정한다.
_RRN_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)

_RRN_PATTERN = re.compile(r"\d{6}-\d{7}")

# 휴대전화: 010/011/016/017/018/019 + 3~4자리 + 4자리, 구분자는 하이픈/공백/없음
_PHONE_PATTERN = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# 카드번호: 4자리 4묶음
_CARD_PATTERN = re.compile(r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}")


@dataclass(frozen=True)
class Finding:
    """탐지 결과 한 건. 원문 좌표(start, end)로만 위치를 표현한다."""

    category: str
    start: int
    end: int


def _rrn_checksum_ok(digits: str) -> bool:
    total = sum(int(d) * w for d, w in zip(digits[:12], _RRN_WEIGHTS))
    return (11 - (total % 11)) % 10 == int(digits[12])


def _rrn_birthdate_ok(digits: str) -> bool:
    """앞 6자리 생년월일과 7번째 성별코드의 정합성을 본다.

    2020.10 이후 발급분은 뒷자리가 임의번호라 체크섬이 통과하지 않는다.
    체크섬을 유일한 관문으로 두면 그 세대를 통째로 놓치므로, 생년월일이
    실재하는 날짜인지를 별도 관문으로 쓴다.
    """
    century = {
        "1": 1900, "2": 1900, "5": 1900, "6": 1900,
        "3": 2000, "4": 2000, "7": 2000, "8": 2000,
        "9": 1800, "0": 1800,
    }.get(digits[6])
    if century is None:
        return False
    year, month, day = century + int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    if not 1 <= month <= 12:
        return False
    days_in_month = [31, 29 if _is_leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= day <= days_in_month[month - 1]


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _luhn_ok(digits: str) -> bool:
    """카드번호 Luhn 검증.

    전치·단일오타를 잡는 알고리즘이라 규칙적인 반복 패턴은 통과할 수 있다.
    오탐을 줄이는 관문이지 카드번호임을 증명하는 근거는 아니다.
    """
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def detect(text: str) -> list[Finding]:
    findings = []

    for match in _RRN_PATTERN.finditer(text):
        digits = match.group().replace("-", "")
        if _rrn_checksum_ok(digits) or _rrn_birthdate_ok(digits):
            findings.append(Finding(category="RRN", start=match.start(), end=match.end()))

    for match in _PHONE_PATTERN.finditer(text):
        findings.append(Finding(category="PHONE", start=match.start(), end=match.end()))

    for match in _EMAIL_PATTERN.finditer(text):
        findings.append(Finding(category="EMAIL", start=match.start(), end=match.end()))

    for match in _CARD_PATTERN.finditer(text):
        digits = re.sub(r"[-\s]", "", match.group())
        if _luhn_ok(digits):
            findings.append(Finding(category="CARD", start=match.start(), end=match.end()))

    return findings
