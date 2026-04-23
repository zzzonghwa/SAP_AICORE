"""
news_data.json 의 각 뉴스를 Launchpad 에 저장된 orchestration 으로 호출하고
입력 + 응답 + usage 를 test_results_v2.json 에 덤프하는 배치 러너 (v2 경로).

run_batch.py 와의 차이:
  - analyze_news_v2() 를 사용 (orchestration_v2 SDK).
  - 로컬 템플릿 JSON 불필요 — Launchpad configuration 을 ID/name 로 참조.
  - 출력 파일을 test_results_v2.json 으로 분리해 구 배치 결과와 충돌 방지.

Exit codes (run_batch.py 와 동일):
  0 - 전부 성공
  1 - 입력 파일/포맷 문제 또는 pre-flight 검증 실패 (한 건도 호출 안 함)
  2 - 호출은 돌았는데 일부 실패 (부분 성공)
"""

import json
import sys
from pathlib import Path
from typing import Any

from main_v2 import analyze_news_v2, extract_content

BASE_DIR = Path(__file__).parent
INPUT_FILE = BASE_DIR / "news_data.json"
OUTPUT_FILE = BASE_DIR / "test_results_v2.json"

REQUIRED_KEYS = ("id", "title", "source")


def _try_parse_json(text: str) -> Any:
    """JSON 파싱 시도, 실패하면 원본 문자열 반환."""
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text


def _extract_usage(response: Any) -> dict | None:
    """
    SDK response 에서 usage 정보를 추출.
    pydantic 모델이면 model_dump() 로 dict 화, 아니면 속성 유무만 체크.
    """
    usage = getattr(response.final_result, "usage", None)
    if usage is None:
        return None
    # pydantic v2
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    # pydantic v1 / fallback
    if hasattr(usage, "dict"):
        return usage.dict()
    return dict(usage) if isinstance(usage, dict) else None


def _validate_items(news_items: list) -> list[str]:
    """스키마 pre-flight 검증 — 네트워크 호출 전에 빠르게 실패."""
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
    if not INPUT_FILE.exists():
        print(f"입력 파일을 찾을 수 없습니다: {INPUT_FILE}")
        return 1

    with INPUT_FILE.open("r", encoding="utf-8") as f:
        news_items = json.load(f)

    if not isinstance(news_items, list):
        print(f"news_data.json 은 리스트여야 합니다. got={type(news_items).__name__}")
        return 1

    errors = _validate_items(news_items)
    if errors:
        print("입력 검증 실패. 네트워크 호출 전에 중단합니다:")
        for err in errors:
            print(f"  {err}")
        return 1

    print(f"로드된 뉴스: {len(news_items)}개")
    print("=" * 70)

    records = []
    success, failure = 0, 0
    for idx, news in enumerate(news_items, start=1):
        print(f"\n[{idx}/{len(news_items)}] id={news.get('id')} title={news.get('title')}")
        try:
            resp = analyze_news_v2(news)
            content = extract_content(resp)
            parsed = _try_parse_json(content)
            success += 1
            print(f"응답: {content}")
            records.append({
                "input": news,
                "status": "success",
                "response": parsed,
                "usage": _extract_usage(resp),
            })
        except Exception as e:
            failure += 1
            print(f"실패: {type(e).__name__}: {e}")
            records.append({
                "input": news,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
            })

    summary = {
        "template": "news-sentiment-classifier",
        "mode": "orchestration_v2 (config_ref)",
        "total": len(news_items),
        "success": success,
        "failure": failure,
        "results": records,
    }
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print(f"완료: 성공 {success}개 / 실패 {failure}개 / 전체 {len(news_items)}개")
    print(f"결과 저장: {OUTPUT_FILE}")
    return 0 if failure == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
