from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "유튜브 영상 분석 · 다운로드"

COLUMNS = [
    "포맷 ID",
    "유형(영상/음원)",
    "해상도/음질",
    "확장자(ext)",
    "예상 파일 크기",
    "비고",
]

KIND_LABEL = {
    "both": "영상+음원",
    "video": "영상",
    "audio": "음원",
}

COLOR_HIGH = QColor("#e7f0ff")
COLOR_AUDIO = QColor("#fff6e8")
COLOR_NORMAL = QColor("#ffffff")

FFMPEG_HELP = (
    "1080p 이상처럼 영상과 음원이 분리된 포맷은 ffmpeg가 있어야 한 파일로 합칠 수 있습니다.\n\n"
    "Windows 설치:\n"
    "1. https://ffmpeg.org/download.html 또는 https://www.gyan.dev/ffmpeg/builds/ 에서 받습니다.\n"
    "2. ffmpeg.exe 와 ffprobe.exe 가 있는 bin 폴더를 시스템 PATH에 추가합니다.\n"
    "3. 새 터미널에서 ffmpeg -version 이 동작하는지 확인한 뒤 이 프로그램을 다시 실행합니다.\n\n"
    "PATH 없이 쓰려면 ffmpeg.exe 와 ffprobe.exe 를 main.py 와 같은 폴더(또는 bin 폴더)에 두세요."
)

STYLESHEET = """
QWidget {
    background: #f4f6f8;
    color: #1f2933;
    font-family: "Malgun Gothic", "Segoe UI", sans-serif;
    font-size: 10pt;
}
QLineEdit {
    background: #ffffff;
    border: 1px solid #d0d7e2;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: #2563eb;
}
QLineEdit:disabled {
    background: #eef1f5;
    color: #334155;
}
QPushButton {
    background: #2563eb;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
    min-width: 72px;
}
QPushButton:hover { background: #1d4ed8; }
QPushButton:pressed { background: #1e40af; }
QPushButton:disabled { background: #c5ceda; color: #f8fafc; }
QPushButton#secondary { background: #475569; }
QPushButton#secondary:hover { background: #334155; }
QPushButton#secondary:disabled { background: #c5ceda; }
QTableWidget {
    background: #ffffff;
    border: 1px solid #d0d7e2;
    border-radius: 6px;
    gridline-color: #e6ebf2;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #e8eef6;
    color: #334155;
    padding: 6px;
    border: none;
    border-right: 1px solid #dbe3ee;
    font-weight: 600;
}
QProgressBar {
    background: #ffffff;
    border: 1px solid #d0d7e2;
    border-radius: 6px;
    text-align: center;
    min-height: 22px;
    color: #1f2933;
}
QProgressBar::chunk {
    background: #2563eb;
    border-radius: 5px;
}
QLabel#header { font-size: 14pt; font-weight: 700; }
QLabel#title { font-size: 11pt; font-weight: 600; }
QLabel#status { color: #1e3a5f; font-weight: 600; }
QLabel#hint { color: #64748b; font-size: 9pt; }
QLabel#thumb {
    background: #e5e7eb;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    color: #6b7280;
}
"""


def default_download_dir() -> Path:
    """시스템 기본 다운로드 폴더. 명세대로 사용자 홈의 Downloads 를 쓴다."""
    return Path.home() / "Downloads"


def find_ffmpeg_executable() -> str | None:
    """PATH 또는 프로그램 옆 폴더에서 ffmpeg 실행 파일을 찾는다."""
    found = shutil.which("ffmpeg")
    if found:
        return found

    names = ["ffmpeg.exe", "ffmpeg"] if sys.platform == "win32" else ["ffmpeg"]
    roots = [
        Path.cwd(),
        Path.cwd() / "bin",
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent / "bin",
        Path.home() / "ffmpeg" / "bin",
    ]
    if sys.platform == "win32":
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(env_name)
            if root:
                roots.append(Path(root) / " " / "bin")
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return str(candidate)
    return None


