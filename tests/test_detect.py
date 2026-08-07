"""탐지 코어(L1) 테스트.

테스트에 쓰는 식별자는 전부 합성값이다. 실제 발급 번호를 쓰지 않되,
체크섬 검증 로직을 실제로 시험하기 위해 검증식은 통과하도록 계산했다.
"""

from sumunjang.detect import _luhn_ok, detect


def test_체크섬이_유효한_주민등록번호를_탐지한다():
    text = "고객 정보: 900101-1234568 확인 바랍니다"

    findings = detect(text)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "RRN"
    assert text[finding.start : finding.end] == "900101-1234568"


def test_체크섬이_없는_2020년_10월_이후_주민등록번호도_탐지한다():
    """2020.10부터 뒷 6자리가 임의번호가 되어 검증식을 통과하지 않는다.

    체크섬만 믿으면 그 이후 발급분을 통째로 놓친다. 생년월일이 유효하면
    체크섬 실패와 무관하게 탐지해야 한다.
    """
    text = "신입사원 020315-3000001 입사 처리"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "RRN"


def test_휴대전화번호를_탐지한다():
    text = "연락처 010-1234-5678 로 회신 주세요"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "PHONE"
    assert text[findings[0].start : findings[0].end] == "010-1234-5678"


def test_이메일_주소를_탐지한다():
    text = "담당자 메일은 hong.gildong@example.co.kr 입니다"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "EMAIL"
    assert text[findings[0].start : findings[0].end] == "hong.gildong@example.co.kr"


def test_Luhn_검증을_통과하는_카드번호를_탐지한다():
    text = "결제 카드 4242-4242-4242-4242 승인 완료"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "CARD"


def test_Luhn_검증에_실패하는_숫자열은_카드번호로_보지_않는다():
    """16자리라고 전부 카드번호는 아니다. 주문번호·일련번호 오탐을 막는다.

    다만 Luhn은 전치·단일오타 검출용이라 규칙적인 반복 패턴
    (예: 1111-2222-3333-4444)은 통과해버린다. 오탐을 완전히 막지는 못한다.
    """
    text = "주문번호 1234-5678-9012-3456 접수"

    findings = detect(text)

    assert [f for f in findings if f.category == "CARD"] == []


def test_체크섬이_유효한_사업자등록번호를_탐지한다():
    text = "거래처 사업자등록번호 123-45-67891 입니다"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "BRN"
    assert text[findings[0].start : findings[0].end] == "123-45-67891"


def test_API_키를_탐지한다():
    """개발자가 코드·설정을 붙여넣을 때 가장 위험한 유출 경로."""
    text = "export ANTHROPIC_API_KEY=sk-ant-api03-QkxBSDEyMzQ1Njc4OTBhYmNkZQ"

    findings = detect(text)

    secrets = [f for f in findings if f.category == "SECRET"]
    assert len(secrets) == 1
    assert text[secrets[0].start : secrets[0].end].startswith("sk-ant-")


def test_긴_숫자열_안의_일부를_주민등록번호로_오탐하지_않는다():
    """견적번호 20260806-0012345 의 뒷부분이 주민번호 형태와 우연히 일치한다.

    골든셋 채점에서 실제로 나온 오탐이다.
    """
    text = "견적 번호는 20260806-0012345 입니다"

    findings = detect(text)

    assert [f for f in findings if f.category == "RRN"] == []


def test_접속_문자열의_호스트를_이메일로_오탐하지_않는다():
    """postgresql://app:pw@10.0.3.14 의 일부가 이메일처럼 보인다.

    이메일 최상위 도메인은 숫자일 수 없다는 조건으로 걸러낸다.
    """
    text = "DATABASE_URL=postgresql://app:pw@10.0.3.14:5432/prod"

    findings = detect(text)

    assert [f for f in findings if f.category == "EMAIL"] == []


def test_전각_숫자로_쓴_주민등록번호도_탐지한다():
    """전각 숫자는 눈으로는 같아 보이지만 코드포인트가 달라 정규식을 빠져나간다."""
    text = "고객 ９００１０１-１２３４５６８ 확인"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "RRN"
    assert text[findings[0].start : findings[0].end] == "９００１０１-１２３４５６８"


def test_하이픈_없이_붙여쓴_주민등록번호도_탐지한다():
    """DB 컬럼과 로그에는 하이픈 없이 13자리로 들어가 있는 경우가 흔하다."""
    text = "INSERT INTO members (rrn) VALUES ('8803121000068')"

    findings = detect(text)

    rrns = [f for f in findings if f.category == "RRN"]
    assert len(rrns) == 1
    assert text[rrns[0].start : rrns[0].end] == "8803121000068"


def test_하이픈이_없으면_검증식까지_통과해야_주민등록번호로_본다():
    """하이픈은 그 자체가 "주민번호를 쓰려는 의도"의 증거다.

    증거가 없는 13자리 숫자에까지 "체크섬 또는 생년월일" 을 적용하면 오탐이
    터진다. 그래서 하이픈이 없을 때는 두 관문을 모두 요구한다.

    대가가 있다. 2020.10 이후 발급분은 뒷자리가 임의번호라 체크섬을 통과하지
    않으므로, 하이픈 없이 쓰면 잡히지 않는다. 아래 값이 바로 그 세대다.
    문맥 앵커(앞에 "주민번호" 같은 말이 붙는 경우)로 메워야 할 자리다.
    """
    text = "코드 0411303912345 처리 완료"

    findings = detect(text)

    assert [f for f in findings if f.category == "RRN"] == []


