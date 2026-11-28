# PDF 원문 추출

`extract_pdf`는 원문 글자·벡터·이미지의 위치를 읽는다. 읽기 순서와 표 셀은 [레이아웃 추론](layout.md)에서, 모호한 순서 후보는 선택 기능인 [Decision](decision.md)에서 처리한다. 패키지 버전 `0.1.0`은 저장소에서 빌드하는 개발 버전이며 공개 배포를 뜻하지 않는다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

```python
from pdfgyeol import extract_pdf

document = extract_pdf("notes.pdf")
for page in document.pages:
    print(page.page_number, page.kind.value, page.width, page.height)
    for fragment in page.fragments:
        print(fragment.id, fragment.text, fragment.bbox, fragment.style.font_name)
```

## 원문과 ID

- 반환값은 타입이 명시된 불변 dataclass `Document`, `Page`, `TextFragment`, `TextStyle`, `VectorEvidence`, `ImageEvidence`다. 컬렉션은 tuple이며 외부 PDF 객체나 열린 파일에 의존하지 않는다. `py.typed`를 포함한다.
- 문서 ID는 `pdfgyeol:v1:<파일 SHA-256>`, 페이지 ID는 그 뒤 `:p000001` 형식이다. 객체 ID는 페이지 ID 뒤 종류와 0부터 시작하는 원문 인덱스다. `source_index`는 글리프·이미지·각 벡터 종류별 pdfplumber 목록 인덱스다. `page_number`는 1부터 시작한다. 경로를 바꿔도 같은 바이트는 같은 ID를 얻는다. 바이트가 달라지면 모든 ID가 달라진다.
- 같은 파일과 같은 파서 버전에서 ID와 순서를 재현한다. 파서 업그레이드 간 글리프 분해와 순서 호환은 보장하지 않으며 `extractor_version`에 pdfplumber와 pdfminer.six 버전을 기록한다. 장기 재현에는 환경 의존성 버전도 고정해야 한다.
- pdfplumber 0.11.11과 pdfminer.six 20260107 조합에서는 일반 글리프에서 원문 모델에 필요한 속성만 읽는다. 바이트 인코딩 글꼴 이름, 벡터, 이미지, 다른 라이브러리 버전 조합은 pdfplumber의 기본 객체 변환을 사용한다.
- 같은 버전 조합에 Rust 확장을 설치하면 PDF 내용 스트림에서 숫자·문자열·연산자를 읽는 단계와 수평 텍스트의 글리프 위치·경계 계산을 Rust가 맡는다. 여러 스트림을 이어 읽거나 인라인 이미지가 있거나 크기·중첩 한도에 걸리면 해당 스트림 전체를 pdfminer로 읽는다. 설치 방법은 [API·CLI 안내](api-cli.md)에 있다.
- 단일 스트림의 초기 피연산자 스택이 비어 있고 모든 연산자와 인자 수가 감사된 핸들러와 일치하면, Rust가 스트림 전체를 준비한 뒤 Python 핸들러를 원래 순서로 호출한다. 최상위 숫자는 정수와 실수를 구분해 보관하고, 각 핸들러를 호출할 때 Python 값으로 변환한다. 미지 연산자, 인자 수 불일치, 남은 피연산자가 있으면 기존 스택 처리로 돌아간다. strict 모드와 파서 디버그 로깅은 pdfminer에서 실행한다.
- 수평 CID·TrueType·Type 1 글꼴의 문자 코드와 너비는 pdfminer가 해석한다. Rust는 텍스트 표시 연산 하나를 받아 글자·단어 간격, `TJ` 오프셋, 변환 행렬, 글리프 상자를 계산한다. 연산 결과가 모두 검증된 뒤 Python 장치에 글리프를 추가한다. 세로 쓰기, 그 밖의 글꼴, 지원 범위를 벗어난 수치·입력 크기, strict 모드와 글꼴 디버그 로깅은 기존 텍스트 처리기를 사용한다. 내용 스트림 읽기와 텍스트 표시 연산은 각각 적합성을 판단한다.
- 수평 `PDFCIDFont`의 정확한 `IdentityCMap`에서 각 문자열 토큰이 2바이트이고 같은 페이지의 글꼴 객체 캐시에 필요한 글리프가 모두 있으면, Rust가 CID를 읽고 해당 글리프를 바로 배치한다. 캐시 미적중이나 그 밖의 토큰 길이에서는 해당 표시 연산 전체를 기존 준비 경로에 넘긴다. 페이지별 캐시의 유니코드와 너비는 처음에 pdfminer에서 얻는다.
- 같은 확장은 단일 직선 경로(`m l` 또는 `m l h`)를 칠할 때 양 끝점의 CTM 적용과 경계 계산을 한 번에 처리한다. Python은 현재 그래픽 상태와 태그를 사용해 선을 추가하고 추출에 필요한 필드를 읽는다. 복합 경로, 다른 도형, 범위를 벗어난 좌표 및 strict 모드의 페인팅은 연산 전체를 pdfminer에 위임한다.
- 감사 버전의 네이티브 페이지는 pdfminer가 만든 글리프를 원문 순서대로 최대 1,024개씩 묶는다. Rust가 좌표와 스타일을 투영해 기존 `TextFragment`를 만들므로 중간 글리프 사전이 필요 없다. 바이트 글꼴 이름 등 배치가 처리할 수 없는 글리프가 있거나 Unicode 정규화·레이아웃 옵션을 사용하면 페이지 전체를 기존 경로로 조립한다.
- 같은 페이지에서 네이티브·기존 페인팅이 만든 `LTLine`을 최대 1,024개씩 묶어 `VectorEvidence`로 조립한다. 직선 배치가 지원 범위를 벗어나면 그 페이지의 모든 직선을 기존 경로로 조립한다. 사각형과 곡선은 기존 투영을 사용하며 출력은 직선, 사각형, 곡선 순이다.
- `TextFragment` 하나는 `page.chars` 항목 하나다. 한 항목의 `text`가 여러 코드포인트이거나 공백이어도 그대로 보존한다. 정규화·중복 제거·공백 제거·단어 추출·위치 정렬을 하지 않는다. 겹쳐 그린 동일 글자도 별개 원문 ID를 갖는다.
- `fragments` 순서는 pdfplumber의 원문 순서이며 사람이 읽는 순서를 뜻하지 않는다. 공백·개행을 추정해 삽입하지 않는다. 글리프 사이 간격과 줄 구조는 좌표로 분석해야 한다. 원문 누락·중복 보장은 pdfplumber가 노출한 항목과의 일대일 대응이며 PDF에 보이는 모든 문자의 정확한 해독을 보장하지 않는다.

