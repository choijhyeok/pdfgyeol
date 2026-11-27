# 기능 3: 선택형 Decision 판정

```python
from pdfgyeol import DecisionConfig, extract_pdf, infer_page, resolve_page

page = extract_pdf("notes.pdf").pages[0]
local = infer_page(page)

# 기본값: 키·환경 변수·.env·네트워크 접근 없이 기하 순서 유지
disabled = resolve_page(page, local)
assert disabled.layout == local

# 사용자가 호출을 명시적으로 켠 경우만 국소 후보 비교
result = resolve_page(page, local, config=DecisionConfig(enabled=True))
for decision in result.decisions:
    print(decision.scope_id, decision.outcome, decision.reason)
ordered_source_ids = result.layout.ordered_fragment_ids()
```

`resolve_page`는 기능 2의 원본 기하 결과를 받아 새 `DecisionResult(layout, decisions)`를 반환한다. `layout`을 생략하면 내부에서 `infer_page(page)`를 실행한다. 입력 `Page`·`PageLayout`은 불변이며 원문 텍스트를 모델 응답으로 교체하지 않는다. 기능 2 자체와 `iter_layout`은 계속 로컬에서만 동작한다.

## 명시적 설정과 전송 범위

활성 상태에서도 서로 다른 유효 후보가 없으면 클라이언트를 만들거나 키를 읽지 않는다. 호출이 필요한 시점에만 프로세스 환경의 `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `DECISION_MODEL_DEPLOYMENT`를 읽는다. 배포명 기본값은 `Decision-1.1`이다. 운영자가 실제 Decision-1.1 배포명을 지정해야 하며 배포 별칭의 원격 모델 버전을 클라이언트가 독립적으로 증명하지는 않는다. `.env` 자동 탐색·로딩은 없다. 키 누락은 `not_configured` 보류 기록을 남긴다.

직접 설정하거나 테스트 제공자를 주입할 수도 있다.

```python
import os
from pdfgyeol import DecisionClient, DecisionConfig, resolve_page

