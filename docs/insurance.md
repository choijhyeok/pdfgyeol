# 보험 약관의 구조 표시

보험 약관에서 조·특약·별표 제목과 본문 줄의 관계를 찾으려면 이 옵션을 사용합니다.

```sh
pdfgyeol input.pdf --insurance --output annotated.json
```

```python
from pdfgyeol import parse_pdf

document = parse_pdf("input.pdf", insurance=True)
assert document.insurance is not None
for section in document.insurance.sections:
    print(section.kind, section.heading.line_ids)
```

보험 옵션은 오프라인에서 파싱이 끝난 뒤 실행됩니다. `--decision`과 함께 쓰면 Decision까지 반영한 읽기 순서를 사용합니다. 보험 옵션을 켜기 전후에 원문, 레이아웃, 읽기 순서, 표시 텍스트, Decision 요청과 기록은 동일합니다.

## JSON과 Python 타입

`pdfgyeol.parse/1`에 최상위 `insurance` 객체가 옵션으로 추가됩니다. 기본 JSON에는 이 키가 없고, Python에서는 `document.insurance`가 `None`입니다. 옵션을 켰는데 인식한 절이 없으면 `{"insurance": {"sections": []}}` 형태로 빈 배열을 내보냅니다.

```json
{
  "insurance": {
    "sections": [
      {
        "kind": "rider",
        "heading": {"page_id": "page-1", "region_id": "region-a", "line_ids": ["line-1"]},
        "rider_heading_line_id": null,
        "spans": []
      },
      {
        "kind": "article",
        "heading": {"page_id": "page-1", "region_id": "region-b", "line_ids": ["line-2"]},
        "rider_heading_line_id": "line-1",
        "spans": [
          {"page_id": "page-1", "region_id": "region-b", "line_ids": ["line-3"]},
          {"page_id": "page-2", "region_id": "region-c", "line_ids": ["line-4", "line-5"]}
        ]
      }
    ]
  }
}
```

- `InsuranceAnnotations.sections`는 제목의 읽기 순서대로 정렬된 `InsuranceSection`의 튜플입니다. `kind`는 `article`, `rider`, `appendix` 중 하나입니다.
- `heading`과 `spans`는 `InsuranceSpan`을 사용합니다. 각 참조는 기존 페이지, 그 페이지의 영역, 영역에 속한 연속된 줄을 가리킵니다. 표는 셀 내부 영역을 참조합니다. 제목은 한 줄이며 제목과 본문이 한 영역을 공유할 수 있습니다. 각 줄의 `fragment_ids`로 원문 글자와 좌표를 찾을 수 있습니다.
- `spans`는 절에 직접 속한 본문을 읽기 순서대로 담습니다. 연속되는 본문은 다음 페이지까지 참조할 수 있습니다. 특약 안에서 인식된 조의 `rider_heading_line_id`에는 특약 제목 줄 ID가 들어갑니다. 특약에 직접 속한 본문만 해당 특약의 `spans`에 들어가므로 조만 있는 특약은 빈 배열을 가질 수 있습니다.

## 인식 범위

보험금·보험계약·피보험자 등 문서에서 보험 약관의 단서를 찾은 경우에만 제목을 판별합니다. 한 줄의 괄호형 조 제목, `특별약관`·`특약`으로 끝나는 제목, 숫자가 붙은 `별표`를 줄 간격·글꼴·위치와 함께 확인합니다. 인식한 새 특약이나 별표에서 이전 소속이 끝납니다. 본문 안의 인용, 쪽수가 붙은 목차 항목, 사례·예시·관련법규 상자 안의 줄은 제목으로 등록하지 않습니다. 테두리가 확인되는 상자 뒤에는 기존 조의 본문이 이어질 수 있습니다.

보험 구조는 규칙으로 추정합니다. 제목이 쪼개지거나 본문과 같은 줄에 합쳐진 경우, 괄호 없는 조 제목, 테두리가 분명하지 않은 예시, 문서 중간부터 잘라낸 파일에서는 참조가 누락될 수 있습니다. 모호한 제목 뒤나 끝을 확인하지 못한 예시 뒤에는 소속 표시가 중단될 수 있습니다. 빈 페이지와 이미지 전용 페이지는 연속 소속을 끊습니다. 보험 용어와 비슷한 제목이 있는 다른 문서에서는 잘못 인식할 수 있습니다. 특약의 법적 범위, 보장 내용, 본문 인용의 참조 대상은 판단하지 않습니다.

## 검증

- 주택화재보험 약관에서 다음 페이지로 이어지는 조 본문과 특약이 시작될 때 소속이 갱신되는지 확인했습니다. 같은 영역에 있는 제목과 본문은 별도의 줄로 참조했고 목차에는 소속 표시가 없었습니다.
- 단체상해보험 약관에서 사례 상자를 건너뛴 뒤 앞 페이지의 조 본문이 이어지고 새 조가 시작되는지 확인했습니다.
- 운전자보험과 생명보험 약관에서는 별표 안의 표 줄이 해당 별표에 속하는지 확인했습니다.
- 어린이보험과 치아보험 약관에서 두 단의 순서와 예시·용어해설·관련법규 상자 처리, 조 간 전환을 확인했습니다. 일반 문서인 미국 의회조사국 재정정책 보고서의 3쪽에서는 인식한 절이 없었습니다.

이 검증은 선택한 페이지의 참조와 합성 사례를 대상으로 합니다. 문서 전체의 제목·본문 정확도를 측정한 수치는 없습니다.