## 좌표와 스타일

- `BBox(x0, top, x1, bottom)`와 벡터의 `points`·`path`는 페이지 **회전을 적용한 MediaBox 왼쪽 위를 (0, 0)**으로 삼는다. x는 오른쪽, y는 아래쪽으로 증가한다. 단위는 PDF point(통상 1/72 inch), 반올림하지 않는다. `width`, `height`도 같은 공간이다. 표준 `/UserUnit=1`을 전제로 하며 비표준 UserUnit의 실제 인치 크기는 정규화하지 않는다.
- CropBox로 자르지 않는다. MediaBox 밖의 원문도 버리지 않아 음수나 페이지 크기를 넘는 좌표가 가능하다. `source_bbox`는 이동 전 pdfplumber 페이지 상자이며 `rotation`은 원문의 회전 각도다. 회전 0/90/180/270도와 0이 아닌 MediaBox 원점을 합성 PDF로 검사한다.
- 글꼴 이름·크기·upright·stroke/fill 색상과 advance를 보존한다. 숫자 색상은 원문의 성분 tuple로 보존한다. 패턴 색상은 PDF 리소스 이름과 파서가 제공한 경우에 한해 기본 색상 성분을 `PatternColor(name, base_components)`에 담는다. 패턴과 그라데이션의 픽셀 색상 계산은 렌더러의 역할이다. 폰트 크기는 pdfplumber의 `size`로 회전/변환 시 원래 `Tf` 값과 다를 수 있다. 글자 상자는 글리프의 실제 잉크 경계가 아니라 파서가 계산한 상자다.
- `source_matrix`만 예외다. pdfminer가 제공한 원본 CTM이며 위의 top-left 좌표로 이동/반전하지 않는다. 글리프 방향 등 원문 변환의 증거로 사용하고 `bbox`와 직접 혼합하지 않는다.

