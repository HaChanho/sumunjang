"""탐지 코어(L1) 테스트.

테스트에 쓰는 식별자는 전부 합성값이다. 실제 발급 번호를 쓰지 않되,
체크섬 검증 로직을 실제로 시험하기 위해 검증식은 통과하도록 계산했다.
"""

from sumunjang.detect import detect


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