def ffmpeg_location_arg() -> str | None:
    """yt-dlp ffmpeg_location 값.

    PATH에서 찾으면 None을 반환해 yt-dlp가 PATH를 그대로 쓰게 한다.
    후보 폴더에서 찾으면 그 디렉터리를 반환한다.
    """
    if shutil.which("ffmpeg"):
        return None
    exe = find_ffmpeg_executable()
    if not exe:
        return None
    return str(Path(exe).resolve().parent)


def ffmpeg_available() -> bool:
    """병합에는 ffmpeg와 ffprobe가 둘 다 필요하다."""
    exe = find_ffmpeg_executable()
    if not exe:
        return False
    if shutil.which("ffprobe"):
        return True
    probe = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    return (Path(exe).resolve().parent / probe).is_file()


def format_size(num: float) -> str:
    value = float(num)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_rate(num: float) -> str:
    return f"{format_size(num)}/s"


def format_eta(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def friendly_error(exc: BaseException) -> str:
    raw = str(exc).strip() or exc.__class__.__name__
    if len(raw) > 1800:
        raw = raw[:1800] + "..."
    lower = raw.lower()
    if any(token in lower for token in ("unsupported url", "unable to extract", "not a valid url", "invalid url")):
        lead = "이 링크에서 영상 정보를 가져오지 못했습니다.\n유튜브 주소가 맞는지 확인해 주세요."
    elif any(token in lower for token in ("timed out", "timeout", "urlopen", "connection", "network", "getaddrinfo", "name or service not known", "temporary failure")):
        lead = "네트워크 오류로 작업에 실패했습니다.\n인터넷 연결을 확인한 뒤 다시 시도해 주세요."
    elif "ffmpeg" in lower or "ffprobe" in lower or "merger" in lower:
        lead = "영상과 음원을 합치지 못했습니다.\n" + FFMPEG_HELP
    elif any(token in lower for token in ("sign in", "login", "private video", "confirm your age")):
        lead = "이 영상은 비공개이거나 로그인·연령 확인이 필요해 받을 수 없습니다."
    elif any(token in lower for token in ("not available", "video unavailable", "removed", "blocked")):
        lead = "이 영상을 현재 계정 또는 지역에서 이용할 수 없습니다."
    elif "playlist" in lower:
        lead = "재생목록 주소는 지원하지 않습니다. 개별 영상 링크를 입력해 주세요."
    else:
        lead = "작업을 완료하지 못했습니다."
    return f"{lead}\n\n상세:\n{raw}"


def _protocol_rank(protocol: str | None) -> int:
    protocol = protocol or ""
    if protocol in ("https", "http"):
        return 0
    if "m3u8" in protocol:
        return 2
    if "dash" in protocol:
        return 3
    return 1


def _prefer_format(new: dict, old: dict) -> bool:
    new_rank = _protocol_rank(new.get("protocol"))
    old_rank = _protocol_rank(old.get("protocol"))
    if new_rank != old_rank:
        return new_rank < old_rank
    new_size = new.get("filesize") or 0
    old_size = old.get("filesize") or 0
    return new_size > old_size


def _classify(fmt: dict) -> str | None:
    vcodec = fmt.get("vcodec") or "none"
    acodec = fmt.get("acodec") or "none"
    if str(vcodec).startswith("images"):
        return None
    has_video = vcodec != "none"
    has_audio = acodec != "none"
    if has_video and has_audio:
        return "both"
    if has_video:
        return "video"
    if has_audio:
        return "audio"
    return None


def _quality_label(fmt: dict, kind: str) -> str:
    if kind == "audio":
        abr = fmt.get("abr") or fmt.get("tbr")
        codec = (fmt.get("acodec") or "").split(".")[0]
        if abr:
            text = f"{int(round(float(abr)))} kbps"
        else:
            text = fmt.get("format_note") or "음질 정보 없음"
        if codec and codec != "none":
            return f"{text} · {codec}"
        return text

    height = fmt.get("height")
    fps = fmt.get("fps") or 0
    if height:
        text = f"{int(height)}p"
        try:
            fps_value = float(fps)
        except (TypeError, ValueError):
            fps_value = 0
        if fps_value >= 50:
            text += str(int(round(fps_value)))
        return text
    return fmt.get("format_note") or fmt.get("resolution") or "-"


def _size_text(fmt: dict) -> str:
    exact = fmt.get("filesize")
    approx = fmt.get("filesize_approx")
    if exact:
        return format_size(exact)
    if approx:
        return "약 " + format_size(approx)
    return "알 수 없음"


def _row_from_format(fmt: dict, kind: str) -> dict:
    height = int(fmt.get("height") or 0)
    if kind == "audio":
        tier = "오디오 전용"
    elif height >= 1080:
        tier = "고화질"
    else:
        tier = "일반화질"

    needs_merge = kind == "video"
    remark = tier
    if needs_merge:
        remark = f"{tier} · 오디오 병합"
    language = fmt.get("language")
    if kind == "audio" and language and language not in ("none", "und"):
        remark = f"{remark} · {language}"

    try:
        fps = float(fmt.get("fps") or 0)
    except (TypeError, ValueError):
        fps = 0.0
    try:
        abr = float(fmt.get("abr") or fmt.get("tbr") or 0)
    except (TypeError, ValueError):
        abr = 0.0

    return {
        "format_id": str(fmt.get("format_id")),
        "kind": kind,
        "kind_label": KIND_LABEL[kind],
        "quality": _quality_label(fmt, kind),
        "ext": fmt.get("ext") or "-",
        "size_text": _size_text(fmt),
        "note": remark,
        "height": height,
        "fps": fps,
        "abr": abr,
        "needs_merge": needs_merge,
        "tier": tier,
    }


def build_format_rows(info: dict) -> list[dict]:
    """분석 결과에서 사용자가 고를 수 있는 포맷만 추린다.

    스토리보드·DRM·영상/음원 모두 없는 항목은 빼며,
    같은 포맷 ID는 직접 다운로드(https)를 우선한다.
    """
    best_by_id: dict[str, dict] = {}
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict) or fmt.get("has_drm"):
            continue
        format_id = str(fmt.get("format_id") or "").strip()
        if not format_id:
            continue
        ext = (fmt.get("ext") or "").lower()
        note = (fmt.get("format_note") or "").lower()
        if ext in {"mhtml", "jpg", "png", "webp"} or "storyboard" in note:
            continue
        if _classify(fmt) is None:
            continue
        previous = best_by_id.get(format_id)
        if previous is None or _prefer_format(fmt, previous):
            best_by_id[format_id] = fmt

    rows = []
    for fmt in best_by_id.values():
        kind = _classify(fmt)
        if kind is None:
            continue
        rows.append(_row_from_format(fmt, kind))

    def sort_key(row: dict):
        if row["kind"] == "audio":
            return (1, 0, 0, -(row["abr"]), row["format_id"])
        # 같은 해상도면 이미 합쳐진 스트림을 위로 둔다.
        merge_penalty = 1 if row["needs_merge"] else 0
        return (0, -(row["height"]), -(row["fps"]), merge_penalty, row["format_id"])

    rows.sort(key=sort_key)
    return rows


