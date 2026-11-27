**여러 단으로 나뉜 PDF 본문을 읽는 순서대로 추출하고, 표의 각 셀을 원문 위치와 대조하려면 PDF결을 쓰세요.**

**한국어** · [English](README.en.md)

<p align="center">
  <img src="assets/otter.png" width="190" alt="물에 누워 두 단짜리 문서의 읽기 순서를 앞발로 따라가는 수달">
</p>

<h1 align="center">PDF결</h1>

PDF결(`pdfgyeol`)은 Python API와 CLI로 사용할 수 있습니다. 읽기 순서에 맞춘 텍스트와 글자 좌표·표 셀을 담은 UTF-8 JSON을 제공합니다. 설치 후 기본 파싱은 오프라인에서 실행됩니다.

## 주요 기능

- **읽기 순서**를 페이지의 제목과 단 배치에서 추론합니다. 텍스트와 그 근거가 된 원문 글자·좌표를 함께 제공합니다.
- **표 구조**를 행과 셀로 나눕니다. 병합 셀, 빈 셀, 중첩 표를 표현하고 각 셀에서 원문을 찾을 수 있습니다.
- **Decision**을 켜면 순서가 모호한 일부 영역에서 이미 찾은 후보를 비교합니다. [설정과 전송 범위](#선택-기능-decision)

## 파서 동작 방식

<a href="docs/parser-flow.png"><img src="docs/parser-flow.png" width="504" alt="PDF 글자와 좌표 추출 → 단·표 복원 → 읽기 순서 확정 → 텍스트·JSON 출력. 모호한 영역에는 선택적으로 Decision을 적용합니다."></a>

단계별 입력과 출력은 대화형 동작도에서 살펴볼 수 있습니다. HTML 파일을 내려받아 브라우저에서 여세요. [대화형 동작도](docs/parser-flow.html)

## 실제 PDF에서 비교한 결과

| 평가 항목 | PDF결 기본 | pdfplumber 기본 | PyMuPDF 기본 |
| --- | ---: | ---: | ---: |
| 보험 약관 6쪽에서 기준 문구를 올바른 순서로 읽은 쌍 | 90/90 | 84/90 | 86/90 |
| 비보험 문서 8쪽에서 기준 문구를 올바른 순서로 읽은 쌍 | 60/80 | 51/80 | 60/80 |
| 열차 시간표에서 역과 열차에 맞는 시간 | 0/12 | 12/12 | 12/12 |

보건 안내문·세무 양식·논문의 텍스트에서는 PDF결과 PyMuPDF가 60쌍을 모두 맞혔습니다. 그림 속 본문 20쌍은 세 파서 모두 읽지 못했습니다. PDF결은 약관 표의 번호·항목명 51쌍을 복원했습니다. 열차 시간표는 선분 수가 기본 상한을 초과해 건너뛰었고, 같은 페이지에서 상한을 1,600으로 올리자 12개 셀을 모두 복원했습니다. [비보험 5개 분야의 자료와 재현 방법](docs/benchmarks-general.md) · [가명 처리한 보험사별 결과와 처리 시간](docs/benchmarks-insurance.md)

## 설치

**Python 3.11 이상**이 필요합니다. GitHub에서 저장소를 복제합니다.

```sh
git clone https://github.com/choijhyeok/pdfgyeol.git
cd pdfgyeol
```

통합 PR을 병합 전에 시험하려면 설치 전에 아래 브랜치로 전환합니다. 병합 뒤에는 기본 브랜치인 `main`에서 바로 설치합니다.

```sh
git switch choijhyeok/feature/pdfgyeol-v1
```

저장소 루트에서 가상 환경을 만든 뒤 설치합니다. 다음 활성화 명령은 macOS/Linux 기준입니다. Windows PowerShell에서는 `.venv\Scripts\Activate.ps1`을 실행합니다.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Windows에서 가상 환경을 만들 때는 `py -3 -m venv .venv`를 사용할 수 있습니다. 설치에는 빌드·실행 의존성을 내려받을 수 있는 환경이 필요합니다.

## 빠른 시작

### CLI로 JSON 저장하기

두 단의 강변 관찰 기록과 수심 표가 들어 있는 한 쪽짜리 예제입니다. [예제 생성기](examples/make_riverside_notes.py)는 Python 표준 라이브러리만 사용합니다. 아래 명령은 저장소 루트에서 실행합니다.

```sh
python examples/make_riverside_notes.py riverside-notes.pdf
pdfgyeol riverside-notes.pdf --output riverside-notes.json
```

`--output`을 생략하면 JSON을 표준 출력으로 보냅니다. 파일을 저장할 때는 새 경로를 지정하세요. 자신의 PDF를 처리하려면 첫 인수를 해당 파일 경로로 바꾸면 됩니다.

### Python으로 읽기 순서와 표 확인하기

```python
from pdfgyeol import parse_pdf

document = parse_pdf("riverside-notes.pdf")
page = document.pages[0]
table = page.layout.tables[0]

print(page.text)
print()
for row in table.rows:
    print([page.cell_text(cell.id) for cell in row.cells])
```

PDF에는 오른쪽 단을 먼저 그렸지만, 출력은 제목에서 왼쪽 단, 오른쪽 단, 표 순으로 이어집니다. 뒤의 세 줄은 같은 표를 행과 셀로 읽은 결과입니다.

```text
Riverside field notes
Morning fog covered the west bank.
The footpath opened after sunrise.
Two herons stood near the reeds.
The west team finished at nine.
Wind picked up on the east bank.
The team walked past the bridge.
They noted ripples by the sluice.
The east team finished at ten.
Bank
Depth (cm)
West
42
East
36

['Bank', 'Depth (cm)']
['West', '42']
['East', '36']
```

`document.to_dict()`와 `document.to_json()`으로 CLI와 같은 구조를 얻을 수 있습니다. 영역 순서는 `pages[].layout.reading_order`에 있고, 영역·줄에서 `pages[].source.fragments`의 원문 글자와 좌표를 참조할 수 있습니다. [API와 JSON 문서](docs/api-cli.md#구조와-읽기-순서)

## 선택 기능 Decision

Decision은 기본으로 꺼져 있습니다. CLI에서 `--decision`, Python에서 `decision_config=DecisionConfig(enabled=True)`로 켭니다. 서로 다른 유효 후보가 발견되면 그 영역의 제한된 텍스트 발췌와 배치, 후보 순서를 사용자가 설정한 HTTPS 채점 주소로 보냅니다. Python에서는 제공자를 직접 지정할 수도 있습니다. 응답은 기존 후보의 점수로만 쓰며, 실패하거나 점수 차이가 충분하지 않으면 기하 규칙의 순서를 유지합니다. 선택과 보류는 JSON에 기록됩니다. [연결 설정과 요청 형식](docs/decision.md)

새로 만든 합성 문서의 독립 검증 표본 6개에서 실제 순서 교정은 **0건**이었습니다. PDF결은 기본 임곗값을 유지합니다. [Decision 검증 결과](docs/decision-validation.md)

## 지원 범위

텍스트 레이어가 있는 PDF를 대상으로 합니다. 이미지 전용 페이지에서는 이미지의 위치 정보를 남기지만 OCR로 글자를 인식하지는 않습니다. 가로쓰기와 왼쪽에서 오른쪽으로 읽는 배치가 중심이며, 세로쓰기·오른쪽에서 왼쪽으로 읽는 배치·테두리가 불완전하거나 없는 표에서는 순서나 셀 귀속이 틀릴 수 있습니다. [레이아웃 규칙과 한계](docs/layout.md#검증과-한계)

한국어 보험 약관의 조·특약·별표 소속이 필요하면 `--insurance` 또는 `parse_pdf(..., insurance=True)`를 사용할 수 있습니다. 기존 줄을 참조하는 구조 표시를 로컬에서 추가합니다. [보험 약관 옵션](docs/insurance.md)

## 문서

- [Python API, CLI와 JSON 형식](docs/api-cli.md)
- [원문 글자, 좌표와 페이지 상태](docs/extraction.md)
- [단, 영역과 표를 읽는 규칙](docs/layout.md)
- [Decision 설정과 검증](docs/decision.md)

## 라이선스

PDF결과 강변 관찰 예제는 [MIT 라이선스](LICENSE)로 제공됩니다.
