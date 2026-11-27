# 기능 4. Python API와 CLI

저장소 루트에서 설치한 뒤 `input.pdf`를 읽습니다. Python 3.11 이상이 필요합니다. 현재 `pdfplumber` 의존성이 요구하는 Pillow 13의 최소 버전입니다. [Pillow 13 패키지 정보](https://pypi.org/project/pillow/13.0.0/)

```sh
python -m pip install .
pdfgyeol input.pdf
pdfgyeol input.pdf --output result.json
```

`python -m pdfgyeol input.pdf`도 같은 명령입니다. 출력은 UTF-8 JSON이며 마지막에 줄바꿈이 있습니다. `--output`을 생략하면 stdout으로 내보냅니다. 파일로 저장할 때는 이미 존재하는 디렉터리 안에 새 경로를 지정하세요. 하드링크를 지원하는 파일시스템에서 완전히 쓴 임시 파일을 목적지에 원자적으로 연결합니다. 입력 파일과 입력을 가리키는 링크, 기존 출력 경로는 보존합니다.

성공 시 종료 코드는 `0`입니다. 잘못된 인수는 `2`, 읽기·파싱·쓰기 실패는 `1`을 반환하며 stderr에 오류를 표시합니다. 파싱이나 직렬화가 실패하면 JSON 출력이 생성되지 않습니다. 암호가 필요한 PDF는 아래 Python API에서 `password`를 전달해 읽습니다.

```python
from pdfgyeol import parse_pdf

document = parse_pdf("input.pdf")
print(document.pages[0].text)
assert document.to_dict()["schema_version"] == "pdfgyeol.parse/1"
```

`parse_pdf(path, password=..., layout_config=...)`는 [`extract_pdf`](extraction.md)와 [`infer_page`](layout.md)를 페이지별로 연결합니다. `ParsedDocument.to_json()`은 CLI와 동일한 JSON 문자열을 반환하고, `to_dict()`는 JSON과 동일한 값의 사전·리스트 트리를 반환합니다. 입력 파일이 없거나 파싱에 실패하면 예외가 호출자에게 전달됩니다.

## 구조와 읽기 순서

- 최상위 `schema_version`은 `pdfgyeol.parse/1`입니다. `id`, `sha256`, `source_schema_version`, `extractor_version`은 원문과 추출 버전을 식별합니다. `pages`는 파일의 페이지 순서입니다.
- `pages[].source`는 페이지 ID·번호·크기·회전·종류·`source_bbox`와 원문 `fragments`, `vectors`, `images`를 담습니다. `source_bbox`는 pdfplumber의 원래 페이지 상자입니다. 글자·영역·표·셀의 `bbox`는 회전된 MediaBox 좌상단에서 시작하는 PDF 포인트 좌표이며 키는 `x0`, `top`, `x1`, `bottom`입니다. 글자의 `text`와 원래 순번은 그대로 보존됩니다.
- `pages[].layout.reading_order`는 페이지 최상위 영역 ID 순서입니다. 일반 영역은 `line_ids`를, 표 영역은 `table_id`를 참조합니다. 표는 `rows[].cells[]`의 행·열·병합 범위와 각 셀의 `region_ids`를 보존합니다. 셀에서 같은 순회 규칙을 반복하면 중첩 표도 읽을 수 있습니다. 각 줄의 `fragment_ids`는 원문 문자 조각을 참조합니다. `page.ordered_fragments()`로 이 순서의 원문 조각을 얻습니다.
- `pages[].text`는 읽기 순서의 표시용 원문입니다. 줄마다 줄바꿈을 넣고, 수평 글자가 3포인트 넘게 떨어진 곳에 원문 공백이 없으면 공백을 보탭니다. `page.region_text(region_id)`와 `page.cell_text(cell_id)`는 이 규칙을 해당 영역과 셀에 적용합니다. 잘못된 ID는 `KeyError`입니다. 빈 페이지·빈 셀의 텍스트는 빈 문자열이며 `document.text`는 페이지를 `\n\f\n`으로 연결합니다.

## Decision

기본 API와 CLI는 키나 네트워크 없이 동작합니다. `decision_enabled`는 `false`, `pages[].decisions`는 빈 배열입니다. 기하 규칙에서 찾은 순서 대안은 `layout.alternatives`에 남습니다.

```sh
pdfgyeol input.pdf --decision --output result-with-decisions.json
```

Python에서는 `parse_pdf("input.pdf", decision_config=DecisionConfig(enabled=True))`로 켭니다. `DecisionConfig`는 `pdfgyeol`에서 가져옵니다. 필요하면 `provider`에 구현을 주입할 수 있으며 활성화 설정은 동일합니다. Decision을 켜면 유효한 대안의 제한된 발췌가 제공자에게 전송될 수 있습니다. 자격 증명 설정, 요청 한도와 선택 기준은 [Decision 문서](decision.md)에 있습니다. 보류 시 기하 순서가 유지되며 선택·보류 기록은 `pages[].decisions`에 들어갑니다. 설정 누락, 호출 실패, 응답 무효는 CLI stderr에도 표시되며 로컬 결과 내보내기는 성공합니다.

출력 품질은 [레이아웃 규칙의 한계](layout.md#검증과-한계)를 따릅니다. 이미지 전용 페이지는 이미지 위치와 함께 표시되며 OCR 텍스트는 생성되지 않습니다. 문서 순서와 표의 정확도는 원본의 배치에 따라 달라집니다.

## 검증

테스트 환경에서 `python -m pytest -q`를 실행합니다. 배포본 검사는 `.[test]`와 `build`를 설치한 개발 환경에서 다음과 같이 실행할 수 있습니다. 검사용 인터프리터에 맞는 wheelhouse를 미리 준비한 뒤 인덱스 접근 없이 각 배포본을 새 가상 환경에 설치합니다.

```sh
python -m pip download --only-binary=:all: -d wheelhouse 'setuptools>=77' 'pdfplumber>=0.11.11,<0.12'
PIP_NO_INDEX=1 PIP_FIND_LINKS="$PWD/wheelhouse" python -m build
python tools/smoke_distribution.py dist/pdfgyeol-0.1.0-py3-none-any.whl dist/pdfgyeol-0.1.0.tar.gz wheelhouse
```

배포본의 Python 예시, 콘솔 명령, 모듈 명령, UTF-8 출력, 파일 출력이 같은 합성 PDF를 읽는지 확인합니다. 실행 검사에서 Decision 자격 증명은 비우고 소켓 사용을 차단합니다.

CRS의 재정정책 보고서 `IF11253` 2025-02-27판에서 3쪽의 원문 조각 11,678개를 읽었습니다. 1·2쪽에서 국소 순서 대안이 생성됐습니다. 원본과 같은 글자·좌표를 가진 1쪽 사본에 한해서 Decision을 켰습니다. 실제 요청은 한 번이며 상태 2,845바이트에 발췌 205자가 들어갔습니다. 유효한 점수 두 개를 받은 뒤 `ambiguous_scores`로 보류했습니다. 원문 조각과 활성 범위 밖의 순서가 보존됐습니다. 1쪽의 일부 본문 줄은 기본 기하 결과에서 단 사이에 섞였고 이 호출로 개선되지 않았습니다. [CRS 원본 PDF](https://www.congress.gov/crs_external_products/IF/PDF/IF11253/IF11253.10.pdf)
