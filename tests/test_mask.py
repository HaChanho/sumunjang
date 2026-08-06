"""마스킹·복원 계층 테스트."""

from sumunjang.mask import Session, mask, restore


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
