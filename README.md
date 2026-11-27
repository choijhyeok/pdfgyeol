# PDF결 · pdfgyeol

<img src="assets/otter.png" width="260" alt="물에 누워 두 단짜리 문서의 읽기 순서를 앞발로 따라가는 수달">

PDF결은 PDF의 단과 표를 구분하고 원문의 읽기 순서를 복원하는 Python 파서입니다. `pdfplumber`로 글자와 좌표를 읽으며, 선택 기능인 Decision으로 기하 규칙에서 찾은 순서 대안을 평가할 수 있습니다.

저장소에서 설치한 뒤 PDF를 UTF-8 JSON으로 변환합니다.

```sh
python -m pip install .
pdfgyeol input.pdf --output result.json
```

기본 모드는 키와 네트워크 없이 실행됩니다. [Python API와 CLI 문서](docs/api-cli.md)에 사용 예시와 JSON 구조가 있습니다.
