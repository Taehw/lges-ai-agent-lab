"""LLM 기반 계약서 검토 웹.

왼쪽에서 가이드라인과 계약서를 올리고, 오른쪽에서 검토 결과와 일반 질문을 본다.
업로드와 검토는 진행률을 한 줄씩 흘려 보내고, 일반 질문은 JSON으로 한 번 답한다.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from review.config import CONTRACT_UPLOAD_DIR, MAX_UPLOAD_BYTES, RAG_UPLOAD_DIR
from review.pipeline import ContractService

ALLOWED_EXTENSION = ".pdf"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.json.ensure_ascii = False

service = ContractService()
# 임베딩 저장과 검토가 동시에 chroma를 건드리지 않도록 막는다.
job_lock = threading.Lock()


def _safe_pdf_name(filename: str) -> str | None:
    name = Path(filename or "").name.strip()
    if not name or name in {".", ".."}:
        return None
    if Path(name).suffix.lower() != ALLOWED_EXTENSION:
        return None
    return name


def _save_pdfs(files, directory: Path) -> tuple[list[Path], list[str]]:
    """PDF만 디스크에 저장하고, 같은 이름은 마지막 파일로 남긴다."""
    directory.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for storage in files:
        safe_name = _safe_pdf_name(storage.filename or "")
        if not safe_name:
            rejected.append(storage.filename or "(이름 없음)")
            continue
        dest = directory / safe_name
        storage.save(dest)
        if safe_name in seen:
            saved = [path for path in saved if path.name != safe_name]
        seen.add(safe_name)
        saved.append(dest)
    return saved, rejected


def _sse(events: Iterator[dict], release_lock: bool) -> Response:
    """이벤트를 브라우저로 바로 흘려 보낸다."""

    def generate():
        # 첫 진행률이 버퍼에 갇히지 않도록 연결 직후 한 번 밀어 준다.
        yield ":" + (" " * 2048) + "\n\n"
        try:
            for event in events:
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: {event.get('type', 'message')}\ndata: {payload}\n\n"
        except Exception as exc:
            payload = json.dumps(
                {"type": "error", "error": f"처리 중 오류: {exc}"},
                ensure_ascii=False,
            )
            yield f"event: error\ndata: {payload}\n\n"
        finally:
            if release_lock:
                job_lock.release()

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _busy_response():
    return jsonify({"ok": False, "error": "다른 작업이 진행 중입니다. 끝난 뒤 다시 시도해 주세요."}), 409


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    return jsonify(service.snapshot())


@app.route("/api/rag/upload", methods=["POST"])
def upload_rag():
    """가이드라인 PDF 여러 개를 저장한 뒤 임베딩 진행률을 흘려 보낸다."""
    if not job_lock.acquire(blocking=False):
        return _busy_response()

    files = request.files.getlist("files")
    saved, rejected = _save_pdfs(files, RAG_UPLOAD_DIR)
    if not saved:
        job_lock.release()
        return jsonify(
            {"ok": False, "error": "PDF 파일만 업로드할 수 있습니다.", "rejected": rejected}
        ), 400

    def events():
        if rejected:
            names = ", ".join(rejected)
            yield {"type": "progress", "bar": f"제외된 파일: {names}", "current": 0, "total": 0, "percent": 0}
        yield from service.ingest_rag(saved)

    return _sse(events(), release_lock=True)


@app.route("/api/contract/upload", methods=["POST"])
def upload_contract():
    """계약서 PDF 하나를 저장하고 분할 진행률을 흘려 보낸다."""
    if not job_lock.acquire(blocking=False):
        return _busy_response()

    saved, rejected = _save_pdfs(request.files.getlist("file"), CONTRACT_UPLOAD_DIR)
    if len(saved) != 1:
        job_lock.release()
        return jsonify({"ok": False, "error": "계약서는 PDF 파일 하나를 올려 주세요."}), 400

    return _sse(service.ingest_contract(saved[0]), release_lock=True)


@app.route("/api/contract/review", methods=["POST"])
def review_contract():
    """업로드가 끝난 계약서를 문장마다 검토한다."""
    if service.contract_chunks is None:
        return jsonify({"ok": False, "error": "계약서를 먼저 업로드해 주세요."}), 400
    if not job_lock.acquire(blocking=False):
        return _busy_response()
    return _sse(service.review_contract(), release_lock=True)


@app.route("/api/chat", methods=["POST"])
def chat():
    """일반 질문. 가이드라인 검색 없이 모델에 직접 요청한다."""
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "질문을 입력해 주세요."}), 400
    try:
        answer = service.ask(question)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"답변 생성 중 오류: {exc}"}), 500
    return jsonify({"ok": True, "answer": answer})


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"ok": False, "error": "파일은 50MB 이하만 업로드할 수 있습니다."}), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
