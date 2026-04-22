# SAP AI Core Orchestration — Tenant-Level Template 호출 가이드

AI Launchpad **Generative AI Hub → Orchestration** 메뉴에서 UI로 만든 "Orchestration Configuration" 템플릿을 Python 코드에서 호출하는 방법을 정리한 실무 가이드. 본 프로젝트(`main.py`, `run_batch.py`) 가 이 패턴을 따른다.

---

## 1. 핵심 개념 — 같은 이름, 다른 객체

SAP는 두 가지 완전히 다른 리소스를 모두 "Configuration" 이라고 부른다. 이걸 구분하지 못하면 API 쿼리 결과와 UI 표시가 달라 보여 긴 디버깅 늪에 빠진다.

| 구분 | AI Core v2 Configuration | Orchestration Configuration (UI) |
|---|---|---|
| 위치 | Resource Group 소속 | **Tenant 전역** (RG 무관) |
| 역할 | Executable + binding 템플릿 (deployment 생성 재료) | Prompt template + model 설정 묶음 |
| 예시 | `defaultOrchestrationConfig` (scenario=orchestration) | `news-sentiment-classifier` |
| 조회 API | `pc.ai_core_client.configuration.query()` | 해당 SDK 메서드 **없음** |
| UI 위치 | **ML Operations → Configurations** | **Generative AI Hub → Orchestration** |

**표시 확인법:** AI Launchpad 상단 헤더 `default-aicore-key (default)` 의 괄호 안은 활성 RG 지만, Generative AI Hub → Orchestration 탭의 카드 목록은 **RG 와 무관하게 항상 동일**하다. 이게 Tenant 레벨이라는 증거.

---

## 2. SDK 한계 — `config_name` 은 함정

```python
# ❌ 직관적이지만 동작 안 함 — "A configuration is required to invoke the orchestration service."
OrchestrationService(
    proxy_client=pc,
    deployment_id=DEPLOYMENT_ID,
    config_name="news-sentiment-classifier",   # tenant-level template 이름
)
```

`gen_ai_hub` SDK 의 `config_name` / `config_id` 는 **AI Core deployment 의 configuration_name/id 매칭** 용도다 (`discover_orchestration_api_url` 내부 로직). tenant-level 저장소에서 template 을 로드해 자동 주입하는 기능이 아니다.

또한 SAP Orchestration `/completion` API 는 **저장된 config ID 참조 호출을 지원하지 않는다**:

```
POST .../inference/deployments/{id}/completion
body: {"config_id": "c690b8...", "input_params": {...}}
→ 400: "'orchestration_config' is a required property"
```

**결론: Tenant-level template 으로 호출하려면 template JSON 을 `orchestration_config` body 에 inline 으로 포함시켜야 한다.** 방법은 UI 에서 export → 로컬 파일 → raw HTTP POST.

---

## 3. 호출 흐름

```
[UI] Orchestration Configuration 생성/저장
   │
   │ UI 편집화면 우측 상단 Form / JSON 토글 → JSON 복사
   ▼
[프로젝트] {template}.json 파일로 저장
   │
   │ UI format → API runtime format 변환
   ▼
[Python] httpx.post(
    {deployment_url}/completion,
    json={"orchestration_config": {...}, "input_params": {...}, "messages_history": []}
)
```

---

## 4. UI JSON ↔ API Body 매핑표

UI 가 export 하는 JSON (`spec.modules.prompt_templating.*`) 은 **API runtime body 와 키가 다르다**. 변환 필요.

| UI export 경로 | API body 경로 |
|---|---|
| `spec.modules.prompt_templating.prompt.template` | `orchestration_config.module_configurations.templating_module_config.template` |
| `spec.modules.prompt_templating.prompt.defaults` | `...templating_module_config.defaults` |
| `spec.modules.prompt_templating.model.name` | `...llm_module_config.model_name` |
| `spec.modules.prompt_templating.model.version` | `...llm_module_config.model_version` |
| `spec.modules.prompt_templating.model.params` | `...llm_module_config.model_params` |

`content` 필드는 UI 가 `[{type:"text", text:"..."}]` 배열로 export 하는데, API 가 **배열과 문자열 둘 다 수락**한다 (검증 완료). 그대로 넘겨도 된다.

---

## 5. 최소 동작 코드 — UI format → API body 변환

