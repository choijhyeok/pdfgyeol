# 공개 PDF 표 구조 비교

Caltrain 시간표에서 pdfplumber와 PyMuPDF는 표본 12개를 모두 올바른 역·열차 조합에 배치했다. PDF결은 기본 설정에서 표를 만들지 못했다. 논문의 실험표에서는 세 도구 모두 여러 지표를 한 셀에 넣어 표본 10개에서 개별 지표와 수치를 연결하지 못했다.

| 원본과 검증 항목 | PDF결 | pdfplumber | PyMuPDF |
| --- | ---: | ---: | ---: |
| Caltrain 북행 시간표 1쪽, 역 × 열차 번호 → 시각 | 0/12 | 12/12 | 12/12 |
| *Attention Is All You Need* 9쪽 표 3, 모델·변형 × 지표 → 수치 | 0/10 | 0/10 | 0/10 |

### 출력에서 확인한 사례

- **Caltrain.** pdfplumber와 PyMuPDF 모두 시간표를 31행 × 25열로 반환했다. Palo Alto 행의 401번 열차는 `6:10a`, 901번은 `9:40a`였다. PDF결은 `table_edge_budget_exceeded` 경고를 남겼고 표 0개를 반환했다. 표 탐지에 쓰는 선분은 1,588개로 기본 상한인 1,200개를 넘었다. `6:10a`는 PDF결의 페이지 텍스트에 있었다. [Caltrain 원본 PDF, 2026-11-04 특별 운행](https://www.caltrain.com/media/38067/download)
- **Attention.** PDF결과 pdfplumber는 표 가운데 일부를 8행 × 1열로 반환했다. PyMuPDF는 8행 × 3열을 반환해 `base` 행을 구분했지만 `4.92 25.8 65`를 한 셀에 넣었고, 세 지표의 열 머리글도 한 셀로 합쳤다. 원본에서 `4.92`는 PPL (dev), `25.8`은 BLEU (dev), `65`는 백만 단위 매개변수 수다. [arXiv v7 원본 PDF](https://arxiv.org/pdf/1706.03762v7#page=9)

이 결과는 위 두 페이지의 표 구조를 비교한 것이다. 표 전체의 정확도, 본문 읽기 순서, 처리 속도는 이 점수의 범위 밖이다.

## 재현

Python 3.12.11, macOS arm64에서 PDF결 커밋 `334087610ec19cc4b84d8c3dd7c030f206033e8a`의 기본 파서를 사용했다. 세 도구 모두 페이지 전체에서 기본 옵션으로 표를 찾았다. PDF결에서는 Decision과 보험 어노테이션이 기본값대로 꺼져 있었다.

원본 PDF 두 개를 아래 명령처럼 저장한다. 스크립트는 추출을 시작하기 전에 각 파일의 SHA-256을 검사하고 실행 중에는 로컬 파일만 읽는다.

```sh
mkdir -p benchmark-inputs
curl -fsSL https://www.caltrain.com/media/38067/download -o benchmark-inputs/caltrain.pdf
curl -fsSL https://arxiv.org/pdf/1706.03762v7 -o benchmark-inputs/attention.pdf
python -m pip install -e . pdfplumber==0.11.11 pdfminer.six==20260107 PyMuPDF==2.2
python tools/benchmark_tables.py benchmark-inputs
```

| 파일 | SHA-256 |
| --- | --- |
| `caltrain.pdf` | `dd01a3c645da952dd94678bfe0e52fad0135202ef91723f3d30308bc23694ddc` |
| `attention.pdf` | `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697` |

추출 결과를 보기 전에 렌더링한 PDF 페이지에서 기준 셀을 직접 옮겼다. Caltrain에서는 역 4곳과 열차 번호 `101`, `401`, `901`의 교차점 12개를 확인했다. 논문에서는 `base`와 `big`의 학습 스텝·PPL·BLEU·매개변수 수, 변형 `(E)`의 PPL·BLEU를 확인했다. 스크립트는 기준값과 항목별 결과를 JSON으로 출력한다. [비교 스크립트](../tools/benchmark_tables.py)

PDF결의 표 셀 좌표와 `cell_text()`, pdfplumber의 `extract_tables()`, PyMuPDF의 `find_tables()`와 `extract()`를 각각 행·열 좌표로 읽었다. 같은 격자에서 행 이름의 오른쪽이자 열 머리글의 아래에 있는 교차점이 하나이고 그 값이 기준값과 같을 때 1점을 줬다. 비교 시 유니코드 NFKC 정규화, 대소문자 통합, 공백 제거를 적용했다.

## Caltrain 선분 상한의 영향

기본 설정 비교를 마친 뒤 같은 Caltrain 1쪽에서 `max_table_edges`만 1,200에서 1,600으로 올려 진단했다. PDF결은 31행 × 25열과 1행 × 2열 표를 반환했고, 같은 역·열차 조합 12개에서 시각을 모두 올바르게 복원했다. 위에서 원본을 내려받은 뒤 저장소 루트에서 실행할 수 있다.

```sh
python - <<'PY'
from hashlib import sha256
from pathlib import Path

from pdfgyeol import LayoutConfig, parse_pdf
from tools.benchmark_tables import TARGETS, score

target = TARGETS[0]
path = Path("benchmark-inputs") / target.filename
assert sha256(path.read_bytes()).hexdigest() == target.sha256
document = parse_pdf(path, layout_config=LayoutConfig(max_table_edges=1600))
page = document.pages[target.page - 1]
grids = []
for table in page.layout.tables:
    grids.append({
        (cell.row_index, cell.column_index): page.cell_text(cell.id)
        for row in table.rows
        for cell in row.cells
    })
result = score(grids, target)
print(f"{result['correct']}/{result['total']}", result["table_shapes"])
PY
```
