"""tqdm 진행률을 웹 응답으로 보낼 수 있는 문자열로 만든다."""

from __future__ import annotations

import io

from tqdm import tqdm

# 답변 창에 그대로 보여줄 막대 형식이다.
BAR_FORMAT = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}"


def render_bar(bar: tqdm) -> str:
    """현재 tqdm 상태를 한 줄 진행률 문자열로 바꾼다."""
    info = bar.format_dict
    return tqdm.format_meter(
        n=info["n"],
        total=info["total"],
        elapsed=info.get("elapsed") or 0,
        ncols=72,
        prefix=info.get("prefix") or "",
        unit=info.get("unit") or "it",
        unit_scale=bool(info.get("unit_scale")),
        rate=info.get("rate"),
        bar_format=info.get("bar_format") or BAR_FORMAT,
        postfix=info.get("postfix"),
        unit_divisor=info.get("unit_divisor") or 1000,
        initial=info.get("initial") or 0,
    )


class Progress:
    """반복 작업의 진행률을 tqdm으로 재고, 화면용 이벤트를 만든다."""

    def __init__(self, total: int, desc: str) -> None:
        self.bar = tqdm(
            total=max(total, 0),
            desc=desc,
            ncols=72,
            file=io.StringIO(),
            mininterval=0,
            bar_format=BAR_FORMAT,
        )
        self._closed = False

    def event(self) -> dict:
        current = int(self.bar.n)
        total = int(self.bar.total or 0)
        percent = int(current * 100 / total) if total else 0
        return {
            "type": "progress",
            "bar": render_bar(self.bar),
            "current": current,
            "total": total,
            "percent": percent,
        }

    def advance(self, step: int = 1) -> None:
        self.bar.update(step)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.bar.close()