```python
import json, os, httpx
from dotenv import load_dotenv
from gen_ai_hub.proxy.core.proxy_clients import get_proxy_client

load_dotenv()

def ui_to_api(ui_config: dict) -> dict:
    """UI export JSON → /completion 의 orchestration_config 필드."""
    pt = ui_config["spec"]["modules"]["prompt_templating"]
    prompt, model = pt["prompt"], pt["model"]
    return {
        "module_configurations": {
            "templating_module_config": {
                "template": prompt["template"],
                "defaults": prompt.get("defaults", {}),
            },
            "llm_module_config": {
                "model_name": model["name"],
                "model_version": model.get("version", "latest"),
                "model_params": model.get("params", {}),
            },
        }
    }

def call(template_path: str, input_params: dict) -> dict:
    with open(template_path, encoding="utf-8") as f:
        ui_config = json.load(f)
    pc = get_proxy_client(
        proxy_version="gen-ai-hub",
        client_id=os.environ["AICORE_CLIENT_ID"],
        client_secret=os.environ["AICORE_CLIENT_SECRET"],
        auth_url=os.environ["AICORE_AUTH_URL"],
        base_url=os.environ["AICORE_API_URL"],
        resource_group=os.getenv("AICORE_RESOURCE_GROUP", "default"),
    )
    url = f'{pc.ai_core_client.base_url.rstrip("/")}/inference/deployments/{os.environ["DEPLOYMENT_ID"]}/completion'
    r = httpx.post(url, headers=pc.request_header, json={
        "orchestration_config": ui_to_api(ui_config),
        "input_params": input_params,
        "messages_history": [],
    }, timeout=60.0)
    r.raise_for_status()
    return r.json()

# 호출
resp = call("news_sentiment_classifier.json",
            {"id": "001", "title": "Brazil drought", "source": "Reuters"})
print(resp["orchestration_result"]["choices"][0]["message"]["content"])
```

---

## 6. 주의사항 (Gotcha)

1. **Placeholder 이름을 UI 와 정확히 맞출 것.** UI 편집화면 우측 "Input Variables" 패널에 실제 사용 가능한 placeholder 가 나열된다. `{{?news}}` 같은 추정 이름으로 `input_params` 를 채우면 치환이 안 돼 빈 프롬프트가 전송된다.
2. **Deployment 는 어떤 RG 든 상관없다.** `scenario_id=orchestration` 이고 `RUNNING` 이면 그 deployment 로 tenant-level template 을 호출할 수 있다. Template 이 `grounding-rg` 에 "보이더라도" 호출은 `default` RG 의 deployment 로 해도 된다.
3. **`DEPLOYMENT_ID` 만 있으면 `ORCH_NAME` 은 런타임에 불필요.** Template 은 body 에 inline 으로 들어가므로 이름으로 조회할 필요 없음. `.env` 에 남겨둔다면 메모/문서화 용도.
4. **Template 업데이트 시 JSON 재-export 필요.** 런타임 자동 동기화 없음. UI 에서 template 을 수정했으면 JSON 파일을 다시 받아 프로젝트에 반영할 것.
5. **Credential 비교는 `clientid` 만으론 불충분.** 같은 이름의 service key 가 서로 다른 AI Core 인스턴스에 중복 존재할 수 있다. `url` (auth URL), `serviceurls.AI_API_URL` 도 함께 맞춰야 같은 인스턴스.
6. **`configuration.query()` 로 UI 의 Orchestration Configuration 을 찾으려 하지 말 것.** 별개 저장소이므로 결과에 나오지 않는다. UI 에서 직접 확인하거나 JSON export 로 우회.

---

## 7. 트러블슈팅 체크리스트

| 증상 | 원인 | 조치 |
|---|---|---|
| 400 `'orchestration_config' is a required property` | body 에 config 참조(`config_id`) 만 보냄 | Template JSON 을 inline 으로 포함 |
| `ValueError: A configuration is required to invoke the orchestration service.` | SDK `config_name` 만 주고 `config` 인자 생략 | SDK 대신 raw HTTP 사용 (본 가이드 §5) |
| UI 에 보이는 template 이 `configuration.query()` 로 안 나옴 | 별도 tenant-level 저장소 | UI 또는 JSON export 로 접근 |
| `AIAPINotFoundException: Configuration ... not found` (by UI 카드의 ID) | 같은 이유 | 위와 동일 |
| 응답 `content` 가 비거나 엉뚱 | Placeholder 이름 불일치 | UI 의 Input Variables 패널과 `input_params` 키 비교 |
| 크레덴셜 맞는데 다른 인스턴스처럼 보임 | `url` / `AI_API_URL` 만 다른 경우 | Service key JSON 전체를 교체 |

---

## 8. 본 프로젝트 파일 맵

| 파일 | 역할 |
|---|---|
| `.env` | AI Core credential, `DEPLOYMENT_ID` |
| `news_sentiment_classifier.json` | UI export 한 template 스냅샷 |
| `main.py` | 변환 + raw POST (`analyze_news()` 제공) |
| `news_data.json` | 배치 테스트 입력 (id/title/source 5건) |
| `run_batch.py` | `news_data.json` 순회 호출 + `test_results.json` 생성 |
| `requirements.txt` / `requirements.lock` | 의존성 선언 + 해시 pin 된 resolved 버전 |

실행:
```bash
python run_batch.py
```

---

## 9. 참고

- Orchestration API spec: <https://help.sap.com/doc/generative-ai-hub-sdk/CLOUD/en-US/_reference/orchestration-service2.html>
- `/completion` 응답 스키마: `response.orchestration_result.choices[0].message.content`, `response.orchestration_result.usage`
- 본 가이드는 `generative-ai-hub-sdk` 6.x 기준. 향후 SDK 에 tenant-level template native 지원이 추가되면 §2, §5 는 간소화 가능.
