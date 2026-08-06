# SBOM (소프트웨어 자재명세서)

프로젝트: 수문장(sumunjang) v0.1.0 · 라이선스: Apache-2.0
기준일: 2026-08-06 · 생성 근거: `uv pip tree`, 패키지 메타데이터

## 설계 원칙: 개인정보 경로에 서드파티 코드를 두지 않는다

개인정보를 실제로 읽고 가리는 모듈(`detect`, `mask`, `anthropic`, `goldenset`, `cli`)은
**파이썬 표준 라이브러리만** 사용한다. 외부 패키지는 네트워크 계층(`proxy`)에만 쓰인다.
보안 도구에서 의존성은 공급망 공격 표면이므로 의도적으로 최소화했다.

## 런타임 의존성 (직접)

| 번호 | 라이브러리명 | 버전 | 라이선스 | 공식 저장소 URL | 사용 목적 및 주요 기능 |
|---|---|---|---|---|---|
| 1 | httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx | 마스킹된 요청을 업스트림 AI API로 전달하는 비동기 HTTP 클라이언트 |
| 2 | uvicorn | 0.52.1 | BSD-3-Clause | https://github.com/encode/uvicorn | 게이트웨이 ASGI 서버 구동 (웹 프레임워크 없이 순수 ASGI 앱을 직접 구현) |

## 런타임 의존성 (전이)

| 번호 | 라이브러리명 | 버전 | 라이선스 | 공식 저장소 URL | 사용 목적 및 주요 기능 |
|---|---|---|---|---|---|
| 3 | anyio | 4.14.2 | MIT | https://github.com/agronholm/anyio | httpx의 비동기 실행 추상화 |
| 4 | certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi | TLS 루트 인증서 번들 (업스트림 HTTPS 검증) |
| 5 | httpcore | 1.0.9 | BSD-3-Clause | https://github.com/encode/httpcore | httpx의 저수준 전송 계층 |
| 6 | h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 | HTTP/1.1 프로토콜 상태 기계 |
| 7 | idna | 3.18 | BSD-3-Clause | https://github.com/kjd/idna | 국제화 도메인 이름 처리 |
| 8 | typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions | 하위 파이썬 버전 타입 힌트 호환 |
| 9 | click | 8.4.2 | BSD-3-Clause | https://github.com/pallets/click | uvicorn 내부 명령줄 처리 |

## 개발·검증 의존성 (배포물에 포함되지 않음)

| 번호 | 라이브러리명 | 버전 | 라이선스 | 공식 저장소 URL | 사용 목적 및 주요 기능 |
|---|---|---|---|---|---|
| 10 | pytest | 9.1.1 | MIT | https://github.com/pytest-dev/pytest | 테스트 실행 |
| 11 | pytest-cov | 7.1.0 | MIT | https://github.com/pytest-dev/pytest-cov | 커버리지 측정 |
| 12 | hatchling | (빌드시) | MIT | https://github.com/pypa/hatch | 패키지 빌드 백엔드 |

## 라이선스 양립성 검토

본 프로젝트는 Apache-2.0으로 배포한다. 사용 의존성의 라이선스는 다음과 같이 양립한다.

- **BSD-3-Clause, MIT, PSF-2.0**: 허용적 라이선스로 Apache-2.0 배포에 제약이 없다.
  저작권 고지 유지 의무만 있으며, 각 패키지는 배포물에 자체 라이선스 파일을 포함한다.
- **MPL-2.0 (certifi)**: 파일 단위 약한 카피레프트다. 본 프로젝트는 certifi를 수정 없이
  의존성으로 사용하므로 소스 공개 의무가 본 프로젝트 코드로 전파되지 않는다.
  certifi 파일을 수정할 경우에만 해당 파일에 MPL-2.0이 적용된다.
- 강한 카피레프트(GPL/AGPL) 의존성은 직접·전이 모두 **없다**.

## AI 모델

본 v0.1.0은 **AI 모델을 탑재하지 않는다**. 탐지는 전부 규칙과 검증식(체크섬)으로 이루어지며,
모델 가중치·학습 데이터를 포함하지 않는다. 따라서 모델 라이선스 전파 문제가 발생하지 않는다.
(한국어 개체명 인식 모델을 이용한 이름·주소 탐지는 로드맵 항목이다.)
