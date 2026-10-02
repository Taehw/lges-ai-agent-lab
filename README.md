# lges-ai-agent-lab

로컬에서 쓰는 데스크톱 도구입니다. Python으로 실행합니다.

## PDF 분할 · 병합

페이지를 나누거나, 여러 PDF를 원하는 순서대로 하나의 파일로 합칩니다.

![PDF 분할 · 병합](docs/pdf-split-merge.png)

- PDF를 끌어다 놓거나 불러오면 페이지 썸네일이 표시됩니다.
- 분할은 범위 입력(`1-5, 8-10`), 페이지 지정(`1, 3, 5`), 고정 페이지 단위를 지원합니다.
- 병합은 파일 순서를 바꾼 뒤 하나의 PDF로 저장합니다.

```text
cd pdf2
pip install -r requirements.txt
python app.py
```

같은 폴더의 `PDF분할병합.exe`로도 실행할 수 있습니다.

## 유튜브 영상 분석 · 다운로드

유튜브 링크를 분석한 뒤, 화질이나 음질을 골라 저장합니다.

![유튜브 영상 분석 · 다운로드](docs/youtube-downloader.png)

- 분석이 끝나면 포맷, 해상도, 확장자, 예상 용량이 표로 나옵니다.
- 720p 이하 일부 포맷은 영상과 음원이 한 파일입니다.
- 1080p 이상은 보통 영상과 음원이 분리되어 있어 ffmpeg로 합칩니다. ffmpeg가 없으면 병합 다운로드는 시작하지 않습니다.

```text
cd yt-dlp
pip install -r requirements.txt
python main.py
```

ffmpeg는 [ffmpeg.org](https://ffmpeg.org/download.html) 또는 [gyan.dev 빌드](https://www.gyan.dev/ffmpeg/builds/)에서 받아 `ffmpeg.exe`가 있는 폴더를 PATH에 넣으면 됩니다. PATH에 넣지 않으려면 `ffmpeg.exe`와 `ffprobe.exe`를 `main.py`와 같은 폴더, 또는 그 아래 `bin` 폴더에 두세요. 창 하단에 `ffmpeg: 사용 가능`이 보이면 준비된 것입니다.
