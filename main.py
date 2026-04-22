"""
SAP AI Core Orchestration API를 호출해 뉴스 감성을 분류한다.

동작 흐름:
  1. news_sentiment_classifier.json (AI Launchpad UI에서 export한 orchestration configuration)을 로드
  2. UI export 포맷 → API runtime 포맷으로 변환
     (UI가 돌려주는 JSON 과 /completion 이 받는 JSON 은 구조가 다르다 — 이게 이 파일의 핵심)
  3. deployment 의 /completion 엔드포인트에 raw HTTP POST
     (gen_ai_hub SDK 는 OpenAI 호환 client 만 제공하므로 Orchestration /completion 은 직접 쏜다)
"""

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from gen_ai_hub.proxy.core.proxy_clients import get_proxy_client

# .env 파일이 있으면 자동으로 os.environ 에 로드. 이미 설정된 환경변수는 덮어쓰지 않음.
# 배포 환경(컨테이너/CI)에서는 .env 없이 실제 env 만으로 동작해도 됨.
load_dotenv()

BASE_DIR = Path(__file__).parent
TEMPLATE_FILE = BASE_DIR / "news_sentiment_classifier.json"


@cache
def _get_proxy_client():
    """
    gen_ai_hub proxy client 를 lazy 하게 한 번만 만든다.

    왜 @cache:
      - get_proxy_client() 가 OAuth 2.0 token 을 받아오는 비싼 호출이라 매 요청마다 돌리면 안 됨.
      - @functools.cache 는 thread-safe (내부적으로 lock). FastAPI/Flask 워커에 import 돼도 안전.
      - 테스트에서 토큰 바꾸고 싶으면 _get_proxy_client.cache_clear() 호출.

    왜 os.environ[...] vs os.getenv():
      - 필수 값은 os.environ[...] 로 KeyError 유발 (.env 누락 시 조기 실패).
      - resource_group 은 optional 이라 getenv 로 기본값 제공.
    """
    return get_proxy_client(
        proxy_version="gen-ai-hub",
        client_id=os.environ["AICORE_CLIENT_ID"],
        client_secret=os.environ["AICORE_CLIENT_SECRET"],
        auth_url=os.environ["AICORE_AUTH_URL"],
        base_url=os.environ["AICORE_API_URL"],
        resource_group=os.getenv("AICORE_RESOURCE_GROUP", "default"),
    )


@cache
def _get_ui_config() -> dict[str, Any]:
    """
    news_sentiment_classifier.json 을 한 번만 파싱해 dict 로 캐시.
    배치 처리 중 같은 템플릿을 N번 파싱할 이유가 없음.
    """
    with TEMPLATE_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _ui_config_to_api_body(ui_config: dict[str, Any]) -> dict[str, Any]:
    """
    UI export JSON → /completion API 가 기대하는 orchestration_config 포맷으로 변환.

    왜 이 변환이 필요한가:
      AI Launchpad UI 에서 "Export as JSON" 으로 받는 스냅샷은 K8s-style 스펙 (`spec.modules.*`) 이고,
      런타임 API (/completion) 는 `module_configurations.*_module_config` 구조를 요구한다.
      같은 "configuration" 인데 포맷이 다르다 — 이게 오랜 삽질의 원인이라
      ORCHESTRATION_GUIDE.md §4 에 상세히 정리돼 있음.

    매 호출마다 dict 를 새로 만들지만 순수 함수이고 비용이 무시할 수준이라 별도 캐시는 안 함.
    """
    # UI 스펙 트리에서 prompt_templating 모듈만 꺼낸다 (현재 샘플은 이 모듈만 사용).
    pt = ui_config["spec"]["modules"]["prompt_templating"]
    prompt = pt["prompt"]
    model = pt["model"]
    return {
        "module_configurations": {
            # templating_module_config: 프롬프트 템플릿 + 기본값.
            # template 은 role 별 메시지 리스트 ([{"role":"system","content":[...]}, ...]).
            "templating_module_config": {
                "template": prompt["template"],
                "defaults": prompt.get("defaults", {}),
            },
            # llm_module_config: 어떤 모델을, 어떤 파라미터로 돌릴지.
            # version 은 UI 에서 고를 수 있지만 없으면 SAP 기본 "latest" 로.
            "llm_module_config": {
                "model_name": model["name"],
                "model_version": model.get("version", "latest"),
                "model_params": model.get("params", {}),
            },
        }
    }


def analyze_news(news: dict[str, Any]) -> dict[str, Any]:
    """
    한 건의 뉴스에 대해 저장된 orchestration 템플릿을 실행하고 원본 응답을 그대로 반환.

    Args:
        news: id / title / source 키를 가진 dict (템플릿의 {{?id}}/{{?title}}/{{?source}} 에 대응).

    Returns:
        /completion API 의 raw JSON (orchestration_result.choices[0].message.content 안에 모델 출력).

    Raises:
        httpx.HTTPStatusError: 4xx/5xx 응답.
        KeyError: 필수 env 또는 news 필드 누락.
    """
    pc = _get_proxy_client()
    ui_config = _get_ui_config()

    # /completion 에 보낼 body. orchestration_config + input_params + messages_history 3종 세트.
    body = {
        "orchestration_config": _ui_config_to_api_body(ui_config),
        # input_params 는 UI 의 Input Variables 패널에 나오는 placeholder 에 매핑된다.
        # str() 로 감싸는 이유: API 가 string 을 기대하고 숫자 id 가 들어오면 400 을 뱉는 경우가 있음.
        "input_params": {
            "id": str(news["id"]),
            "title": str(news["title"]),
            "source": str(news["source"]),
        },
        # messages_history: multi-turn 대화일 때만 채운다. single-shot 분류는 빈 리스트 필수.
        # 키 자체를 빼면 일부 런타임에서 400.
        "messages_history": [],
    }

    # SDK 는 OpenAI 호환 client 만 감싸줘서 Orchestration /completion 은 raw POST.
    # pc.ai_core_client.base_url 은 AI_API_URL, pc.request_header 는 OAuth Bearer + resource_group 헤더 조합.
    # rstrip("/") 은 base_url 이 슬래시로 끝나도 "//inference/..." 안 만들게 방어.
    base = pc.ai_core_client.base_url.rstrip("/")
    deployment_url = f"{base}/inference/deployments/{os.environ['DEPLOYMENT_ID']}/completion"

    # timeout=60s: gpt-4.1-mini 가 스트리밍 없이 5~15s 정도 걸림. 안전 마진 포함.
    response = httpx.post(
        deployment_url, headers=pc.request_header, json=body, timeout=60.0
    )
    # 4xx/5xx 면 호출자에게 HTTPStatusError 로 알림. 배치 러너가 except 로 잡아서 기록.
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    # 파일 단독 실행 시의 스모크 테스트 — 배치 없이 한 건만 돌려본다.
    news = {
        "id": "001",
        "title": "Brazil's soybean crop hit by severe drought conditions",
        "source": "Reuters",
    }
    result = analyze_news(news)
    # 모델 출력은 항상 choices[0].message.content 에 문자열로 들어있음.
    content = result["orchestration_result"]["choices"][0]["message"]["content"]
    print("Raw content:")
    print(content)
    # 템플릿이 JSON 을 요구하지만 모델이 가끔 설명 텍스트를 덧붙이는 경우가 있어
    # 파싱 실패해도 크래시 안 나게 보호.
    try:
        print("\nParsed:")
        print(json.dumps(json.loads(content), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print("(content is not JSON)")
