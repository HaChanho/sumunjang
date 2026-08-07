"""마스킹·복원 계층.

원문 → placeholder 치환(mask) → 모델 응답에서 원문 복구(restore).
탐지 코어와 마찬가지로 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import re

from .detect import Finding, find_in_normalized, normalize

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
# 상한은 **퇴출선이 아니라 거부선**이다. 처음에는 오래된 것부터 버렸는데, 버린
# 값은 다음 턴에 다시 가려지지 않아 그대로 유출됐다 — 대화 기록은 매 턴 다시
# 전송되므로 퇴출은 과거를 지우는 것이 아니라 보호를 푸는 것이었다. 상한에
# 닿으면 버리는 대신 요청을 거부한다. 메모리도 상한이고 유출도 없다.
_DEFAULT_CAPACITY = 10_000

# 값 하나가 담을 수 있는 길이. 개수만 세면 거대한 값 하나로 메모리를 밀어낼 수
# 있다. 이 길이를 넘는 개인정보는 현실에 없다.
_MAX_VALUE_LENGTH = 4_096


class SessionFull(Exception):
    """세션 상한에 닿았다. 요청을 거부해야 한다.

    프록시가 이 예외를 잡아 업스트림 전송 없이 오류를 돌려준다. 서로 다른
    개인정보 만 건은 현실적으로 닿기 어려운 지점이므로, 닿았다면 프록시를
    다시 띄워야 할 상황이다.
    """


class Session:
    """한 대화에서 쓰는 마스킹 매핑.

    원문 개인정보를 그대로 담고 있으므로 기본은 메모리에만 두고, 담는 양에
    상한을 둔다. 상한에 닿으면 버리지 않고 SessionFull 을 던져 요청을 거부한다 —
    이유는 _DEFAULT_CAPACITY 주석에 적었다.
    """

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._placeholder_by_value: dict[tuple[str, str], str] = {}
        self._value_by_placeholder: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        # known_spans() 가 훑는 값→카테고리. 긴 값을 먼저 보아야 짧은 값이
        # 긴 값의 앞부분을 먼저 먹지 않는다.
        self._category_of: dict[str, str] = {}
        # 첫 글자 → 그 글자로 시작하는 값들(긴 것부터). known_spans 가 쓰는 색인이다.
        self._by_first: dict[str, list[str]] = {}
        # 우리가 업스트림 응답에서 실제로 내보낸 추론 서명들.
        # 예외를 요청자의 선언이 아니라 출처로 판정하기 위한 기억이다.
        self._emitted: dict[str, str] = {}

    def placeholder_for(self, category: str, value: str, canonical: str | None = None) -> str:
        """같은 값에는 언제나 같은 이름을 준다 — 모델이 문맥을 잃지 않도록.

        `canonical` 은 재탐색에 쓸 정규화된 형태다. 복원은 사용자가 쓴 원문을
        되돌려야 하지만 재탐색은 정규화된 텍스트에서 이루어지므로, 둘을 나눠
        기억한다. 원문 조각만 기억했더니 NFD 로 처음 본 값이 영원히 자기를
        알아보지 못했다 — 프록시가 자기 출력을 다시 못 알아보는 자기 유발
        유출이었다.
        """
        # 동일성은 정규형으로 판단하고, 복원은 원문을 되돌린다. 키를 원문으로
        # 두면 같은 사람의 NFC 표기와 NFD 표기가 다른 가명을 받아, 모델이
        # 두 사람으로 읽는다.
        찾을값 = canonical if canonical is not None else value
        key = (category, 찾을값)
        if key in self._placeholder_by_value:
            return self._placeholder_by_value[key]

        if len(value) > _MAX_VALUE_LENGTH:
            raise SessionFull(
                f"가릴 값이 {_MAX_VALUE_LENGTH}자를 넘습니다. 개인정보로 보기 어렵습니다."
            )

        if len(self._placeholder_by_value) >= self._capacity:
            raise SessionFull(
                f"세션 상한 {self._capacity}건에 도달했습니다. "
                "가린 값을 버리면 다음 턴에 다시 가려지지 않아 유출되므로 요청을 거부합니다."
            )

        self._counts[category] = self._counts.get(category, 0) + 1
        label = _LABELS.get(category, category)
        placeholder = f"[{label}_{self._counts[category]}]"
        self._placeholder_by_value[key] = placeholder
        self._value_by_placeholder[placeholder] = value
        self._category_of[찾을값] = category
        묶음 = self._by_first.setdefault(찾을값[0], [])
        묶음.append(찾을값)
        # 긴 값을 먼저 봐야 짧은 값이 긴 값의 앞부분을 먼저 먹지 않는다.
        묶음.sort(key=len, reverse=True)
        return placeholder

    def remember_thinking(self, signature: str, body: str) -> None:
        """업스트림이 내려보낸 추론 블록을 서명과 본문 **짝으로** 기억한다.

        추론 블록을 마스킹에서 빼주려면 그것이 진짜인지 알아야 하는데, 본문에
        적힌 `type: "thinking"` 은 요청자가 그냥 쓰는 값이라 근거가 되지 못한다.
        위조할 수 없는 신호는 우리가 그 블록을 내보낸 적이 있는가다.

        서명만 기억했더니 그것으로도 부족했다 — 진짜 서명을 그대로 두고 본문만
        개인정보로 갈아 끼우면 통과했다. 업스트림이 나중에 서명 불일치로 거부해도
        원문은 이미 나간 뒤다. 짝으로 기억해 본문까지 대조한다.
        """
        if signature:
            self._emitted[signature] = body

    def emitted_thinking(self, signature: str, body: str) -> bool:
        return self._emitted.get(signature) == body

    def original_for(self, placeholder: str) -> str | None:
        return self._value_by_placeholder.get(placeholder)

    def known_spans(self, text: str) -> list[tuple[str, int, int]]:
        """이미 가린 값이 이 텍스트에 다시 나타난 자리를 (카테고리, 시작, 끝)로.

        마스킹은 문맥에 의존하지만(앵커) 복원은 문맥과 무관하다. 그래서 복원이
        값을 탐지기가 알아볼 수 없는 문맥으로 옮겨 놓는다 — 모델이 가명 표시를
        설명 대상으로 언급하면 그 자리에 원문이 놓이고, 다음 턴에 대화 기록이
        다시 전송될 때 앵커가 없어 잡히지 않는다. 실왕복에서 이름이 실제로
        업스트림에 두 번 나갔다.

        세션은 자기가 가린 값을 알고 있다. 그것으로 고리를 닫는다.
        한 번 가린 값은 문맥이 바뀌어도 계속 가린다.

        찾는 방법은 문자열 탐색이다. 처음에는 값 전부를 하나의 거대한 정규식
        대안으로 묶었는데, 파이썬 정규식은 대안을 trie 로 접지 않고 **모든 입력
        위치에서 모든 대안을 차례로 시도한다.** 상한 10,000 에 도달하면 짧은
        본문 한 번 훑는 데 20초를 넘겼다. 상한값 자체가 프록시를 사용 불능으로
        만드는 지점을 허용하고 있었던 셈이다. str.find 와 첫 글자 색인으로
        바꾼 지금은 같은 조건(세션 1만 건, 138KB 본문)에서 0.1~0.3초다.
        여전히 요청 하나를 동기로 붙잡으므로 큰 본문에서는 이벤트 루프가
        그만큼 멈춘다 — 정확성이 아니라 가용성의 한계다.
        """
        # 본문에 실제로 등장하는 첫 글자만 훑는다. 세션 값 전부를 매번 정렬해
        # 훑으면 비용이 (본문 문자열 수 × 세션 크기)로 곱해진다 — 세션 1만 건에
        # 138KB 본문이 1.4초였다. 색인을 미리 만들어 두고 등장하는 글자만 본다.
        후보: list[str] = []
        본문글자 = set(text)
        for 첫글자, 값들 in self._by_first.items():
            if 첫글자 in 본문글자:
                후보.extend(값들)
        if not 후보:
            return []

        spans: list[tuple[str, int, int]] = []
        for value in sorted(후보, key=len, reverse=True):
            start = text.find(value)
            while start != -1:
                if self._boundary_ok(value, text, start):
                    spans.append((self._category_of[value], start, start + len(value)))
                start = text.find(value, start + 1)

        return spans

    @staticmethod
    def _boundary_ok(value: str, text: str, start: int) -> bool:
        """이 자리에서 값을 가려도 되는가.

        한글 값(이름)만 앞경계를 본다. "박이준" 안의 "이준" 은 다른 사람이므로
        가리면 안 되기 때문이다. 뒤쪽은 보지 않는다 — 한국어는 이름 뒤에 조사와
        직함이 그대로 붙는다("김수현씨", "김수현 책임").

        숫자·영문 값에는 앞경계를 적용하지 않는다. 적용했더니 "잔액110-234-567890"
        처럼 한글이 바로 붙은 자리에서 계좌번호가 가려지지 않았다 — 낱말이
        망가지는 것을 막으려던 규칙이 유출 방향으로 작동했다.
        """
        앞 = text[start - 1] if start else ""
        뒤 = text[start + len(value)] if start + len(value) < len(text) else ""

        if "가" <= value[0] <= "힣":
            # 한글 값(이름)은 앞경계만 본다. "박이준" 안의 "이준" 은 다른 사람이다.
            return not ("가" <= 앞 <= "힣")

        # 숫자로 시작하는 값은 앞뒤로 숫자가 붙으면 안 된다. 경계를 아예 두지
        # 않았더니 세션이 아는 계좌번호가 더 긴 주문번호 가운데를 갈랐다 —
        # "주문번호 991234567890123" 이 "주문번호 99[계좌번호_1]123" 이 됐다.
        # 유출은 아니지만 무관한 데이터를 조용히 훼손해 모델에게 넘긴다.
        if value[0].isdecimal():
            return not 앞.isdecimal() and not 뒤.isdecimal()

        return True

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
    """탐지된 개인정보를 placeholder로 바꾼 텍스트를 돌려준다.

    정규화를 한 번만 하고 규칙 탐지와 세션 재탐색이 **같은 텍스트**를 보게 한다.
    세션 재탐색이 원문을 그대로 훑던 동안, 이미 가린 이름에 제로폭 하나만
    끼우면 빠져나갈 수 있었다 — 두 경로가 다른 것을 보면 한쪽만 뚫린다.
    """
    scan_text, starts, ends = normalize(text)
    구간 = set(find_in_normalized(scan_text)) | set(session.known_spans(scan_text))
    if not 구간:
        return text

    # 정규화 좌표를 원문 좌표와 짝지어 들고 간다. 치환은 원문 위에서 하고,
    # 세션에 기억시킬 재탐색용 값은 정규화된 쪽에서 떠낸다.
    정규화좌표 = {(starts[s], ends[e - 1]): (s, e) for _, s, e in 구간}
    findings = sorted(
        (Finding(c, starts[s], ends[e - 1]) for c, s, e in 구간),
        key=lambda f: (f.start, f.end),
    )

    pieces = []
    cursor = 0
    for category, start, end in _merge_overlapping(findings):
        pieces.append(text[cursor:start])
        n = 정규화좌표.get((start, end))
        표준형 = scan_text[n[0] : n[1]] if n else None
        pieces.append(session.placeholder_for(category, text[start:end], 표준형))
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
