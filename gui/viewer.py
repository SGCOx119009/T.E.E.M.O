"""gui/viewer.py — the viewer: what the machine sees, and what the hands are doing.

WHAT THIS IS
    A panel cut in two, the way a broadcasting desk is cut in two.

    ON THE LEFT, THE CAMERA. Not a picture of the user from last Tuesday:
    the camera that is open right now, mirrored the way a mirror shows it,
    so that moving a hand to the right moves it to the right here too.

    ON THE RIGHT, THE SCREEN. The picture the assistant is working on, and
    the name of the window in front. This is the part that used to be a
    nothing: the interface showed a camera frame for six seconds when the
    model happened to send one, and nothing at all the rest of the time.

    UNDERNEATH, THE HANDS. The pose the tracker read, the gesture that
    pose means, whether the button is down and where the pointer is. The
    point of this strip is that a gesture can be CHECKED: the user can see
    that the machine read the hand the way the user meant it, and correct
    it when it did not.

WHY IT IS A MODULE OF ITS OWN
    gui/window.py was already four thousand lines of layout. The viewer is
    a self-contained instrument with its own workers, and keeping it here
    means the window only has to hold it.

THE THREADS
    Both sources are slow — a screen capture takes tens of milliseconds and
    a camera frame takes longer — so neither is ever read on the Qt thread.
    Each worker owns a thread, publishes through a signal, and is stopped
    by the panel that owns it.
"""
from __future__ import annotations

import io
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout,
    QWidget,
)

from core.localization import t as _t
from gui.style import px_font
from gui.theme import C
from gui.widgets import (GestureReadout, HoloPanel, IconButton, ObservationCanvas,
                         SegmentedRail)

# How often the screen is looked at. Fast enough to feel alive, slow enough
# not to fight the assistant for the same pixels.
_SCREEN_PERIOD = 1.5
# How often the camera is read. Twelve frames a second is enough to check
# a gesture and cheap enough to leave running.
_CAMERA_PERIOD = 0.08


