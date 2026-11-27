# Python API와 CLI

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
- 글자와 벡터의 `stroking_color`, `non_stroking_color`는 숫자 배열, 패턴 객체 또는 `null`입니다. 패턴 객체의 `name`은 PDF 리소스 이름이며, `base_components`는 파서가 제공한 기본 색상 성분 배열 또는 `null`입니다.
- `pages[].layout.reading_order`는 페이지 최상위 영역 ID 순서입니다. 일반 영역은 `line_ids`를, 표 영역은 `table_id`를 참조합니다. 표는 `rows[].cells[]`의 행·열·병합 범위와 각 셀의 `region_ids`를 보존합니다. 셀에서 같은 순회 규칙을 반복하면 중첩 표도 읽을 수 있습니다. 각 줄의 `fragment_ids`는 원문 문자 조각을 참조합니다. `page.ordered_fragments()`로 이 순서의 원문 조각을 얻습니다.
- `pages[].text`는 읽기 순서의 표시용 원문입니다. 줄마다 줄바꿈을 넣고, 수평 글자가 3포인트 넘게 떨어진 곳에 원문 공백이 없으면 공백을 보탭니다. `page.region_text(region_id)`와 `page.cell_text(cell_id)`는 이 규칙을 해당 영역과 셀에 적용합니다. 잘못된 ID는 `KeyError`입니다. 빈 페이지·빈 셀의 텍스트는 빈 문자열이며 `document.text`는 페이지를 `\n\f\n`으로 연결합니다.

## Decision

Decision을 끈 기본 실행은 키나 네트워크 없이 동작합니다. `decision_enabled`는 `false`, `pages[].decisions`는 빈 배열입니다. 기하 규칙에서 찾은 순서 대안은 `layout.alternatives`에 남습니다.

```sh
pdfgyeol input.pdf --decision --output result-with-decisions.json
```

CLI는 `--decision`을 켜고 채점이 필요한 경우 `AZURE_OPENAI_ENDPOINT`의 전체 채점 URL, `AZURE_OPENAI_API_KEY`, 선택 사항인 `DECISION_MODEL_DEPLOYMENT`를 읽습니다.