provider = DecisionClient(
    endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],  # 완전한 HTTPS scoring URL
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    model="Decision-1.1",
    timeout=20,
)
result = resolve_page(page, local, config=DecisionConfig(enabled=True), provider=provider)
```

HTTP 요청은 `model`, 문자열 `state`, `questions`를 보낸다. 질문별 형태는 `{"type": "noul", "instructions": "..."}`이며 응답은 `answers.candidate_N.noul` 수치다. OpenAI chat/completions 형식은 사용하지 않는다. HTTPS만 허용하고 리다이렉트는 거부한다. 호출은 대안별 한 번이며 타임아웃·429·5xx 등 실패 시 재시도 없이 기본 기하 순서를 유지한다. 키·endpoint는 클라이언트 `repr`에서 제외하고 예외 본문을 판정 기록에 저장하지 않는다.

상태에는 해당 후보의 영역 ID·좌표·제한된 줄 발췌·후보별 ID 순열만 들어간다. 활성 영역 밖의 제목·인접 셀·페이지 나머지·문서 이름·다른 페이지는 보내지 않는다. 표가 후보 영역이면 해당 표 하위 구조만 담는다. 기본 상한은 전체 발췌 1,600자, 줄별 160자, 하위 영역 64개, 줄 128개, UTF-8 상태 32,768바이트다. 잘린 발췌는 `truncated`로 표시한다. 영역·줄·바이트 예산을 넘거나 원문 좌표가 활성 상자를 벗어나면 호출을 보류한다. `DecisionConfig`에서 양의 정수 상한을 조절할 수 있다. 응답 본문도 65,536바이트로 제한한다.

## 적용 전 검증과 보류

입력의 정규 소유권 경로는 페이지 루트 → 표 → 행·셀 → 셀 영역 → 줄 → 원문 ID다. 중복·누락·순환·다른 페이지 소유권이 있는 레이아웃은 모델 호출 전에 `ValueError`로 거부한다. 개별 대안은 다음을 모두 만족해야 한다.

- 범위는 현재 페이지 또는 실제 도달 가능한 표 셀이다. 중첩 표의 셀도 독립 범위다.
- 기본 후보는 해당 범위의 연속 부분 구간이다. 모든 후보는 같은 중복 없는 영역 ID 집합의 순열이다.
- 후보 영역은 활성 상자 안에 있고, 활성 상자는 선언된 범위 안에 있다.
- 같은 범위의 대안은 겹치지 않고 대안 ID도 중복되지 않는다.

응답은 질문 키와 정확히 일치하는 유한한 0~1 점수만 허용한다. 불리언·문자열·누락·추가 키·중복 JSON 키·NaN·무한대·과도한 본문은 거부한다. 확률의 합이 1일 필요는 없다. 최고 점수가 `min_score`(기본 0.55) 이상이고 차순위와의 차이가 `min_margin`(기본 0.10)보다 커야 선택한다. 정확한 동점은 여유를 0으로 설정해도 보류한다. 이 임곗값은 경험적으로 보정된 정확도 보장이 아니다.

선택 시 해당 범위의 부분 순서만 교체하고 다시 원문 ID 보존을 검사한다. 중첩 셀의 선택은 부모 셀·형제 셀·표 좌표·페이지 루트 순서를 바꾸지 않는다. 무효 대안이나 판정 실패 시 입력 기하 결과가 남으며 다른 유효한 대안은 독립적으로 처리한다.

`DecisionRecord`는 대안·범위 ID, 후보 이름, 기본·선택 인덱스, 검증된 점수, 제공자 클래스·모델 배포명, 보낸 상태의 SHA-256, 임곗값, 결과·사유를 기록한다. 원문 발췌나 비밀 설정은 기록하지 않는다. 후보별 실제 ID 순열은 `result.layout.alternatives`에 유지된다.

| outcome | 의미 | 대표 reason |
|---|---|---|
| `disabled` | 사용자가 켜지 않음 | `disabled` |
| `deferred` | 기하 순서 유지 | `invalid_scope`, `no_distinct_candidates`, `payload_budget_or_bounds`, `not_configured`, `provider_failure`, `invalid_answer`, `ambiguous_scores` |
| `kept_default` | 명확한 점수로 기본 후보 선택 | `scored_default` |
| `applied` | 검증된 대안 순열 적용 | `scored_alternative` |

## 검증과 한계

`tests/test_decision.py`는 합성 PDF·가짜 제공자·가짜 HTTP 응답만 사용한다. 루트·중첩 셀·서로 다른 국소 범위, 기본 모드·무후보 호출 0회, 원문 보존, 잘못된 범위·응답·점수·타임아웃·429·503과 발췌 상한을 검사한다. 실제 endpoint 검증은 별도 명시적 명령으로 실행한다.

```sh
python tools/decision_live_probe.py --env-file /authorized/path/.env --run-live --report /tmp/decision-outcomes.json
```

이 검증 도구는 직접 만든 비민감 PDF만 사용하고 키·URL·응답·원문 발췌 대신 집계 결과만 출력한다. 선택된 `.env`의 세 설정만 메모리에서 읽고 복사하거나 저장하지 않는다. 4·8행 측정 목록, 짧은 독립 본문 단, 중첩 표 셀, 무후보 대조군의 정답과 후보 생성을 호출 전에 확인한다. 실제 결과와 제한은 [검증 기록](decision-validation.md)에 있다.

후보 탐지는 기능 2의 범위다. 기존 후보가 없거나 올바른 순열이 후보에 없으면 Decision이 복구할 수 없다. 잘린 발췌와 문맥 생략으로 잘못 선택할 수 있다. 외부 제공자는 활성 발췌를 수신하므로 사용자가 전송을 허용한 문서에만 켜야 한다. 문서 전역 의미, OCR, 표 셀 재귀속, 보험 규칙, CLI는 이 기능에서 추가하지 않는다.
