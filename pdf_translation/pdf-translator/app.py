import json
import os
import re
import time
import uuid
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_file, stream_with_context
from openai import OpenAI
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
CHUNK_LIMIT = 3000  # 이 글자 수를 넘는 페이지는 문단 기준으로 다시 자른다
MAX_RETRIES = 2

SYSTEM_PROMPTS = {
    "en2ko": (
        "You are a professional translator. Translate the user's English text into natural Korean. "
        "Preserve the original meaning and paragraph structure, including line breaks. "
        "Output only the translated text, with no explanations, notes, or quotation marks."
    ),
    "ko2en": (
        "You are a professional translator. Translate the user's Korean text into natural English. "
        "Preserve the original meaning and paragraph structure, including line breaks. "
        "Output only the translated text, with no explanations, notes, or quotation marks."
    ),
}

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

# 실행 시 업로드/결과 폴더가 없으면 자동 생성
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 업로드 최대 50MB

# 작업 정보는 메모리에 보관 (job_id -> 원본 파일명, 페이지별 텍스트, 결과 경로)
jobs: dict[str, dict] = {}

# Windows 파일명에 쓸 수 없는 경로 문자와 제어 문자
INVALID_NAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def safe_stem(filename: str) -> str:
    """확장자를 뗀 원본 파일명에서 경로 문자만 제거한다. (한글은 그대로 유지)"""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = INVALID_NAME_CHARS.sub("", stem).strip().rstrip(".")
    return stem or "translated"


def extract_pages(pdf_path: Path) -> list[str]:
    """pypdf로 페이지별 텍스트를 추출한다."""
    reader = PdfReader(str(pdf_path))
    return [(page.extract_text() or "").strip() for page in reader.pages]


def split_long(text: str, limit: int) -> list[str]:
    """문단(빈 줄) → 줄 → 글자 수 순서로 잘라 limit 이하 조각으로 만든다."""
    if len(text) <= limit:
        return [text]
    for sep, pattern in (("\n\n", r"\n\s*\n"), ("\n", r"\n")):
        parts = [p for p in re.split(pattern, text) if p.strip()]
        if len(parts) > 1:
            break
    else:
        # 줄바꿈이 전혀 없으면 글자 수로 강제 분할
        return [text[i:i + limit] for i in range(0, len(text), limit)]

    chunks, buf = [], ""
    for part in parts:
        # 한 문단이 limit보다 길면 더 잘게 나눈다
        for piece in split_long(part, limit):
            if buf and len(buf) + len(sep) + len(piece) > limit:
                chunks.append(buf)
                buf = piece
            else:
                buf = f"{buf}{sep}{piece}" if buf else piece
    if buf:
        chunks.append(buf)
    return chunks


def build_chunks(pages: list[str]) -> list[str]:
    """페이지 단위로 나누고, 긴 페이지는 문단 기준으로 다시 자른다."""
    chunks = []
    for page in pages:
        if page:
            chunks.extend(split_long(page, CHUNK_LIMIT))
    return chunks


def translate_chunk(client: OpenAI, text: str, direction: str) -> str:
    """청크 하나를 번역한다. 실패하면 최대 2회 재시도 후 예외를 그대로 올린다."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPTS[direction]},
                    {"role": "user", "content": text},
                ],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)  # 1초, 2초 대기 후 재시도


def sse(payload: dict) -> str:
    """SSE 형식(data: ...\\n\\n)의 이벤트 문자열을 만든다."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.route("/")
def index():
    return render_template("index.html", api_key_missing=not os.environ.get("OPENAI_API_KEY"))


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify(error="업로드할 PDF 파일을 선택해 주세요."), 400

    # 확장자 검사
    if not file.filename.lower().endswith(".pdf"):
        return jsonify(error="PDF 파일만 업로드할 수 있습니다."), 400

    data = file.read()
    if not data:
        return jsonify(error="빈 파일입니다."), 400
    # 확장자만 바꾼 파일을 거르기 위해 PDF 시그니처 확인
    if data.lstrip()[:5] != b"%PDF-":
        return jsonify(error="올바른 PDF 파일이 아닙니다."), 400

    # 저장 파일명은 job_id로 만들어 충돌을 피하고, 원본 이름은 따로 보관
    job_id = uuid.uuid4().hex
    pdf_path = UPLOAD_DIR / f"{job_id}.pdf"
    pdf_path.write_bytes(data)

    try:
        pages = extract_pages(pdf_path)
    except (PdfReadError, ValueError, OSError) as e:
        pdf_path.unlink(missing_ok=True)
        return jsonify(error=f"PDF를 읽을 수 없습니다: {e}"), 400

    # 모든 페이지에 텍스트가 없으면 스캔 이미지 PDF로 판단
    if not any(pages):
        pdf_path.unlink(missing_ok=True)
        return jsonify(error="텍스트를 추출할 수 없는 PDF입니다. (스캔 이미지 PDF는 지원하지 않습니다)"), 400

    jobs[job_id] = {
        "original_name": file.filename,
        "stem": safe_stem(Path(file.filename.replace("\\", "/")).name),
        "pages": pages,
        "output_path": None,
    }

    return jsonify(
        job_id=job_id,
        page_count=len(pages),
        text="\n\n".join(p for p in pages if p),
    )


@app.route("/translate/<job_id>")
def translate(job_id):
    direction = request.args.get("direction", "en2ko")

    def generate():
        job = jobs.get(job_id)
        if job is None:
            yield sse({"type": "error", "message": "작업을 찾을 수 없습니다. PDF를 다시 업로드해 주세요."})
            return
        if direction not in SYSTEM_PROMPTS:
            yield sse({"type": "error", "message": f"지원하지 않는 번역 방향입니다: {direction}"})
            return
        if not os.environ.get("OPENAI_API_KEY"):
            yield sse({"type": "error", "message": "OPENAI_API_KEY 환경 변수가 설정되지 않았습니다."})
            return

        client = OpenAI()  # OPENAI_API_KEY를 환경 변수에서 자동으로 읽음
        chunks = build_chunks(job["pages"])
        total = len(chunks)
        results = []

        for i, chunk in enumerate(chunks, start=1):
            try:
                translated = translate_chunk(client, chunk, direction)
            except Exception as e:
                # 재시도까지 실패하면 오류 표시를 남기고 다음 청크로 진행
                translated = f"[번역 오류: {i}번째 청크 - {e}]"
            results.append(translated)
            yield sse({
                "type": "progress",
                "percent": round(i / total * 100),
                "current": i,
                "total": total,
                "text": translated,
            })

        # 원본 PDF와 같은 이름의 txt로 UTF-8 저장
        output_path = OUTPUT_DIR / f"{job['stem']}.txt"
        try:
            output_path.write_text("\n\n".join(results), encoding="utf-8")
        except OSError as e:
            yield sse({"type": "error", "message": f"결과 파일을 저장하지 못했습니다: {e}"})
            return
        job["output_path"] = output_path
        yield sse({"type": "done", "filename": output_path.name})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if job is None or job["output_path"] is None or not job["output_path"].exists():
        abort(404, description="번역 결과 파일이 없습니다.")
    return send_file(
        job["output_path"],
        mimetype="text/plain; charset=utf-8",
        as_attachment=True,
        download_name=f"{job['stem']}.txt",
    )


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="파일이 너무 큽니다. (최대 50MB)"), 413


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("[오류] 환경 변수 OPENAI_API_KEY가 설정되지 않았습니다. 번역 기능을 사용할 수 없습니다.")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
