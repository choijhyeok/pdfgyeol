# 선택 기능 Decision

```python
from pdfgyeol import DecisionConfig, extract_pdf, infer_page, resolve_page

page = extract_pdf("notes.pdf").pages[0]
local = infer_page(page)

# 기본값은 로컬 기하 순서를 유지한다.
disabled = resolve_page(page, local)
assert disabled.layout == local

# 명시적으로 켜면 기존 Azure 환경 설정으로 국소 후보를 비교한다.
result = resolve_page(page, local, config=DecisionConfig(enabled=True))
for decision in result.decisions:
    print(decision.scope_id, decision.outcome, decision.reason)
ordered_source_ids = result.layout.ordered_fragment_ids()
```

`resolve_page`는 기하 규칙이 만든 레이아웃을 받아 새 `DecisionResult(layout, decisions)`를 반환한다. `layout`을 생략하면 내부에서 `infer_page(page)`를 실행한다. 입력 `Page`·`PageLayout`은 불변이며 모델 응답은 순서 후보를 고르는 데만 사용한다. `infer_page`와 `iter_layout`은 로컬에서 동작한다. `resolve_page`에서 채점하려면 `config=DecisionConfig(enabled=True)`를 넘긴다. `provider`를 생략하면 기존 Azure 환경 설정을 쓴다.

## 명시적 설정과 전송 범위

### Python 모듈 설정

`pdfgyeol.host`를 설정하면 `parse_pdf()`를 호출할 때 해당 제공자로 Decision을 켠다. 호출 시작 시점에 `host`, `endpoint`, `apikey`, `model`을 읽는다. `endpoint`를 지정할 때는 API 경로가 포함된 전체 채점 URL을 사용한다. `model`을 생략하면 제공자 기본값을 쓰고, `ms`에 지정할 때는 실제 배포명을 사용한다. 제공자를 바꿀 때는 `endpoint`, `apikey`, `model`도 새 제공자에 맞게 설정한다. 선택한 제공자의 기본 URL과 모델을 쓰려면 각각 `None`으로 둔다.

