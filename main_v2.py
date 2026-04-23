"""
SAP AI Core Orchestration API 를 호출해 뉴스 감성을 분류한다 (v2 경로).

main.py 와의 차이:
  - 로컬 JSON 파일 로드/포맷 변환/raw HTTP POST 없음.
  - AI Launchpad 에 저장된 Orchestration Configuration 을 ID 또는
    name+scenario+version 으로 직접 참조해 호출한다.
  - `sap-ai-sdk-gen>=6.1.2` 의 `gen_ai_hub.orchestration_v2` 모듈 사용.
    (구 `generative-ai-hub-sdk` 4.x 에는 이 모듈이 없다 — 업그레이드 필수.)

장점:
  - Template 드리프트 제거. UI 에서 수정하고 publish 하면 즉시 반영됨
    (단, Launchpad 에서 draft 를 실행 버전으로 publish 해야 런타임이 본다).
  - `news_sentiment_classifier.json` 로컬 스냅샷 불필요.
  - UI format → API body 변환 로직 불필요.

필요한 .env:
  AICORE_CLIENT_ID, AICORE_CLIENT_SECRET, AICORE_AUTH_URL, AICORE_API_URL
  AICORE_RESOURCE_GROUP=default
  DEPLOYMENT_ID=<orchestration deployment id>

  다음 중 택1 (ID 우선):
    (A) ORCHESTRATION_CONFIG_ID=c690b803-d848-4504-960c-2e172756ed7f
    (B) ORCHESTRATION_CONFIG_NAME=news-sentiment-classifier
        ORCHESTRATION_CONFIG_SCENARIO=orchestration
        ORCHESTRATION_CONFIG_VERSION=0.0.1
"""

import json
import os
from functools import cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()


@cache
def _get_service():
    """
    OrchestrationService 인스턴스를 lazy 하게 한 번만 생성.

    내부적으로 AICORE_* env 를 읽어 OAuth 토큰을 받아오는 비싼 호출이라
    @cache 로 프로세스 수명 동안 재사용. 테스트 시 리셋하려면
    _get_service.cache_clear().
    """
    from gen_ai_hub.orchestration_v2.service import OrchestrationService

    # deployment_id 는 생성자 인자로 받지만, SDK 가 env 에서 자동으로 읽는 경우도 있음.
    # 명시 전달이 안정적이므로 명시.
    return OrchestrationService(deployment_id=os.environ["DEPLOYMENT_ID"])


@cache
def _get_config_ref():
    """
    .env 에서 configuration 참조 정보를 읽어 config_ref 객체 생성.

    우선순위:
      1) ORCHESTRATION_CONFIG_ID 가 있으면 ById 방식 (가장 안정적, 버전 고정)
      2) 없으면 NAME+SCENARIO+VERSION 세트 확인
      3) 둘 다 불완전하면 RuntimeError — 네트워크 호출 전 조기 실패

    왜 ID 우선인가:
      name+scenario+version 은 version 을 올리면 재지정해야 하는데,
      ID 는 configuration 자체를 재생성하지 않는 한 불변. 운영에선 ID 고정이 안전.
    """
    from gen_ai_hub.orchestration_v2.models.config import (
        CompletionRequestConfigurationReferenceByIdConfigRef,
        CompletionRequestConfigurationReferenceByNameScenarioVersionConfigRef,
    )

    config_id = os.getenv("ORCHESTRATION_CONFIG_ID")
    if config_id:
        return CompletionRequestConfigurationReferenceByIdConfigRef(id=config_id)

    name = os.getenv("ORCHESTRATION_CONFIG_NAME")
    scenario = os.getenv("ORCHESTRATION_CONFIG_SCENARIO")
    version = os.getenv("ORCHESTRATION_CONFIG_VERSION")
    if name and scenario and version:
        return CompletionRequestConfigurationReferenceByNameScenarioVersionConfigRef(
            name=name,
            scenario=scenario,
            version=version,
        )

    raise RuntimeError(
        "Orchestration configuration reference 가 .env 에 없습니다. "
        "ORCHESTRATION_CONFIG_ID 를 지정하거나, "
        "ORCHESTRATION_CONFIG_NAME + ORCHESTRATION_CONFIG_SCENARIO + "
        "ORCHESTRATION_CONFIG_VERSION 셋을 모두 지정하세요."
    )


def analyze_news_v2(news: dict[str, Any]) -> Any:
    """
    한 건의 뉴스를 Launchpad 에 저장된 orchestration 으로 분류.

    Args:
        news: id / title / source 키를 가진 dict.
              (Launchpad 템플릿의 {{?id}}/{{?title}}/{{?source}} placeholder 에 매핑)

    Returns:
        SDK 의 orchestration response 객체.
        모델 출력은 response.final_result.choices[0].message.content.

    Raises:
        OrchestrationError: 4xx/5xx 응답 (SDK 가 감싸서 던짐).
          - `Unused parameters: [...]` 는 보통 코드 버그가 아니라
            Launchpad 템플릿 본문에서 해당 placeholder 를 사용하지 않고 있거나
            draft 가 실행 버전에 publish 되지 않은 경우. 순서대로 점검:
              1) 템플릿 본문에 {{?key}} 로 참조되고 있는가
              2) Launchpad 에서 draft 가 publish 되었는가
              3) 실행 중 name/scenario/version 이 수정본과 일치하는가
        KeyError: 필수 env 또는 news 필드 누락.
    """
    service = _get_service()
    config_ref = _get_config_ref()

    # str() 래핑 이유: API 가 placeholder 값을 string 으로 기대함.
    # 숫자 id 가 들어오면 일부 런타임이 400 을 뱉는 케이스가 있어 방어.
    placeholder_values = {
        "id": str(news["id"]),
        "title": str(news["title"]),
        "source": str(news["source"]),
    }

    return service.run(config_ref=config_ref, placeholder_values=placeholder_values)


def extract_content(response: Any) -> str:
    """
    response 객체에서 모델 출력 문자열만 꺼낸다.
    raw HTTP 의 orchestration_result 대신 SDK 래퍼인 final_result 를 사용.
    """
    return response.final_result.choices[0].message.content


if __name__ == "__main__":
    # 파일 단독 실행 시 스모크 테스트 — 한 건만 돌려본다.
    news = {
        "id": "001",
        "title": "Brazil's soybean crop hit by severe drought conditions",
        "source": "Reuters",
    }
    resp = analyze_news_v2(news)
    content = extract_content(resp)
    print("Raw content:")
    print(content)
    try:
        print("\nParsed:")
        print(json.dumps(json.loads(content), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print("(content is not JSON)")