def test_밀리초_타임스탬프를_주민등록번호로_오탐하지_않는다():
    """epoch_ms 는 13자리다. 로그마다 있으므로 오탐하면 즉시 소음이 된다.

    2020~2030년대 밀리초 타임스탬프는 앞 6자리의 3·4번째 자리가 언제나 12를
    넘어 월(月)로 성립하지 않는다. 생년월일 관문이 통째로 걸러낸다.
    """
    text = "epoch_ms=1754500951000 epoch_s=1754500951"

    findings = detect(text)

    assert [f for f in findings if f.category == "RRN"] == []


def test_점으로_구분한_전화번호도_탐지한다():
    text = "직통 010.9876.5432 으로 연락 주세요"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "PHONE"
    assert text[findings[0].start : findings[0].end] == "010.9876.5432"


def test_국가번호를_붙인_전화번호도_탐지한다():
    """해외 지사·해외 결제 로그에서는 +82 표기로 들어온다. 앞의 0 은 빠진다."""
    text = "담당자 직통: +82-10-2255-8830"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "PHONE"
    assert text[findings[0].start : findings[0].end] == "+82-10-2255-8830"


def test_카드사_식별번호가_아니면_카드번호로_보지_않는다():
    """Luhn 은 16자리 중 10분의 1을 그냥 통과시킨다. 전표번호가 그렇게 걸린다.

    카드번호 첫 자리는 카드사를 가리키는 번호(3 항공·여행, 4 Visa, 5 Mastercard,
    6 Discover·UnionPay)로 정해져 있다. 그 밖의 숫자로 시작하면 카드가 아니다.
    """
    text = "전표번호 2026-0000-0000-0006 확인"

    findings = detect(text)

    assert [f for f in findings if f.category == "CARD"] == []


def test_국내전용_카드는_Luhn을_통과하지_않아도_탐지한다():
    """국내전용(9로 시작) 카드는 검증번호 방식이 카드사마다 달라 Luhn 이 안 맞는다.

    Luhn 만 관문으로 두면 한국 카드를 통째로 놓친다. 한국 특화 도구가 반드시
    잡아야 하는 자리다.
    """
    text = "결제 카드 9410-1234-5678-9012 승인"
    assert not _luhn_ok("9410123456789012"), "이 표본은 Luhn 을 통과하면 안 된다"

    findings = detect(text)

    cards = [f for f in findings if f.category == "CARD"]
    assert len(cards) == 1
    assert text[cards[0].start : cards[0].end] == "9410-1234-5678-9012"


def test_제로폭_문자가_끼어든_주민등록번호도_원문_좌표로_탐지한다():
    """보이지 않는 문자를 끼워넣는 것은 가장 값싼 우회 수단이다.

    정규화 후 매칭하되, 좌표는 반드시 원문 기준이어야 마스킹이 정확해진다.
    """
    text = "고객 900101-123​4568 확인"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "RRN"
    assert text[findings[0].start : findings[0].end] == "900101-123​4568"


# ── 문맥 앵커 규칙 ────────────────────────────────────────────────────────
# 체크섬이 없는 식별자는 형태만으로 판별할 수 없다. 앞에 붙은 말("성명:",
# "계좌:")을 증거로 삼는다. 원칙은 하나다 — 증거가 없으면 관문을 더 요구한다.


def test_앵커가_붙은_한국어_이름을_탐지한다():
    text = "  성명: 최윤서\n  주민등록번호: 991130-1000008"

    names = [f for f in detect(text) if f.category == "NAME"]

    assert len(names) == 1
    assert text[names[0].start : names[0].end] == "최윤서"


def test_이름_뒤의_직함은_가리지_않는다():
    """가려야 할 것은 이름이지 직함이 아니다. 직함까지 먹으면 문맥이 사라진다."""
    text = "- 담당: 김수현 책임"

    names = [f for f in detect(text) if f.category == "NAME"]

    assert len(names) == 1
    assert text[names[0].start : names[0].end] == "김수현"


def test_앵커가_없는_자유서술_속_이름은_잡지_않는다():
    """선언된 한계다. 문맥 없이 판별하려면 규칙이 아니라 모델이 필요하다.

    goldenset-gaps/G2 에 정답으로 박아 두고 0점으로 공표한다.
    """
    text = "어제 김수현 책임이랑 통화했는데 급하시다고 하네요"

    assert [f for f in detect(text) if f.category == "NAME"] == []


def test_앵커와_값이_다른_줄에_있으면_잡지_않는다():
    """앵커의 사정거리는 같은 줄까지다.

    넓히면 문서 전체가 앵커 하나에 물들어 오탐이 터진다. 서식과 로그는 줄 단위로
    쓰이므로 줄이 자연스러운 경계다.
    """
    text = "성명:\n다음 항목은 김수현 책임이 작성함"

    assert [f for f in detect(text) if f.category == "NAME"] == []


def test_앵커_뒤라도_성씨가_아니면_이름으로_보지_않는다():
    """앵커만으로는 부족하다. 한국 성씨는 닫힌 집합이라 사전으로 한 겹 더 거른다."""
    text = "담당: 미정\n분류: 환불 요청"

    assert [f for f in detect(text) if f.category == "NAME"] == []
