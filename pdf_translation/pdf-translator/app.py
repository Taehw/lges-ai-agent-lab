import os
import re
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from pypdf import PdfReader
from pypdf.errors import PdfReadError

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


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="파일이 너무 큽니다. (최대 50MB)"), 413


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("[오류] 환경 변수 OPENAI_API_KEY가 설정되지 않았습니다. 번역 기능을 사용할 수 없습니다.")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
