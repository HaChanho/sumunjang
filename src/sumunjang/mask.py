"""마스킹·복원 계층.

원문 → placeholder 치환(mask) → 모델 응답에서 원문 복구(restore).
탐지 코어와 마찬가지로 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import re

from .detect import detect

# placeholder에 쓰는 한국어 라벨. 모델이 "가려진 값"임을 이해하고 문맥을 유지하도록
# 사람이 읽을 수 있는 이름을 쓴다.
_LABELS = {
    "RRN": "주민등록번호",
    "BRN": "사업자등록번호",
    "CARD": "카드번호",
    "PHONE": "전화번호",
    "EMAIL": "이메일",
    "SECRET": "시크릿",
}

_PLACEHOLDER_PATTERN = re.compile(r"\[(?:" + "|".join(_LABELS.values()) + r")_\d+\]")


class Session:
    """한 대화에서 쓰는 마스킹 매핑.

    원문 개인정보를 그대로 담고 있으므로 기본은 메모리에만 둔다.
    """

    def __init__(self) -> None:
        self._placeholder_by_value: dict[tuple[str, str], str] = {}
        self._value_by_placeholder: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def placeholder_for(self, category: str, value: str) -> str:
        """같은 값에는 언제나 같은 이름을 준다 — 모델이 문맥을 잃지 않도록."""
        key = (category, value)
        if key not in self._placeholder_by_value:
            self._counts[category] = self._counts.get(category, 0) + 1
            label = _LABELS.get(category, category)
            placeholder = f"[{label}_{self._counts[category]}]"
            self._placeholder_by_value[key] = placeholder
            self._value_by_placeholder[placeholder] = value
        return self._placeholder_by_value[key]

    def original_for(self, placeholder: str) -> str | None:
        return self._value_by_placeholder.get(placeholder)

    def entries(self) -> list[tuple[str, str]]:
        """가린 항목을 (카테고리, placeholder) 순서대로. 원문은 내보내지 않는다."""
        return [
            (category, self._placeholder_by_value[(category, value)])
            for category, value in self._placeholder_by_value
        ]

    def __len__(self) -> int:
        return len(self._value_by_placeholder)


def mask(text: str, session: Session) -> str:
    """탐지된 개인정보를 placeholder로 바꾼 텍스트를 돌려준다."""
    findings = detect(text)
    if not findings:
        return text

    pieces = []
    cursor = 0
    for finding in findings:
        if finding.start < cursor:
            # 앞선 탐지와 겹친다. 이미 가려진 구간이므로 건너뛴다.
            continue
        pieces.append(text[cursor : finding.start])
        pieces.append(session.placeholder_for(finding.category, text[finding.start : finding.end]))
        cursor = finding.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def restore(text: str, session: Session) -> str:
    """placeholder를 원문으로 되돌린다.

    정확히 일치하는 placeholder만 복원한다. 모델이 형태를 바꿔버린 경우는
    조용히 추측하지 않고 그대로 둔다 — 잘못된 복원이 미복원보다 위험하다.
    """

    def swap(match: re.Match[str]) -> str:
        original = session.original_for(match.group())
        return original if original is not None else match.group()

    return _PLACEHOLDER_PATTERN.sub(swap, text)
