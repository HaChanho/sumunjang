"""README 가 공표한 수치를 코드가 실제로 내는가.

점수를 문서에 적기만 하면 문서와 코드는 각자 흘러간다. 그 틈이 이미 한 번
벌어졌다 — PyPI 에 게시한 배포본이 README 가 약속한 수치를 내지 못했다.
게시 이후로 코드가 열여섯 커밋 나아가는 동안 문서만 함께 나아갔고, 설치본을
받아 `sumunjang report` 를 돌린 사람은 README 에 없는 숫자를 보게 된다.
README 66 줄이 "저장소를 클론하지 않아도 아래 수치를 그대로 재현할 수 있다"
고 적고 있으므로, 그 문장이 곧 지켜야 할 약속이다.

CI 에 채점 게이트가 있지만 하한만 본다(`--min-recall`). `goldenset-gaps` 는
낮은 것이 정상이라 하한으로 감시할 수 없어 게이트에서 빠져 있었는데,
**감시할 수단이 없다는 것이 감시하지 않아도 된다는 뜻은 아니다.** gaps 는
내려가면 회귀이고 올라가면 문서가 틀린 것이라, 필요한 것은 하한이 아니라
정확한 일치다.

이 테스트는 결함을 고치지 않는다. 지금 수치는 맞다. 계약이 다시 조용히
어긋나는 것을 막는 자물쇠다.
"""

import io
import re
from pathlib import Path

from sumunjang.cli import DEFAULT_GOLDENSETS, run

저장소 = Path(__file__).resolve().parents[1]
README = 저장소 / "README.md"

# 공표 표의 한 줄. 첫 칸이 백틱으로 감싼 셋 이름인 것만 본다. 본문에도 같은
# 이름과 숫자가 나오지만("도입 당시 0.684 / 0.765") 그건 계약이 아니라 이력이다.
_공표행 = re.compile(r"^\|\s*`(?P<이름>goldenset[\w-]*)/`\s*\|(?P<나머지>.*)\|\s*$", re.M)

_지표 = (("재현율", "recall"), ("정밀도", "precision"))


def _세자리(값: float) -> float:
    """표에 찍히는 것과 같은 자리로 맞춘다.

    공표한 것은 나눗셈 결과가 아니라 표에 찍힌 숫자다. 14/18 을 그대로 비교하면
    0.7777… 과 0.778 이 달라 영원히 실패한다. 반올림 방식까지 표와 같아야 하므로
    round() 가 아니라 같은 서식을 쓴다.
    """
    return float(f"{값:.3f}")


def _공표된_수치() -> dict[str, dict[str, float]]:
    """README 의 공표 표에서 셋 이름 → 선언된 지표를 읽는다.

    셋마다 선언하는 지표가 다르다. gaps 는 재현율만 적는다 — 못 잡는다고 선언한
    것들이라 정밀도는 이야기의 초점이 아니기 때문이다. 그래서 "적혀 있는 것만"
    대조한다. 적히지 않은 것을 요구하면 문서가 하지 않은 약속을 검사하게 된다.
    """
    표: dict[str, dict[str, float]] = {}
    for 행 in _공표행.finditer(README.read_text(encoding="utf-8")):
        선언: dict[str, float] = {}
        for 한글이름, 키 in _지표:
            찾음 = re.search(rf"{한글이름}\s+([\d.]+)", 행.group("나머지"))
            if 찾음:
                선언[키] = float(찾음.group(1))
        표[행.group("이름")] = 선언
    return 표


def _실제_수치() -> dict[str, dict[str, float]]:
    """`sumunjang report` 가 실제로 내는 셋별 전체 수치.

    내부 함수를 부르지 않고 CLI 를 거친다. README 가 약속한 것은 함수가 아니라
    `sumunjang report` 라는 명령이고, 사용자와 심사위원이 두드리는 것도 그쪽이다.
    """
    out = io.StringIO()
    코드 = run(
        ["report", "--json", *(str(저장소 / 이름) for 이름 in DEFAULT_GOLDENSETS)],
        stdin=io.StringIO(),
        stdout=out,
        stderr=io.StringIO(),
    )
    assert 코드 == 0, "채점이 실패했다"

    import json

    실측: dict[str, dict[str, float]] = {}
    for 이름, 결과 in json.loads(out.getvalue()).items():
        정답, 탐지, 적중 = (결과["total"][k] for k in ("expected", "detected", "hit"))
        실측[이름] = {
            "recall": _세자리(적중 / 정답 if 정답 else 0.0),
            "precision": _세자리(적중 / 탐지 if 탐지 else 0.0),
        }
    return 실측


def test_README_공표_표를_실제로_읽어낸다():
    """파서가 조용히 빈 결과를 돌려주면 아래 대조가 전부 통과한다.

    표 형식이 바뀌어 검사가 사라지는 것과 계약을 지키는 것은 다르다. 골든셋
    파서가 닫히지 않은 마커를 오류로 올리는 것과 같은 이유다 — 정답이 소리 없이
    사라지면 채점이 실제보다 좋아 보인다.
    """
    공표 = _공표된_수치()

    assert set(공표) == set(DEFAULT_GOLDENSETS), (
        "README 공표 표와 채점 대상 셋이 어긋난다. 셋을 늘렸다면 표에도 줄을 더해야 한다."
    )
    for 이름, 선언 in 공표.items():
        assert 선언, f"{이름} 행에서 지표를 하나도 읽지 못했다 — 표 형식이 바뀌었는가"


def test_공표한_수치를_실제_채점이_그대로_낸다():
    """하한이 아니라 정확한 일치를 요구한다.

    하한만 보면 gaps 를 감시할 수 없다. 0.778 을 0.9 로 올리는 변경은 회귀가
    아니지만, 그대로 두면 README 가 틀린 숫자를 공표하게 된다. 좋아진 것도
    문서에 반영해야 계약이다.
    """
    공표 = _공표된_수치()
    실측 = _실제_수치()

    어긋난것 = [
        f"{이름} {키}: README {값:.3f} ≠ 실측 {실측[이름][키]:.3f}"
        for 이름, 선언 in sorted(공표.items())
        for 키, 값 in sorted(선언.items())
        if 실측[이름][키] != 값
    ]

    assert not 어긋난것, "README 공표 수치와 실제 채점이 어긋난다:\n  " + "\n  ".join(어긋난것)