## 표·이미지와 페이지 상태

- 벡터는 `line`, `rect`, `curve` 원문 객체를 한 번씩 보존한다. 벡터 목록은 종류별 line → rect → curve 순서이며 PDF 페인트 순서가 아니다. 선 두께, stroke/fill, even-odd, 색상, dash, 꼭짓점과 Bézier 제어점을 포함한 path를 제공한다. `page.edges`나 추정 표를 함께 넣지 않아 사각형의 변을 다시 세지 않는다. 표의 행·셀과 텍스트 귀속은 [레이아웃 추론](layout.md)에서 처리한다.
- 이미지는 배치 상자, 원본 픽셀 크기, bits, mask 여부만 보존한다. 이미지 바이트를 복사하거나 OCR하지 않는다.
- `text`: 원문 글리프가 하나 이상. 이미지·벡터도 함께 있을 수 있다. 공백 글리프나 숨은 OCR 텍스트도 글리프로 센다.
- `image_only`: 글리프 없이 이미지가 하나 이상. 벡터 장식이 함께 있을 수 있다. OCR 필요 여부를 판정한 결과는 아니다.
- `vector_only`: 글리프와 이미지 없이 벡터가 존재. 윤곽선으로 변환된 문자도 여기에 속할 수 있다.
- `blank`: 위 세 종류의 원문 객체가 모두 없음. 렌더링한 화면의 시각적 공백을 판정하지 않는다. 주석·폼 위젯·클리핑·투명도·가려짐·텍스트 렌더링 모드의 표시 상태를 해석하지 않으므로 해당 내용만 있는 페이지까지 시각적으로 빈 페이지라고 보장하지 않는다.
- 잘못된 파일·비밀번호·읽기 오류는 예외로 전파하며 빈 페이지로 대체하지 않는다. `password=`는 pdfplumber로 전달한다. 네트워크·모델 호출은 없다.

## 검증 범위

`tests/test_extraction.py`는 직접 만든 정원 노트와 실험실 캡션을 사용해 PDF를 생성한다. 보험 문서나 외부 PDF를 저장소에 포함하지 않는다. 반복 읽기·경로 독립 ID·글리프 일대일 대응·오버프린트·두 단의 원문 순서·전폭 제목·표 선·곡선·빈/이미지/벡터 페이지·회전과 좌표를 검사한다.

외부 문서는 경로만 전달해 제자리에서 검사한다. 스모크 도구는 두 번의 전체 추출 일치, 원문 글리프 순서와 개수, 벡터·이미지 개수, ID 유일성을 확인하고 통계만 출력한다. 이 검사는 파서 대응 확인이며 읽기 순서·표 셀 정확도 평가는 아니다.

```sh
.venv/bin/python tools/smoke_extraction.py notes.pdf
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

메모리에는 반환할 전체 원문 증거가 남는다. 페이지 처리 후 pdfplumber 캐시는 해제하지만 대용량 문서의 메모리 상한이나 처리량은 아직 보장하지 않는다. 스캔 OCR, 잘못된 ToUnicode/CMap 보정, 원문에 없는 공백·개행 생성은 지원하지 않는다.
