from __future__ import annotations

import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from rag.config import MAX_DISTANCE, UPLOAD_DIR
from rag.pipeline import RAGPipeline

ALLOWED_EXTENSION = ".pdf"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

pipeline = RAGPipeline()


def _safe_pdf_name(filename: str) -> str | None:
    name = Path(filename or "").name.strip()
    if not name or name in {".", ".."}:
        return None
    if Path(name).suffix.lower() != ALLOWED_EXTENSION:
        return None
    return name.replace("\\", "_").replace("/", "_")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
        return jsonify(
            {
                **pipeline.status,
                "files": pipeline.list_files(),
                "max_distance": MAX_DISTANCE,
            }
        )


@app.route("/api/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "error": "업로드할 PDF 파일을 선택해 주세요."}), 400

    saved_paths: list[Path] = []
    rejected: list[str] = []
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for storage in files:
        safe_name = _safe_pdf_name(storage.filename or "")
        if not safe_name:
            rejected.append(storage.filename or "(이름 없음)")
            continue
        dest = UPLOAD_DIR / safe_name
        storage.save(dest)
        saved_paths.append(dest)

    if not saved_paths:
        return jsonify(
            {
                "ok": False,
                "error": "PDF 파일만 업로드할 수 있습니다.",
                "rejected": rejected,
            }
        ), 400

    try:
        result = pipeline.ingest_pdfs(saved_paths)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"문서 처리 중 오류: {exc}"}), 500

    return jsonify(
        {
            "ok": True,
            "files": result["files"],
            "chunk_count": result["chunk_count"],
            "rejected": rejected,
            "all_files": pipeline.list_files(),
        }
    )


@app.route("/api/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    session_id = str(payload.get("session_id") or "default").strip() or "default"
    if not question:
        return jsonify({"ok": False, "error": "질문을 입력해 주세요."}), 400

    try:
        result = pipeline.chat(question=question, session_id=session_id)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"답변 생성 중 오류: {exc}"}), 500
    return jsonify({"ok": True, **result})


@app.route("/api/files")
def files():
    return jsonify({"files": pipeline.list_files()})


@app.route("/api/reset", methods=["POST"])
def reset():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "default").strip() or "default"
    pipeline.clear_history(session_id)
    return jsonify({"ok": True})


def _warmup() -> None:
    try:
        pipeline.ensure_embeddings()
    except Exception as exc:
        pipeline.status["message"] = f"임베딩 모델 로드 실패: {exc}"


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_warmup, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
