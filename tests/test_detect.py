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


def test_제로폭_문자가_끼어든_주민등록번호도_원문_좌표로_탐지한다():
    """보이지 않는 문자를 끼워넣는 것은 가장 값싼 우회 수단이다.

    정규화 후 매칭하되, 좌표는 반드시 원문 기준이어야 마스킹이 정확해진다.
    """
    text = "고객 900101-123​4568 확인"

    findings = detect(text)

    assert len(findings) == 1
    assert findings[0].category == "RRN"
    assert text[findings[0].start : findings[0].end] == "900101-123​4568"
