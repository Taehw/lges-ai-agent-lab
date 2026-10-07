"""뉴스 수집·분석. Gradio 화면과 LangGraph 노드(news, analyze, report)."""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path

import gradio as gr
from PIL import Image

import agent


HOST = "127.0.0.1"
PORT = int(os.environ.get("NEWS_PORT", "7862"))
CSS = """
.gradio-container, .gradio-container textarea, .gradio-container button, .gradio-container label {
  font-family: "Malgun Gothic", "맑은 고딕", sans-serif;
}
"""


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, message: str) -> str:
        self.lines.append(f"{datetime.now():%H:%M:%S}  {message}")
        return "\n".join(self.lines)


def key_notice() -> str:
    if os.environ.get("SERPAPI_API_KEY") or os.environ.get("SERPAPI_KEY"):
        serp = "SERPAPI_API_KEY를 읽었습니다."
    else:
        serp = "SERPAPI_API_KEY가 없습니다. news_analysis/.env 파일에 넣으세요."
    if os.environ.get("OPENAI_API_KEY"):
        openai = "OPENAI_API_KEY를 읽었습니다."
    else:
        openai = "OPENAI_API_KEY 환경 변수가 없습니다."
    return f"{serp} {openai} 키 값은 화면에 표시하지 않습니다."


def articles_json(articles: list[dict] | None) -> str:
    return json.dumps(articles or [], ensure_ascii=False, indent=2)


def groups_markdown(groups: list[dict] | None) -> str:
    if not groups:
        return ""
    blocks = []
    for index, group in enumerate(groups, 1):
        numbers = ", ".join(str(number) for number in group.get("기사번호") or [])
        blocks.append(
            "\n".join(
                [
                    f"### {index}. {group.get('제목', '')}",
                    group.get("요약") or "",
                    f"시사점: {group.get('시사점', '')}",
                    f"포함 기사: {numbers}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _view(log_text: str, articles=None, groups=None, image=None, figure=None, report_path=None, save_status=""):
    return (
        log_text,
        articles_json(articles),
        groups_markdown(groups),
        image,
        figure,
        report_path,
        report_path,
        save_status,
    )


def _opened_image(path: str) -> Image.Image:
    with Image.open(path) as opened:
        return opened.convert("RGB").copy()


def _error_text(exc: Exception) -> str:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, agent.NewsError):
            return str(current)
        current = current.__cause__ or current.__context__
    return f"처리 중 오류가 발생했습니다. {exc}"


def on_run(keyword, count, source):
    log = Log()
    yield _view(log.add("입력을 확인합니다."))
    try:
        checked = agent.validate_request(keyword, count, source)
        agent.require_runtime_keys()
    except agent.NewsError as exc:
        yield _view(log.add(str(exc)))
        return

    keyword, count, source_key = checked
    run_dir = agent.OUTPUT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    graph = agent.build_graph()
    state = {
        "keyword": keyword,
        "count": count,
        "source": source_key,
        "work_dir": str(run_dir),
    }
    articles: list[dict] = []
    groups: list[dict] = []
    figure = None
    image = None
    report_path = None

    yield _view(
        log.add(f"[news] {agent.SOURCE_LABELS[source_key]}에서 '{keyword}' 관련 최신 뉴스 {count}건을 수집합니다.")
    )
    try:
        for chunk in graph.stream(state, stream_mode="updates"):
            node_name, update = next(iter(chunk.items()))
            state.update(update)
            if node_name == "news":
                articles = state.get("articles") or []
                notice = state.get("notice") or f"{len(articles)}건을 JSON으로 정리했습니다."
                yield _view(
                    log.add(f"[news] {notice} [analyze] 키워드, 요약, 시사점을 정리합니다."),
                    articles,
                )
            elif node_name == "analyze":
                groups = state.get("groups") or []
                figure = agent.build_frequency_chart(state.get("keywords") or [])
                image = _opened_image(state["wordcloud_path"])
                names = {group.get("제목") for group in groups}
                if "전체 뉴스" in names:
                    fallback = " 전체를 한 묶음으로 요약했습니다."
                elif "기타 기사" in names:
                    fallback = " 빠뜨린 기사는 기타 기사로 묶었습니다."
                else:
                    fallback = ""
                yield _view(
                    log.add(
                        f"[analyze] 그룹 {len(groups)}개, 워드클라우드, 빈도 그래프를 만들었습니다.{fallback} "
                        "[report] 워드 파일을 작성합니다."
                    ),
                    articles,
                    groups,
                    image,
                    figure,
                )
            elif node_name == "report":
                report_path = state.get("report_path")
                yield _view(
                    log.add(f"[report] 보고서를 만들었습니다. {Path(report_path).name}"),
                    articles,
                    groups,
                    image,
                    figure,
                    report_path,
                )
    except Exception as exc:
        if not isinstance(exc, agent.NewsError):
            traceback.print_exc()
        yield _view(
            log.add(_error_text(exc)),
            articles,
            groups,
            image,
            figure,
            report_path,
        )


def on_save(report_path, folder):
    try:
        dest = agent.copy_report(report_path or "", folder)
    except agent.NewsError as exc:
        return str(exc)
    return f"저장했습니다. {dest}"


def build_ui():
    with gr.Blocks(title="뉴스 분석") as demo:
        gr.Markdown("## 뉴스 분석")
        gr.Markdown("키워드로 구글 뉴스 또는 네이버 뉴스를 모아 주제별로 묶고, 워드 보고서로 저장합니다.")
        gr.Markdown(key_notice())
        report_state = gr.State(None)

        with gr.Row():
            keyword = gr.Textbox(label="뉴스 키워드", placeholder="예: 반도체 수출")
            count = gr.Slider(label="수집 건수", minimum=1, maximum=agent.MAX_COUNT, value=10, step=1)
            source = gr.Radio(label="뉴스 출처", choices=list(agent.SOURCES), value="구글 뉴스")
        save_dir = gr.Textbox(label="저장 폴더", placeholder=r"예: C:\Users\USER\Desktop")
        run_btn = gr.Button("뉴스 수집 및 분석", variant="primary")
        log_box = gr.Textbox(label="진행 로그", lines=10, interactive=False)

        gr.Markdown("### 수집 결과")
        news_json = gr.Code(label="순번, 날짜, 제목, 주요내용, URL", language="json", interactive=False)
        gr.Markdown("### 분석")
        analysis_md = gr.Markdown()
        with gr.Row():
            cloud = gr.Image(label="워드클라우드", type="pil", interactive=False)
            chart = gr.Plot(label="주요 단어 발생 빈도")

        gr.Markdown("### 보고서")
        gr.Markdown("다운로드를 누르거나, 저장 폴더를 입력한 뒤 지정 폴더에 저장을 누르세요.")
        with gr.Row():
            report_file = gr.File(label="보고서 다운로드")
            save_btn = gr.Button("지정 폴더에 저장")
        save_status = gr.Textbox(label="저장 결과", interactive=False)

        run_btn.click(
            on_run,
            inputs=[keyword, count, source],
            outputs=[log_box, news_json, analysis_md, cloud, chart, report_file, report_state, save_status],
            concurrency_limit=1,
        )
        save_btn.click(on_save, inputs=[report_state, save_dir], outputs=save_status)
    return demo


if __name__ == "__main__":
    agent.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = build_ui()
    app.queue()
    app.launch(server_name=HOST, server_port=PORT, css=CSS)