class ScreenWatcher(QObject):
    """Reads the screen and the window in front, off the Qt thread."""

    frame_sig = pyqtSignal(bytes, str)      # picture, caption
    state_sig = pyqtSignal(str)             # a sentence for the strip

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="screen-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            if thread.is_alive():
                thread.join(timeout=3.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                picture, caption = self._read_once()
            except Exception as exc:                       # noqa: BLE001
                self.state_sig.emit(f"the screen could not be read: "
                                     f"{type(exc).__name__}")
                picture, caption = b"", ""
            if picture:
                self.frame_sig.emit(picture, caption)
            elif caption:
                self.state_sig.emit(caption)
            self._stop.wait(_SCREEN_PERIOD)

    def _read_once(self) -> tuple[bytes, str]:
        """One picture of the screen and one line about what is in front.

        THE ACCESSIBILITY TREE IS NOT READ HERE, AND THAT IS DELIBERATE.
            The tree is COM: UI Automation objects belong to the apartment
            of the thread that created them, and this is a worker thread.
            Reading the tree from here put a COM object in an apartment
            that was never initialised, and the process died with an
            access violation — the same crash as two cameras, and the same
            kind of answer.

            So the picture is a capture (no COM) and the name of the
            window in front is read with the plain Windows API (no COM).
            What the viewer shows is what the user is looking at, and the
            tree stays where it belongs: in the thread that owns it, when
            the assistant really needs to read a control.
        """
        from core import screenview

        caption = ""
        try:
            app, title = screenview.foreground_window()
            if app or title:
                caption = f"{app} — {title}" if title else str(app)
        except Exception:
            caption = ""

        image, _monitor, _scale = screenview.capture()
        buf = io_bytes(image)
        return buf, caption


def io_bytes(image) -> bytes:
    """A PIL image as PNG bytes."""
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class CameraWatcher(QObject):
    """Reads the camera, off the Qt thread."""

    frame_sig = pyqtSignal(object)
    state_sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # THE SUBSCRIPTION ENDS WITH THE WIDGET.
        #
        # The hub of the camera keeps a list of readers, and a reader is
        # this object. If the window goes without saying goodbye, the hub
        # would keep calling a widget that is not there any more. It was
        # measured: a window destroyed left one reader alive in the hub.
        self.destroyed.connect(self._forget)

    def _forget(self) -> None:
        """Let go of the subscription when this widget is destroyed."""
        try:
            from core.camera import unsubscribe as _unsubscribe
            _unsubscribe(self._on_frame)
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="camera-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            if thread.is_alive():
                thread.join(timeout=3.0)
            self._thread = None

    def _loop(self) -> None:
        """Read the camera OF THE PROCESS and publish the frames.

        The camera is not opened here: it is the one of `gui/camera.py`,
        and this is one more reader of it. Two handles to the same device
        is what made the process die with an access violation.
        """
        try:
            from core.camera import hub as _camera_hub
            hub = _camera_hub()
            index = self._camera_index()
            if index is not None:
                hub.set_index(index)
            hub.subscribe(self._on_frame)
            # A KEY, NOT A SENTENCE.
            #
            # The state used to be an English sentence written here, and it
            # was shown in the panel as it was: "the camera is open", in a
            # product whose language is Spanish. The sentence now comes
            # from the catalog, in the language of the user.
            self.state_sig.emit("viewer.camera_open")
            slow = False
            while not self._stop.is_set():
                time.sleep(0.05)
                if hub.error:
                    self.state_sig.emit(hub.error)
                    continue
                # THE CAMERA IS BUSY, AND THE PROGRAM SAYS SO.
                #
                # When another application keeps the device, the driver
                # hands out one picture a second instead of fifteen, and
                # the hand on the screen trails behind by a whole second
                # with nobody explaining why. Measured: 15 fps with the
                # device free, 1 fps with it held.
                busy = hub.busy
                if busy and not slow:
                    slow = True
                    self.state_sig.emit("viewer.camera_busy")
                elif not busy and slow:
                    slow = False
                    self.state_sig.emit("viewer.camera_open")
        except Exception as exc:                           # noqa: BLE001
            self.state_sig.emit(f"viewer.camera_stopped:{type(exc).__name__}")
        finally:
            try:
                from core.camera import unsubscribe as _unsubscribe
                _unsubscribe(self._on_frame)
            except Exception:
                pass

    def _on_frame(self, ok: bool, jpeg: bytes) -> None:
        """One frame of the camera, as a picture.

        THE BYTES GO AS THEY ARE. The picture is already a JPEG —it
        was encoded in the process that owns the camera— and the
        interface knows how to draw a JPEG. Encoding it again here, in
        the thread that was reading, was work that nobody had asked
        for.

        BUT IT IS UNPACKED HERE, AND NOT ON THE SCREEN. Unpacking a JPEG
        is a piece of work of its own, and doing it on the thread that
        draws the interface meant that the interface stopped drawing
        while the picture was being unpacked. A QImage can be unpacked
        on any thread; only the painting of it has to happen on the one
        that owns the screen.
        """
        if not ok or not jpeg:
            return
        try:
            from PyQt6.QtGui import QImage

            image = QImage()
            if not image.loadFromData(jpeg):
                return
            self.frame_sig.emit(image)
        except Exception:
            pass

    @staticmethod
    def _camera_index():
        """Which camera the user picked, or None when nobody picked one."""
        try:
            from memory.config_manager import get_camera_index
            return get_camera_index()
        except Exception:
            return None


class ViewerPanel(QWidget):
    """The panel: camera on the left, the screen on the right, the hands below.

    It is a HoloPanel so that it looks like the rest of the interface, and
    it carries its own close button because it is a page of the stack under
    the head, not a window of its own.

    IT HAS TWO TABS. The first is the view: what the camera sees and what
    the machine can read on the screen. The second is the questionnaire,
    which used to be a panel of its own in the middle of the interface
    asking questions about nothing. Now the two are side by side, and the
    user can watch the screen while the assistant asks.
    """

    def __init__(self, parent=None, quiz=None):
        super().__init__(parent)
        self._panel = HoloPanel(_t("panel.viewer"), "", parent=self)
        self._panel.setObjectName("ViewerPanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._panel)

        self._title = _panel_text(self._panel, "title")
        self._code = _panel_text(self._panel, "code")

        close = IconButton("close", 22)
        close.setToolTip(_t("window.dismiss"))
        close.clicked.connect(self._on_close)
        self._panel.add_header_widget(close)

        body = self._panel.body
        body.setSpacing(8)

        # THE TABS.
        self._rail = SegmentedRail([
            ("view", _t("viewer.tab_view")),
            ("quiz", _t("viewer.tab_quiz")),
        ])
        self._rail.changed.connect(self._select_page)
        body.addWidget(self._rail)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")

        # THE TWO HALVES.
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self._camera = ObservationCanvas()
        self._camera.set_mirrored(True)
        self._camera.set_placeholder(_t("viewer.camera_wait"))
        self._camera.set_badge(_t("viewer.camera"), live=False)
        # Whether another program is keeping the device. The badge of the
        # tile says so while the pictures keep arriving.
        self._camera_busy = False
        grid.addWidget(self._camera, 0, 0)

        self._screen = ObservationCanvas()
        self._screen.set_placeholder(_t("viewer.screen_wait"))
        self._screen.set_badge(_t("viewer.screen"), live=False)
        grid.addWidget(self._screen, 0, 1)

        self._hands = GestureReadout()
        self._hands.set_title(_t("viewer.hands"))
        self._hands.set_rows([(_t("viewer.tracker"), "--", "TEXT_DIM"),
                              (_t("viewer.pose"), "--", "TEXT_DIM"),
                              (_t("viewer.action"), "--", "TEXT_DIM")])
        grid.addWidget(self._hands, 1, 0, 1, 2)

        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        grid.setRowStretch(0, 1)

        view_page = QWidget()
        view_page.setStyleSheet("background: transparent;")
        view_lay = QVBoxLayout(view_page)
        view_lay.setContentsMargins(0, 0, 0, 0)
        view_lay.addLayout(grid)
        self._stack.addWidget(view_page)

        self._quiz = quiz
        if quiz is not None:
            self._stack.addWidget(quiz)

        body.addWidget(self._stack, stretch=1)

        self._workers = ScreenWatcher(self)
        self._camera_worker = CameraWatcher(self)
        self._workers.frame_sig.connect(self._on_screen_frame)
        self._workers.state_sig.connect(self._on_state)
        self._camera_worker.frame_sig.connect(self._on_camera_frame)
        self._camera_worker.state_sig.connect(self._on_camera_state)

        self._started = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    # -- the tabs -------------------------------------------------------------

    def _select_page(self, key: str) -> None:
        """The view, or the questionnaire."""
        key = str(key)
        if key == "quiz" and self._quiz is not None:
            self._stack.setCurrentIndex(1)
            self._title.setText(_t("panel.quiz"))
        else:
            self._stack.setCurrentIndex(0)
            self._title.setText(_t("panel.viewer"))
        self._rail.set_current(key)

    def show_view(self) -> None:
        """The tab of the two pictures."""
        self._select_page("view")

    def show_quiz(self) -> None:
        """The tab of the questionnaire."""
        if self._quiz is None:
            return
        self._select_page("quiz")

    # -- the outside world ---------------------------------------------------

    def _on_close(self) -> None:
        """The mouse closes the viewer: it is a page, and the page goes back."""
        parent = self.parent()
        hide = getattr(parent, "_hide_viewer", None)
        if callable(hide):
            hide()

    def start(self) -> None:
        """Open the camera and the eye."""
        if self._started:
            return
        self._started = True
        self._workers.start()
        self._camera_worker.start()

    def stop(self) -> None:
        """Close the camera and the eye.

        The camera of the process is NOT closed here: the hands may be
        watching it too, and the hub knows how many readers it has. What
        stops is this reader's part of it.
        """
        if not self._started:
            return
        self._started = False
        self._workers.stop()
        self._camera_worker.stop()

    def _on_screen_frame(self, data: bytes, caption: str) -> None:
        if self._screen.set_frame(data):
            self._screen.set_caption(caption)
            self._screen.set_badge(_t("viewer.screen_live"), live=True)

    def _on_camera_frame(self, data: bytes) -> None:
        if self._camera.set_frame(data):
            if getattr(self, "_camera_busy", False):
                # The badge keeps saying what is wrong, instead of being
                # painted over by the next picture that arrives.
                self._camera.set_badge(_t("viewer.camera_busy"), live=True)
            else:
                self._camera.set_badge(_t("viewer.camera_live"), live=True)

    def _on_state(self, text: str) -> None:
        self._screen.set_caption(text)

    def _on_camera_state(self, text: str) -> None:
        """Say what the camera is doing, in the language of the user.

        A key of the catalog comes back translated; anything else — the
        words the driver itself uses — is shown as it is, because a
        technical message that is mistranslated is worse than one that is
        in English.
        """
        raw = str(text or "")
        key, sep, detail = raw.partition(":")
        shown = _t(key) if key.startswith("viewer.") else raw
        if sep and detail.strip():
            shown = f"{shown} · {detail.strip()}"
        if not self._camera.is_live():
            self._camera.set_placeholder(shown)
        if key == "viewer.camera_busy":
            # THE BADGE OF THE TILE, because the camera IS live and the
            # picture is there — what is wrong is how slowly it comes.
            # A warning that only appears when nothing works is a
            # warning nobody ever reads.
            self._camera_busy = True
            self._camera.set_badge(shown, live=True)
        else:
            self._camera_busy = False
        self._code.setText(shown[:28])

    # -- the hands -----------------------------------------------------------

    def set_gesture(self, tracker: str, pose: str, action: str,
                    extra: list[tuple[str, str, str]] | None = None) -> None:
        """What the tracker read, and what it means."""
        rows = [(_t("viewer.tracker"), str(tracker or "--"), "PRI"),
                (_t("viewer.pose"), str(pose or "--"), "TEXT"),
                (_t("viewer.action"), str(action or "--"), "GREEN")]
        for extra_row in (extra or []):
            rows.append(tuple(str(x) for x in extra_row))
        self._hands.set_rows(rows)

    def set_status(self, text: str) -> None:
        self._code.setText(str(text or "")[:28])


def _panel_text(panel: HoloPanel, which: str):
    """The title/code of a panel, through the same stand-in the window uses."""
    from gui.widgets import PanelText
    return PanelText(panel, which)
