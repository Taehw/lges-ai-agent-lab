# -*- coding: utf-8 -*-
"""PDF 분할 및 병합 데스크톱 애플리케이션.

실행:
    pip install -r requirements.txt
    python app.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import traceback
from collections import OrderedDict
from pathlib import Path

# 창만 있는 실행 파일에서는 콘솔이 없어 stdout/stderr 가 None 이다.
# 라이브러리가 로그를 찍는 순간 종료되지 않도록 비워 둔다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 썸네일은 Poppler 없이 동작하는 PyMuPDF를 사용한다.
# 라이브러리가 없어도 분할/병합 자체는 가능하도록 분리한다.
try:
    import fitz
except ImportError:
    fitz = None

from pypdf import PdfReader, PdfWriter
from pypdf.errors import FileNotDecryptedError, PdfReadError
from PyQt5.QtCore import QEvent, QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# 목록 항목에 경로, 비밀번호, 페이지 수를 함께 보관한다.
ROLE_PATH = Qt.UserRole
ROLE_PASSWORD = Qt.UserRole + 1
ROLE_PAGES = Qt.UserRole + 2

TILE_WIDTH = 148
TILE_IMAGE_SIZE = (132, 176)
GRID_SPACING = 12
THUMB_TARGET_WIDTH = 280


class PdfToolError(Exception):
    """사용자에게 그대로 안내할 수 있는 처리 오류."""


class EncryptedPdfError(PdfToolError):
    """비밀번호가 필요하거나 일치하지 않을 때."""


class InvalidPageSpecError(PdfToolError):
    """페이지 범위/번호 입력이 잘못되었을 때."""


class CancelledError(Exception):
    """사용자가 진행 중인 저장 작업을 중단했을 때."""


def _normalize_page_text(text: str) -> str:
    """전각 쉼표, 물결표를 하이픈 표기로 통일한다."""
    return (
        text.replace("～", "-")
        .replace("~", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("，", ",")
        .replace("、", ",")
    )


def _parse_token(token: str, page_count: int) -> list[int]:
    """'1-5' 또는 '3' 하나를 0부터 세는 페이지 인덱스 목록으로 바꾼다."""
    matched = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", token.strip())
    if not matched:
        raise InvalidPageSpecError(
            f"인식할 수 없는 형식입니다: '{token}'\n"
            "숫자와 하이픈(-)만 사용할 수 있습니다.\n"
            "예: 1-5, 8-10"
        )

    start = int(matched.group(1))
    end = int(matched.group(2)) if matched.group(2) is not None else start
    if start < 1 or end < 1:
        raise InvalidPageSpecError(f"페이지 번호는 1부터 시작합니다. ('{token}')")
    if start > page_count or end > page_count:
        raise InvalidPageSpecError(
            f"'{token}' 범위가 전체 페이지 수({page_count})를 초과합니다."
        )
    if start > end:
        raise InvalidPageSpecError(
            f"'{token}'에서 시작 페이지가 끝 페이지보다 큽니다."
        )
    # 끝 번호는 포함해야 하므로 stop 은 1-based end 와 같다.
    return list(range(start - 1, end))


def parse_page_groups(text: str, page_count: int) -> list[list[int]]:
    """쉼표로 구분된 구간을 각각 하나의 묶음으로 반환한다.

    범위 분할에서는 묶음마다 파일이 하나씩 만들어진다.
    """
    if page_count < 1:
        raise InvalidPageSpecError("페이지가 없는 PDF입니다.")

    normalized = _normalize_page_text(text).strip()
    if not normalized:
        raise InvalidPageSpecError("입력값이 비어 있습니다.\n예: 1-5, 8-10")

    tokens = [part.strip() for part in normalized.split(",") if part.strip()]
    if not tokens:
        raise InvalidPageSpecError("입력값이 비어 있습니다.\n예: 1-5, 8-10")
    return [_parse_token(token, page_count) for token in tokens]


def parse_selected_pages(text: str, page_count: int) -> list[int]:
    """지정한 페이지만, 적은 순서를 유지하고 중복은 제거한다."""
    ordered: list[int] = []
    seen: set[int] = set()
    for group in parse_page_groups(text, page_count):
        for index in group:
            if index in seen:
                continue
            seen.add(index)
            ordered.append(index)
    if not ordered:
        raise InvalidPageSpecError("추출할 페이지가 없습니다.")
    return ordered


def parse_fixed_groups(page_count: int, size: int) -> list[list[int]]:
    """N페이지씩 잘라 여러 묶음으로 나눈다. 마지막 묶음은 남은 페이지만 담는다."""
    if page_count < 1:
        raise InvalidPageSpecError("페이지가 없는 PDF입니다.")
    if size < 1:
        raise InvalidPageSpecError("분할 단위는 1페이지 이상이어야 합니다.")

    groups: list[list[int]] = []
    for start in range(0, page_count, size):
        groups.append(list(range(start, min(start + size, page_count))))
    return groups


def compress_page_numbers(pages_1based: list[int]) -> str:
    """[1, 2, 3, 8, 10] 을 '1-3, 8, 10' 형태로 줄인다."""
    pages = sorted(set(pages_1based))
    if not pages:
        return ""

    ranges: list[tuple[int, int]] = []
    start = previous = pages[0]
    for number in pages[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append((start, previous))
        start = previous = number
    ranges.append((start, previous))

    parts = [str(start) if start == end else f"{start}-{end}" for start, end in ranges]
    return ", ".join(parts)


def open_reader(path: str, password: str | None = None) -> tuple[PdfReader, str | None]:
    """PDF를 열고 (리더, 실제 사용한 비밀번호)를 반환한다.

    암호가 없으면 비밀번호는 None이다.
    빈 비밀번호로 열리는 문서는 '' 를 반환해 미리보기에도 같은 값을 쓴다.
    """
    if not os.path.isfile(path):
        raise PdfToolError(f"파일을 찾을 수 없습니다.\n{path}")

    try:
        reader = PdfReader(path, strict=False)
    except PermissionError as exc:
        raise PdfToolError("파일을 읽을 권한이 없습니다.") from exc
    except PdfReadError as exc:
        raise PdfToolError(
            "PDF를 열 수 없습니다. 손상되었거나 PDF 형식이 아닙니다."
        ) from exc
    except OSError as exc:
        raise PdfToolError(f"파일을 읽는 중 오류가 발생했습니다.\n{exc}") from exc

    if not reader.is_encrypted:
        return reader, None

    # 비밀번호를 아직 모르면 빈 암호를 먼저 시도한다.
    trial = "" if password is None else password
    try:
        decrypted = int(reader.decrypt(trial))
    except PdfReadError as exc:
        reader.close()
        raise PdfToolError("비밀번호를 확인하는 중 오류가 발생했습니다.") from exc

    if decrypted == 0:
        reader.close()
        if password is None:
            raise EncryptedPdfError("비밀번호가 필요합니다.")
        raise EncryptedPdfError("비밀번호가 올바르지 않습니다.")
    return reader, trial


def read_pdf_info(path: str, password: str | None = None) -> tuple[int, str | None]:
    """페이지 수를 확인하고 파일 잠금을 바로 풀어 준다."""
    reader, used_password = open_reader(path, password)
    try:
        try:
            page_count = len(reader.pages)
        except FileNotDecryptedError as exc:
            raise EncryptedPdfError(
                "비밀번호가 올바르지 않거나 문서가 잠겨 있습니다."
            ) from exc
        except PdfReadError as exc:
            raise PdfToolError(
                "PDF 페이지를 읽지 못했습니다. 파일이 손상되었을 수 있습니다."
            ) from exc
    finally:
        reader.close()

    if page_count < 1:
        raise PdfToolError("페이지가 없는 PDF입니다.")
    return page_count, used_password


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def reserve_output_path(directory: Path, filename: str, reserved: set[Path]) -> Path:
    """같은 폴더에 같은 이름이 있으면 _2, _3 을 붙여 덮어쓰지 않는다."""
    candidate = directory / filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix or ".pdf"
    number = 2
    while candidate in reserved or candidate.exists():
        candidate = directory / f"{stem}_{number}{suffix}"
        number += 1
    reserved.add(candidate)
    return candidate


def build_split_filenames(mode: str, stem: str, groups: list[list[int]]) -> list[str]:
    """분할 방식에 맞는 저장 파일 이름을 만든다. 페이지 번호는 1부터 표시한다."""
    names: list[str] = []
    multiple = len(groups) > 1
    for part, group in enumerate(groups, start=1):
        first = group[0] + 1
        last = group[-1] + 1
        if mode == "pages":
            names.append(f"{stem}_extract.pdf")
        elif mode == "fixed":
            names.append(f"{stem}_part{part:02d}_p{first}-{last}.pdf")
        elif len(group) == 1:
            names.append(f"{stem}_p{first}.pdf")
        elif multiple or group == list(range(group[0], group[-1] + 1)):
            names.append(f"{stem}_p{first}-{last}.pdf")
        else:
            names.append(f"{stem}_extract.pdf")
    return names


def export_page_groups(
    source: str,
    password: str | None,
    groups: list[list[int]],
    destinations: list[Path],
    progress=None,
    is_cancelled=None,
) -> list[str]:
    """각 페이지 묶음을 개별 PDF로 저장한다."""
    if not groups or len(groups) != len(destinations):
        raise PdfToolError("저장할 페이지 구성이 올바르지 않습니다.")

    source_abs = os.path.abspath(source)
    for destination in destinations:
        if _same_path(destination, source_abs):
            raise PdfToolError(
                "원본 PDF와 같은 경로에는 저장할 수 없습니다. 다른 이름을 사용하세요."
            )

    reader, _used = open_reader(source, password)
    written: list[str] = []
    try:
        page_count = len(reader.pages)
        for group in groups:
            if not group or any(index < 0 or index >= page_count for index in group):
                raise InvalidPageSpecError("페이지 번호가 문서 범위를 벗어났습니다.")

        total = len(groups)
        for number, (group, destination) in enumerate(zip(groups, destinations), start=1):
            if is_cancelled and is_cancelled():
                raise CancelledError()

            destination.parent.mkdir(parents=True, exist_ok=True)
            writer = PdfWriter()
            try:
                for index in group:
                    writer.add_page(reader.pages[index])
                with open(destination, "wb") as handle:
                    writer.write(handle)
            finally:
                writer.close()
            written.append(str(destination))
            if progress:
                progress(number, total)
    finally:
        reader.close()
    return written


def merge_documents(
    items: list[tuple[str, str | None]],
    destination: Path,
    progress=None,
    is_cancelled=None,
) -> str:
    """목록 순서대로 페이지를 이어 하나의 PDF로 저장한다."""
    if len(items) < 2:
        raise PdfToolError("병합할 PDF를 2개 이상 추가하세요.")

    for path, _password in items:
        if _same_path(path, destination):
            raise PdfToolError(
                "저장 파일이 병합할 원본과 같습니다. 다른 이름으로 저장하세요."
            )

    # 페이지 내용은 저장 시점에 읽히므로, 쓰기가 끝날 때까지 원본 리더를 닫지 않는다.
    writer = PdfWriter()
    readers: list[PdfReader] = []
    try:
        total = len(items)
        for number, (path, password) in enumerate(items, start=1):
            if is_cancelled and is_cancelled():
                raise CancelledError()
            reader, _used = open_reader(path, password)
            readers.append(reader)
            if len(reader.pages) < 1:
                raise PdfToolError(f"페이지가 없는 파일입니다.\n{os.path.basename(path)}")
            for page in reader.pages:
                writer.add_page(page)
            if progress:
                progress(number, total)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "wb") as handle:
            writer.write(handle)
    finally:
        writer.close()
        for reader in readers:
            reader.close()
    return str(destination)


def pdf_paths_from_urls(urls) -> list[str]:
    """드롭된 주소 가운데 실제 PDF 파일만 고른다."""
    paths: list[str] = []
    for url in urls:
        if not url.isLocalFile():
            continue
        local = url.toLocalFile()
        if os.path.isfile(local) and local.lower().endswith(".pdf"):
            paths.append(os.path.normpath(local))
    return paths


def open_folder(folder: str) -> None:
    """저장된 파일이 있는 폴더를 연다."""
    try:
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        else:
            subprocess.run(["xdg-open", folder], check=False)
    except OSError:
        pass


def build_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(Qt.white)
    painter.drawRoundedRect(6, 4, 52, 56, 6, 6)
    painter.setBrush(QColor("#1D4ED8"))
    painter.drawRoundedRect(6, 4, 52, 16, 6, 6)
    painter.drawRect(6, 12, 52, 8)
    painter.setPen(QColor("#1D4ED8"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(18)
    painter.setFont(font)
    painter.drawText(pixmap.rect().adjusted(0, 10, 0, 0), Qt.AlignCenter, "PDF")
    painter.end()
    return QIcon(pixmap)


class ThumbnailWorker(QThread):
    """페이지 이미지를 백그라운드에서 만들어 UI가 멈추지 않게 한다."""

    page_ready = pyqtSignal(int, int, bytes)
    progress = pyqtSignal(int, int, int)
    failed = pyqtSignal(int, str)

    def __init__(self, path: str, password: str | None, job_id: int):
        super().__init__()
        self.path = path
        self.password = password
        self.job_id = job_id
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        if fitz is None:
            self.failed.emit(
                self.job_id,
                "미리보기를 사용하려면 PyMuPDF 패키지가 필요합니다.\n"
                "명령 프롬프트에서 pip install PyMuPDF 를 실행하세요.",
            )
            return

        document = None
        try:
            document = fitz.open(self.path)
            if document.needs_pass and not document.authenticate(self.password or ""):
                self.failed.emit(self.job_id, "미리보기를 열 수 없습니다. 비밀번호가 올바르지 않습니다.")
                return

            total = document.page_count
            for index in range(total):
                if self._cancel:
                    return
                page = document.load_page(index)
                zoom = min(2.0, THUMB_TARGET_WIDTH / max(float(page.rect.width), 1.0))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                self.page_ready.emit(self.job_id, index, pixmap.tobytes("png"))
                self.progress.emit(self.job_id, index + 1, total)
        except Exception as exc:  # 미리보기 실패가 분할 기능까지 막지 않도록 한다.
            if not self._cancel:
                self.failed.emit(self.job_id, f"미리보기를 만드는 중 오류가 발생했습니다.\n{exc}")
        finally:
            if document is not None:
                document.close()


class JobWorker(QThread):
    """분할 또는 병합 저장을 백그라운드에서 실행한다."""

    progress = pyqtSignal(int, int)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, task):
        super().__init__()
        self._task = task
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = self._task(self.progress.emit, self._is_cancelled)
            self.succeeded.emit(result)
        except CancelledError:
            self.failed.emit("작업이 취소되었습니다. 이미 저장된 파일이 남아 있을 수 있습니다.")
        except PdfToolError as exc:
            self.failed.emit(str(exc))
        except PermissionError:
            self.failed.emit(
                "파일을 저장할 권한이 없습니다.\n다른 폴더를 선택하거나 파일이 열려 있는지 확인하세요."
            )
        except OSError as exc:
            self.failed.emit(f"파일을 저장하는 중 오류가 발생했습니다.\n{exc}")
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"예상하지 못한 오류가 발생했습니다.\n{exc}")

    def _is_cancelled(self) -> bool:
        return self._cancel


class DropZone(QFrame):
    """PDF를 끌어다 놓거나 버튼으로 불러오는 영역."""

    files_dropped = pyqtSignal(list)
    browse_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(148)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)

        title = QLabel("PDF 끌어다 놓기")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignCenter)
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        hint = QLabel("또는 버튼을 눌러 파일을 선택하세요")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.browse_button = QPushButton("PDF 불러오기")
        self.browse_button.setObjectName("primary")
        self.browse_button.setCursor(Qt.PointingHandCursor)
        self.browse_button.setMinimumHeight(36)
        self.browse_button.clicked.connect(self.browse_clicked.emit)
        # 버튼 위에서 놓아도 드롭 영역이 받도록 한다.
        self.browse_button.installEventFilter(self)
        self.browse_button.setAcceptDrops(True)

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.browse_button, alignment=Qt.AlignCenter)

    def eventFilter(self, watched, event):
        if watched is self.browse_button and event.type() in (
            QEvent.DragEnter,
            QEvent.DragMove,
            QEvent.Drop,
        ):
            if event.type() == QEvent.DragEnter:
                self.dragEnterEvent(event)
            elif event.type() == QEvent.DragMove:
                self.dragMoveEvent(event)
            else:
                self.dropEvent(event)
            return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        if pdf_paths_from_urls(event.mimeData().urls()):
            self.setProperty("hover", "true")
            self._refresh_style()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if pdf_paths_from_urls(event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setProperty("hover", "false")
        self._refresh_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.setProperty("hover", "false")
        self._refresh_style()
        paths = pdf_paths_from_urls(event.mimeData().urls())
        if not paths:
            event.ignore()
            return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def _refresh_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class MergeList(QListWidget):
    """순서를 바꾸고 파일을 떨어뜨려 추가할 수 있는 병합 목록."""

    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setDropIndicatorShown(True)
        self.setAlternatingRowColors(False)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if pdf_paths_from_urls(event.mimeData().urls()):
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            if pdf_paths_from_urls(event.mimeData().urls()):
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = pdf_paths_from_urls(event.mimeData().urls())
            if not paths:
                event.ignore()
                return
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class PageTile(QFrame):
    """썸네일 한 장과 페이지 번호."""

    clicked = pyqtSignal(int)

    def __init__(self, page_no: int, parent=None):
        super().__init__(parent)
        self.page_no = page_no
        self._has_image = False
        self._selectable = True
        self.setObjectName("tile")
        self.setProperty("selected", "false")
        self.setFixedWidth(TILE_WIDTH)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.image = QLabel("불러오는 중")
        self.image.setObjectName("thumb")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setFixedSize(*TILE_IMAGE_SIZE)
        self.image.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.caption = QLabel(f"Page {page_no}")
        self.caption.setObjectName("caption")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        layout.addWidget(self.image, alignment=Qt.AlignCenter)
        layout.addWidget(self.caption)

    @property
    def has_image(self) -> bool:
        return self._has_image

    def set_selectable(self, enabled: bool) -> None:
        self._selectable = enabled
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            self.image.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image.setPixmap(scaled)
        self.image.setText("")
        self._has_image = True

    def mousePressEvent(self, event):
        if self._selectable and event.button() == Qt.LeftButton:
            self.clicked.emit(self.page_no)
        super().mousePressEvent(event)


class ThumbnailBoard(QWidget):
    """스크롤 안에 들어가는 페이지 타일 격자."""

    page_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tiles: list[PageTile] = []
        self._selected: set[int] = set()
        self._columns = 0
        self._selectable = True
        self._fitting = False
        self.setObjectName("board")
        # 가로 길이는 뷰포트에 맞추고, 세로는 타일 줄 수만큼 늘어나게 한다.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setHorizontalSpacing(GRID_SPACING)
        self._grid.setVerticalSpacing(GRID_SPACING)
        self._grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tiles.clear()
        self._selected.clear()
        self._columns = 0

    def set_page_count(self, count: int) -> None:
        self.clear()
        for page_no in range(1, count + 1):
            tile = PageTile(page_no)
            tile.set_selectable(self._selectable)
            tile.clicked.connect(self._on_tile_clicked)
            self._tiles.append(tile)
        self._columns = 0
        self.fit_width(self._viewport_width())

    def minimumSizeHint(self):
        # 격자 최소 너비가 칸 수를 다시 늘리지 않도록 타일 한 장 기준으로 고정한다.
        return QSize(TILE_WIDTH + 16, 160)

    def fit_width(self, width: int) -> None:
        """보이는 너비 안에 타일이 잘리지 않도록 열 수를 다시 계산한다."""
        if self._fitting:
            return
        self._fitting = True
        try:
            self._apply_width(width)
            # 세로 스크롤바가 생기면 뷰포트가 좁아지므로 한 번 더 맞춘다.
            parent = self.parentWidget()
            scroll = parent.parentWidget() if parent is not None else None
            if isinstance(scroll, QScrollArea):
                updated = scroll.viewport().width()
                if abs(updated - int(width)) > 2:
                    self._apply_width(updated)
        finally:
            self._fitting = False

    def _apply_width(self, width: int) -> None:
        usable = max(int(width), TILE_WIDTH + 16)
        self._relayout(usable)
        if abs(self.width() - usable) > 1:
            self.setFixedWidth(usable)

    def _viewport_width(self) -> int:
        parent = self.parentWidget()
        viewport = parent.parentWidget() if parent is not None else None
        if isinstance(viewport, QScrollArea):
            return viewport.viewport().width()
        return max(self.width(), TILE_WIDTH + 16)

    def set_pixmap(self, index: int, pixmap: QPixmap) -> None:
        if 0 <= index < len(self._tiles):
            self._tiles[index].set_pixmap(pixmap)

    def loaded_count(self) -> int:
        return sum(1 for tile in self._tiles if tile.has_image)

    def tile_count(self) -> int:
        return len(self._tiles)

    def selected_pages(self) -> list[int]:
        return sorted(self._selected)

    def set_selection_enabled(self, enabled: bool) -> None:
        self._selectable = enabled
        for tile in self._tiles:
            tile.set_selectable(enabled)

    def set_selected_pages(self, pages_1based: set[int]) -> None:
        valid = {page for page in pages_1based if 1 <= page <= len(self._tiles)}
        self._selected = valid
        for tile in self._tiles:
            tile.set_selected(tile.page_no in valid)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._fitting:
            self._relayout(event.size().width())

    def _on_tile_clicked(self, page_no: int) -> None:
        if page_no in self._selected:
            self._selected.remove(page_no)
        else:
            self._selected.add(page_no)
        self._tiles[page_no - 1].set_selected(page_no in self._selected)
        self.page_clicked.emit(page_no)

    def _relayout(self, width: int) -> None:
        if not self._tiles:
            return
        usable = max(int(width) - 8, TILE_WIDTH)
        columns = 1
        used = TILE_WIDTH
        while used + GRID_SPACING + TILE_WIDTH <= usable:
            columns += 1
            used += GRID_SPACING + TILE_WIDTH
        if columns == self._columns:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, tile in enumerate(self._tiles):
            self._grid.addWidget(tile, index // columns, index % columns)


class PreviewScroll(QScrollArea):
    """썸네일 보드를 뷰포트 너비에 맞춰 다시 배치하는 스크롤 영역."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        board = self.widget()
        if isinstance(board, ThumbnailBoard):
            board.fit_width(self.viewport().width())

    def showEvent(self, event):
        super().showEvent(event)
        board = self.widget()
        if isinstance(board, ThumbnailBoard):
            board.fit_width(self.viewport().width())


