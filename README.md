# sap_aicore — SAP AI Core Orchestration 호출 샘플

SAP AI Core 의 Generative AI Hub **Orchestration** 기능으로 만든 tenant-level 템플릿(`news-sentiment-classifier`)을 Python 에서 호출해, `news_data.json` 의 뉴스들을 분류하는 최소 샘플.

저장된 UI 템플릿을 코드에서 호출하는 **패턴/함정/트러블슈팅** 은 [`ORCHESTRATION_GUIDE.md`](./ORCHESTRATION_GUIDE.md) 참고.

## 파일 구조

| 파일 | 역할 |
|---|---|
| `main.py` | `analyze_news(news)` — 템플릿 JSON 로드 + API body 변환 + `/completion` raw POST |
| `run_batch.py` | `news_data.json` 의 뉴스 전체를 순회 호출, 결과를 `test_results.json` 에 저장 |
| `news_sentiment_classifier.json` | AI Launchpad UI 에서 export 한 Orchestration Configuration 스냅샷 |
| `news_data.json` | 테스트 입력 (id / title / source 5건) |
| `.env` | AI Core credential + `DEPLOYMENT_ID` |
| `requirements.txt` | 의존성 선언 (`generative-ai-hub-sdk`, `python-dotenv`, `httpx`) |
| `requirements.lock` | 해시 pin 된 resolved 버전 (install 시 사용) |

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
```bash
python run_batch.py
```

결과:
```
로드된 뉴스: 5개
[1/5] id=001 title=Brazil's soybean crop hit by severe drought conditions
응답: {"category": "weather_br", "sentiment_score": 1.0}
...
완료: 성공 5개 / 실패 0개 / 전체 5개
결과 저장: /.../test_results.json
```

`test_results.json` 에 입력 · 응답 · usage 가 함께 저장된다.

## 다른 UI 템플릿으로 교체하려면

1. AI Launchpad → Generative AI Hub → **Orchestration** 에서 원하는 템플릿을 열기
2. 우측 상단 **Form / JSON** 토글 → JSON 전체 복사
3. `news_sentiment_classifier.json` 을 바꾸거나 새 파일로 저장, `main.py` 의 `TEMPLATE_FILE` 경로 수정
4. UI 우측 **Input Variables** 패널에 나열된 placeholder 이름에 맞춰, 호출 쪽의 `input_params` 키를 일치시키기 (현재는 `id`, `title`, `source`)

자세한 매핑 규칙과 흔한 실수는 [`ORCHESTRATION_GUIDE.md`](./ORCHESTRATION_GUIDE.md) 의 §4, §6, §7 참고.

## 보안

- `.env` 는 커밋하지 않음 (`.gitignore` 적용됨).
- Service key 전체 JSON 을 저장해야 할 때도 git 제외 위치에 둘 것.
