"""마스킹·복원 계층 테스트."""

from sumunjang.detect import CATEGORIES
import pytest

from sumunjang.mask import SEVERITY, Session, SessionFull, mask, restore


def test_주민등록번호를_placeholder로_치환한다():
    session = Session()

    masked = mask("고객 900101-1234568 확인 바랍니다", session)

    assert masked == "고객 [주민등록번호_1] 확인 바랍니다"


def test_같은_값은_세션_안에서_같은_placeholder를_받는다():
    """같은 사람을 가리키는 값이 매번 다른 이름이 되면 모델이 문맥을 잃는다."""
    session = Session()

    masked = mask("발신 010-1234-5678 수신 010-1234-5678", session)

    assert masked == "발신 [전화번호_1] 수신 [전화번호_1]"


def test_다른_값은_다른_placeholder를_받는다():
    session = Session()

    masked = mask("발신 010-1234-5678 수신 010-9876-5432", session)

    assert masked == "발신 [전화번호_1] 수신 [전화번호_2]"


def test_마스킹한_텍스트를_복원하면_원문으로_돌아온다():
    """이 왕복이 깨지면 도구 전체가 무의미하다."""
    session = Session()
    original = "김철수 900101-1234568, 연락처 010-1234-5678, 메일 kim@example.com"

    masked = mask(original, session)
    assert restore(masked, session) == original


def test_모델_답변에_섞인_placeholder만_골라_복원한다():
    session = Session()
    mask("연락처 010-1234-5678", session)

    answer = "고객님 번호 [전화번호_1] 로 발송했습니다. 문제 없으면 회신 주세요."

    assert restore(answer, session) == "고객님 번호 010-1234-5678 로 발송했습니다. 문제 없으면 회신 주세요."


def test_탐지가_겹쳐도_뒤쪽_구간이_평문으로_남지_않는다():
    """겹치는 탐지를 "앞선 것이 이겼으니 뒤는 버린다"로 처리하면 부분 유출이 된다.

    아래 입력에서 전화번호(010-9921-3348)와 카드번호(9921-3348-0000-0002)가
    8자리를 공유한다. 전화번호만 가리고 카드 탐지를 버리면 카드 뒷 8자리가
    그대로 남는다. 겹치는 구간은 합집합으로 가려야 한다.
    """
    session = Session()

    masked = mask("연락처 010-9921-3348-0000-0002 입니다", session)

    assert "0000-0002" not in masked
    assert "9921" not in masked


def test_겹친_구간은_더_민감한_쪽의_라벨을_받는다():
    """전화번호와 카드번호가 겹치면 카드번호로 부른다.

    서열의 축은 "이 값 하나로 다른 문을 얼마나 열 수 있는가, 그 자물쇠를 바꿀 수
    있는가" 하나다. 카드번호는 금전을 열고 재발급이 되지만, 전화번호는 본인인증
    2차 채널이라 열리는 문이 더 적다.
    """
    session = Session()

    masked = mask("연락처 010-9921-3348-0000-0002 입니다", session)

    assert masked == "연락처 [카드번호_1] 입니다"


def test_주민등록번호가_섞이면_언제나_주민등록번호로_부른다():
    """가장 민감한 것이 라벨을 가져간다 — 서열 1위는 유출 시 되돌릴 수 없기 때문이다."""
    session = Session()

    masked = mask("문의자 900101-1234568kim@example.com", session)

    assert masked == "문의자 [주민등록번호_1]"


def test_모든_탐지_카테고리가_민감도_표에_들어_있다():
    """탐지기를 늘리면서 서열을 빠뜨리면 여기서 먼저 걸린다.

    빠진 카테고리는 런타임에서는 가장 민감한 쪽으로 취급되므로 유출로는 이어지지
    않지만, 라벨이 조용히 틀어진다. 조용한 오류를 시끄러운 실패로 바꿔 둔다.
    """
    assert set(CATEGORIES) == set(SEVERITY), (
        f"서열에 없는 카테고리: {set(CATEGORIES) - set(SEVERITY)} / "
        f"탐지기에 없는 카테고리: {set(SEVERITY) - set(CATEGORIES)}"
    )


def test_상한에_닿으면_버리지_않고_요청을_거부한다():
    """상한은 퇴출선이 아니라 거부선이다.

    처음에는 오래된 것부터 버렸다. 그런데 버린 값은 다음 턴에 다시 가려지지 않아
    그대로 유출됐다 — 대화 기록은 매 턴 다시 전송되므로 퇴출은 과거를 지우는 것이
    아니라 보호를 푸는 것이었다. 메모리 상한과 유출 방지를 둘 다 지키려면 버리는
    대신 거부해야 한다.
    """
    session = Session(capacity=2)
    mask("연락처 010-1111-1111", session)
    mask("연락처 010-2222-2222", session)

    with pytest.raises(SessionFull):
        mask("연락처 010-3333-3333", session)

    assert len(session) == 2


def test_상한에_닿아도_이미_아는_값은_계속_가린다():
    """거부는 새 값에만 적용된다. 이미 가린 값은 끝까지 지켜야 한다."""
    session = Session(capacity=2)
    가림 = mask("연락처 010-1111-1111", session)
    mask("연락처 010-2222-2222", session)

    assert restore(가림, session) == "연락처 010-1111-1111"
    assert "010-1111-1111" not in mask("이전 번호 010-1111-1111 확인", session)