def fetch_thumbnail(url: str | None) -> bytes | None:
    if not url:
        return None
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(2_000_000)
        return data or None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def open_save_location(path: str) -> None:
    if sys.platform == "win32" and path and os.path.isfile(path):
        subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
        return
    folder = path if path and os.path.isdir(path) else (os.path.dirname(path) if path else "")
    if folder and os.path.isdir(folder):
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


class AnalyzeWorker(QThread):
    """링크 정보를 메인 스레드 밖에서 가져온다."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def run(self) -> None:
        try:
            import yt_dlp
        except ImportError:
            self.failed.emit(
                "yt-dlp가 설치되어 있지 않습니다.\n\n"
                "pip install -r requirements.txt"
            )
            return
        options = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 30,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self.url, download=False)
        except Exception as exc:
            self.failed.emit(friendly_error(exc))
            return

        if not isinstance(info, dict):
            self.failed.emit("영상 정보를 읽지 못했습니다.\n링크를 다시 확인해 주세요.")
            return
        if info.get("_type") in ("playlist", "multi_video"):
            self.failed.emit("재생목록 주소는 지원하지 않습니다. 개별 영상 링크를 입력해 주세요.")
            return
        entries = info.get("entries")
        if isinstance(entries, list) and not info.get("formats"):
            self.failed.emit("재생목록 주소는 지원하지 않습니다. 개별 영상 링크를 입력해 주세요.")
            return

        rows = build_format_rows(info)
        if not rows:
            self.failed.emit("이 영상에서 받을 수 있는 포맷을 찾지 못했습니다.")
            return

        self.succeeded.emit(
            {
                "title": info.get("title") or "제목 없음",
                "channel": info.get("channel") or info.get("uploader") or "",
                "duration": info.get("duration"),
                "is_live": bool(info.get("is_live")),
                "thumbnail": fetch_thumbnail(info.get("thumbnail")),
                "formats": rows,
            }
        )


class DownloadWorker(QThread):
    """선택한 포맷을 받고, 진행률을 시그널로 보낸다."""

    progress = pyqtSignal(object)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        save_dir: str,
        format_spec: str,
        needs_merge: bool,
        ffmpeg_location: str | None,
    ) -> None:
        super().__init__()
        self.url = url
        self.save_dir = save_dir
        self.format_spec = format_spec
        self.needs_merge = needs_merge
        self.ffmpeg_location = ffmpeg_location
        self._result_path: str | None = None
        self._lock = threading.Lock()
        self._last_emit = 0.0

    def _remember_path(self, path: str | None) -> None:
        if path:
            self._result_path = path

    def _emit_progress(self, payload: dict, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and (now - self._last_emit) < 0.15:
                return
            self._last_emit = now
        self.progress.emit(payload)

    def _hook(self, data: dict) -> None:
        status = data.get("status")
        if status == "finished":
            self._remember_path(data.get("filename"))
            return
        if status != "downloading":
            return

        downloaded = data.get("downloaded_bytes") or 0
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        percent = None
        if total:
            percent = max(0.0, min(100.0, downloaded * 100.0 / total))
        else:
            frag_count = data.get("fragment_count") or 0
            frag_index = data.get("fragment_index") or 0
            if frag_count:
                percent = max(0.0, min(100.0, frag_index * 100.0 / frag_count))

        speed = data.get("speed")
        eta = data.get("eta")
        speed_text = format_rate(speed) if speed else "계산 중"
        eta_text = format_eta(eta) if eta is not None else "계산 중"

        info = data.get("info_dict") or {}
        vcodec = info.get("vcodec") or "none"
        acodec = info.get("acodec") or "none"
        if vcodec != "none" and acodec == "none":
            phase = "영상"
        elif vcodec == "none" and acodec != "none":
            phase = "음원"
        else:
            phase = ""
        if phase:
            text = f"다운로드 중 · {phase} ({speed_text}, 남은 시간 {eta_text})"
        else:
            text = f"다운로드 중 ({speed_text}, 남은 시간 {eta_text})"

        self._emit_progress({"percent": percent, "text": text})

    def _post_hook(self, data: dict) -> None:
        info = data.get("info_dict") or {}
        self._remember_path(info.get("filepath") or info.get("filename"))
        if data.get("status") == "started":
            self._emit_progress({"percent": 100, "text": "병합 중..."}, force=True)

    def run(self) -> None:
        try:
            import yt_dlp
        except ImportError:
            self.failed.emit(
                "yt-dlp가 설치되어 있지 않습니다.\n\n"
                "pip install -r requirements.txt"
            )
            return

        options = {
            "format": self.format_spec,
            "outtmpl": {"default": "%(title)s [%(id)s].%(ext)s"},
            "paths": {"home": self.save_dir},
            "progress_hooks": [self._hook],
            "postprocessor_hooks": [self._post_hook],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "windowsfilenames": True,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "concurrent_fragment_downloads": 4,
        }
        # 영상 전용(DASH) 포맷은 format_spec 이 "ID+bestaudio/best" 이다.
        # 병합 결과 컨테이너를 mp4로 고정한다.
        if self.needs_merge:
            options["merge_output_format"] = "mp4"
        if self.ffmpeg_location:
            options["ffmpeg_location"] = self.ffmpeg_location

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self.url, download=True)
        except Exception as exc:
            self.failed.emit(friendly_error(exc))
            return

        path = None
        if isinstance(info, dict):
            requested = info.get("requested_downloads") or []
            if requested and isinstance(requested[-1], dict):
                path = requested[-1].get("filepath") or requested[-1].get("filename")
            path = path or info.get("filepath") or info.get("_filename")
        self.succeeded.emit(path or self._result_path or self.save_dir)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(980, 700)
        self.setMinimumSize(860, 620)

        self._busy = False
        self._analyzed_url = ""
        self._analyze_thread: AnalyzeWorker | None = None
        self._download_thread: DownloadWorker | None = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel(APP_TITLE)
        header.setObjectName("header")
        layout.addWidget(header)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("유튜브 링크:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_edit.returnPressed.connect(self.start_analyze)
        self.url_edit.textChanged.connect(self._on_url_edited)
        url_row.addWidget(self.url_edit, 1)
        self.analyze_btn = QPushButton("분석")
        self.analyze_btn.clicked.connect(self.start_analyze)
        url_row.addWidget(self.analyze_btn)
        layout.addLayout(url_row)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("저장 폴더:"))
        self.path_edit = QLineEdit(str(default_download_dir()))
        self.path_edit.setReadOnly(True)
        path_row.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton("폴더 선택")
        self.browse_btn.setObjectName("secondary")
        self.browse_btn.clicked.connect(self.choose_folder)
        path_row.addWidget(self.browse_btn)
        layout.addLayout(path_row)

        info_row = QHBoxLayout()
        self.thumb_label = QLabel("썸네일")
        self.thumb_label.setObjectName("thumb")
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setFixedSize(176, 99)
        info_row.addWidget(self.thumb_label)
        self.title_label = QLabel("영상을 분석하면 제목과 포맷이 여기에 표시됩니다.")
        self.title_label.setObjectName("title")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        info_row.addWidget(self.title_label, 1)
        layout.addLayout(info_row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(True)
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        header_view.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 130)
        layout.addWidget(self.table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        layout.addWidget(self.progress)

        status_row = QHBoxLayout()
        self.status_label = QLabel("대기 중")
        self.status_label.setObjectName("status")
        status_row.addWidget(self.status_label, 1)
        self.download_btn = QPushButton("다운로드")
        self.download_btn.setEnabled(False)
        self.download_btn.setToolTip("분석이 끝나고 포맷을 선택하면 다운로드할 수 있습니다.")
        self.download_btn.clicked.connect(self.start_download)
        status_row.addWidget(self.download_btn)
        layout.addLayout(status_row)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        foot = QHBoxLayout()
        self.hint_label = QLabel(
            "1080p 이상 고화질은 보통 영상과 음원이 분리되어 있어 ffmpeg로 병합해야 합니다."
        )
        self.hint_label.setObjectName("hint")
        self.hint_label.setWordWrap(True)
        foot.addWidget(self.hint_label, 1)
        self.ffmpeg_label = QLabel()
        self.ffmpeg_label.setObjectName("hint")
        foot.addWidget(self.ffmpeg_label)
        layout.addLayout(foot)
        self._update_ffmpeg_label()

    def _update_ffmpeg_label(self) -> None:
        if ffmpeg_available():
            self.ffmpeg_label.setText("ffmpeg: 사용 가능")
            self.ffmpeg_label.setStyleSheet("color: #047857; font-size: 9pt;")
        else:
            self.ffmpeg_label.setText("ffmpeg: 없음 · 1080p 병합에 필요")
            self.ffmpeg_label.setStyleSheet("color: #b45309; font-size: 9pt;")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.analyze_btn.setEnabled(not busy)
        self.url_edit.setEnabled(not busy)
        self.browse_btn.setEnabled(not busy)
        self.table.setEnabled(not busy)
        if busy:
            self.download_btn.setEnabled(False)
        else:
            self._on_selection_changed()

    def _set_indeterminate(self, enabled: bool) -> None:
        if enabled:
            self.progress.setRange(0, 0)
            self.progress.setFormat("분석 중...")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("%p%")

    def _selected_format(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _on_url_edited(self) -> None:
        if self._busy or not self._analyzed_url:
            return
        if self.url_edit.text().strip() != self._analyzed_url:
            self._clear_analysis()
            self.status_label.setText("대기 중")

    def _clear_analysis(self) -> None:
        self._analyzed_url = ""
        self.table.setRowCount(0)
        self.title_label.setText("영상을 분석하면 제목과 포맷이 여기에 표시됩니다.")
        self.thumb_label.setPixmap(QPixmap())
        self.thumb_label.setText("썸네일")
        self.download_btn.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

    def choose_folder(self) -> None:
        current = self.path_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", current)
        if selected:
            self.path_edit.setText(selected)

    def start_analyze(self) -> None:
        if self._busy:
            return
        url = self.url_edit.text().strip()
        invalid = self._validate_url(url)
        if invalid:
            QMessageBox.critical(self, "잘못된 URL", invalid)
            return

        self._clear_analysis()
        self.status_label.setText("분석 중...")
        self._set_indeterminate(True)
        self._set_busy(True)

        worker = AnalyzeWorker(url)
        worker.succeeded.connect(self._on_analyzed)
        worker.failed.connect(self._on_analyze_failed)
        worker.finished.connect(self._on_analyze_thread_finished)
        self._analyze_thread = worker
        worker.start()

    def _validate_url(self, url: str) -> str | None:
        if not url:
            return "유튜브 링크를 입력하세요.\n예: https://www.youtube.com/watch?v=xxxxxxxxxxx"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return (
                "http 또는 https로 시작하는 올바른 링크를 입력하세요.\n"
                "예: https://www.youtube.com/watch?v=xxxxxxxxxxx"
            )
        return None

    def _on_analyze_thread_finished(self) -> None:
        self._analyze_thread = None

    def _on_analyzed(self, payload: dict) -> None:
        self._set_indeterminate(False)
        self._analyzed_url = self.url_edit.text().strip()
        self._show_video_info(payload)
        self._fill_table(payload.get("formats") or [])
        count = self.table.rowCount()
        self._set_busy(False)
        self.status_label.setText(f"분석 완료 · 포맷 {count}개 중 하나를 선택하세요")
        if count:
            self.table.selectRow(0)
        if payload.get("is_live"):
            QMessageBox.warning(
                self,
                "실시간 스트리밍",
                "실시간 스트리밍 영상입니다.\n다운로드가 실패하거나 일부만 저장될 수 있습니다.",
            )

    def _on_analyze_failed(self, message: str) -> None:
        self._set_indeterminate(False)
        self._set_busy(False)
        self.status_label.setText("대기 중")
        QMessageBox.critical(self, "분석 실패", message)

    def _show_video_info(self, payload: dict) -> None:
        title = payload.get("title") or "제목 없음"
        channel = payload.get("channel") or ""
        duration = format_duration(payload.get("duration"))
        meta = "  ·  ".join(part for part in (channel, duration) if part)
        self.title_label.setText(f"{title}\n{meta}" if meta else title)

        data = payload.get("thumbnail")
        if isinstance(data, (bytes, bytearray)):
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                scaled = pixmap.scaled(
                    self.thumb_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                self.thumb_label.setText("")
                self.thumb_label.setPixmap(scaled)
                return
        self.thumb_label.setPixmap(QPixmap())
        self.thumb_label.setText("썸네일 없음")

    def _fill_table(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        alignments = [
            Qt.AlignCenter,
            Qt.AlignCenter,
            Qt.AlignCenter,
            Qt.AlignCenter,
            Qt.AlignRight | Qt.AlignVCenter,
            Qt.AlignLeft | Qt.AlignVCenter,
        ]
        for row_index, row in enumerate(rows):
            values = [
                row["format_id"],
                row["kind_label"],
                row["quality"],
                row["ext"],
                row["size_text"],
                row["note"],
            ]
            if row["tier"] == "고화질":
                color = COLOR_HIGH
            elif row["kind"] == "audio":
                color = COLOR_AUDIO
            else:
                color = COLOR_NORMAL
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(alignments[col])
                item.setBackground(color)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                if col == 0:
                    item.setData(Qt.UserRole, row)
                self.table.setItem(row_index, col, item)
            self.table.setRowHeight(row_index, 28)

    def _on_selection_changed(self) -> None:
        fmt = self._selected_format()
        if self._busy or fmt is None or not self._analyzed_url:
            self.download_btn.setEnabled(False)
            return
        self.download_btn.setEnabled(True)
        if fmt["needs_merge"]:
            self.hint_label.setText(
                "선택한 포맷은 영상 전용입니다. 다운로드 시 가장 좋은 음원과 합쳐 mp4로 저장합니다. ffmpeg가 필요합니다."
            )
        elif fmt["kind"] == "audio":
            self.hint_label.setText("오디오 전용 포맷입니다. 음원 파일만 저장됩니다.")
        else:
            self.hint_label.setText("영상과 음원이 함께 들어 있는 포맷입니다.")

    def start_download(self) -> None:
        if self._busy:
            return
        fmt = self._selected_format()
        url = self._analyzed_url
        if not url or fmt is None:
            QMessageBox.critical(self, "다운로드 실패", "먼저 영상을 분석하고 포맷을 선택하세요.")
            return

        save_dir = self.path_edit.text().strip()
        if not save_dir or not Path(save_dir).is_dir():
            QMessageBox.critical(
                self,
                "다운로드 실패",
                "저장 폴더를 찾을 수 없습니다.\n폴더 선택으로 실제 폴더를 지정해 주세요.",
            )
            return

        self._update_ffmpeg_label()
        needs_merge = bool(fmt["needs_merge"])
        if needs_merge and not ffmpeg_available():
            QMessageBox.critical(self, "ffmpeg 필요", FFMPEG_HELP)
            return

        if needs_merge:
            # 오디오가 없는 DASH 영상은 최고 음원과 합쳐 받는다.
            format_spec = f"{fmt['format_id']}+bestaudio/best"
        else:
            format_spec = fmt["format_id"]

        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText("다운로드 중 (계산 중, 남은 시간 계산 중)")
        self._set_busy(True)

        worker = DownloadWorker(
            url,
            save_dir,
            format_spec,
            needs_merge,
            ffmpeg_location_arg(),
        )
        worker.progress.connect(self._on_download_progress)
        worker.succeeded.connect(self._on_download_succeeded)
        worker.failed.connect(self._on_download_failed)
        worker.finished.connect(self._on_download_thread_finished)
        self._download_thread = worker
        worker.start()

    def _on_download_thread_finished(self) -> None:
        self._download_thread = None

    def _on_download_progress(self, payload: dict) -> None:
        percent = payload.get("percent")
        if isinstance(percent, (int, float)):
            self.progress.setRange(0, 100)
            self.progress.setValue(int(percent))
        text = payload.get("text")
        if text:
            self.status_label.setText(text)

    def _on_download_succeeded(self, path: str) -> None:
        self._set_busy(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status_label.setText("완료")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("다운로드 완료")
        box.setText("다운로드가 완료되었습니다.")
        shown = path if path else self.path_edit.text().strip()
        box.setInformativeText(shown)
        open_btn = box.addButton("폴더 열기", QMessageBox.ActionRole)
        box.addButton("확인", QMessageBox.AcceptRole)
        box.exec_()
        if box.clickedButton() is open_btn:
            open_save_location(path)

    def _on_download_failed(self, message: str) -> None:
        self._set_busy(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status_label.setText("대기 중")
        QMessageBox.critical(self, "다운로드 실패", message)

    def closeEvent(self, event) -> None:
        running = any(
            thread is not None and thread.isRunning()
            for thread in (self._analyze_thread, self._download_thread)
        )
        if running:
            answer = QMessageBox.question(
                self,
                "종료",
                "작업이 진행 중입니다. 창을 닫으면 작업이 중단될 수 있습니다.\n종료할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


def main() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        QMessageBox.critical(
            None,
            "의존성 없음",
            "yt-dlp가 설치되어 있지 않습니다.\n\n"
            "이 폴더에서 다음을 실행한 뒤 다시 시작해 주세요.\n\n"
            "pip install -r requirements.txt",
        )
        sys.exit(1)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
