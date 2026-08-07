"""마스킹·복원 계층.

원문 → placeholder 치환(mask) → 모델 응답에서 원문 복구(restore).
탐지 코어와 마찬가지로 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from .detect import Finding, detect

# placeholder에 쓰는 한국어 라벨. 모델이 "가려진 값"임을 이해하고 문맥을 유지하도록
# 사람이 읽을 수 있는 이름을 쓴다.
_LABELS = {
    "RRN": "주민등록번호",
    "NAME": "이름",
    "ACCOUNT": "계좌번호",
    "PASSPORT": "여권번호",
    "LICENSE": "운전면허번호",
    "BRN": "사업자등록번호",
    "CARD": "카드번호",
    "PHONE": "전화번호",
    "EMAIL": "이메일",
    "SECRET": "시크릿",
}

_PLACEHOLDER_PATTERN = re.compile(r"\[(?:" + "|".join(_LABELS.values()) + r")_\d+\]")


# 세션이 들고 있을 매핑의 상한. 프록시는 세션 하나를 프로세스 수명 내내 들고
# 있으므로, 상한이 없으면 그날 오간 모든 개인정보가 원문 그대로 메모리에 쌓인다.
# 보안 도구가 스스로 개인정보 저장소가 되어서는 안 된다.
#
# 하루치 대화에서 서로 다른 개인정보가 만 건을 넘는 일은 드물고, 넘더라도 대가는
# 미복원이지 유출이 아니다.
_DEFAULT_CAPACITY = 10_000


class Session:
    """한 대화에서 쓰는 마스킹 매핑.

    원문 개인정보를 그대로 담고 있으므로 기본은 메모리에만 두고, 담는 양에
    상한을 둔다. 상한을 넘으면 가장 오래 쓰이지 않은 것부터 버린다.

    버려진 값은 복원되지 않을 뿐 유출되지 않는다 — 화면에 원문 대신 가명 표시가
    남는다. 잘못된 복원이 미복원보다 위험하다는 원칙과 같은 방향이다.
    """

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._placeholder_by_value: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._value_by_placeholder: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def placeholder_for(self, category: str, value: str) -> str:
        """같은 값에는 언제나 같은 이름을 준다 — 모델이 문맥을 잃지 않도록."""
        key = (category, value)
        if key in self._placeholder_by_value:
            # 다시 쓰였으므로 뒤로 보낸다. 도구는 매 턴 대화 전체를 다시 보내므로
            # 앞부분의 개인정보도 매번 다시 가려진다. 등장 순서가 아니라 마지막으로
            # 쓰인 시점을 기준으로 버려야 긴 대화에서 앞부분이 먼저 사라지지 않는다.
            self._placeholder_by_value.move_to_end(key)
            return self._placeholder_by_value[key]

        self._counts[category] = self._counts.get(category, 0) + 1
        label = _LABELS.get(category, category)
        placeholder = f"[{label}_{self._counts[category]}]"
        self._placeholder_by_value[key] = placeholder
        self._value_by_placeholder[placeholder] = value
        self._evict_overflow()
        return placeholder

    def _evict_overflow(self) -> None:
        while len(self._placeholder_by_value) > self._capacity:
            _, evicted = self._placeholder_by_value.popitem(last=False)
            # 두 사전을 함께 비운다. 한쪽만 지우면 원문이 계속 메모리에 남는다.
            self._value_by_placeholder.pop(evicted, None)

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


# 겹친 구간의 라벨을 고르는 서열. 앞쪽이 더 민감하다.
#
# 축은 하나다 — **이 값 하나로 다른 문을 얼마나 열 수 있는가, 그리고 그 자물쇠를
# 바꿀 수 있는가.** 축을 하나로 두어야 "왜 그 순서냐"에 한 문장으로 답할 수 있다.
#
#   RRN     한국 본인확인의 마스터키. 유출돼도 바꿀 수 없다 (개인정보보호법
#           제24조의2가 처리 자체를 별도 법정주의로 묶어둔 이유이기도 하다).
#   SECRET  그 뒤의 시스템 전체를 연다. 다만 회전으로 즉시 무효화할 수 있다.
#   ACCOUNT 예금을 연다. 카드보다 위인 것은 자물쇠를 바꾸기가 더 어렵기
#           때문이다 — 카드는 재발급이면 끝나지만 계좌를 바꾸려면 연결된
#           자동이체와 급여 계좌를 전부 옮겨야 한다.
#   CARD    금전을 연다. 재발급으로 무효화된다.
#   LICENSE 국내 본인확인 수단이다. 휴대폰 개통·성인인증에 쓰인다.
#   PASSPORT 신분 증명의 근거이지만 여는 문이 출입국 맥락에 한정된다.
#           재발급하면 번호가 바뀐다.
#   PHONE   본인인증 2차 채널(문자 OTP)을 연다. 바꾸는 비용이 크다.
#   EMAIL   계정 복구 채널을 연다. 바꾸는 비용이 크다.
#   NAME    그 자체로는 문을 열지 못한다. 동명이인이 흔해 단독으로는 특정도
#           어렵다. 다만 다른 값과 붙는 순간 특정력이 급격히 오른다.
#   BRN     아무 문도 열지 못한다. 국세청에서 공개 조회된다.
#
# 라벨은 두 독자를 향한다. 모델에게는 가려진 자리가 무엇이었는지 알려 문맥을
# 유지시키고, 로그를 보는 사람에게는 이 자리가 얼마나 위험했는지 알린다.
# 겹쳤을 때 덜 민감한 이름을 붙이면 뒤쪽 독자가 위험을 과소평가한다.
SEVERITY = (
    "RRN", "SECRET", "ACCOUNT", "CARD", "LICENSE", "PASSPORT",
    "PHONE", "EMAIL", "NAME", "BRN",
)


def _merged_category(overlapping: list[Finding]) -> str:
    """겹쳐서 한 구간으로 합쳐진 탐지들에게 줄 카테고리 하나를 고른다."""
    if len(overlapping) == 1:
        return overlapping[0].category

    def rank(finding: Finding) -> int:
        # 서열에 없는 카테고리는 가장 민감한 쪽으로 친다. 규칙을 늘리면서 서열을
        # 빠뜨렸을 때 위험을 낮춰 부르는 실수를 하지 않기 위해서다. 빠뜨림 자체는
        # 테스트가 CATEGORIES 와 대조해 시끄럽게 잡는다.
        return SEVERITY.index(finding.category) if finding.category in SEVERITY else -1

    return min(overlapping, key=rank).category


def _merge_overlapping(findings: list[Finding]) -> list[tuple[str, int, int]]:
    """겹치는 탐지를 합집합 구간으로 합친다.

    겹침을 "앞선 것이 이겼으니 뒤는 버린다"로 처리하면 뒤 탐지가 앞 탐지 밖으로
    삐져나온 부분이 평문으로 남는다. 전화번호와 카드번호가 자릿수를 공유하는
    경우가 실제로 그렇다. 탐지기가 표시한 바이트는 하나도 남기지 않는다.
    """
    merged: list[tuple[str, int, int]] = []
    group: list[Finding] = []
    group_end = 0

    for finding in findings:
        # 끝점은 그룹 안에서 가장 먼 곳으로 잡는다. 정렬이 (start, end) 순이라
        # 뒤에 오는 탐지가 앞 탐지 안에 완전히 들어앉는 경우가 있고, 그때
        # 마지막 탐지의 end 를 쓰면 구간이 도로 줄어든다.
        if group and finding.start < group_end:
            group.append(finding)
            group_end = max(group_end, finding.end)
            continue
        if group:
            merged.append((_merged_category(group), group[0].start, group_end))
        group = [finding]
        group_end = finding.end

    if group:
        merged.append((_merged_category(group), group[0].start, group_end))
    return merged


def mask(text: str, session: Session) -> str:
    """탐지된 개인정보를 placeholder로 바꾼 텍스트를 돌려준다."""
    findings = detect(text)
    if not findings:
        return text

    pieces = []
    cursor = 0
    for category, start, end in _merge_overlapping(findings):
        pieces.append(text[cursor:start])
        pieces.append(session.placeholder_for(category, text[start:end]))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def placeholders_in(text: str) -> list[str]:
    """텍스트에 있는 가명 표시의 카테고리 목록. 라벨을 카테고리 코드로 되돌린다."""
    by_label = {label: category for category, label in _LABELS.items()}
    return [
        by_label[match.group()[1:].rsplit("_", 1)[0]]
        for match in _PLACEHOLDER_PATTERN.finditer(text)
    ]


def restore(text: str, session: Session) -> str:
    """placeholder를 원문으로 되돌린다.

    정확히 일치하는 placeholder만 복원한다. 모델이 형태를 바꿔버린 경우는
    조용히 추측하지 않고 그대로 둔다 — 잘못된 복원이 미복원보다 위험하다.
    """

    def swap(match: re.Match[str]) -> str:
        original = session.original_for(match.group())
        return original if original is not None else match.group()

    return _PLACEHOLDER_PATTERN.sub(swap, text)
