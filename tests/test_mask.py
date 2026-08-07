"""마스킹·복원 계층 테스트."""

from sumunjang.detect import CATEGORIES
from sumunjang.mask import SEVERITY, Session, mask, restore


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


def test_세션은_상한을_넘어서면_오래된_것부터_버린다():
    """프록시는 세션 하나를 프로세스 수명 내내 들고 있다.

    상한이 없으면 그날 오간 모든 개인정보가 원문 그대로 메모리에 쌓인다.
    보안 도구가 스스로 개인정보 저장소가 되어서는 안 된다.
    """
    session = Session(capacity=3)

    for n in range(5):
        mask(f"연락처 010-0000-000{n}", session)

    assert len(session) == 3


def test_버려진_값은_복원되지_않을_뿐_유출되지_않는다():
    """퇴출의 대가는 미복원이지 유출이 아니다.

    화면에 원문 대신 [전화번호_1] 이 남는다. 잘못된 복원이 미복원보다
    위험하다는 원칙과 같은 방향이다.
    """
    session = Session(capacity=2)
    first = mask("연락처 010-1111-1111", session)

    mask("연락처 010-2222-2222", session)
    mask("연락처 010-3333-3333", session)

    assert restore(first, session) == first
    assert "010-1111-1111" not in restore(first, session)


def test_다시_쓰인_값은_오래되었다고_버려지지_않는다():
    """대화가 길어져도 계속 등장하는 값은 남아야 한다.

    도구는 매 턴 대화 전체를 다시 보내므로, 앞부분의 개인정보도 매번 다시
    가려진다. 등장 순서가 아니라 마지막으로 쓰인 시점을 기준으로 버린다.
    """
    session = Session(capacity=2)
    first = mask("연락처 010-1111-1111", session)

    mask("연락처 010-2222-2222", session)
    mask("연락처 010-1111-1111", session)   # 다시 등장
    mask("연락처 010-3333-3333", session)   # 이때 밀려나는 것은 2222 여야 한다

    assert restore(first, session) == "연락처 010-1111-1111"
