# sap_aicore — SAP AI Core Orchestration 호출 샘플

SAP AI Core 의 Generative AI Hub **Orchestration** 기능으로 만든 tenant-level 템플릿(`news-sentiment-classifier`)을 Python 에서 호출해, `news_data.json` 의 뉴스들을 분류하는 최소 샘플.

저장된 UI 템플릿을 코드에서 호출하는 **패턴/함정/트러블슈팅** 은 [`ORCHESTRATION_GUIDE.md`](./ORCHESTRATION_GUIDE.md) 참고.

## 파일 구조

| 파일 | 역할 |
|---|---|
| `main.py` | **(legacy)** `analyze_news(news)` — 템플릿 JSON 로드 + API body 변환 + `/completion` raw POST |
| `run_batch.py` | **(legacy)** `news_data.json` 의 뉴스 전체를 순회 호출, 결과를 `test_results.json` 에 저장 |
| `main_v2.py` | **(권장)** `analyze_news_v2(news)` — Launchpad configuration 을 ID/name 로 직접 참조 (orchestration_v2) |
| `run_batch_v2.py` | **(권장)** `main_v2` 기반 배치 러너, 결과를 `test_results_v2.json` 에 저장 |
| `news_sentiment_classifier.json` | AI Launchpad UI 에서 export 한 Orchestration Configuration 스냅샷 (legacy 용) |
| `news_data.json` | 테스트 입력 (id / title / source 5건) |
| `.env` | AI Core credential + `DEPLOYMENT_ID` + Orchestration config 참조 |
| `requirements.txt` | 의존성 선언 (`sap-ai-sdk-gen>=6.1.2`, `python-dotenv`, `httpx`) |
| `requirements.lock` | 해시 pin 된 resolved 버전 (legacy — v2 전환 후 재생성 필요) |

`run_batch.py` 실행 시 `test_results.json` 이 생성됩니다.

## 빠른 시작

### 1. 설치
```bash
# 해시 검증 포함 (권장) — uv 필요
uv pip install --require-hashes -r requirements.lock

# uv 가 없으면
pip install -r requirements.txt
```

`requirements.lock` 은 `uv pip compile --generate-hashes` 로 생성된 파일. 변조된 패키지가 끼어들 수 없게 해시 고정되어 있음.

### 2. `.env` 설정
BTP Cockpit → AI Core 인스턴스 → Service Keys 의 JSON 값을 다음과 같이 매핑.

```
AICORE_CLIENT_ID=<clientid>
AICORE_CLIENT_SECRET=<clientsecret>
AICORE_AUTH_URL=<url>                        # /oauth/token 제거
AICORE_API_URL=<serviceurls.AI_API_URL>
AICORE_RESOURCE_GROUP=default                # deployment 가 속한 RG
DEPLOYMENT_ID=<orchestration deployment id>  # scenario=orchestration, RUNNING
```

Deployment ID 는 AI Launchpad **ML Operations → Deployments** 에서 scenario 가 `orchestration` 인 RUNNING 항목에서 확인.

### 3. 실행

**권장 (v2 — Launchpad configuration 직접 참조):**
```bash
python run_batch_v2.py
```
- 로컬 JSON 파일 불필요, Launchpad 에서 수정 즉시 반영 (publish 후).
- 결과는 `test_results_v2.json`.
- `.env` 에 `ORCHESTRATION_CONFIG_ID` (권장) 또는 `ORCHESTRATION_CONFIG_NAME/SCENARIO/VERSION` 필요.

**Legacy (v1 — 로컬 JSON 스냅샷 inline 호출):**
```bash
python run_batch.py
```
결과는 `test_results.json`.

결과 예시:
```
로드된 뉴스: 5개
[1/5] id=001 title=Brazil's soybean crop hit by severe drought conditions
응답: {"category": "weather_br", "sentiment_score": 1.0}
...
완료: 성공 5개 / 실패 0개 / 전체 5개
```

## v1 vs v2 — 언제 무엇을 쓸까

| 상황 | 권장 |
|---|---|
| Launchpad 에서 template 을 자주 수정함 | **v2** — 드리프트 없음 |
| Template 을 git 으로 버전 관리하고 싶음 | v1 — JSON 스냅샷이 git 에 남음 |
| Config ID 가 운영 환경에서 고정 | **v2 + ORCHESTRATION_CONFIG_ID** |
| SDK 를 업그레이드할 수 없음 (sap-ai-sdk-gen<6.1.2) | v1 |

v2 경로의 흔한 에러 `Unused parameters: [...]` 는 코드 버그가 아닌 **Launchpad 상태 문제**인 경우가 대부분:
1. 템플릿 본문에 `{{?key}}` 가 실제로 쓰이고 있는지
2. Launchpad draft 가 publish 되어 실행 버전에 반영됐는지
3. 실행 중인 `name/scenario/version` 이 수정본과 일치하는지

## 다른 UI 템플릿으로 교체하려면

1. AI Launchpad → Generative AI Hub → **Orchestration** 에서 원하는 템플릿을 열기
2. 우측 상단 **Form / JSON** 토글 → JSON 전체 복사
3. `news_sentiment_classifier.json` 을 바꾸거나 새 파일로 저장, `main.py` 의 `TEMPLATE_FILE` 경로 수정
4. UI 우측 **Input Variables** 패널에 나열된 placeholder 이름에 맞춰, 호출 쪽의 `input_params` 키를 일치시키기 (현재는 `id`, `title`, `source`)

자세한 매핑 규칙과 흔한 실수는 [`ORCHESTRATION_GUIDE.md`](./ORCHESTRATION_GUIDE.md) 의 §4, §6, §7 참고.

## 보안

- `.env` 는 커밋하지 않음 (`.gitignore` 적용됨).
- Service key 전체 JSON 을 저장해야 할 때도 git 제외 위치에 둘 것.
