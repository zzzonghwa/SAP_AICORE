"""
news_data.json 의 각 뉴스를 news-sentiment-classifier 템플릿으로 LLM 호출하고
입력 + 응답 + usage 를 test_results.json 에 덤프하는 배치 러너.

흐름:
  1. 입력 파일 존재 + 리스트 타입 확인
  2. 각 아이템의 필수 키 + 값 유효성 pre-flight 검증 (네트워크 호출 전 빠른 실패)
  3. 아이템별로 analyze_news() 호출, 실패해도 배치 계속 (한 건 오류로 전체 중단 방지)
  4. 결과를 구조화된 JSON 으로 저장

Exit codes:
  0 - 전부 성공
  1 - 입력 파일/포맷 문제 또는 pre-flight 검증 실패 (한 건도 호출 안 함)
  2 - 호출은 돌았는데 일부 실패 (부분 성공)
"""

import json
import sys
from pathlib import Path

from main import analyze_news

BASE_DIR = Path(__file__).parent
INPUT_FILE = BASE_DIR / "news_data.json"
OUTPUT_FILE = BASE_DIR / "test_results.json"

# 각 뉴스 아이템이 반드시 가져야 하는 키. tuple 로 두는 이유:
#   - 런타임에 실수로 변경 안 되게 (list 였으면 누가 .append 할 수 있음)
#   - 템플릿의 {{?id}}/{{?title}}/{{?source}} 와 1:1 매핑되는 계약
REQUIRED_KEYS = ("id", "title", "source")


def _extract_content(resp: dict) -> str:
    """
    /completion 응답에서 모델 출력 문자열만 꺼낸다.
    SAP 포맷은 orchestration_result.choices[0].message.content 고정.
    호출자의 try/except 안에서 쓰는 것을 전제로 방어 코드 없음.
    """
    return resp["orchestration_result"]["choices"][0]["message"]["content"]


def _try_parse_json(text: str):
    """
    문자열을 JSON 으로 파싱 시도, 실패하면 원본 문자열 반환.

    왜 이게 필요한가:
      템플릿 system prompt 가 "JSON 만 반환" 이라고 지시해도 모델이 가끔
      설명 텍스트나 마크다운 코드펜스를 덧붙인다. 이 경우 raw 텍스트로 저장해
      후속 분석에서 확인할 수 있게 함.
    """
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        # TypeError: text 가 None 등 str 이 아닌 경우.
        # JSONDecodeError: 파싱 실패.
        return text


def _validate_items(news_items: list) -> list[str]:
    """
    모든 아이템의 스키마를 pre-flight 검증해 문제가 있으면 에러 메시지 리스트로 반환.

    설계 포인트:
      - 첫 에러에서 바로 리턴하지 않고 전부 수집 → 사용자가 한 번에 다 고칠 수 있게.
      - None 과 공백 문자열도 "비어있음" 으로 간주 (키는 있지만 쓸모없는 값).
      - 숫자 타입은 통과시킴: main.py 의 str() 래퍼가 자연스럽게 "123" 으로 바꿔서 처리.
    """
    errors = []
    for idx, news in enumerate(news_items, start=1):
        if not isinstance(news, dict):
            errors.append(f"[{idx}] dict 가 아님 — got {type(news).__name__}")
            continue
        for k in REQUIRED_KEYS:
            if k not in news:
                errors.append(f"[{idx}] 필수 키 누락: {k}")
            elif news[k] is None or str(news[k]).strip() == "":
                errors.append(f"[{idx}] {k} 비어있음")
    return errors


def main() -> int:
    # ── 1) 입력 파일 존재 확인 ────────────────────────────────────
    if not INPUT_FILE.exists():
        print(f"입력 파일을 찾을 수 없습니다: {INPUT_FILE}")
        return 1

    with INPUT_FILE.open("r", encoding="utf-8") as f:
        news_items = json.load(f)

    # ── 2) 최상위 타입 확인 ───────────────────────────────────────
    if not isinstance(news_items, list):
        print(f"news_data.json 은 리스트여야 합니다. got={type(news_items).__name__}")
        return 1

    # ── 3) Pre-flight 스키마 검증 ─────────────────────────────────
    # 여기서 실패시키는 이유: 10건 중 5번째가 깨져있으면, 검증 없이 돌릴 경우
    # 앞 4건 LLM 호출 (= 돈 + 시간) 태우고 나서야 에러를 만나게 됨.
    errors = _validate_items(news_items)
    if errors:
        print("입력 검증 실패. 네트워크 호출 전에 중단합니다:")
        for err in errors:
            print(f"  {err}")
        return 1

    print(f"로드된 뉴스: {len(news_items)}개")
    print("=" * 70)

    # ── 4) 아이템별 LLM 호출 ───────────────────────────────────────
    records = []
    success, failure = 0, 0
    for idx, news in enumerate(news_items, start=1):
        print(f"\n[{idx}/{len(news_items)}] id={news.get('id')} title={news.get('title')}")
        try:
            resp = analyze_news(news)
            content = _extract_content(resp)
            parsed = _try_parse_json(content)
            success += 1
            print(f"응답: {content}")
            records.append({
                "input": news,
                "status": "success",
                "response": parsed,
                # usage 는 응답에 있으면 저장, 없으면 None.
                # 비용/토큰 추적용이라 없어도 배치는 계속 진행.
                "usage": resp.get("orchestration_result", {}).get("usage"),
            })
        except Exception as e:
            # except Exception 이 광범위해 보이지만 배치 러너에선 올바른 선택:
            # - 네트워크/타임아웃/4xx/5xx/SDK 버그 등 무엇이 터져도 한 건 실패로 격리.
            # - 결과 JSON 에 에러 타입과 메시지 남겨서 나중에 재실행 판단 가능.
            # KeyboardInterrupt 는 BaseException 이라 여기 안 걸림 → Ctrl+C 정상 작동.
            failure += 1
            print(f"실패: {type(e).__name__}: {e}")
            records.append({
                "input": news,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
            })

    # ── 5) 결과 덤프 ──────────────────────────────────────────────
    summary = {
        "template": "news-sentiment-classifier",
        "total": len(news_items),
        "success": success,
        "failure": failure,
        "results": records,
    }
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        # ensure_ascii=False: 한글/유니코드를 \uXXXX 로 이스케이프하지 않고 그대로 저장.
        # indent=2: 사람이 읽기 편하게.
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print(f"완료: 성공 {success}개 / 실패 {failure}개 / 전체 {len(news_items)}개")
    print(f"결과 저장: {OUTPUT_FILE}")
    # 전부 성공이면 0, 일부라도 실패면 2. CI 에서 "부분 실패" 를 "완전 성공" 과 구분하려는 의도.
    return 0 if failure == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
