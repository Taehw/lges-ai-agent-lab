import os

import gradio as gr

from detector import detect, get_model

HOST = "127.0.0.1"
PORT = int(os.environ.get("VISION_PORT", "7860"))

CSS = """
.split { flex-wrap: nowrap !important; }
.half { flex: 1 1 0 !important; min-width: 0 !important; }
"""


def build_ui():
    with gr.Blocks(title="사물 인식") as demo:
        gr.Markdown("## 사물 인식 (YOLO11n)")
        with gr.Row(equal_height=False, elem_classes="split"):
            with gr.Column(elem_classes="half"):
                gr.Markdown("### 이미지")
                image_in = gr.Image(label="이미지 업로드", type="numpy", sources=["upload", "clipboard"])
                image_out = gr.Image(label="인식 결과", type="numpy", interactive=False)
                image_in.change(detect, inputs=image_in, outputs=image_out)

            with gr.Column(elem_classes="half"):
                gr.Markdown("### 웹캠")
                cam_in = gr.Image(label="웹캠", type="numpy", sources=["webcam"], streaming=True)
                cam_out = gr.Image(label="실시간 인식", type="numpy", interactive=False, streaming=True)
                cam_in.stream(
                    detect,
                    inputs=cam_in,
                    outputs=cam_out,
                    stream_every=0.1,
                    concurrency_limit=1,
                    show_progress="hidden",
                )
    return demo


if __name__ == "__main__":
    get_model()
    build_ui().launch(server_name=HOST, server_port=PORT, css=CSS)
