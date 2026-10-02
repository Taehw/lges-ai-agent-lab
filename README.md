# lges-ai-agent-lab

수업 때 만든 작은 도구들을 모아둔 저장소입니다. 폴더마다 앱이 하나씩 들어 있고, 전부 Python으로 만들었습니다.

| 폴더 | 뭐하는 앱인지 |
| --- | --- |
| `pdf_split&merge` | PDF 나누기, 합치기 (데스크톱) |
| `yt_download` | 유튜브 영상 분석하고 받기 (데스크톱) |
| `pdf_translation/pdf-translator` | PDF를 올리면 영어↔한국어로 번역 (웹) |

---

## PDF 분할 · 병합

PDF를 원하는 페이지만 잘라내거나, 여러 개를 순서대로 하나로 합치는 프로그램입니다.

![PDF 분할 · 병합](docs/pdf-split-merge.png)

- PDF를 창에 끌어다 놓으면 페이지 썸네일이 쭉 나옵니다. 썸네일을 눌러서 페이지를 고를 수 있어요.
- 나누는 방법은 세 가지입니다. 범위(`1-5, 8-10`), 페이지 직접 지정(`1, 3, 5`), 그리고 N페이지씩 끊기.
- 합칠 때는 목록에서 순서를 바꾼 다음 저장하면 됩니다.
- 비밀번호가 걸린 PDF는 열 때 비밀번호를 물어봅니다.

```text
cd "pdf_split&merge"
pip install -r requirements.txt
python app.py
```

## 유튜브 영상 분석 · 다운로드

링크를 붙여넣고 분석하면 받을 수 있는 포맷이 표로 나옵니다. 그중에 하나 골라서 받으면 됩니다.

![유튜브 영상 분석 · 다운로드](docs/youtube-downloader.png)

- 표에는 포맷 ID, 영상/음원 구분, 해상도, 확장자, 예상 용량이 나옵니다.
- 720p 이하는 영상과 소리가 한 파일인 경우가 많습니다.
- 1080p 이상은 영상과 소리가 따로 있어서 ffmpeg로 합쳐야 해요. ffmpeg가 없으면 그런 포맷은 다운로드가 시작되지 않습니다.

```text
cd yt_download
pip install -r requirements.txt
python main.py
```

**ffmpeg 설치**

[gyan.dev](https://www.gyan.dev/ffmpeg/builds/)에서 release essentials를 받아서 압축을 풀고, `ffmpeg.exe`가 들어 있는 `bin` 폴더를 PATH에 추가하면 됩니다. 터미널에서 `ffmpeg -version`이 나오면 끝입니다.

PATH를 건드리기 싫으면 `ffmpeg.exe`와 `ffprobe.exe`를 `main.py` 옆에 두어도 찾아서 씁니다. 프로그램 아래쪽에 `ffmpeg: 사용 가능`이라고 뜨면 정상입니다.

## PDF 번역

PDF를 올리면 글자를 뽑아서 OpenAI로 번역해주는 웹 앱입니다. 영어→한국어, 한국어→영어 둘 다 됩니다.

![PDF 번역](docs/pdf-translator.png)

- 왼쪽에서 PDF를 올리고 방향을 고른 다음 `번역 시작`을 누릅니다.
- 가운데에는 PDF에서 뽑은 원문, 오른쪽에는 번역 결과가 나옵니다. 번역은 조각 단위로 끝나는 대로 바로바로 채워지고, 진행률도 같이 올라갑니다.
- 다 끝나면 `저장`으로 원본 PDF 이름과 같은 `.txt` 파일을 받을 수 있습니다.
- 페이지가 너무 길면 문단 기준으로 잘라서 보냅니다. 요청이 실패하면 두 번까지 다시 시도하고, 그래도 안 되면 그 부분만 오류로 표시하고 다음으로 넘어갑니다.
- 다크 모드도 있습니다.

글자를 뽑는 방식이라 스캔본(이미지로만 된 PDF)은 안 됩니다. 업로드는 50MB까지입니다.

먼저 OpenAI API 키를 환경 변수로 넣어야 합니다. 키가 없으면 화면 위에 안내가 뜨고 번역 버튼이 눌리지 않아요.

```text
cd pdf_translation/pdf-translator
pip install -r requirements.txt
set OPENAI_API_KEY=여기에_키
python app.py
```

PowerShell이면 `set` 대신 `$env:OPENAI_API_KEY="여기에_키"`를 쓰세요. 실행한 뒤 브라우저에서 http://127.0.0.1:5000 으로 들어가면 됩니다.

기본 모델은 `gpt-4o-mini`이고, 바꾸고 싶으면 `OPENAI_MODEL` 환경 변수에 모델 이름을 넣으면 됩니다.
