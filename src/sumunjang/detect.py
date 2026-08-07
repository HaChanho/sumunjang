"""한국 개인정보 탐지 코어(L1) — 규칙 + 체크섬.

표준 라이브러리만 사용한다. 개인정보가 지나가는 경로에 서드파티 코드를 두지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 주민등록번호 뒷자리 검증에 쓰는 가중치.
# 앞 12자리 × 가중치의 합을 11로 나눈 나머지로 13번째 자리를 결정한다.
_RRN_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)

# 숫자 식별자에는 모두 경계 조건을 붙인다. 앞뒤에 숫자가 붙어 있으면 더 긴 번호의
# 일부일 뿐이다 — 견적번호 20260806-0012345 의 뒷부분이 주민번호 형태와 우연히
# 일치하는 사례를 골든셋 채점에서 실제로 만났다.
# 하이픈은 선택이다. DB 컬럼과 로그에는 13자리로 붙여 쓴 형태가 흔하다.
# 다만 하이픈 유무에 따라 요구하는 관문이 다르다 — _rrn_valid 참고.
_RRN_PATTERN = re.compile(r"(?<!\d)\d{6}-?\d{7}(?!\d)")

# 휴대전화: 010/011/016/017/018/019 + 3~4자리 + 4자리.
# 구분자는 하이픈·점·공백·없음. 해외 지사에서 오는 +82 표기도 받는다
# (국가번호를 붙이면 앞의 0 이 빠진다).
# 리터럴 0·1 대신 \d 를 쓰는 이유는 전각 숫자 때문이다. \d 는 유니코드 십진
# 숫자를 인식하지만 리터럴 "0" 은 "０" 과 다른 코드포인트라 전각 표기를 놓쳤다.
# 통신사 식별번호 확인은 패턴이 아니라 _phone_valid 가 한다.
_PHONE_PATTERN = re.compile(
    r"(?:(?<!\d)\d|\+\d\d[-.\s]?)\d{2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
)

# 국내 휴대전화 식별번호. 010 이 대부분이고 나머지는 번호이동 전 구번호다.
_PHONE_PREFIXES = ("010", "011", "016", "017", "018", "019")

# 이메일. 최상위 도메인이 숫자면 이메일이 아니다 —
# postgresql://app:pw@10.0.3.14 같은 접속 문자열을 걸러내기 위한 조건이다.
#
# 수량자에 상한을 둔 것은 스타일이 아니라 방어다. 무한 반복 `[\w.+-]+@` 는 @ 가
# 없는 긴 입력에서 시작 위치마다 전체를 되짚어 입력 길이의 제곱으로 커진다.
# base64·JWT·hex 처럼 문자·숫자·하이픈·밑줄만 길게 이어지는 문자열이 전부
# 해당하고, 도구가 파일 내용을 그대로 실어 보내므로 실제로 닿는 경로다.
# 로컬파트 64자·라벨 63자는 RFC 5321·1035 의 상한이기도 하다.
_EMAIL_PATTERN = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){0,8}\.[A-Za-z]{2,24}")

# 카드번호. 16자리 4묶음이 대부분이지만 Amex 는 15자리(4-6-5), Diners 는
# 14자리(4-6-4)다. 16자리만 받으면 그 둘이 평문으로 나간다.
# 자릿수·카드사 대역·Luhn 검사는 _card_valid 가 한다.
_CARD_PATTERN = re.compile(
    r"(?<!\d)\d{4}[-\s]?\d{4,6}[-\s]?\d{4,5}(?:[-\s]?\d{1,7})?(?!\d)"
)

# 사업자등록번호: 3-2-5 형식
_BRN_PATTERN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")
_BRN_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)

# API 키·토큰. 개발자가 코드나 설정을 붙여넣을 때 함께 새어나가는 경로다.
# 각 제공자가 공표한 접두사만 사용한다 — 접두사 없는 임의 문자열까지 잡으려 하면
# 오탐이 폭증한다.
# 여기서도 \b 대신 영숫자 경계를 쓴다 — 한글이 \w 라서 "키sk-ant-…" 가 새어나간다.
_SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-ant-[A-Za-z0-9_-]{16,}"      # Anthropic
    r"|sk-[A-Za-z0-9_-]{20,}"          # OpenAI
    r"|gh[pousr]_[A-Za-z0-9]{36,}"     # GitHub
    r"|AKIA[0-9A-Z]{16}"               # AWS Access Key ID
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"   # Slack
    r")"
)


# 한국 성씨는 닫힌 집합이다. 앵커("성명:")만으로는 "담당: 미정" 같은 말까지
# 이름으로 읽히므로 사전으로 한 겹 더 거른다. 인구의 대부분을 덮는 성씨를 담되,
# 두 글자 성씨를 먼저 둔다 — 정규식 선택지는 앞에서부터 시도되므로 "남궁" 이
# "남" 에 가려지면 안 된다.
# 인구의 약 97%를 덮는 상위 50개 성씨. 꼬리를 자른 것은 커버리지 때문이 아니라
# 오탐 때문이다 — 연·소·도·선·설·명·표·기·금·인·예·사·부 같은 희귀 성씨는
# 한국어 단어의 첫 음절로도 흔해서(연락, 소속, 도착, 선택, 설정, 명세, 표기,
# 기간, 금액, 인수, 예정, 사업, 부서) 3%를 더 얻자고 오탐을 몇 배로 늘린다.
# 두 글자 성씨를 먼저 둔다 — 정규식 선택지는 앞에서부터 시도되므로 "남궁" 이
# "남" 에 가려지면 안 된다.
_SURNAMES = (
    "남궁|황보|선우|제갈|독고|사공|서문|"
    "김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|류|전|홍|고|문|양|손|"
    "배|백|허|유|남|심|노|하|곽|성|차|주|우|구|나|민|진|지|엄|채|원|천|방|공|현"
)

# 이름은 성씨 + 1~2자, 뒤에 한글이 이어지지 않아야 한다.
#
# 길이를 3자로 넓히지 않는 이유: "김수현책임" 처럼 붙여 쓴 직함까지 먹는다.
# 뒤에 한글이 오면 안 된다는 조건이 오탐을 크게 줄인다 — "연락처는" 이 성씨
# "연" 으로 읽히던 실제 오탐이 이 조건으로 걸러진다. 대가로 "최윤서입니다"
# 처럼 조사·서술어가 바로 붙은 형태는 놓치지만, 구조화된 문서에서 이름 뒤는
# 줄바꿈·공백·쉼표다.
_NAME_VALUE = rf"(?:{_SURNAMES})[가-힣]{{1,2}}(?![가-힣])"

# 앵커와 값 사이에 허용하는 것. 값의 생김새가 증거를 얼마나 담고 있느냐에 따라
# 둘로 나눈다.
#
# 이름은 생김새가 증거를 거의 담지 못한다 — "최윤서" 와 "배송팀" 은 형태가 같다.
# 그래서 앵커를 바짝 조여 콜론·등호를 반드시 요구한다. 이것이 "구조화된 문서
# 한정" 이라는 범위 결정을 주석이 아니라 정규식으로 강제하는 자리다. 구분자를
# 선택으로 두면 "담당자 연락처는" 같은 산문까지 걸린다.
_GAP_STRICT = r"[ \t]*[:：=＝][ \t]*"

# 숫자 식별자는 자릿수와 묶음 자체가 이미 증거다. 그래서 앵커를 느슨하게 둬도
# 된다 — "계좌: 국민은행 110-234-567890" 처럼 은행 이름이 끼어드는 쪽이 오히려
# 보통이다. 숫자를 넣지 않아 값의 앞부분을 먹지 않고, 줄바꿈을 넣지 않아
# 사정거리는 같은 줄까지다. 넓히면 문서 전체가 앵커 하나에 물든다.
_GAP_LOOSE = r"[^\n\d]{0,12}"


# 계좌번호는 은행마다 형식이 다르고(3-3-6, 6-2-6, 4-3-6, 3-4-4-2 …) 검증식이
# 없다. 형태를 좁게 잡을 수도, 검증할 수도 없으므로 자릿수 범위만 두고 나머지는
# 앵커에 맡긴다.
_ACCOUNT_ANCHORS = "계좌번호|계좌|예금주|입금|출금|송금|이체|account"
# 하이픈으로 묶인 형태는 묶음 자체가 증거다. 맨 숫자는 아무 증거도 아니라
# 따로 둔다 — 앵커를 다르게 걸기 위해서다.
_ACCOUNT_GROUPED = r"(?<!\d)\d{2,6}(?:-\d{2,7}){1,3}(?!\d)"
_ACCOUNT_BARE = r"(?<!\d)\d{10,16}(?!\d)"

# 여권번호: 영문 1자 + 숫자 8자(구형), 2021년부터 가운데 영문자가 한 자 늘었다
# (M12345678 → M123A4567).
#
# 경계에 \b 를 쓰면 안 된다. 파이썬 정규식에서 한글은 \w 이므로 "M123A4567입니다"
# 처럼 한글이 바로 붙으면 경계가 성립하지 않아 통째로 미탐지된다 — 한국어 문서에서
# 가장 흔한 형태다. 영숫자만 경계로 본다.
_PASSPORT_VALUE = r"(?<![A-Za-z0-9])[A-Z](?:\d{3}[A-Z]\d{4}|\d{8})(?![A-Za-z0-9])"

# 운전면허번호: 지역코드(2)-연도(2)-일련번호(6)-검사번호(2). 2024년 말부터
# 지역명 표기가 사라져 전부 숫자다. 10자리 구형 표기도 함께 받는다.
# 검사번호 산출식은 공개돼 있지 않아 검증할 수 없다.
_LICENSE_VALUE = r"(?<!\d)\d{2}-\d{2}-\d{6}-\d{2}(?!\d)|(?<!\d)\d{2}-\d{6}-\d{2}(?!\d)"


def _account_valid(matched: str) -> bool:
    """계좌번호는 검증할 수 없다. 자릿수만 본다.

    국내 은행 계좌는 10~16자리다. 검증식이 없으므로 이 관문은 오탐을 줄이는
    체가 아니라 형태의 하한·상한일 뿐이다. 실제 판별은 앵커가 한다.
    """
    return 10 <= len(re.sub(r"\D", "", matched)) <= 16


def _anchored(anchors: str, value: str, gap: str = _GAP_LOOSE) -> re.Pattern[str]:
    """앵커가 앞서는 값만 잡는 패턴. 가려지는 것은 `value` 그룹뿐이다.

    체크섬이 없는 식별자는 형태만으로 판별할 수 없다. 계좌번호에는 검증식이
    없고, 운전면허 검사번호 산출식은 공개돼 있지 않다. 형태만 보고 잡으면
    오탐이 터지므로, 앞에 붙은 말을 증거로 삼는다.
    """
    return re.compile(rf"(?:{anchors}){gap}(?P<value>{value})")


# 눈에 보이지 않아 탐지를 빠져나가는 문자들.
#
# 목록으로 관리하다 계속 뚫렸다. U+200B 를 막으면 U+2063 으로, 그것을 막으면
# U+00AD·U+2066·U+034F 로 우회한다. 목록은 언제나 공격자보다 늦다.
#
# 그래서 부류로 막는다. 유니코드 범주 Cf(format)와 폭 0 결합 문자(Mn)를 걷어낸다.
#
# 다만 범주만으로는 닫히지 않는다. **눈에 보이지 않는 것과 유니코드가 분류하는
# 방식이 일치하지 않기 때문이다.** U+3164 한글 채움 문자는 범주가 Lo(글자)인데
# 화면에는 아무것도 그리지 않고, 한국에서 공백 닉네임용으로 널리 쓰인다.
# U+2800 점자 공백은 So(기호), U+00A0 은 Zs(공백)다. 범주로 한 겹 막고
# 목록으로 한 겹 더 막는다 — 어느 한쪽도 혼자서는 충분하지 않다.
#
# 전각 숫자는 이 처리가 필요 없다 — 정규식의 \d 와 int() 가 유니코드 십진
# 숫자를 그대로 인식한다.
_INVISIBLE_EXTRA = frozenset(
    "\u3164"      # 한글 채움 문자 (Lo) — 공백 닉네임에 쓰인다
    "\u115f\u1160"  # 한글 초성·중성 채움 (Lo)
    "\uffa0"      # 반각 한글 채움 (Lo)
    "\u2800"      # 점자 공백 (So)
    "\u00a0\u2007\u202f"  # 줄바꿈 없는 공백들 (Zs)
    "\u2028\u2029"  # 줄·문단 구분자 (Zl, Zp)
)


def _invisible(char: str) -> bool:
    """스스로는 자리를 차지하지 않는 문자인가.

    Cf(서식 제어), Mn(폭 0 결합), Me(감싸는 결합) 전부를 걷어낸다.

    한때 Mn 중 결합 클래스가 0 이 아닌 것은 남겨 두고 NFC 에 맡겼다. 숫자에는
    결합형이 없어 그 방식으로도 걸러졌기 때문이다. 그런데 **라틴 글자에는
    결합형이 있다** — `sk-ant-…` 에 U+0301 을 얹으면 NFC 가 `sḱ` 로 합쳐 버려
    접두사 매칭이 깨지고 API 키가 통째로 빠져나갔다. 같은 공격이 카테고리에
    따라 다르게 작동한 것이라, 한쪽을 고쳤다고 닫힌 것이 아니었다.

    결합 기호는 어차피 스스로 자리를 차지하지 않는다. 합쳐지든 안 합쳐지든
    걷어내면 이 부류가 통째로 닫힌다. 대가는 `é` 를 `e` 로 읽는 것인데, 한국
    개인정보 탐지에서 그것이 문제가 되는 자리는 없다.
    """
    return char in _INVISIBLE_EXTRA or unicodedata.category(char) in ("Cf", "Mn", "Me")


# 한글 자모 중 앞 글자에 붙어 한 음절을 이루는 것들. 중성(V)과 종성(T)이다.
# 이들은 결합 문자(combining class 0 이 아님)가 아니라 독립 코드포인트라
# unicodedata.combining() 으로는 잡히지 않는다. NFD 로 분해된 "김" 은
# ㄱ+ㅣ+ㅁ 세 코드포인트인데 셋 다 결합 클래스가 0 이다.
_JAMO_MEDIAL = range(0x1161, 0x1176)
_JAMO_FINAL = range(0x11A8, 0x11C3)


def _joins_previous(char: str) -> bool:
    """앞 글자에 붙어 한 음절을 이루는가.

    결합 문자는 _invisible 이 이미 걷어냈으므로 여기서는 한글 자모만 본다.
    """
    code = ord(char)
    return code in _JAMO_MEDIAL or code in _JAMO_FINAL


@dataclass(frozen=True)
class Finding:
    """탐지 결과 한 건. 원문 좌표(start, end)로만 위치를 표현한다."""

    category: str
    start: int
    end: int


def _normalize(text: str) -> tuple[str, list[int], list[int]]:
    """탐지용 텍스트와, 각 글자가 원문 어디서 왔는지의 지도.

    두 가지를 편다. 보이지 않는 문자를 걷어내고, 한글 자모 분해(NFD)를 완성형
    (NFC)으로 합친다. macOS 가 만든 파일명이나 일부 입력기가 NFD 를 내보내므로
    "김수현" 이 눈에는 같아 보이는데 코드포인트가 달라 사전과 어긋난다.

    **두 일을 한 번에 하면 안 된다.** 한 번에 훑으면 자모 사이에 끼어든 제로폭
    문자가 묶음을 끊어 NFC 가 완성되지 않는다 — 두 우회 수단을 겹쳐 쓰면 둘 다
    막았는데도 빠져나간다. 그래서 먼저 전부 걷어내고, 그 다음 합친다.

    지도는 시작과 끝을 **둘 다** 적는다. 시작만 적었더니 NFC 가 세 코드포인트를
    한 글자로 합친 자리에서 끝이 한 칸으로 계산돼, "김수현" 이 "[이름_1]ᅧᆫ" 으로
    반쯤 남았다.
    """
    # 1단계 — 보이지 않는 문자를 걷어낸다. 원문 자리를 함께 들고 간다.
    보이는글자: list[str] = []
    원자리: list[int] = []
    for index, char in enumerate(text):
        if not _invisible(char):
            보이는글자.append(char)
            원자리.append(index)

    # 2단계 — 결합 문자와 한글 자모를 묶어 NFC 로 합친다.
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []

    index = 0
    while index < len(보이는글자):
        end = index + 1
        while end < len(보이는글자) and _joins_previous(보이는글자[end]):
            end += 1
        composed = unicodedata.normalize("NFC", "".join(보이는글자[index:end]))

        # 길이가 **달라지면** 한 덩어리로 다룬다. 줄어드는 것만 생각했다가
        # 늘어나는 경우에 좌표가 범위를 벗어나 크래시가 났다 — 합성 제외
        # (composition exclusion) 문자 73개는 NFC 가 오히려 늘린다.
        합쳐짐 = len(composed) != end - index
        for offset, composed_char in enumerate(composed):
            chars.append(composed_char)
            if 합쳐짐:
                # 이 한 글자가 원문 [원자리[index], 원자리[end-1]+1) 전체에서 왔다.
                starts.append(원자리[index])
                ends.append(원자리[end - 1] + 1)
            else:
                starts.append(원자리[index + offset])
                ends.append(원자리[index + offset] + 1)
        index = end

    return "".join(chars), starts, ends


def _digits(text: str) -> str:
    r"""숫자만 남기고 전각을 반각으로 맞춘다.

    정규식의 \d 와 int() 는 유니코드 십진 숫자를 그대로 인식하지만, 검증기가
    자릿값을 ASCII 문자와 비교하는 자리가 있다(주민등록번호 성별코드 표).
    거기서 전각이 조용히 빠져나가므로 한 곳에서 맞춰 둔다.
    """
    return "".join(str(int(ch)) for ch in text if ch.isdecimal())


def _phone_valid(matched: str) -> bool:
    r"""통신사 식별번호와 자릿수를 본다.

    패턴에서 리터럴 0·1 을 빼고 \d 로 바꾼 대신 여기서 확인한다. 리터럴은
    전각 "０" 을 놓쳤다.
    """
    digits = _digits(matched)
    if "+" in matched:
        # 국가번호를 붙이면 앞의 0 이 빠진다 (+82-10-... = 010-...).
        # 국가번호 자리도 \d 로 받는 이유는 전각 때문이다 — 리터럴 "82" 는
        # "８２" 를 놓친다. 대한민국 번호만 다루므로 값으로 확인한다.
        if digits[:2] != "82":
            return False
        digits = "0" + digits[2:]
    return digits.startswith(_PHONE_PREFIXES) and len(digits) in (10, 11)


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
    """하이픈이 있으면 관문 하나, 없으면 둘.

    하이픈은 그 자체가 "주민등록번호를 쓰려는 의도"의 증거다. 증거가 없는 13자리
    숫자에까지 관문 하나만 요구하면 오탐이 터진다 — 밀리초 타임스탬프와 상품
    바코드가 모두 13자리다.

    대가를 적어 둔다. 2020.10 이후 발급분은 뒷자리가 임의번호라 검증식을 통과하지
    않으므로, 하이픈 없이 쓰면 잡히지 않는다.
    """
    digits = _digits(matched)
    if "-" in matched:
        return _rrn_checksum_ok(digits) or _rrn_birthdate_ok(digits)
    return _rrn_checksum_ok(digits) and _rrn_birthdate_ok(digits)


def _card_valid(matched: str) -> bool:
    """첫 자리(카드사 식별번호)마다 물어야 할 것이 다르다.

    Luhn 은 16자리 숫자 열 개 중 하나를 그냥 통과시킨다. 전표번호가 그렇게
    걸리므로, 애초에 카드번호일 수 없는 대역을 먼저 잘라낸다.

    9(국내전용)를 따로 두는 것이 한국에서 특히 중요하다. 국내전용 카드는
    검증번호 산출 방식과 위치가 카드사마다 달라 **Luhn 이 적용되지 않는다.**
    Luhn 만 관문으로 두면 국내전용 카드를 통째로 놓친다. 검증할 수단이 없으므로
    사람이 구분자를 넣어 옮겨적은 형태일 때만 인정한다 — 하이픈 없는 RRN 과
    같은 원칙이다. 증거가 없으면 관문을 더 요구한다.
    """
    digits = _digits(matched)
    # ISO/IEC 7812 는 13~19자리를 허용한다. 16자리만 받았다가 Amex(15)·Diners(14)를
    # 놓쳤고, 14~16 으로 넓혔다가 구 Visa(13)·UnionPay(19)를 놓쳤다. 규격대로 연다.
    if not 13 <= len(digits) <= 19:
        return False
    head = digits[0]

    if head == "9":
        return bool(re.search(r"[-\s]", matched))
    if head == "2":
        # 2017년부터 Mastercard 가 쓰기 시작한 대역. 그 밖의 2 는 카드가 아니다.
        return 2221 <= int(digits[:4]) <= 2720 and _luhn_ok(digits)
    if head in "3456":
        # 3 여행·엔터테인먼트(Amex·JCB·Diners), 4 Visa, 5 Mastercard,
        # 6 Discover·UnionPay. 국제 브랜드는 Luhn 을 따른다.
        return _luhn_ok(digits)
    return False


def _brn_valid(matched: str) -> bool:
    return _brn_checksum_ok(_digits(matched))


# 규칙 표: (카테고리, 패턴, 검증기). 검증기가 없으면 패턴만으로 확정한다.
_RULES = (
    ("RRN", _RRN_PATTERN, _rrn_valid),
    ("BRN", _BRN_PATTERN, _brn_valid),
    ("CARD", _CARD_PATTERN, _card_valid),
    ("PHONE", _PHONE_PATTERN, _phone_valid),
    ("EMAIL", _EMAIL_PATTERN, None),
    ("SECRET", _SECRET_PATTERN, None),
    # ── 앵커 규칙 ────────────────────────────────────────────────────────
    # 값 자체로는 판별할 수 없어 앞에 붙은 말을 증거로 쓴다. 두 종류가 있다.
    #
    # (가) 원래 형태가 없거나 검증식이 없는 것 — 이름·계좌·여권·면허.
    #      앵커가 없으면 아예 잡지 않는다.
    # (나) 우리가 스스로 관문을 좁혀 놓은 것 — 붙여 쓴 주민등록번호·국내전용
    #      카드·사업자등록번호. 앵커가 있으면 좁힌 관문을 하나 면제한다.
    #      면제하는 것은 "표기가 흐릿하다" 는 이유로 더 걸었던 관문뿐이고,
    #      원래 있던 검증식은 그대로 요구한다.
    (
        "NAME",
        _anchored(
            "성명|이름|고객명|예금주|수취인|대표자|신청인|계약자|가입자|담당자?",
            _NAME_VALUE,
            gap=_GAP_STRICT,
        ),
        None,
    ),
    # 하이픈 묶음(3-3-6, 6-2-6 …)이 증거 노릇을 하므로 앵커는 느슨해도 된다.
    ("ACCOUNT", _anchored(_ACCOUNT_ANCHORS, _ACCOUNT_GROUPED), _account_valid),
    # 맨 숫자 계좌번호는 아무 증거도 담지 않는다. "이체 수수료 정산
    # 2026080612345678" 이 계좌번호로 읽히던 오탐이 여기서 나왔다.
    ("ACCOUNT", _anchored(_ACCOUNT_ANCHORS, _ACCOUNT_BARE, gap=_GAP_STRICT), _account_valid),
    # 여권번호는 영문 1자 + 숫자 8자다. 제품 코드·대기번호와 형태가 겹쳐
    # ("여권 발급 대기열 A00000001") 증거로 삼을 수 없다.
    ("PASSPORT", _anchored("여권번호|여권|passport", _PASSPORT_VALUE, gap=_GAP_STRICT), None),
    # 2-2-6-2 묶음은 다른 번호와 잘 겹치지 않는다.
    ("LICENSE", _anchored("운전면허번호|운전면허|면허번호|면허", _LICENSE_VALUE), None),
    # 붙여 쓴 13자리. 앵커가 있으면 생년월일만으로 인정한다 — 이것이 2020.10
    # 이후 발급분(뒷자리 임의번호)을 되찾는 경로다. 생년월일 관문이 남아 있어
    # 값 자체도 증거를 담으므로 앵커는 느슨하게 둔다.
    ("RRN", _anchored("주민등록번호|주민번호|주민|rrn|jumin", r"(?<!\d)\d{13}(?!\d)"), _rrn_birthdate_ok),
    # 구분자 없는 국내전용 카드. Luhn 이 적용되지 않아 값 쪽에 증거가 전혀 없다.
    # 앵커가 유일한 근거이므로 가장 엄격하게 건다.
    ("CARD", _anchored("카드번호|카드|결제|card", r"(?<!\d)9\d{15}(?!\d)", gap=_GAP_STRICT), None),
    # 붙여 쓴 10자리. 검증식이 그대로 증거 노릇을 하므로 앵커는 느슨해도 된다.
    ("BRN", _anchored("사업자등록번호|사업자번호|사업자|brn", r"(?<!\d)\d{10}(?!\d)"), _brn_checksum_ok),
)

# 이 탐지기가 붙일 수 있는 카테고리 전부. 마스킹 계층이 카테고리마다 정책을
# 갖고 있어서, 규칙을 늘렸는데 정책을 빠뜨리면 조용히 어긋난다. 표를 하나로
# 두고 테스트가 대조하게 한다.
CATEGORIES = tuple(category for category, _, _ in _RULES)


def _scan(pattern: re.Pattern[str], validator, text: str):
    """패턴을 훑되, 검증기가 거부하면 **시작점 다음 칸부터** 다시 찾는다.

    finditer 는 매치 끝에서 다음 탐색을 시작하므로, 검증기가 거부한 후보가
    그 안에 든 유효한 값을 통째로 삼킨다. `ref 000-010-1234-5678` 에서
    무효 후보 `000-010-1234` 가 유효한 `010-1234-5678` 의 시작점을 먹어
    전화번호가 통째로 빠져나갔다.

    검증기로 오탐을 거르는 설계가 미탐지를 만들고 있었던 셈이다.
    """
    group = "value" if "value" in pattern.groupindex else 0
    position = 0
    while position <= len(text):
        match = pattern.search(text, position)
        if match is None:
            return
        if validator is None or validator(match.group(group)):
            yield match.start(group), match.end(group)
            # 빈 매치에서 제자리걸음 하지 않도록 최소 한 칸은 전진한다.
            position = max(match.end(), match.start() + 1)
        else:
            position = match.start() + 1


def find_in_normalized(scan_text: str) -> list[tuple[str, int, int]]:
    """이미 정규화된 텍스트에서 규칙 탐지 결과를 (카테고리, 시작, 끝)로.

    마스킹 계층이 정규화를 한 번만 하고 규칙 탐지와 세션 재탐색에 함께 쓰도록
    분리해 둔다. 두 경로가 같은 텍스트를 보지 않으면 한쪽만 뚫린다 — 세션
    재탐색이 원문을 그대로 훑던 동안, 이미 가린 이름에 제로폭을 끼우는 것만으로
    빠져나갈 수 있었다.
    """
    found: list[tuple[str, int, int]] = []
    for category, pattern, validator in _RULES:
        for start, end in _scan(pattern, validator, scan_text):
            found.append((category, start, end))
    return found


def normalize(text: str) -> tuple[str, list[int], list[int]]:
    """탐지용 텍스트와 원문 좌표 지도. 마스킹 계층이 함께 쓴다."""
    return _normalize(text)


def detect(text: str) -> list[Finding]:
    """텍스트에서 한국 개인정보·시크릿을 찾아 원문 좌표로 돌려준다."""
    scan_text, starts, ends = _normalize(text)
    findings = [
        Finding(category=category, start=starts[start], end=ends[end - 1])
        for category, start, end in find_in_normalized(scan_text)
    ]
    # 같은 값을 두 규칙이 잡는 일이 있다. "rrn=8803121000068" 은 형태 규칙과
    # 앵커 규칙에 모두 걸린다. Finding 은 (카테고리, 좌표)로 정해지는 값이므로
    # 중복은 지운다 — 그냥 두면 "가린 항목 N건" 이 부풀고 scan 출력이 겹친다.
    return sorted(set(findings), key=lambda f: (f.start, f.end))