def test_복원된_값이_앵커_없는_문맥으로_돌아와도_다시_가린다():
    """실왕복에서 잡힌 유출이다.

    ① 도구가 "담당자: 김수현" 을 읽어 [이름_1] 로 가림
    ② 모델이 가명 표시를 설명 대상으로 언급 ("내용이 `[이름_1]` 같은…")
    ③ 프록시가 복원 → 앵커가 사라진 자리에 원문이 놓임
    ④ 다음 턴에 대화 기록이 다시 전송 → 탐지기가 못 알아봄
    """
    session = Session()
    mask("담당자: 김수현", session)

    풀린문장 = restore("내용이 `[이름_1]` 처럼 보입니다", session)
    assert "김수현" in 풀린문장, "이 테스트의 전제(복원이 앵커를 지운다)가 깨졌다"

    assert "김수현" not in mask(풀린문장, session)


def test_앵커에_기대는_카테고리가_모두_구제된다():
    """앵커 의존 카테고리(이름·계좌·여권·면허)가 이 경로로 전부 샜다."""
    표본 = [
        ("담당자: 김수현", "김수현"),
        ("계좌: 110-234-567890", "110-234-567890"),
        ("여권번호: M123A4567", "M123A4567"),
        ("면허: 11-23-456789-70", "11-23-456789-70"),
    ]
    for 원문, 값 in 표본:
        session = Session()
        mask(원문, session)
        assert 값 not in mask(f"내용이 `{값}` 처럼 보입니다", session), f"{값} 유출"


def test_이름_뒤에_조사나_직함이_붙어도_가린다():
    """앞쪽 경계만 본다. 한국어는 이름 뒤에 조사·직함이 그대로 붙는다."""
    session = Session()
    mask("담당자: 김수현", session)

    for 문장 in ("김수현씨가 말하길", "김수현 책임께", "김수현님", "김수현이랑"):
        assert "김수현" not in mask(문장, session), f"놓침: {문장}"


def test_다른_이름의_일부와_겹쳐도_남의_이름을_잘라먹지_않는다():
    """앞에 한글이 오면 가리지 않는다. '박이준' 안의 '이준' 은 다른 사람이다."""
    session = Session()
    mask("담당자: 이준", session)

    assert mask("작성자 박이준 확인", session) == "작성자 박이준 확인"


def test_세션이_모르는_값은_건드리지_않는다():
    """이 장치는 이미 가린 값에만 적용된다. 새 값은 규칙이 판단한다."""
    session = Session()

    assert mask("내용이 `김수현` 처럼 보입니다", session) == "내용이 `김수현` 처럼 보입니다"


def test_세션_값_재마스킹이_큰_세션에서도_실용적이다():
    """세션이 커져도 요청 하나가 몇 밀리초 안에 끝나야 한다.

    이미 가린 값을 거대한 정규식 대안으로 찾으면 파이썬 정규식 엔진이 매 위치마다
    모든 대안을 시도해 본문 길이 × 값 개수로 비용이 커진다. 세션 상한이 10,000
    이므로 긴 대화에서 프록시가 사실상 멈춘다. 실제로 값 2,000개에서 9KB 본문
    마스킹이 2분을 넘겼다.
    """
    import time

    session = Session()
    for n in range(2000):
        mask(f"연락처 010-{n // 10000:04d}-{n % 10000:04d}", session)

    본문 = "일반 로그 줄입니다 " * 500  # 약 9KB
    시작 = time.perf_counter()
    mask(본문, session)
    걸린시간 = time.perf_counter() -시작

    assert 걸린시간 < 0.5, f"세션 {len(session)}건에서 9KB 마스킹에 {걸린시간:.1f}초"


def test_한글이_바로_앞에_붙어도_숫자_값은_가린다():
    """앞경계 규칙은 한글 낱말이 망가지는 것을 막으려는 것이다.

    숫자 값에까지 적용하면 "잔액110-234-567890" 처럼 한글이 바로 붙은 자리에서
    계좌번호가 가려지지 않는다. 경계 규칙이 유출 방향으로 작동한 셈이다.
    값이 한글일 때만 앞경계를 본다.
    """
    session = Session()
    mask("계좌: 110-234-567890", session)

    assert "110-234-567890" not in mask("잔액110-234-567890 입니다", session)


def test_세션이_아는_숫자값이_더_긴_숫자의_가운데를_가르지_않는다():
    """경계를 아예 두지 않았더니 무관한 데이터를 조용히 훼손했다.

    유출은 아니지만 모델에게 망가진 값을 넘기는 것이라 품질 결함이다.
    한글 값(이름)은 앞경계만 보고, 숫자 값은 앞뒤 숫자 경계를 본다.
    """
    session = Session()
    mask("계좌: 1234567890", session)

    assert mask("주문번호 991234567890123 확인", session) == "주문번호 991234567890123 확인"
    assert "1234567890" not in mask("입금 계좌 1234567890 확인", session)


def test_큰_세션에서도_큰_본문이_실용적인_시간에_끝난다():
    """세션 값 전부를 매번 정렬해 훑으면 비용이 (본문 문자열 수 × 세션 크기)로
    곱해진다. 코딩 에이전트는 매 턴 대화 전체를 다시 보내므로 일상적으로 닿는다."""
    import time

    session = Session()
    for n in range(5000):
        mask(f"연락처 010-{n // 10000:04d}-{n % 10000:04d}", session)

    본문 = "일반 로그 줄입니다 " * 5000  # 약 90KB
    시작 = time.perf_counter()
    mask(본문, session)

    assert time.perf_counter() - 시작 < 1.0
