"""한국 개인정보 탐지 코어(L1) — 규칙 + 체크섬.

표준 라이브러리만 사용한다. 개인정보가 지나가는 경로에 서드파티 코드를 두지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 주민등록번호 뒷자리 검증에 쓰는 가중치.
# 앞 12자리 × 가중치의 합을 11로 나눈 나머지로 13번째 자리를 결정한다.
_RRN_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)

# 숫자 식별자에는 모두 경계 조건을 붙인다. 앞뒤에 숫자가 붙어 있으면 더 긴 번호의
# 일부일 뿐이다 — 견적번호 20260806-0012345 의 뒷부분이 주민번호 형태와 우연히
# 일치하는 사례를 골든셋 채점에서 실제로 만났다.
_RRN_PATTERN = re.compile(r"(?<!\d)\d{6}-\d{7}(?!\d)")

# 휴대전화: 010/011/016/017/018/019 + 3~4자리 + 4자리, 구분자는 하이픈/공백/없음
_PHONE_PATTERN = re.compile(r"(?<!\d)01[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")

# 이메일. 최상위 도메인이 숫자면 이메일이 아니다 —
# postgresql://app:pw@10.0.3.14 같은 접속 문자열을 걸러내기 위한 조건이다.
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")

# 카드번호: 4자리 4묶음
_CARD_PATTERN = re.compile(r"(?<!\d)\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}(?!\d)")

# 사업자등록번호: 3-2-5 형식
_BRN_PATTERN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")
_BRN_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)

# API 키·토큰. 개발자가 코드나 설정을 붙여넣을 때 함께 새어나가는 경로다.
# 각 제공자가 공표한 접두사만 사용한다 — 접두사 없는 임의 문자열까지 잡으려 하면
# 오탐이 폭증한다.
_SECRET_PATTERN = re.compile(
    r"\b(?:"
    r"sk-ant-[A-Za-z0-9_-]{16,}"      # Anthropic
    r"|sk-[A-Za-z0-9_-]{20,}"          # OpenAI
    r"|gh[pousr]_[A-Za-z0-9]{36,}"     # GitHub
    r"|AKIA[0-9A-Z]{16}"               # AWS Access Key ID
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"   # Slack
    r")"
)


# 눈에 보이지 않아 탐지를 빠져나가는 문자들. 전각 숫자는 별도 처리가 필요 없다 —
# 파이썬 정규식의 \d와 int()가 유니코드 십진 숫자를 그대로 인식하기 때문이다.
_INVISIBLE = "​‌‍⁠﻿"


@dataclass(frozen=True)
class Finding:
    """탐지 결과 한 건. 원문 좌표(start, end)로만 위치를 표현한다."""

    category: str
    start: int
    end: int


def _strip_invisible(text: str) -> tuple[str, list[int]]:
    """보이지 않는 문자를 걷어낸 텍스트와, 각 글자가 원문 어디서 왔는지의 인덱스.

    마스킹은 언제나 원문 좌표 위에서 일어나야 하므로 정규화된 문자열만으로는
    부족하다. 되돌아갈 지도를 함께 들고 다닌다.
    """
    if not any(ch in _INVISIBLE for ch in text):
        return text, list(range(len(text)))
    chars, origin = [], []
    for index, char in enumerate(text):
        if char in _INVISIBLE:
            continue
        chars.append(char)
        origin.append(index)
    return "".join(chars), origin


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


def _brn_checksum_ok(digits: str) -> bool:
    """사업자등록번호 검증. 9번째 자리는 가중치를 곱한 뒤 십의 자리를 따로 더한다."""
    total = sum(int(d) * w for d, w in zip(digits[:9], _BRN_WEIGHTS))
    total += (int(digits[8]) * 5) // 10
    return (10 - (total % 10)) % 10 == int(digits[9])


def _rrn_valid(matched: str) -> bool:
    digits = matched.replace("-", "")
    return _rrn_checksum_ok(digits) or _rrn_birthdate_ok(digits)


def _card_valid(matched: str) -> bool:
    return _luhn_ok(re.sub(r"[-\s]", "", matched))


def _brn_valid(matched: str) -> bool:
    return _brn_checksum_ok(matched.replace("-", ""))


# 규칙 표: (카테고리, 패턴, 검증기). 검증기가 없으면 패턴만으로 확정한다.
_RULES = (
    ("RRN", _RRN_PATTERN, _rrn_valid),
    ("BRN", _BRN_PATTERN, _brn_valid),
    ("CARD", _CARD_PATTERN, _card_valid),
    ("PHONE", _PHONE_PATTERN, None),
    ("EMAIL", _EMAIL_PATTERN, None),
    ("SECRET", _SECRET_PATTERN, None),
)

# 이 탐지기가 붙일 수 있는 카테고리 전부. 마스킹 계층이 카테고리마다 정책을
# 갖고 있어서, 규칙을 늘렸는데 정책을 빠뜨리면 조용히 어긋난다. 표를 하나로
# 두고 테스트가 대조하게 한다.
CATEGORIES = tuple(category for category, _, _ in _RULES)


def detect(text: str) -> list[Finding]:
    """텍스트에서 한국 개인정보·시크릿을 찾아 원문 좌표로 돌려준다."""
    scan_text, origin = _strip_invisible(text)
    findings = []

    for category, pattern, validator in _RULES:
        for match in pattern.finditer(scan_text):
            if validator is not None and not validator(match.group()):
                continue
            findings.append(
                Finding(
                    category=category,
                    start=origin[match.start()],
                    end=origin[match.end() - 1] + 1,
                )
            )

    return sorted(findings, key=lambda f: (f.start, f.end))