| `host` | 전체 채점 URL | `apikey` | 기본 `model` | 제공자 문서 |
|---|---|---|---|---|
| `jev` | 기본 `https://api.typesafe.ai/v1/systemone` | TypeSafe 키 → `Authorization: Bearer` | `jev-latest` | [TypeSafe 빠른 시작](https://docs.typesafe.ai/introduction/quickstart) |
| `openai` | 기본 `https://api.openai.com/v1/decisions` | OpenAI 키 → `Authorization: Bearer` | `gpt-6-luna` | [OpenAI Decisions API](https://developers.openai.com/api/reference/resources/decisions/methods/create) |
| `ms` | 직접 지정. Foundry에서 쓰는 경로는 `/providers/microsoft/v1/systemone` | Azure 리소스 키 → `api-key` | `Decision-1.1` | [Microsoft Foundry 배포 및 호출](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision) |
| `custom` 또는 `local` | 직접 지정. 경로 끝이 `/decisions`이면 OpenAI Decisions, 그 외에는 System One 형식 | 선택 사항 → `Authorization: Bearer` | System One은 `jev-latest`, Decisions는 `gpt-6-luna` | [Ollama System One](https://ollama.com/blog/ollama-now-supports-jev-style-decision-models) |

Jev의 기본 URL과 모델로 채점하는 예제다. 환경 변수에서 키를 읽어 `apikey`에 명시적으로 넘긴다.

```python
import os
import pdfgyeol

pdfgyeol.host = "jev"
pdfgyeol.endpoint = None
pdfgyeol.apikey = os.environ["TYPESAFE_API_KEY"]
pdfgyeol.model = None
document = pdfgyeol.parse_pdf("notes.pdf")
```

Decision API를 지원하는 로컬 Ollama 서버를 실행한 뒤 `ollama pull nimble`로 모델을 내려받으면 키 없이 이 예제를 실행할 수 있다. `custom`과 `local`에서 평문 HTTP는 `localhost` 또는 루프백 IP에만 허용한다. 다른 주소에는 HTTPS를 사용한다. [Ollama의 Decision API 설정](https://ollama.com/blog/ollama-now-supports-jev-style-decision-models)

```python
import pdfgyeol

pdfgyeol.host = "local"
pdfgyeol.endpoint = "http://localhost:11434/v1/systemone"
pdfgyeol.apikey = None
pdfgyeol.model = "nimble"
document = pdfgyeol.parse_pdf("notes.pdf")
```

`pdfgyeol.host = None`으로 모듈의 제공자 선택을 해제한다. `decision_config=pdfgyeol.DecisionConfig(enabled=False)`를 넘기면 해당 호출에서 Decision을 끈다. 모듈 또는 `from_host()`에 잘못된 URL을 지정하거나 필수 키를 빠뜨리면 `ValueError`가 발생한다.

### 호출별 설정과 CLI

스레드나 요청마다 독립 설정이 필요하면 `DecisionClient.from_host()`로 제공자를 만든 다음, `provider`와 `DecisionConfig(enabled=True)`를 함께 넘긴다. 호출에 넘긴 제공자는 모듈에서 선택한 제공자보다 우선한다. 같은 방법으로 `resolve_page()`에도 명시적 제공자를 넘길 수 있다.

```python
import os
from pdfgyeol import DecisionClient, DecisionConfig, parse_pdf

provider = DecisionClient.from_host("openai", apikey=os.environ["OPENAI_API_KEY"])
document = parse_pdf(
    "notes.pdf",
    decision_config=DecisionConfig(enabled=True),
    provider=provider,
)
```

CLI의 `--decision`과 제공자를 지정하지 않은 기존 Python 활성화 방식은 채점이 필요한 시점에 `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `DECISION_MODEL_DEPLOYMENT`를 읽는다. 배포명 기본값은 `Decision-1.1`이다. 환경 설정이 없으면 `not_configured`로 보류한다. `DecisionClient(endpoint=..., api_key=..., model=...)`로 Azure 제공자를 직접 만들 수도 있다. `.env`는 자동으로 찾거나 읽지 않는다.

### 전송 범위와 요청 형식

서로 다른 유효 후보가 발견된 범위에만 채점 요청을 보낸다. System One 요청은 `model`, 문자열 `state`, 이름별 `questions`를 보낸다. 각 질문에 `type="noul"`과 `instructions`를 지정하며 응답의 `answers.candidate_N.noul` 수치를 사용한다. OpenAI Decisions 요청은 같은 상태를 `input`으로 보내고 `questions` 목록에 `type="predicate"`, `name`, `instructions`를 지정한다. 응답의 `answers` 목록에서 후보 이름별 `probability`를 사용한다.

리다이렉트는 거부한다. 대안별로 한 번 요청하고 타임아웃·429·5xx 등 실패 시 재시도 없이 기본 기하 순서를 유지한다. 키, `endpoint`, `model`은 클라이언트 `repr`에서 제외하고 예외 본문은 판정 기록에 저장하지 않는다.

상태에는 해당 후보의 영역 ID·좌표·제한된 줄 발췌·후보별 ID 순열만 들어간다. 활성 영역 밖의 제목·인접 셀·페이지 나머지·문서 이름·다른 페이지는 보내지 않는다. 표가 후보 영역이면 해당 표 하위 구조만 담는다. 기본 상한은 전체 발췌 1,600자, 줄별 160자, 하위 영역 64개, 줄 128개, UTF-8 상태 32,768바이트다. 잘린 발췌는 `truncated`로 표시한다. 영역·줄·바이트 예산을 넘거나 원문 좌표가 활성 상자를 벗어나면 호출을 보류한다. `DecisionConfig`에서 양의 정수 상한을 조절할 수 있다. 응답 본문도 65,536바이트로 제한한다.

## 적용 전 검증과 보류

입력의 정규 소유권 경로는 페이지 루트 → 표 → 행·셀 → 셀 영역 → 줄 → 원문 ID다. 중복·누락·순환·다른 페이지 소유권이 있는 레이아웃은 모델 호출 전에 `ValueError`로 거부한다. 개별 대안은 다음을 모두 만족해야 한다.

- 범위는 현재 페이지 또는 실제 도달 가능한 표 셀이다. 중첩 표의 셀도 독립 범위다.
- 기본 후보는 해당 범위의 연속 부분 구간이다. 모든 후보는 같은 중복 없는 영역 ID 집합의 순열이다.
- 후보 영역은 활성 상자 안에 있고, 활성 상자는 선언된 범위 안에 있다.
- 같은 범위의 대안은 겹치지 않고 대안 ID도 중복되지 않는다.

응답은 질문 키와 정확히 일치하는 유한한 0~1 점수만 허용한다. 불리언·문자열·누락·추가 키·중복 JSON 키·NaN·무한대·과도한 본문은 거부한다. 확률의 합이 1일 필요는 없다. 최고 점수가 `min_score`(기본 0.55) 이상이고 차순위와의 차이가 `min_margin`(기본 0.10)보다 커야 선택한다. 정확한 동점은 여유를 0으로 설정해도 보류한다. 이 임곗값은 경험적으로 보정된 정확도 보장이 아니다.

기본 0.55/0.10은 유지한다. 특정 배포의 점수 범위를 직접 검증하는 실험에서는 절대 하한만 낮출 수 있다. 다음 0.35는 신규 합성 평가에서 미리 고정한 비교값이며 권장값이나 보정된 확률이 아니다. 낮추면 잘못된 순열 적용이 늘 수 있으므로 전송을 허용한 자료에서 별도 검증해야 한다. 여유·기하 검증·발췌 상한·실패 시 보류 계약은 그대로 적용된다.

```python
# 사용자가 외부 전송과 실험적 임곗값을 명시적으로 선택한 경우
experimental = DecisionConfig(enabled=True, min_score=0.35, min_margin=0.10)
result = resolve_page(page, local, config=experimental)
```

선택 시 해당 범위의 부분 순서만 교체하고 다시 원문 ID 보존을 검사한다. 중첩 셀의 선택은 부모 셀·형제 셀·표 좌표·페이지 루트 순서를 바꾸지 않는다. 무효 대안이나 판정 실패 시 입력 기하 결과가 남으며 다른 유효한 대안은 독립적으로 처리한다.

`DecisionRecord`는 대안·범위 ID, 후보 이름, 기본·선택 인덱스, 검증된 점수, 제공자·모델 또는 배포명, 보낸 상태의 SHA-256, 임곗값, 결과·사유를 기록한다. 호스트를 선택한 제공자는 `jev`, `openai`, `ms`, `custom` 중 하나로 기록되며 `local`은 `custom`의 별칭이다. 기존 생성자와 직접 구현한 제공자는 클래스 이름으로 기록된다. 원문 발췌나 비밀 설정은 기록하지 않는다. 후보별 실제 ID 순열은 `result.layout.alternatives`에 유지된다.

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

별도 신규 평가의 예제·정답·분할·판정 기준은 [`decision_evaluation_protocol.json`](../tools/decision_evaluation_protocol.json)에 고정되어 있다. 행별 목록·독립 본문·저증거 각 4개를 개발 6개와 holdout 6개로 균등 분할한다. 저증거는 순서 추측 대신 보류를 정답으로 검사한다. 다음 첫 명령은 네트워크 없이 12개 후보·정답을 확인하고 평가 정의·도구·Decision·레이아웃 코드의 해시를 출력한다. 두 번째 명령은 해당 해시가 일치할 때만 실행하며 각 항목당 한 번, 최대 12번 요청한다. 두 정책은 메모리의 같은 응답을 재사용한다. 개발 판정은 holdout 호출 전에 확정하고, holdout은 한 번만 평가한다.

```sh
python tools/decision_evaluation.py
python tools/decision_evaluation.py --run-live --env-file /authorized/path/.env --frozen-sha256 HASH_FROM_PREFLIGHT --report /tmp/decision-evaluation.json
```

신규 도구 보고서는 항목 ID·클래스·점수 쌍·선택·사유와 클래스별 집계만 기록한다. 키·URL·설정 파일 경로·발췌·원시 응답·실행 세션은 저장하지 않는다. 직접 생성한 예제는 공개 정의로 재현하며 임시 PDF는 실행 후 제거한다. 이 소규모 합성 평가만으로 기본값 변경이나 실제 문서의 정확도를 주장하지 않는다.

후보는 [레이아웃 규칙](layout.md#국소-대안)이 찾는다. 기존 후보가 없거나 올바른 순열이 후보에 없으면 Decision이 복구할 수 없다. 잘린 발췌와 문맥 생략으로 잘못 선택할 수 있다. 외부 제공자는 활성 발췌를 수신하므로 사용자가 전송을 허용한 문서에만 켜야 한다.
