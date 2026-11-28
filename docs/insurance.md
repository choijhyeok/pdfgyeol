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

보험 옵션은 읽기 순서가 확정된 뒤 로컬에서 실행됩니다. 추가 설치나 네트워크, 자격 증명이 필요하지 않습니다. Decision은 별도 옵션이며 켜면 채점 결과를 반영한 읽기 순서를 사용합니다.

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
- `heading`과 `spans`는 기존 페이지, 영역, 그 영역에 속한 연속 줄을 가리킵니다. 표는 셀 내부 영역을 참조합니다. 조 제목과 본문 첫 문장이 한 줄에 있으면 `heading`이 그 줄 전체를 가리킵니다. 뒤따르는 본문 줄은 `spans`에 나타납니다. 각 줄의 `fragment_ids`로 원문 글자와 좌표를 찾을 수 있습니다.
- `spans`는 절에 직접 속한 본문을 읽기 순서대로 담습니다. 연속되는 본문은 다음 페이지까지 참조할 수 있습니다. 하위 보장의 조도 소속 특약의 실제 제목 줄 ID를 `rider_heading_line_id`에서 참조합니다. 특약에 직접 속한 본문이 없으면 해당 특약의 `spans`는 빈 배열입니다.

## 인식 범위

보험 약관의 단서가 있으면 문서 전체에서 반복되는 글꼴·크기·괄호 형식을 살피고, 줄 간격과 들여쓰기로 조 제목과 인용을 구분합니다. `(...)`, `[...]`, `【...】` 같은 괄호형 조 제목과 본문이 같은 줄에서 시작하는 형식도 처리합니다. 새 특약, 편, 별표나 부록은 조의 범위를 끝낼 수 있습니다. 목차, 반복되는 쪽 표시, 범위를 확인할 수 있는 예시와 법령 인용은 소속 표시에서 건너뜁니다.

보험 구조는 규칙으로 추정합니다. 여러 줄로 나뉜 제목, 괄호 없는 조 제목, 경계가 모호한 예시, 문서 중간부터 잘라낸 파일에서는 참조가 누락될 수 있습니다. 빈 페이지와 이미지 전용 페이지는 연속 소속을 끊습니다. 법적 범위나 보장 여부는 약관 원문을 확인하세요.

## 검증

실제 약관의 고정 표본과 합성 PDF에서 조 경계, 다음 쪽으로 이어지는 본문, 하위 보장의 특약 참조, 목차·예시·법령 인용 처리를 확인했습니다. 정확도 검증은 선택한 페이지와 사례를 대상으로 했습니다.
