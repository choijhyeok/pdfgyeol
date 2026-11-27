# PDF결 · pdfgyeol

<img src="assets/otter.png" width="260" alt="물에 누워 두 단짜리 문서의 읽기 순서를 앞발로 따라가는 수달">

PDF결은 보험 약관의 단과 표를 구분하고 원문의 읽기 순서를 복원하기 위한 Python 파서입니다. 현재 설계 단계입니다.

`pdfplumber`로 글자와 좌표를 추출하고, 읽기 순서나 경계가 모호한 구간에서는 Decision으로 후보를 평가하는 방식을 검토하고 있습니다.