Python에서는 `pdfgyeol.host`와 해당 제공자의 설정을 지정하면 `parse_pdf()`에서 Decision이 켜집니다. `pdfgyeol.host = None`으로 모듈의 선택을 해제합니다. `decision_config=pdfgyeol.DecisionConfig(enabled=False)`를 넘기면 해당 호출에서 Decision을 끕니다. 호출마다 독립된 설정이 필요하면 `DecisionClient.from_host()`로 만든 `provider`와 `DecisionConfig(enabled=True)`를 함께 넘깁니다. 모듈과 호출 양쪽에서 제공자를 선택하지 않은 상태에서 `DecisionConfig(enabled=True)`를 넘기면 기존 Azure 환경 변수를 사용합니다. [제공자별 예제와 설정](decision.md#명시적-설정과-전송-범위)

Decision을 켜면 유효한 대안 영역의 제한된 발췌가 제공자에게 전송될 수 있습니다. 보류 시 기하 순서가 유지되며 선택·보류 기록은 `pages[].decisions`에 들어갑니다. CLI의 환경 설정 누락, 호출 실패, 응답 무효는 stderr에 표시되며 로컬 결과 내보내기는 성공합니다. [요청 한도와 전송 범위](decision.md#전송-범위와-요청-형식) · [선택 기준](decision.md#적용-전-검증과-보류)

출력 품질은 [레이아웃 규칙의 한계](layout.md#검증과-한계)를 따릅니다. 이미지 전용 페이지는 이미지 위치와 함께 표시되며 OCR 텍스트는 생성되지 않습니다. 문서 순서와 표의 정확도는 원본의 배치에 따라 달라집니다.

## 보험 약관

`parse_pdf("input.pdf", insurance=True)` 또는 `pdfgyeol input.pdf --insurance`로 조·특약·별표의 소속을 표시합니다. 이 옵션은 최종 읽기 순서에서 로컬로 동작하며 최상위 `insurance` 필드를 추가합니다. [보험 옵션과 JSON 참조 형식](insurance.md)

## 검증

테스트 환경에서 `python -m pytest -q`를 실행합니다. 배포본 검사는 `.[test]`와 `build`를 설치한 개발 환경에서 다음과 같이 실행할 수 있습니다. 검사용 인터프리터에 맞는 wheelhouse를 미리 준비한 뒤 인덱스 접근 없이 각 배포본을 새 가상 환경에 설치합니다.

```sh
python -m pip download --only-binary=:all: -d wheelhouse 'setuptools>=77' 'setuptools-rust>=1.11' 'pdfplumber>=0.11.11,<0.12'
PIP_NO_INDEX=1 PIP_FIND_LINKS="$PWD/wheelhouse" python -m build
python tools/smoke_distribution.py dist/pdfgyeol-0.1.0-py3-none-any.whl dist/pdfgyeol-0.1.0.tar.gz wheelhouse
```

배포본의 Python 예시, 콘솔 명령, 모듈 명령, UTF-8 출력, 파일 출력이 같은 합성 PDF를 읽는지 확인합니다. 실행 검사에서 Decision 자격 증명은 비우고 소켓 사용을 차단합니다.

선택 가속 모듈을 포함하는 배포본은 Rust와 Cargo가 있는 CPython 환경에서 `PDFGYEOL_BUILD_RUST=1 python -m build --wheel`로 빌드합니다. wheel 태그의 `cp311-abi3`는 일반 CPython 3.11 이상에서 플랫폼 태그가 일치할 때 설치할 수 있음을 뜻합니다. wheel을 설치한 환경에서 `python -c "import pdfgyeol._native"`로 가속 모듈을 불러올 수 있는지 확인합니다.

패키지 인덱스에서 받은 소스로 다시 빌드할 때 pip가 이전 Python wheel을 재사용할 수 있습니다. 소스 파일 경로를 직접 지정해 설치하거나 캐시를 건너뛰려면 `--no-cache-dir` 옵션을 사용합니다. [pip의 wheel 캐시](https://pip.pypa.io/en/stable/topics/caching/#avoiding-caching)

CRS의 재정정책 보고서 `IF11253` 2025-02-27판에서 3쪽의 원문 조각 11,678개를 각각 한 번씩 읽었습니다. 기본 API와 CLI의 JSON은 바이트 단위로 일치했습니다. 1쪽에서 가로 간격 12.88pt와 13.77pt인 두 본문 줄에 양쪽 단의 글자가 섞였던 문제를 수정했습니다. 1쪽의 좌우 본문 기준 문구 13개에서 원문 좌표와 순번을 확인했고, 왼쪽 본문 전체가 오른쪽 본문 앞에 나오며 같은 줄에 두 단의 본문이 섞이지 않았습니다. 2쪽의 상단 오른쪽 머리글은 왼쪽 단 본문 뒤에 나옵니다. 현재 이 파일의 국소 순서 대안은 0개입니다. [CRS 원본 PDF](https://www.congress.gov/crs_external_products/IF/PDF/IF11253/IF11253.10.pdf)

수정 전에는 1·2쪽에서 국소 순서 대안이 한 개씩 생성됐습니다. 같은 글자·좌표를 가진 1쪽 사본을 대상으로 시행한 이전 Decision 호출 한 번은 상태 2,845바이트에 발췌 205자를 담았고, 유효한 점수 두 개를 받아 `ambiguous_scores`로 보류했습니다. 수정 결과는 기본 기하 처리로 검증했습니다. Decision은 선택 기능이며 점수 보정 근거는 여전히 부족합니다. [Decision 검증 기록](decision-validation.md)