class ElidedLabel(QLabel):
    """가로 폭에 맞춰 가운데를 생략하고, 전체 내용은 툴팁으로 보여 준다."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setMinimumWidth(40)
        self.setText(text)
        if text:
            self.setToolTip(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._refresh()

    def sizeHint(self):
        # 긴 경로는 툴팁으로만 보여주고, 칸 너비는 부모 레이아웃을 따른다.
        return QSize(120, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(40, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(self.contentsRect().width(), 40)
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideMiddle, width))


class MainWindow(QMainWindow):
    """분할 탭과 병합 탭을 가진 메인 창."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF 분할 · 병합")
        self.setWindowIcon(build_app_icon())
        self.resize(1220, 780)
        self.setMinimumSize(980, 640)
        self.setAcceptDrops(True)

        self._split_path: str | None = None
        self._split_password: str | None = None
        self._split_pages = 0
        self._split_selection: set[int] = set()
        self._preview_path: str | None = None
        self._preview_key = None
        self._thumb_job = 0
        self._thumb_worker: ThumbnailWorker | None = None
        self._thumb_threads: list[ThumbnailWorker] = []
        self._thumb_cache: OrderedDict = OrderedDict()
        self._incoming: list[QPixmap | None] = []
        self._job_worker: JobWorker | None = None
        self._busy = False
        self._last_dir = os.path.expanduser("~")

        self._build_ui()
        self._apply_style()
        self._bind_shortcuts()
        self._on_mode_changed()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 20, 14)
        header_layout.setSpacing(2)
        title = QLabel("PDF 분할 · 병합")
        title.setObjectName("title")
        subtitle = QLabel("페이지를 나누거나, 여러 PDF를 원하는 순서대로 하나의 파일로 합칩니다.")
        subtitle.setObjectName("subtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        outer.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 10)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_file_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.addWidget(self._build_tool_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([300, 620, 340])
        body_layout.addWidget(splitter)
        outer.addWidget(body, 1)

        status = QFrame()
        status.setObjectName("statusFrame")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(16, 10, 16, 10)
        status_layout.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFixedHeight(8)
        self.status_label = QLabel("준비됨")
        self.status_label.setObjectName("statusText")
        self.status_label.setMinimumWidth(240)
        status_layout.addWidget(self.progress, 1)
        status_layout.addWidget(self.status_label, 0)
        outer.addWidget(status)

    def _build_file_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sideCard")
        panel.setMinimumWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        section = QLabel("문서 정보")
        section.setObjectName("section")
        layout.addWidget(section)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._load_split_paths)
        self.drop_zone.browse_clicked.connect(self._browse_split_file)
        layout.addWidget(self.drop_zone)

        self.name_label = ElidedLabel("선택된 파일 없음")
        self.name_label.setObjectName("metaValue")
        self.page_label = QLabel("-")
        self.page_label.setObjectName("metaValue")
        self.path_label = ElidedLabel("-")
        self.path_label.setObjectName("metaValue")

        layout.addLayout(self._info_row("파일명", self.name_label))
        layout.addLayout(self._info_row("페이지 수", self.page_label))
        layout.addLayout(self._info_row("파일 경로", self.path_label))

        tip = QLabel("분할할 PDF를 불러온 뒤, 가운데 썸네일을 클릭해 페이지를 고를 수 있습니다.")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return panel

    def _info_row(self, caption: str, value: QWidget) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(2)
        key = QLabel(caption)
        key.setObjectName("metaKey")
        column.addWidget(key)
        column.addWidget(value)
        return column

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sideCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.preview_title = QLabel("페이지 미리보기")
        self.preview_title.setObjectName("section")
        self.preview_meta = QLabel("파일을 불러오면 페이지가 표시됩니다.")
        self.preview_meta.setObjectName("hint")
        title_box.addWidget(self.preview_title)
        title_box.addWidget(self.preview_meta)
        header.addLayout(title_box, 1)

        self.apply_selection_button = QPushButton("선택 반영")
        self.select_all_button = QPushButton("전체 선택")
        self.clear_selection_button = QPushButton("선택 해제")
        for button in (
            self.apply_selection_button,
            self.select_all_button,
            self.clear_selection_button,
        ):
            button.setCursor(Qt.PointingHandCursor)
            header.addWidget(button)
        self.apply_selection_button.setToolTip(
            "고른 페이지를 오른쪽 분할 입력란에 넣습니다."
        )
        layout.addLayout(header)

        self.preview_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        self.empty_label = QLabel("PDF를 불러오면 페이지 미리보기가 여기에 표시됩니다.")
        self.empty_label.setObjectName("emptyHint")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        empty_layout.addWidget(self.empty_label)

        self.board = ThumbnailBoard()
        self.board.page_clicked.connect(self._on_page_clicked)
        scroll = PreviewScroll()
        scroll.setWidget(self.board)

        self.preview_stack.addWidget(empty)
        self.preview_stack.addWidget(scroll)
        layout.addWidget(self.preview_stack, 1)
        return panel

    def _build_tool_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sideCard")
        panel.setMinimumWidth(320)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_split_page(), "분할 모드")
        self.tabs.addTab(self._build_merge_page(), "병합 모드")
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setDocumentMode(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)
        return panel

    def _build_split_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 12, 10, 8)
        layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setStyleSheet("background: #FFFFFF;")
        inner = QWidget()
        inner.setObjectName("formPage")
        form = QVBoxLayout(inner)
        form.setContentsMargins(2, 2, 2, 2)
        form.setSpacing(8)

        form.addWidget(self._section_label("분할 방식"))
        self.range_radio = QRadioButton("범위 입력")
        self.pages_radio = QRadioButton("페이지 지정")
        self.fixed_radio = QRadioButton("고정 페이지 단위")
        self.range_radio.setChecked(True)
        for radio in (self.range_radio, self.pages_radio, self.fixed_radio):
            radio.toggled.connect(self._on_mode_changed)
            form.addWidget(radio)

        self.mode_stack = QStackedWidget()
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("예: 1-5, 8-10")
        self.range_edit.setClearButtonEnabled(True)
        range_page = QWidget()
        range_layout = QVBoxLayout(range_page)
        range_layout.setContentsMargins(0, 8, 0, 0)
        range_layout.addWidget(self.range_edit)
        self.mode_stack.addWidget(range_page)

        self.pages_edit = QLineEdit()
        self.pages_edit.setPlaceholderText("예: 1, 3, 5, 7")
        self.pages_edit.setClearButtonEnabled(True)
        self.pages_edit.editingFinished.connect(self._on_pages_edited)
        pages_page = QWidget()
        pages_layout = QVBoxLayout(pages_page)
        pages_layout.setContentsMargins(0, 8, 0, 0)
        pages_layout.addWidget(self.pages_edit)
        self.mode_stack.addWidget(pages_page)

        self.fixed_spin = QSpinBox()
        self.fixed_spin.setRange(1, 10000)
        self.fixed_spin.setValue(2)
        self.fixed_spin.setSuffix(" 페이지씩")
        fixed_page = QWidget()
        fixed_layout = QVBoxLayout(fixed_page)
        fixed_layout.setContentsMargins(0, 8, 0, 0)
        fixed_label = QLabel("한 파일에 넣을 페이지 수")
        fixed_label.setObjectName("metaKey")
        fixed_layout.addWidget(fixed_label)
        fixed_layout.addWidget(self.fixed_spin)
        self.mode_stack.addWidget(fixed_page)
        form.addWidget(self.mode_stack)

        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("hint")
        self.mode_hint.setWordWrap(True)
        form.addWidget(self.mode_hint)
        form.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        self.target_label = QLabel("대상 파일 없음")
        self.target_label.setObjectName("hint")
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        self.split_button = QPushButton("분할 실행")
        self.split_button.setObjectName("primary")
        self.split_button.setCursor(Qt.PointingHandCursor)
        self.split_button.setMinimumHeight(40)
        self.split_button.clicked.connect(self._run_split)
        layout.addWidget(self.split_button)

        self.apply_selection_button.clicked.connect(self._apply_selection_to_input)
        self.select_all_button.clicked.connect(self._select_all_pages)
        self.clear_selection_button.clicked.connect(self._clear_selected_pages)
        return page

    def _build_merge_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 12, 10, 8)
        layout.setSpacing(8)

        hint = QLabel("목록 위에서 아래 순서로 합쳐집니다. 파일을 끌어다 놓거나 순서를 바꿀 수 있습니다.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.merge_list = MergeList()
        self.merge_list.files_dropped.connect(self._add_merge_paths)
        self.merge_list.currentItemChanged.connect(self._on_merge_current_changed)
        layout.addWidget(self.merge_list, 1)

        row_file = QHBoxLayout()
        self.add_button = QPushButton("파일 추가")
        self.remove_button = QPushButton("선택 삭제")
        row_move = QHBoxLayout()
        self.up_button = QPushButton("위로")
        self.down_button = QPushButton("아래로")
        for button in (self.add_button, self.remove_button, self.up_button, self.down_button):
            button.setCursor(Qt.PointingHandCursor)
        row_file.addWidget(self.add_button)
        row_file.addWidget(self.remove_button)
        row_move.addWidget(self.up_button)
        row_move.addWidget(self.down_button)
        layout.addLayout(row_file)
        layout.addLayout(row_move)

        self.add_button.clicked.connect(self._browse_merge_files)
        self.remove_button.clicked.connect(self._remove_merge_files)
        self.up_button.clicked.connect(lambda: self._move_merge(-1))
        self.down_button.clicked.connect(lambda: self._move_merge(1))

        self.merge_button = QPushButton("병합 실행")
        self.merge_button.setObjectName("primary")
        self.merge_button.setCursor(Qt.PointingHandCursor)
        self.merge_button.setMinimumHeight(40)
        self.merge_button.clicked.connect(self._run_merge)
        layout.addWidget(self.merge_button)
        return page

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    def _bind_shortcuts(self) -> None:
        shortcut = QShortcut(QKeySequence.Open, self)
        shortcut.activated.connect(self._shortcut_open)
        delete_shortcut = QShortcut(QKeySequence.Delete, self)
        delete_shortcut.activated.connect(self._remove_merge_files)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                color: #0F172A;
                font-size: 13px;
            }
            QWidget#root {
                background: #F1F5F9;
            }
            QFrame#header {
                background: #FFFFFF;
                border-bottom: 1px solid #E2E8F0;
            }
            QLabel#title {
                font-size: 20px;
                font-weight: 700;
                color: #0F172A;
            }
            QLabel#subtitle, QLabel#hint, QLabel#metaKey, QLabel#statusText {
                color: #64748B;
            }
            QLabel#subtitle { font-size: 12px; }
            QLabel#section {
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#metaValue {
                font-weight: 600;
            }
            QLabel#emptyHint {
                color: #94A3B8;
                font-size: 14px;
            }
            QFrame#sideCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
            }
            QFrame#statusFrame {
                background: #FFFFFF;
                border-top: 1px solid #E2E8F0;
            }
            QFrame#dropZone {
                background: #F8FAFC;
                border: 2px dashed #CBD5E1;
                border-radius: 12px;
            }
            QFrame#dropZone[hover="true"] {
                background: #EFF6FF;
                border-color: #2563EB;
            }
            QLabel#dropTitle {
                font-size: 15px;
                font-weight: 700;
            }
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 8px;
                padding: 7px 12px;
            }
            QPushButton:hover {
                background: #F8FAFC;
                border-color: #94A3B8;
            }
            QPushButton:disabled {
                color: #94A3B8;
                background: #F8FAFC;
            }
            QPushButton#primary {
                background: #1D4ED8;
                color: #FFFFFF;
                border: none;
                font-weight: 700;
            }
            QPushButton#primary:hover {
                background: #1E40AF;
            }
            QPushButton#primary:disabled {
                background: #93C5FD;
                color: #EFF6FF;
            }
            QTabWidget::pane {
                border: none;
                background: transparent;
                top: 0;
            }
            QTabBar::tab {
                background: transparent;
                color: #64748B;
                font-weight: 700;
                padding: 10px 16px;
                border: none;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected {
                color: #1D4ED8;
                border-bottom: 2px solid #1D4ED8;
            }
            QTabBar::tab:hover {
                color: #0F172A;
            }
            QLineEdit, QSpinBox, QListWidget {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QLineEdit:focus, QSpinBox:focus {
                border: 1px solid #2563EB;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 6px;
            }
            QListWidget::item:selected {
                background: #DBEAFE;
                color: #1E3A8A;
            }
            QRadioButton {
                spacing: 8px;
                padding: 2px 0;
            }
            QWidget#board {
                background: #F8FAFC;
            }
            QWidget#formPage {
                background: #FFFFFF;
            }
            QFrame#tile {
                background: #FFFFFF;
                border: 2px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#tile[selected="true"] {
                background: #EFF6FF;
                border: 2px solid #2563EB;
            }
            QFrame#tile[selected="true"] QLabel#caption {
                color: #1D4ED8;
                font-weight: 700;
            }
            QLabel#thumb {
                background: #F1F5F9;
                border-radius: 6px;
                color: #94A3B8;
            }
            QLabel#caption {
                color: #475569;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QProgressBar {
                background: #E2E8F0;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background: #2563EB;
                border-radius: 4px;
            }
            QSplitter::handle {
                background: transparent;
            }
            """
        )

    def _shortcut_open(self) -> None:
        if self.tabs.currentIndex() == 1:
            self._browse_merge_files()
        else:
            self._browse_split_file()

    def _on_tab_changed(self, index: int) -> None:
        selection_on = index == 0 and not self._busy
        self.board.set_selection_enabled(index == 0)
        self.select_all_button.setEnabled(selection_on)
        self.clear_selection_button.setEnabled(selection_on)
        if index == 0:
            if self._split_path:
                self._show_document_info(self._split_path, self._split_pages)
                self._show_preview(self._split_path, self._split_password, self._split_pages)
                self.board.set_selected_pages(self._split_selection)
                self._update_selection_meta()
            else:
                self._show_document_info(None, 0)
                self._clear_preview("PDF를 불러오면 페이지 미리보기가 여기에 표시됩니다.")
        else:
            current = self.merge_list.currentItem()
            if current is not None:
                self._show_merge_item(current)
            else:
                self._show_document_info(None, 0)
                self._clear_preview("병합 목록에서 파일을 선택하면 미리보기가 표시됩니다.")
        # 고정 단위 모드에서는 '선택 반영'을 켜지 않는다.
        self._on_mode_changed()

    def _current_mode(self) -> str:
        if self.pages_radio.isChecked():
            return "pages"
        if self.fixed_radio.isChecked():
            return "fixed"
        return "range"

    def _on_mode_changed(self, _checked: bool = False) -> None:
        if not hasattr(self, "mode_hint"):
            return
        # 해제되는 라디오에서도 신호가 오므로, 선택된 항목만 반영한다.
        if self.sender() is not None and isinstance(self.sender(), QRadioButton):
            if not self.sender().isChecked():
                return
        mode = self._current_mode()
        self.mode_stack.setCurrentIndex({"range": 0, "pages": 1, "fixed": 2}[mode])
        hints = {
            "range": "쉼표로 구간을 구분합니다. 각 구간이 개별 PDF로 저장됩니다. 예: 1-5, 8-10",
            "pages": "추출할 페이지를 쉼표로 입력합니다. 적은 순서대로 하나의 PDF로 저장됩니다. 예: 1, 3, 5, 7",
            "fixed": "앞에서부터 N페이지씩 잘라 여러 파일로 저장합니다. 마지막 파일은 남은 페이지만 담습니다.",
        }
        self.mode_hint.setText(hints[mode])
        self.apply_selection_button.setEnabled(mode != "fixed" and self.tabs.currentIndex() == 0 and not self._busy)

    def dragEnterEvent(self, event):
        if self._busy:
            event.ignore()
            return
        if pdf_paths_from_urls(event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = pdf_paths_from_urls(event.mimeData().urls())
        if self._busy or not paths:
            event.ignore()
            return
        if self.tabs.currentIndex() == 1:
            self._add_merge_paths(paths)
        else:
            self._load_split_paths(paths)
        event.acceptProposedAction()

    def _browse_split_file(self) -> None:
        if self._busy:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "분할할 PDF 선택",
            self._last_dir,
            "PDF 파일 (*.pdf)",
        )
        if path:
            self._load_split_paths([path])

    def _load_split_paths(self, paths: list[str]) -> None:
        if self._busy:
            self.status_label.setText("작업이 끝난 뒤 파일을 불러오세요.")
            return
        if not paths:
            QMessageBox.warning(self, "파일 없음", "불러올 PDF 파일이 없습니다.")
            return
        if len(paths) > 1:
            QMessageBox.information(
                self,
                "파일 불러오기",
                "분할 모드에서는 한 번에 하나의 PDF만 엽니다.\n"
                "첫 번째 파일을 불러옵니다.\n"
                "여러 파일을 합치려면 병합 모드를 사용하세요.",
            )
        self._load_split_file(paths[0])

    def _load_split_file(self, path: str) -> None:
        resolved = self._resolve_pdf(path)
        if resolved is None:
            return
        page_count, password = resolved
        self._split_path = os.path.abspath(path)
        self._split_password = password
        self._split_pages = page_count
        self._split_selection.clear()
        self.pages_edit.clear()
        self._last_dir = os.path.dirname(self._split_path)
        self.target_label.setText(f"대상: {os.path.basename(self._split_path)}  ·  {page_count}페이지")
        self.target_label.setToolTip(self._split_path)
        # 이미 분할 탭이면 currentChanged 가 오지 않으므로 직접 미리보기를 연다.
        if self.tabs.currentIndex() != 0:
            self.tabs.setCurrentIndex(0)
        else:
            self._on_tab_changed(0)
        self.status_label.setText(f"불러옴: {os.path.basename(self._split_path)} ({page_count}페이지)")

    def _resolve_pdf(self, path: str) -> tuple[int, str | None] | None:
        """암호 문서는 비밀번호를 물어 본 뒤 페이지 수를 돌려준다. 취소하면 None."""
        try:
            return read_pdf_info(path, None)
        except EncryptedPdfError:
            pass
        except PdfToolError as exc:
            QMessageBox.warning(self, "파일을 열 수 없음", str(exc))
            return None

        for _attempt in range(3):
            password, accepted = QInputDialog.getText(
                self,
                "비밀번호 필요",
                f"'{os.path.basename(path)}' 파일은 비밀번호로 보호되어 있습니다.\n비밀번호를 입력하세요.",
                QLineEdit.Password,
            )
            if not accepted:
                return None
            try:
                return read_pdf_info(path, password)
            except EncryptedPdfError:
                QMessageBox.warning(
                    self,
                    "비밀번호 오류",
                    "비밀번호가 올바르지 않습니다. 다시 입력해 주세요.",
                )
            except PdfToolError as exc:
                QMessageBox.warning(self, "파일을 열 수 없음", str(exc))
                return None

        QMessageBox.warning(self, "파일을 열 수 없음", "비밀번호를 3회 잘못 입력했습니다.")
        return None

    def _show_document_info(self, path: str | None, page_count: int) -> None:
        if not path:
            self.name_label.set_full_text("선택된 파일 없음")
            self.page_label.setText("-")
            self.path_label.set_full_text("-")
            return
        self.name_label.set_full_text(os.path.basename(path))
        self.page_label.setText(f"{page_count}페이지")
        self.path_label.set_full_text(path)

    def _clear_preview(self, message: str) -> None:
        self._thumb_job += 1
        if self._thumb_worker is not None:
            self._thumb_worker.cancel()
        self._preview_path = None
        self._preview_key = None
        self._incoming = []
        self.board.clear()
        self.preview_stack.setCurrentIndex(0)
        self.empty_label.setText(message)
        self.preview_title.setText("페이지 미리보기")
        self.preview_meta.setText(message)
        self._reset_progress()

    def _show_preview(self, path: str, password: str | None, page_count: int) -> None:
        key = self._cache_key(path)
        if key is None:
            self._clear_preview("파일을 찾을 수 없어 미리보기를 표시할 수 없습니다.")
            return
        # 같은 문서를 다시 열면 진행 중인 썸네일 작업을 끊지 않는다.
        if self._preview_key == key and self.board.tile_count() == page_count and page_count > 0:
            self.preview_title.setText(os.path.basename(path))
            self.preview_stack.setCurrentIndex(1)
            return

        self._preview_path = os.path.abspath(path)
        self._preview_key = key
        self.preview_title.setText(os.path.basename(path))
        self.preview_meta.setText(f"{page_count}페이지")
        self.board.set_page_count(page_count)
        self.board.set_selection_enabled(self.tabs.currentIndex() == 0)
        if self.tabs.currentIndex() == 0:
            self.board.set_selected_pages(self._split_selection)
        self.preview_stack.setCurrentIndex(1)
        self._update_selection_meta()

        cached = self._thumb_cache.get(key)
        if cached and len(cached) == page_count:
            self._thumb_cache.move_to_end(key)
            for index, pixmap in enumerate(cached):
                self.board.set_pixmap(index, pixmap)
            self._reset_progress()
            self.status_label.setText(f"미리보기 준비 완료 · {page_count}페이지")
            return

        self._incoming = [None] * page_count
        self._thumb_job += 1
        if self._thumb_worker is not None:
            self._thumb_worker.cancel()
        worker = ThumbnailWorker(path, password, self._thumb_job)
        worker.page_ready.connect(self._on_thumb_ready)
        worker.progress.connect(self._on_thumb_progress)
        worker.failed.connect(self._on_thumb_failed)
        worker.finished.connect(lambda finished=worker: self._discard_thumb_worker(finished))
        self._thumb_threads.append(worker)
        self._thumb_worker = worker
        self.progress.setRange(0, max(page_count, 1))
        self.progress.setValue(0)
        self.status_label.setText("썸네일을 만드는 중...")
        worker.start()

    def _cache_key(self, path: str):
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return (os.path.normcase(os.path.abspath(path)), stat.st_mtime_ns, stat.st_size)

    def _on_thumb_ready(self, job_id: int, index: int, png: bytes) -> None:
        if job_id != self._thumb_job:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(png, "PNG"):
            return
        self.board.set_pixmap(index, pixmap)
        if 0 <= index < len(self._incoming):
            self._incoming[index] = pixmap
            if all(image is not None for image in self._incoming) and self._preview_key is not None:
                self._thumb_cache[self._preview_key] = list(self._incoming)
                while len(self._thumb_cache) > 4:
                    self._thumb_cache.popitem(last=False)

    def _on_thumb_progress(self, job_id: int, current: int, total: int) -> None:
        if job_id != self._thumb_job or self._busy:
            return
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.status_label.setText(f"썸네일 생성 중 {current}/{total}")
        if current >= total:
            self.status_label.setText(f"미리보기 준비 완료 · {total}페이지")

    def _on_thumb_failed(self, job_id: int, message: str) -> None:
        if job_id != self._thumb_job:
            return
        self.status_label.setText("미리보기를 만들지 못했습니다.")
        QMessageBox.warning(self, "미리보기 오류", message)

    def _discard_thumb_worker(self, worker: ThumbnailWorker) -> None:
        if worker in self._thumb_threads:
            self._thumb_threads.remove(worker)
        if self._thumb_worker is worker:
            self._thumb_worker = None
        worker.deleteLater()

    def _on_page_clicked(self, _page_no: int) -> None:
        if self.tabs.currentIndex() != 0 or self._preview_path != self._split_path:
            return
        self._split_selection = set(self.board.selected_pages())
        self.pages_edit.setText(", ".join(str(page) for page in sorted(self._split_selection)))
        self._update_selection_meta()

    def _update_selection_meta(self) -> None:
        if self.tabs.currentIndex() != 0 or not self._split_path:
            return
        count = len(self._split_selection)
        base = f"{self._split_pages}페이지"
        if count:
            self.preview_meta.setText(f"{base} · 선택 {count}페이지")
        else:
            self.preview_meta.setText(base)

    def _select_all_pages(self) -> None:
        if self.tabs.currentIndex() != 0 or self._split_pages < 1:
            return
        self._split_selection = set(range(1, self._split_pages + 1))
        self.board.set_selected_pages(self._split_selection)
        self.pages_edit.setText(", ".join(str(page) for page in range(1, self._split_pages + 1)))
        self._update_selection_meta()

    def _clear_selected_pages(self) -> None:
        if self.tabs.currentIndex() != 0:
            return
        self._split_selection.clear()
        self.board.set_selected_pages(set())
        self.pages_edit.clear()
        self._update_selection_meta()

    def _apply_selection_to_input(self) -> None:
        if not self._split_selection:
            QMessageBox.information(self, "페이지 선택", "먼저 썸네일에서 페이지를 선택하세요.")
            return
        pages = sorted(self._split_selection)
        mode = self._current_mode()
        if mode == "range":
            self.range_edit.setText(compress_page_numbers(pages))
        elif mode == "pages":
            self.pages_edit.setText(", ".join(str(page) for page in pages))
        else:
            QMessageBox.information(
                self,
                "고정 단위 분할",
                "고정 페이지 단위는 선택한 페이지가 아니라 입력한 개수만큼 나눕니다.",
            )

    def _on_pages_edited(self) -> None:
        text = self.pages_edit.text().strip()
        if not text or self._split_pages < 1:
            return
        try:
            indexes = parse_selected_pages(text, self._split_pages)
        except InvalidPageSpecError as exc:
            self.status_label.setText(str(exc).splitlines()[0])
            return
        self._split_selection = {index + 1 for index in indexes}
        if self.tabs.currentIndex() == 0 and self._preview_path == self._split_path:
            self.board.set_selected_pages(self._split_selection)
            self._update_selection_meta()

    def _run_split(self) -> None:
        if self._busy:
            return
        if not self._split_path:
            QMessageBox.warning(self, "파일 없음", "분할할 PDF 파일을 먼저 불러오세요.")
            return
        if not os.path.isfile(self._split_path):
            QMessageBox.warning(self, "파일 없음", "원본 PDF를 찾을 수 없습니다. 다시 불러오세요.")
            return

        mode = self._current_mode()
        try:
            if mode == "range":
                groups = parse_page_groups(self.range_edit.text(), self._split_pages)
            elif mode == "pages":
                groups = [parse_selected_pages(self.pages_edit.text(), self._split_pages)]
            else:
                groups = parse_fixed_groups(self._split_pages, self.fixed_spin.value())
        except InvalidPageSpecError as exc:
            QMessageBox.warning(self, "페이지 입력 오류", str(exc))
            return

        destinations = self._ask_split_destinations(mode, groups)
        if not destinations:
            return

        source = self._split_path
        password = self._split_password

        def task(report, is_cancelled):
            return export_page_groups(
                source,
                password,
                groups,
                destinations,
                progress=report,
                is_cancelled=is_cancelled,
            )

        self._start_job(task, len(groups), "분할을 시작합니다...", "분할 완료", "분할한 PDF를 저장했습니다.")

    def _ask_split_destinations(self, mode: str, groups: list[list[int]]) -> list[Path] | None:
        stem = Path(self._split_path or "output").stem
        names = build_split_filenames(mode, stem, groups)
        base_dir = self._last_dir or os.path.expanduser("~")

        if len(groups) == 1:
            default = str(Path(base_dir) / names[0])
            chosen, _filter = QFileDialog.getSaveFileName(
                self,
                "분할 파일 저장",
                default,
                "PDF 파일 (*.pdf)",
            )
            if not chosen:
                return None
            if not chosen.lower().endswith(".pdf"):
                chosen += ".pdf"
            destination = Path(chosen)
            if _same_path(destination, self._split_path or ""):
                QMessageBox.warning(
                    self,
                    "저장할 수 없음",
                    "원본 PDF와 같은 경로에는 저장할 수 없습니다. 다른 이름을 사용하세요.",
                )
                return None
            self._last_dir = str(destination.parent)
            return [destination]

        directory = QFileDialog.getExistingDirectory(self, "분할 파일을 저장할 폴더", base_dir)
        if not directory:
            return None
        reserved: set[Path] = set()
        folder = Path(directory)
        paths = [reserve_output_path(folder, name, reserved) for name in names]
        self._last_dir = directory
        return paths

    def _browse_merge_files(self) -> None:
        if self._busy:
            return
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "병합할 PDF 추가",
            self._last_dir,
            "PDF 파일 (*.pdf)",
        )
        if paths:
            self._add_merge_paths(paths)

    def _add_merge_paths(self, paths: list[str]) -> None:
        if self._busy:
            self.status_label.setText("작업이 끝난 뒤 파일을 추가하세요.")
            return
        if not paths:
            QMessageBox.warning(self, "파일 없음", "추가할 PDF 파일이 없습니다.")
            return

        added = 0
        for path in paths:
            resolved = self._resolve_pdf(path)
            if resolved is None:
                continue
            page_count, password = resolved
            absolute = os.path.abspath(path)
            item = QListWidgetItem(f"{os.path.basename(absolute)}    ·    {page_count}페이지")
            item.setData(ROLE_PATH, absolute)
            item.setData(ROLE_PASSWORD, password)
            item.setData(ROLE_PAGES, page_count)
            item.setToolTip(absolute)
            self.merge_list.addItem(item)
            added += 1
            self._last_dir = os.path.dirname(absolute)

        if added:
            self.merge_list.setCurrentRow(self.merge_list.count() - 1)
            if self.tabs.currentIndex() != 1:
                self.tabs.setCurrentIndex(1)
            else:
                current = self.merge_list.currentItem()
                if current is not None:
                    self._show_merge_item(current)
            self.status_label.setText(f"병합 목록 {self.merge_list.count()}개")
        elif self.merge_list.count() == 0:
            self.status_label.setText("추가된 파일이 없습니다.")

    def _remove_merge_files(self) -> None:
        if self._busy or self.tabs.currentIndex() != 1:
            return
        selected = self.merge_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "선택 없음", "삭제할 파일을 목록에서 선택하세요.")
            return
        for item in selected:
            row = self.merge_list.row(item)
            self.merge_list.takeItem(row)
        if self.merge_list.count() == 0:
            self._show_document_info(None, 0)
            self._clear_preview("병합 목록에서 파일을 선택하면 미리보기가 표시됩니다.")
        self.status_label.setText(f"병합 목록 {self.merge_list.count()}개")

    def _move_merge(self, delta: int) -> None:
        if self._busy:
            return
        row = self.merge_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "선택 없음", "순서를 바꿀 파일을 선택하세요.")
            return
        target = row + delta
        if target < 0 or target >= self.merge_list.count():
            return
        item = self.merge_list.takeItem(row)
        self.merge_list.insertItem(target, item)
        self.merge_list.setCurrentRow(target)

    def _on_merge_current_changed(self, current, _previous) -> None:
        if self.tabs.currentIndex() != 1 or current is None:
            return
        self._show_merge_item(current)

    def _show_merge_item(self, item: QListWidgetItem) -> None:
        path = item.data(ROLE_PATH)
        password = item.data(ROLE_PASSWORD)
        pages = int(item.data(ROLE_PAGES) or 0)
        self._show_document_info(path, pages)
        self._show_preview(path, password, pages)
        self.preview_meta.setText(f"{pages}페이지 · 병합 미리보기")

    def _merge_items(self) -> list[tuple[str, str | None]]:
        items: list[tuple[str, str | None]] = []
        for row in range(self.merge_list.count()):
            item = self.merge_list.item(row)
            items.append((item.data(ROLE_PATH), item.data(ROLE_PASSWORD)))
        return items

    def _run_merge(self) -> None:
        if self._busy:
            return
        items = self._merge_items()
        if len(items) < 2:
            QMessageBox.warning(self, "파일 부족", "병합할 PDF를 2개 이상 추가하세요.")
            return
        missing = [path for path, _password in items if not os.path.isfile(path)]
        if missing:
            QMessageBox.warning(
                self,
                "파일 없음",
                "목록에 더 이상 없는 파일이 있습니다.\n" + "\n".join(missing[:5]),
            )
            return

        first_stem = Path(items[0][0]).stem
        default = str(Path(self._last_dir) / f"{first_stem}_merged.pdf")
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "병합 파일 저장",
            default,
            "PDF 파일 (*.pdf)",
        )
        if not chosen:
            return
        if not chosen.lower().endswith(".pdf"):
            chosen += ".pdf"
        destination = Path(chosen)
        if any(_same_path(path, destination) for path, _password in items):
            QMessageBox.warning(
                self,
                "저장할 수 없음",
                "저장 파일이 병합할 원본과 같습니다. 다른 이름으로 저장하세요.",
            )
            return
        self._last_dir = str(destination.parent)

        def task(report, is_cancelled):
            return [merge_documents(items, destination, progress=report, is_cancelled=is_cancelled)]

        self._start_job(task, len(items), "병합을 시작합니다...", "병합 완료", "PDF를 하나로 합쳤습니다.")

    def _start_job(self, task, total: int, start_message: str, title: str, summary: str) -> None:
        if self._job_worker is not None and self._job_worker.isRunning():
            QMessageBox.information(self, "작업 중", "다른 파일 작업이 끝날 때까지 기다려 주세요.")
            return
        self._job_title = title
        self._job_summary = summary
        self._set_busy(True)
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(0)
        self.status_label.setText(start_message)
        worker = JobWorker(task)
        worker.progress.connect(self._on_job_progress)
        worker.succeeded.connect(self._on_job_succeeded)
        worker.failed.connect(self._on_job_failed)
        worker.finished.connect(worker.deleteLater)
        self._job_worker = worker
        worker.start()

    def _on_job_progress(self, current: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.status_label.setText(f"처리 중... {current}/{total}")

    def _on_job_succeeded(self, result) -> None:
        self._set_busy(False)
        paths = [str(path) for path in result]
        self.progress.setValue(self.progress.maximum())
        self.status_label.setText("작업이 완료되었습니다.")
        self._notify_saved(self._job_title, self._job_summary, paths)

    def _on_job_failed(self, message: str) -> None:
        self._set_busy(False)
        self._reset_progress()
        self.status_label.setText("작업을 완료하지 못했습니다.")
        QMessageBox.critical(self, "작업 실패", message)

    def _notify_saved(self, title: str, summary: str, paths: list[str]) -> None:
        shown = paths[:8]
        extra = f"\n... 외 {len(paths) - 8}개" if len(paths) > 8 else ""
        message = f"{summary} ({len(paths)}개)\n\n" + "\n".join(shown) + extra
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(message)
        open_button = box.addButton("폴더 열기", QMessageBox.ActionRole)
        box.addButton("확인", QMessageBox.AcceptRole)
        box.exec_()
        if box.clickedButton() is open_button and paths:
            open_folder(os.path.dirname(os.path.abspath(paths[0])))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (
            self.split_button,
            self.merge_button,
            self.add_button,
            self.remove_button,
            self.up_button,
            self.down_button,
            self.drop_zone.browse_button,
            self.apply_selection_button,
            self.select_all_button,
            self.clear_selection_button,
        ):
            button.setEnabled(not busy)
        if not busy:
            self._on_mode_changed()
            if self.tabs.currentIndex() == 1:
                self.apply_selection_button.setEnabled(False)
                self.select_all_button.setEnabled(False)
                self.clear_selection_button.setEnabled(False)

    def _reset_progress(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)

    def closeEvent(self, event):
        if self._job_worker is not None and self._job_worker.isRunning():
            answer = QMessageBox.question(
                self,
                "종료",
                "파일 작업이 진행 중입니다. 종료할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._job_worker.cancel()
            self._job_worker.wait(3000)
        if self._thumb_worker is not None and self._thumb_worker.isRunning():
            self._thumb_worker.cancel()
            self._thumb_worker.wait(1500)
        event.accept()


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Malgun Gothic", 10))
    app.setWindowIcon(build_app_icon())
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
