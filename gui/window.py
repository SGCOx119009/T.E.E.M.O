"""gui/window.py — MainWindow — lays the HUD out and owns every signal the rest of the app talks to.

Split out of ui.py in the previous chassis; behaviour unchanged."""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QLineF, QMimeData, QObject, QParallelAnimationGroup,
    QPointF, QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QGraphicsScene, QGraphicsView, QGraphicsPixmapItem,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QSlider,
)

from gui.theme import (
    API_FILE,
    APP_PROTOCOL,
    APP_VERSION,
    C,
    CONFIG_DIR,
    DEFAULT_UI_COLOR,
    HAVE_VIDEO,
    QAudioOutput,
    QGraphicsVideoItem,
    QMediaPlayer,
    _DEFAULT_H,
    _DEFAULT_W,
    _LEFT_W,
    _MIN_H,
    _MIN_W,
    _OS,
    _RIGHT_W,
    _read_full_config,
    apply_ui_accent,
    current_palette,
    retheme_all_widgets,
)
from gui.metrics import (
    _metrics,
)
from gui.hud import (
    HudCanvas,
    _state_text,
)
from gui.appearance import (
    Appearance,
)
from gui.scene import (
    SceneCanvas,
)
from gui.gesture import (
    GestureOverlay,
    GestureService,
)
from gui.blades import (
    BladeStack,
)
from gui.boot import (
    BootOverlay,
)
from gui.floating import (
    FloatingPanel,
)
from gui.widgets import (
    FileDropZone,
    LogWidget,
    MetricBar,
    _CameraPreview,
    _FILE_ICONS,
    _file_category,
    _fmt_size,
)
from gui.overlays import (
    AudioDeviceOverlay,
    ClipboardPanel,
    ConfirmBanner,
    CustomizeOverlay,
    MemoryOverlay,
    PluginManagerOverlay,
    PluginSettingsOverlay,
    RemoteKeyOverlay,
    SetupOverlay,
)
from gui.providers_panel import (
    ProvidersOverlay,
)
from gui.viewer import (
    ViewerPanel,
)

from PyQt6.QtWidgets import QGridLayout

from gui.style import DISPLAY_CSS, MONO_CSS, btn_css, hud_font, px_font, scroll_css
from gui.widgets import (ArcLogo, CommandFrame, CountdownBar, DataRow, GestureReadout,
                         GroupRail, HeaderBar, HoloPanel, IconButton, ObservationCanvas,
                         PanelText, RingGauge, RoutineRow, SegmentedRail, Sparkline,
                         StateChip, StatusStrip, TaskRow)

from core import localization
from core.failures import install_excepthooks
from core.localization import t as _t


def _vbs_quote(text: str) -> str:
    """A string for a VBScript literal, with the quotes doubled.

    A path with a quote in it is rare but legal, and a VBScript that
    does not parse fails in a way that looks like "the shortcut was not
    created".
    """
    return str(text).replace('"', '""')


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    # (state name, prompt fragment, colour) — the affective state, chosen by
    # the user out loud and applied to the interface.
    _emotion_sig    = pyqtSignal(str, str, str)
    # The regulator's reading of the machine. It arrives from a worker thread,
    # so it travels by signal like everything else that crosses the thread.
    _resource_sig   = pyqtSignal(object)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _codejob_sig    = pyqtSignal(object)     # dev_agent job state — live coding panel
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(object)      # live camera frame → HUD area (QImage)
    _video_open_sig  = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _image_open_sig  = pyqtSignal(str, str)   # still picture on the HUD: path, title
    _wake_btns_sig   = pyqtSignal()          # wake state resolved off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig  = pyqtSignal(bool)
    _video_seek_sig  = pyqtSignal(float, bool)          # seconds, relative
    _video_pause_sig = pyqtSignal(bool)
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    # The user asked to leave. Whoever owns the event loop stops it.
    quit_requested  = pyqtSignal()

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "T.E.E.M.O.").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop the assistant mid-speech
        self.on_emotional_state = None  # callable: (name, prompt) -> None — the state changed
        self.on_briefing       = None   # callable: () -> None — say the morning briefing now
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by TeemoLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by TeemoLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by TeemoLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_set_tool_context = None  # callable: (context: str) -> str, set by TeemoLive
        self.get_tool_contexts = None   # callable: () -> list[str], set by TeemoLive
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        # Was the window on the whole screen when the user last asked for
        # it? The appearance layer reads this to put the screen back.
        self._was_fullscreen   = False
        # Was it maximized before it went full screen? Leaving the full
        # screen has to return to what it was, not always to a small
        # window — that was the bug.
        self._was_maximized    = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        # THE MODALS. There is only ever one of them on the screen, and
        # they are all filed here so that closing one can close the others.
        self._audio_overlay = None
        self._providers_overlay = None
        self._memory_overlay = None
        self._plugin_manager_overlay = None
        self._plugin_settings_overlay = None

        # THE SCENE.
        #
        # The central widget is not a blank plate: it is the world the
        # assistant is projected into — a deep field with a grid on it,
        # three rings turning at different speeds, dust in the air, and the
        # corner brackets. It replaces the flat background, and in the
        # corner mode it is switched off so the DESKTOP shows through.
        central = SceneCanvas()
        self._scene = central
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # Kept as attributes so the appearance layer can take the dashboard
        # apart and put it back together: in the corner mode only the core
        # is left, and these are the parts that go away.
        # A FAILURE IN A SLOT MUST NOT TAKE THE PROGRAM WITH IT.
        #
        # Qt turns an exception in a slot into a `qFatal`, and a `qFatal`
        # is the end of the process. The handlers go in before anything
        # is built, so a bug in a panel is a line in the terminal and not
        # a desktop without an assistant.
        install_excepthooks()

        self._header = self._build_header()
        root.addWidget(self._header)

        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)
        # Kept so the deck can be put back in its place between the face
        # and the conversation when it is built.
        self._body_layout = body

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # THE HANDS, DRAWN ON THE GLASS.
        #
        # The overlay is a child of the HUD, so it is drawn on top of the
        # face and follows the HUD's size; and the service that watches the
        # camera lives on its own thread, because the camera and the
        # tracking are both slow.
        self._gesture_overlay = GestureOverlay(self.hud)
        self._gesture_overlay.setGeometry(self.hud.rect())
        self.hud.installEventFilter(self)
        self._gesture = GestureService(self)
        self._gesture.hands_sig.connect(self._gesture_overlay.set_hands)
        self._gesture.state_sig.connect(self._log_sig.emit)
        self._gesture.gesture_sig.connect(self._on_gesture)
        # WHAT THE HAND SAYS THAT IS NOT ABOUT THE MOUSE.
        #
        # These four arrive on the Qt thread, because they touch the
        # interface and the voice of the assistant: the answer to a
        # question, the silence of the barge-in, the wake-up and the
        # sleep of the HUD.
        self._gesture.confirm_sig.connect(self._on_gesture_confirm)
        self._gesture.interrupt_sig.connect(self._do_interrupt)
        self._gesture.wake_sig.connect(self._on_gesture_wake)
        self._gesture.sleep_sig.connect(self._on_gesture_sleep)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()
        self._viewer_panel = self._build_viewer_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet(f"background: {C.CAM_BG};")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel(_t("window.camera_feed"))
        _cam_title.setFont(hud_font(8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton(_t("window.close"))
        _cam_x.setFont(hud_font(8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        # Kept as attributes so the language can change them later on.
        self._cam_title = _cam_title
        self._cam_close_btn = _cam_x
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # ── Video surface ────────────────────────────────────────────────
        # Built exactly like the camera page above and stacked beside it,
        # because it is the same idea: something takes the centre of the HUD
        # for a while and then gives it back. Sharing the stack rather than
        # inventing a second mechanism means the avatar, the camera and a video
        # can never be on screen at once.
        self._video_split = False        # is the sound a separate stream?
        self._video_auto_muted = False   # did the assistant close the mic, or the user?
        # A plain flag rather than reading the widget. video_is_playing() is
        # called from plugin threads, and reading a widget's state from one is
        # not something to rely on; an attribute is.
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel(_t("window.video"))
        self._video_title.setFont(hud_font(9, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(hud_font(8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        # Muted is the default and the button says so, because a soundtrack
        # talking over the assistant is the one way this feature could make the
        # assistant worse rather than better.
        self._video_mute_btn = _vid_btn(_t("window.sound_off"))
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn(_t("window.close"))
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        self._video_close_btn = _vid_x
        _vid_v.addLayout(_vid_hdr)

        # A GRAPHICS ITEM, NOT A QVideoWidget.
        #
        # QVideoWidget gets a native window of its own, and a native window
        # is painted by the compositor above every ordinary widget that
        # shares the screen with it — so the settings drawer opened
        # *underneath* the video and raise_() could not help, because the
        # two are not in the same painting order at all. A video item drawn
        # into a QGraphicsView goes through Qt's own painter like any other
        # widget, and anything laid over it stays over it.
        #
        # The surface is built whether or not Qt can play video, because a
        # STILL PICTURE needs a scene and a view and nothing else. A Qt
        # install without the multimedia plugins can still put a photo where
        # the avatar sits, and `generate_image` has been looking for somewhere
        # to show its own output since the earlier chassis.
        self._video_scene = QGraphicsScene(self)
        self._image_item = QGraphicsPixmapItem()
        self._image_item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation)
        self._image_item.setVisible(False)
        self._video_scene.addItem(self._image_item)
        self._video_widget = QGraphicsView(self._video_scene)
        self._video_widget.setStyleSheet(f"background: {C.VIDEO_BG}; border: none;")
        self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._video_widget.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_widget.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _vid_v.addWidget(self._video_widget, stretch=1)

        if HAVE_VIDEO:
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            # Keep the picture filling the view as the window is resized.
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)
            # Where the video is, kept on the Qt thread so video_state() can be
            # read from any thread without touching the player.
            self._video_player.positionChanged.connect(self._on_video_position)
            self._video_player.durationChanged.connect(self._on_video_duration)
            self._video_player.playbackStateChanged.connect(self._on_video_playback)

            # A SECOND player, for sound that arrives separately.
            #
            # YouTube no longer serves a single stream carrying both picture and
            # sound — not for new uploads and not for old ones; checked against
            # three videos including the oldest on the site, and every one of
            # them offered zero combined formats. Picture and sound are two
            # URLs, so they are two players, started together and nudged back
            # into line by the timer below.
            #
            # A local file or a direct video URL still uses the first player
            # alone: one stream, one player, nothing to synchronise.
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            # Pictures still work; only the moving ones are missing, and the
            # open path says so when one is actually asked for.
            self._video_item = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None

        # Stack: 0 = animated HUD, 1 = live camera, 2 = video
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setHandleWidth(10)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{ background: transparent; }}
            QSplitter::handle:hover {{ background: {C.PRI_GHO}; }}
        """)
        # ONE PANEL AT A TIME BELOW THE HEAD.
        #
        # Three things open under the head: a notification (news, a search
        # result, a briefing), the viewer, and the questionnaire. They used
        # to be three widgets in the splitter, and the splitter gave each of
        # them room — so two of them on screen at once fought over the same
        # pixels, and a search result could land in a panel the quiz had
        # already emptied.
        #
        # They are pages of one stack now. Only one is ever on screen, the
        # switch is explicit, and "the modals must not lie on top of each
        # other" stops being a thing to remember and becomes a property of
        # the layout.
        self._lower_blank = QWidget()
        self._lower_blank.setStyleSheet("background: transparent;")
        self._lower_stack = QStackedWidget()
        self._lower_stack.setStyleSheet("background: transparent;")
        self._lower_stack.addWidget(self._lower_blank)
        self._lower_stack.addWidget(self._content_panel)
        self._lower_stack.addWidget(self._viewer_panel)
        self._lower_stack.setCurrentWidget(self._lower_blank)
        # The viewer only works while it is on screen. A hidden viewer
        # that keeps the camera open is a camera that stays claimed for
        # nothing, and the machine that heats up for a panel nobody is
        # looking at.
        self._lower_stack.currentChanged.connect(self._on_lower_page_changed)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._lower_stack)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        self._footer = self._build_footer()
        root.addWidget(self._footer)

        # How the window shows itself: the whole dashboard, only the core in
        # a corner, or nothing but the icon in the tray.
        self._appearance = Appearance(self)
        self._quitting = False

        # THE CONTROLS DRAWER — one panel, two doors.
        #
        # The gear and the sliders open the same drawer at a different
        # group, so there is one list of switches on screen and never two
        # that could be mistaken for a copy of each other.
        self._ctrl_drawer = self._build_controls_drawer()
        self._quick_drawer = self._ctrl_drawer
        # Whether the drawer is a COLUMN of the interface (next to the face)
        # or a panel that stands in the room under the head.
        self._drawer_in_column = False
        # Whether the drawer is standing in the room under the head or
        # hanging from the button that opened it.
        self._ctrl_in_zone = False
        self._warm_wake_state()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._log._ai_name_lc = self._assistant_name.lower()

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        # A rename made straight in the config file has to reach the screen
        # without a restart. The timer only does work when the file changed.
        self._identity_mtime = None
        self._identity_tmr = QTimer(self)
        self._identity_tmr.timeout.connect(self._watch_identity)
        self._identity_tmr.start(2000)

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._emotion_sig.connect(self._apply_emotional_state)
        self._resource_sig.connect(self._apply_resource_state)
        self._content_sig.connect(self._show_content)
        self._codejob_sig.connect(self._show_code_job)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._image_open_sig.connect(self._on_image_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
        self._video_seek_sig.connect(self._on_video_seek)
        self._video_pause_sig.connect(self._on_video_pause)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        # WHAT WAS ALREADY IN THE CLIPBOARD IS NOT NEWS.
        #
        # Windows hands the clipboard over as soon as anybody looks at
        # it, and the program looks at it when it starts. The text the
        # user had copied BEFORE the program was opened came back as if
        # it were a new copy, and the panel offered to translate it, or
        # summarise it, or correct it — for a text nobody had copied
        # while the program was running. The user asked what that was:
        # it was the program greeting them with their own clipboard.
        self._last_clip_text = self._current_clipboard_text()
        # THE FIRST SECONDS OF THE PROGRAM ARE NOT NEWS EITHER.
        #
        # Windows hands the clipboard over a moment after the program
        # starts, not at the very moment it is asked for it: the text the
        # user had copied BEFORE the program was opened comes back as if
        # it were a new copy, and the panel comes up out of nowhere
        # greeting the user with their own clipboard.
        #
        # So the first clipboard that arrives in the first few seconds is
        # only remembered, never offered.
        self._started_at = time.monotonic()
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        # THE DECK.
        #
        # THE DECK IS A COLUMN OF THE INTERFACE.
        #
        # It used to be a floating panel that put itself on top of the
        # right-hand column: the conversation, its rail and all, went
        # under the cards, and the user could not get them back without
        # closing the deck. Two things in the same place, one of them
        # invisible.
        #
        # Now it is a column between the face and the conversation. It
        # takes its space from the middle when it opens and gives it back
        # when it closes, and it covers nothing at all.
        self._blades = BladeStack()
        self._blades.setFixedWidth(self._DECK_W)
        self._blades.hide()
        try:
            _at = body.indexOf(self._right_panel)
            body.insertWidget(_at if _at >= 0 else body.count(), self._blades)
        except Exception:
            pass
        self._deck_seen = False        # has the deck ever been opened by itself?

        # THE IGNITION.
        #
        # The screen that comes up before the assistant is ready. It runs
        # once, on top of everything, and it says the truth about what it
        # found — the rows it could not check say so instead of ticking.
        self._boot = BootOverlay(self.centralWidget(), brand=_display)
        self._boot.hide()
        self._boot.finished.connect(self._on_boot_finished)

        # THE THING THAT STAYS ON TOP.
        #
        # A panel of glass over every other window on the machine, with
        # the state and the last thing the assistant said. It can be
        # dragged by the mouse and by the hand.
        self._floating = FloatingPanel()
        self._floating.hide()
        # The floating panel is a window of its own, so it does not die with
        # this window. It is destroyed here, in the thread that owns its
        # timer, instead of being left for the garbage collector to pick up
        # wherever it happens to be running.
        self.destroyed.connect(lambda *_: self._destroy_floating())

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

        # The deck. `Ctrl+Alt+E` opens and closes it; the letters on their
        # own only work while it is open, and only when the user is not
        # typing — see `keyPressEvent`.
        sc_blades = QShortcut(QKeySequence("Ctrl+Alt+E"), self)
        sc_blades.activated.connect(self._toggle_blades)

        # THE IGNITION, a moment after the window is up.
        QTimer.singleShot(350, self._start_boot)

        # THE ICON IN THE TRAY.
        #
        # It is the way back from the corner, and the way out from
        # everywhere else: the window, the big window, and the exit. It is
        # built last, because it needs the appearance to talk to.
        self._tray = None
        try:
            from gui.tray import TrayPresence, available as _tray_available
            if _tray_available():
                self._tray = TrayPresence(
                    self,
                    on_show=self.restore_window,
                    on_maximize=lambda: self.apply_appearance("dashboard"),
                    on_exit=self.quit_application,
                )
                self._tray.show()
        except Exception as exc:                   # noqa: BLE001
            print(f"[window] the tray icon is not available: {type(exc).__name__}")
            self._tray = None

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - self._right_panel.width() - pw - 22,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data) -> None:
        """The picture for the head, drawn on the thread that owns the screen."""
        if isinstance(data, (bytes, bytearray)):
            px = QPixmap()
            px.loadFromData(bytes(data))
        else:
            px = QPixmap.fromImage(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()
        # THE SUBSCRIPTION ENDS WITH THE WINDOW.
        #
        # The hub of the camera keeps a list of readers, and this window
        # is one of them. A window that goes without saying goodbye would
        # leave the hub calling a reader that is not there any more.
        try:
            self.destroyed.connect(self._forget_camera)
        except Exception:
            pass

    def _forget_camera(self) -> None:
        """Let go of the camera when the window is destroyed."""
        self._cam_stop.set()
        try:
            from core.camera import unsubscribe as _unsubscribe
            _unsubscribe(self._on_cam_frame_bytes)
        except Exception:
            pass

    def _cam_loop(self) -> None:
        """Publish the frames of the camera OF THE PROCESS.

        The camera is not opened here. It is the one of `gui/camera.py`,
        and this is one more reader of it: the hands, the tile of the
        viewer and this stream all want the same device, and a camera
        opened twice is what made the whole program die with an access
        violation.

        AND OPENCV IS NOT HERE, NOT EVEN FOR NOTHING.
            This thread used to ask for OpenCV and never used it. What it
            really did was load a native library with threads of its own
            from a background thread, right next to the interface. The
            camera is read in the process that holds the camera, and the
            pictures arrive as JPEG bytes; OpenCV has no business in this
            process.
        """
        try:
            from core.camera import hub as _camera_hub
            hub = _camera_hub()
            try:
                from memory.config_manager import get_camera_index
                index = get_camera_index()
            except Exception:
                index = None
            if index is not None:
                hub.set_index(index)
            hub.subscribe(self._on_cam_frame_bytes)
            while not self._cam_stop.wait(0.1):
                if hub.error:
                    print(f"[Camera] {hub.error}")
                    break
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            try:
                from core.camera import unsubscribe as _unsubscribe
                _unsubscribe(self._on_cam_frame_bytes)
            except Exception:
                pass
            self._cam_stream_sig.emit(False)

    def _on_cam_frame_bytes(self, ok: bool, jpeg: bytes) -> None:
        """One frame of the camera, as a picture for the HUD.

        THE BYTES GO AS THEY ARE: the picture is already a JPEG, and
        the interface knows how to draw one. Encoding it a second time
        here, in the thread that was reading the camera, was work that
        nobody had asked for.

        BUT IT IS DECODED HERE, AND NOT ON THE SCREEN. Unpacking a JPEG
        is a piece of work of its own, and doing it on the thread that
        draws the interface meant that the interface stopped drawing
        while the picture was being unpacked — fifteen times a second.
        A QImage can be unpacked on any thread; only the painting of it
        has to happen on the one that owns the screen.
        """
        if not ok or not jpeg:
            return
        try:
            from PyQt6.QtGui import QImage

            image = QImage()
            if not image.loadFromData(jpeg):
                return
            self._cam_frame_sig.emit(image)
        except Exception:
            pass

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(self, source: str, title: str, muted: bool,
                       audio_source: str = "") -> None:
        if not HAVE_VIDEO or not self._video_player:
            self._log.append_log("SYS: Video playback is not available in this Qt "
                           "install.")
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        # A video's title is in its own language; left as written (see _show_content).
        self._video_title.setText(f"▶  {(title[:44] if title else _t('window.video_label'))}")
        self._video_split = bool(audio_source)
        self._video_kind = "video"
        self._video_pos_ms = self._video_dur_ms = 0
        self._video_paused = False
        # A picture may be on the surface; the two share it and only one can
        # be visible.
        self._image_item.setVisible(False)
        self._image_item.setPixmap(QPixmap())
        if self._video_item is not None:
            self._video_item.setVisible(True)
        self._video_mute_btn.setVisible(True)
        self._set_video_muted(bool(muted))

        url = (QUrl.fromLocalFile(source) if Path(source).exists()
               else QUrl(source))
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _on_image_open(self, path: str, title: str) -> None:
        """Put a still picture where the avatar sits.

        It shares the video's surface rather than adding a fourth page to the
        stack, for the reason the video shared the camera's: something takes
        the centre of the HUD for a while and then gives it back, and one
        mechanism for that cannot contradict itself. The close button, the
        stop command and the camera interlock all work on a picture already,
        because they were never about video.
        """
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._log.append_log(
                f"SYS: '{Path(path).name}' could not be shown — this Qt build "
                f"does not read that image format."
            )
            return

        self._cam_stop.set()            # the camera cannot share the centre
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_kind = "image"
        # Silence first: a video whose sound was on has the microphone closed,
        # and the picture replacing it must hand the microphone back.
        self._set_video_muted(True)
        self._video_mute_btn.setVisible(False)

        if self._video_item is not None:
            self._video_item.setVisible(False)
        self._image_item.setPixmap(pixmap)
        self._image_item.setVisible(True)
        self._video_title.setText(f"◧  {(title[:44] if title else _t('window.picture_label'))}")
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        self._sync_mic_for_video()
        self._fit_video()

    def _fit_video(self, *_a) -> None:
        """Size whatever is on the surface to the panel, keeping its shape."""
        if not self._video_widget:
            return
        try:
            showing_image = self._image_item.isVisible()
            item = self._image_item if showing_image else self._video_item
            if item is None:
                return
            native = (QSizeF(self._image_item.pixmap().size()) if showing_image
                      else self._video_item.nativeSize())
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(),
                        view.height() / native.height())
            if showing_image:
                # A pixmap item has no setSize, so it is scaled instead — and
                # capped, because a thumbnail blown up to fill the panel looks
                # broken rather than large.
                item.setScale(min(scale, 2.0))
            else:
                item.setSize(QSizeF(native.width() * scale,
                                    native.height() * scale))
            self._video_scene.setSceneRect(item.sceneBoundingRect())
            self._video_widget.centerOn(item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._video_kind = ""
        self._image_item.setVisible(False)
        self._image_item.setPixmap(QPixmap())   # a full-resolution photo is MB
        if self._video_item is not None:
            self._video_item.setVisible(True)
        self._video_mute_btn.setVisible(True)
        self._hud_cam_stack.setCurrentIndex(0)
        self._sync_mic_for_video()      # gives the microphone back

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        The assistant subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if the assistant closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(self._video_on and self._video_sound_out
                        and not self._video_sound_out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close "
                                  "it, or press F4 to talk. Typing still works.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText(_t("window.sound_off") if muted
                                    else _t("window.sound_on"))

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self._log.append_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ── position and pause, asked for by voice ───────────────────────────────
    # "Skip ahead 30 seconds", "go to 1:20", "pause it": the player knows exactly
    # where it is, so these are exact — unlike a seek sent as arrow keys to some
    # other application.

    def _on_video_position(self, ms: int) -> None:
        self._video_pos_ms = int(ms)

    def _on_video_duration(self, ms: int) -> None:
        self._video_dur_ms = int(ms)

    def _on_video_playback(self, state) -> None:
        self._video_paused = state == QMediaPlayer.PlaybackState.PausedState

    def _on_video_seek(self, seconds: float, relative: bool) -> None:
        p = self._video_player
        if not (HAVE_VIDEO and p and self._video_on and getattr(self, "_video_kind", "") == "video"):
            return
        target = p.position() + seconds * 1000 if relative else seconds * 1000
        duration = p.duration()
        if duration > 0:
            target = min(target, max(0, duration - 500))
        target = int(max(0, target))
        p.setPosition(target)
        if self._video_split and self._video_sound:
            self._video_sound.setPosition(target)
        self._video_pos_ms = target

    def _on_video_pause(self, paused: bool) -> None:
        if not (HAVE_VIDEO and self._video_player and self._video_on
                and getattr(self, "_video_kind", "") == "video"):
            return
        players = [self._video_player] + ([self._video_sound] if self._video_split and self._video_sound else [])
        for player in players:
            player.pause() if paused else player.play()
        if self._video_split and self._video_sync:
            if paused:
                self._video_sync.stop()
            else:
                self._video_sync.start()

    def video_state(self) -> dict:
        """What is on the display: kind ('video' | 'image' | ''), position and
        duration in seconds, paused. Safe to read from any thread."""
        kind = getattr(self, "_video_kind", "") if self._video_on else ""
        return {"kind": kind,
                "position": getattr(self, "_video_pos_ms", 0) / 1000.0,
                "duration": getattr(self, "_video_dur_ms", 0) / 1000.0,
                "paused": bool(getattr(self, "_video_paused", False))}

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_teemo_icon(out_path: Path) -> bool:
        """
        Render a T.E.E.M.O. arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.

        THE SHELL IS TALKED TO FROM ANOTHER PROCESS.

            The obvious way is `win32com.client.Dispatch("WScript.Shell")`,
            and it is a trap: COM is not a library, it is an agreement
            between the threads of a process about which apartment owns
            which object. `win32com.client` initializes COM in the thread
            that imports it, and the program has threads that are already
            inside COM — the hardware panel talks to WMI — so importing it
            in the middle of the program is how a process dies with
            «Windows fatal exception: access violation» and no clue.

            So the shortcut is written by `wscript.exe`, which is a
            GUI-mode host: it never opens a console window, and the COM
            that creates the shortcut lives in THAT process, not in this
            one.
        """
        # ── wscript.exe + VBScript ──────────────────────────────────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{_vbs_quote(lnk)}")',
            f'sc.TargetPath = "{_vbs_quote(target)}"',
            f'sc.Arguments = Chr(34) & "{_vbs_quote(args)}" & Chr(34)',
            f'sc.WorkingDirectory = "{_vbs_quote(work_dir)}"',
            'sc.Description = "T.E.E.M.O. AI Assistant"',
            f'sc.IconLocation = "{_vbs_quote(icon_loc)}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when OneDrive "Known Folder Move" relocates the
        desktop (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11.
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()

        # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
        #       answer; follows OneDrive redirection. No dependencies. ──
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD),
                            ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
            fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                        (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                             0x9A, 0x87, 0xC6, 0x41))
            buf = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                p = Path(buf.value)
                ctypes.windll.ole32.CoTaskMemFree(buf)
                if p.is_dir():
                    return p
        except Exception:
            pass

        # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────────
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\User Shell Folders") as key:
                val, _t = winreg.QueryValueEx(key, "Desktop")
            p = Path(os.path.expandvars(val))
            if p.is_dir():
                return p
        except Exception:
            pass

        # Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows.
        Never opens a terminal, console, or PowerShell window.
        """
        script  = Path(__file__).resolve().parent.parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico)
        ico_path = Path(__file__).resolve().parent.parent / "config" / "teemo.ico"
        if not ico_path.exists():
            self._build_teemo_icon(ico_path)

        try:
            pythonw  = python.parent / "pythonw.exe"
            target   = str(pythonw if pythonw.exists() else python)
            lnk      = str(desktop / "T.E.E.M.O.lnk")
            icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
            self._create_lnk_windows(lnk, target, str(script),
                                     str(script.parent), icon_loc)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    # ── the four shapes of the window ────────────────────────────────────────
    #
    # WHY THEY WERE HALF BUGGY
    #     "The whole screen" and "the window again" were one button that
    #     toggled between two states, and the way back was always
    #     `showNormal()`. So a MAXIMIZED window that went full screen came
    #     back SMALL — the user pressed the button to hide the taskbar and
    #     got a little window in the corner of the screen, with no way to
    #     tell why. The same button could not maximize, and nothing could
    #     minimize except the frame of Windows, which is not there when the
    #     window is in the corner.
    #
    # WHAT IT IS NOW
    #     Four named actions, and the state of the window is remembered
    #     properly: leaving the full screen returns to whatever it was —
    #     maximized stays maximized, a normal window stays normal.

    def _toggle_fullscreen(self):
        """The whole screen, or the way back to what it was.

        The wish is remembered: when the user comes back to the dashboard
        after being in the corner, the window finds itself on the whole
        screen again if that is how they left it. Without the memory the
        screen would quietly shrink and the user would think the program
        had lost its place.
        """
        if self.isFullScreen():
            self._was_fullscreen = False
            # Back to what it was — not always "normal". A maximized window
            # that came back small was the bug.
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            self._raise_window()
        else:
            self._was_maximized = self.isMaximized()
            self._was_fullscreen = True
            self.showFullScreen()

    def _maximize_window(self):
        """Big, with the taskbar still there — and back again.

        IT IS A PAIR OF STATES AND NOT ONE. The button used to maximize
        and nothing else, so pressing it on a window that was already
        maximized did nothing at all — and its own label changed to
        "VENTANA", the word of a button that sat right below it.
        """
        self._was_fullscreen = False
        if self.isMaximized():
            self._was_maximized = False
            self.showNormal()
        else:
            self._was_maximized = True
            self.showMaximized()
        self._raise_window()

    def _restore_window(self):
        """A window on the desktop, at the size it likes."""
        self._was_fullscreen = False
        self._was_maximized = False
        self.showNormal()
        self._raise_window()

    def _minimize_window(self):
        """Out of the way, but alive."""
        self.showMinimized()

    def _raise_window(self) -> None:
        """Bring the window in front, politely.

        A window that is maximized or full screen and then asked to come
        forward can stay behind the window the user was working in, and the
        button looks like it did nothing. Raising it and giving it the
        focus is what the user asked for.
        """
        try:
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def changeEvent(self, event):
        """Keep the truth about the window's shape.

        Windows changes the state on its own too — the title bar's double
        click, Win+Up, dragging it to the top of the screen — and the
        buttons in the drawer have to say what is true.
        """
        try:
            if event.type() == QEvent.Type.WindowStateChange:
                if self.isFullScreen():
                    self._was_fullscreen = True
                else:
                    self._was_fullscreen = False
                    self._was_maximized = self.isMaximized()
                self._refresh_window_btns()
        except Exception:
            pass
        super().changeEvent(event)

    def _refresh_window_btns(self) -> None:
        """The buttons say what the window is doing.

        NO BUTTON SAYS THE SAME THING AS ANOTHER. It used to happen: the
        maximize button changed its words to "VENTANA" when the window
        was maximized, and there was already a button that said
        "VENTANA" — two buttons with the same name, and the user could
        not tell which one was which.
        """
        try:
            if self.isFullScreen():
                self._fullscreen_btn.setText(_t("window.leave_fullscreen"))
            else:
                self._fullscreen_btn.setText(_t("controls.fullscreen"))
            if self.isMaximized():
                self._maximize_btn.setText(_t("window.restore"))
            else:
                self._maximize_btn.setText(_t("window.maximize"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Appearance — the three faces of the assistant
    # ------------------------------------------------------------------
    def apply_appearance(self, mode: str, persist: bool = True) -> str:
        """Put the window into a mode: 'dashboard', 'core' or 'tray'.

        `persist` says whether the mode is a PREFERENCE or a moment. The
        three faces are not all preferences: going to the tray because
        the window was closed is a moment, and writing it down means the
        next start has no window at all — which is how an assistant ends
        up running with nothing to click.
        """
        try:
            applied = self._appearance.set_mode(mode, persist=persist)
        except Exception as exc:                   # noqa: BLE001
            print(f"[window] the appearance could not be changed: {type(exc).__name__}")
            return self._appearance.mode()
        # The selector has to show what is in force, and the corner box is
        # only alive in the core mode.
        self._refresh_appearance_combo()
        return applied

    def appearance_mode(self) -> str:
        return self._appearance.mode()

    def set_core_corner(self, corner: str) -> str:
        """Move the core to another corner of the screen."""
        return self._appearance.set_corner(corner)

    def load_appearance(self) -> str:
        """Apply the appearance the user chose last time."""
        return self._appearance.load()

    def restore_window(self) -> str:
        """Bring the window back from the tray."""
        return self._appearance.restore()

    def quit_application(self) -> None:
        """Leave for real.

        The tray icon goes first: an icon left behind after the app has
        gone is a dead icon that has to be cleaned by hand.

        AND THEN THE EVENT LOOP IS TOLD. Closing the window is not
        enough: when the window was already hidden — which is the case
        when the exit is asked for from the tray, the assistant being
        nothing but an icon — the closing does not reach Qt's "the last
        window is gone", so the loop keeps running and the process stays
        behind with no window and no icon. Measured: the loop was still
        alive eight seconds after "Exit".

        The window does not stop the loop itself: it says so, and whoever
        owns the loop (ui.py) stops it. A window that calls
        `QApplication.quit()` from inside a test would take the test
        process with it.
        """
        self._quitting = True
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.hide()
            except Exception:
                pass
        # The panel that floats over somebody else's window is a window of
        # its own, and it has to go with the rest.
        self._destroy_floating()
        try:
            self.close()
        except Exception:
            pass
        try:
            self.quit_requested.emit()
        except Exception:
            pass

    def _destroy_floating(self) -> None:
        """The floating panel dies with the window — and it dies in the Qt thread.

        IT IS A WINDOW OF ITS OWN, so it does not die with the window that
        owns it. It stays alive with nobody pointing at it, and the garbage
        collector takes it — in whichever thread happens to be running the
        collector, which is not necessarily the one that owns the timer.

        A widget with a running timer destroyed from another thread cannot kill
        that timer, so the timer stays in the event loop pointing at an object
        that is gone: the next event that arrives crashes the program with an
        access violation.

        That is not a theory. Measured on the owner's machine: the test suite
        died in the middle of the event loop with exactly that violation, and
        the same suite with the collector switched off did not. The fix is to
        destroy the panel here, in the thread that owns it, and to let the
        event loop do it.
        """
        panel = getattr(self, "_floating", None)
        if panel is None:
            return
        try:
            from PyQt6.QtCore import sip

            if sip.isdeleted(panel):
                return
        except Exception:
            pass
        try:
            panel.hide()
            panel.deleteLater()
        except Exception:
            pass

    def closeEvent(self, event):
        """Closing the window puts the assistant in the tray.

        A window that vanishes takes the assistant with it: the microphone
        stops, the conversation stops, and the user is left with a taskbar
        button that no longer does anything. So the close button is read as
        a request to hide, and only the tray menu's "Exit" really leaves.

        The taskbar button disappears with the window, which is the point:
        in the tray there is one place to find the assistant, and it is the
        tray.

        TWO THINGS THAT ARE NOT OBVIOUS.

        One: going to the tray is NOT written down. It is a moment, not a
        preference. Writing it down meant that the next start had no
        window at all — no interface, no ignition, nothing to click — and
        the user had to go looking for the icon to find out why.

        Two: if there is no tray —a machine with no notification area—
        hiding the window would leave the assistant running with no way of
        being found. So in that case closing the window means leaving.
        """
        if getattr(self, "_quitting", False):
            event.accept()
            return
        event.ignore()
        if getattr(self, "_tray", None) is None:
            # No icon to go to: staying hidden would be a lost program.
            self.quit_application()
            return
        try:
            self.apply_appearance("tray", persist=False)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """The hands are drawn exactly over the face they belong to.

        The overlay is a child of the HUD, and the HUD changes size when
        the splitter moves — so the overlay is resized with it. Doing it
        here rather than in the window's resizeEvent is the difference
        between the overlay being right and being nearly right: the window
        is not resized when the HUD is.

        The same filter watches the notification panel: the pointer coming
        over it stops the clock, so a notification the user is reading does
        not vanish under their eyes.
        """
        if obj is getattr(self, "hud", None) and event.type() == QEvent.Type.Resize:
            overlay = getattr(self, "_gesture_overlay", None)
            if overlay is not None:
                overlay.setGeometry(self.hud.rect())
        elif obj in getattr(self, "_content_hover", ()):
            # The pointer can be over the TEXT of the notification and not
            # over the panel itself: Qt sends the Enter to the child and
            # the panel never hears of it. So every part of the panel is
            # watched, and the clock stops while the user is reading.
            kind = event.type()
            if kind == QEvent.Type.Enter:
                self._set_content_paused(True)
            elif kind == QEvent.Type.Leave:
                self._set_content_paused(False)
        return super().eventFilter(obj, event)

    def showEvent(self, event):
        """The window appears: the form's field takes the focus.

        The form is built before the window is shown, and a window that is
        shown later gives the focus to whatever had it — a scroll area of
        the panel behind the form, which swallows letters without a sound.
        So the field is focused again here, when the window really
        appears.
        """
        super().showEvent(event)
        # THE ROOM UNDER THE HEAD KEEPS ITS SIZE. It is the place where the
        # drawer of the controls stands and where a report lands, so it is
        # always there; the face takes the rest of the middle.
        self._apply_room(self._ROOM_H)
        overlay = getattr(self, "_overlay", None)
        if overlay is not None and overlay.isVisible():
            # The form was placed when the central panel still had the size it
            # had before the window was laid out — measured: it sat 170 px to
            # the left of the centre. So it is centred again here, when the
            # window really has its final size.
            self._centre_setup_overlay()
            try:
                overlay._key_input.setFocus(Qt.FocusReason.OtherFocusReason)
            except Exception:
                pass

    def _centre_setup_overlay(self):
        """Puts the setup form in the middle of the panel, by its own size."""
        overlay = getattr(self, "_overlay", None)
        if overlay is None:
            return
        cw = self.centralWidget()
        ow, oh = SetupOverlay._OW, SetupOverlay._OH
        overlay.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_side_columns()
        # THE DRAWER CHANGES ITS PLACE WHEN THE WINDOW DOES.
        #
        # A window that becomes wide enough takes the drawer as a column
        # next to the face; a window that becomes narrow gives it back to
        # the room under the head. The drawer is never in a place the
        # window cannot hold.
        drawer = getattr(self, "_ctrl_drawer", None)
        if drawer is not None and drawer.isVisible():
            if self._drawer_in_column and not self._drawer_fits_as_column():
                self._set_drawer_as_column(False)
                self._position_ctrl_drawer()
            elif not self._drawer_in_column and self._drawer_fits_as_column():
                self._set_drawer_as_column(True)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            self._centre_setup_overlay()
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - self._right_panel.width() - pw - 22,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # The ignition covers the whole interface, so it has to be given the
        # whole interface: a child widget that is not in a layout keeps the
        # size it was born with, which is nothing at all.
        boot = getattr(self, "_boot", None)
        if boot is not None and boot.isVisible():
            boot.setGeometry(cw.rect())
        # The deck sits in the layout; there is nothing to place by hand.
        deck = getattr(self, "_blades", None)
        if deck is not None and deck.isVisible():
            deck._ensure_visible()
        # The drawer — reposition if open. There is only one, and the quick
        # drawer and the controls drawer are the same widget.
        if hasattr(self, '_ctrl_drawer') and self._ctrl_drawer.isVisible():
            self._position_ctrl_drawer()
        self._fit_video()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        cpu, mem = snap["cpu"], snap["mem"]
        self._g_cpu.set_value(cpu, f"{cpu:.0f}%")
        self._g_mem.set_value(mem, f"{mem:.0f}%")

        gpu = snap["gpu"]
        if gpu >= 0:
            self._g_gpu.set_value(gpu, f"{gpu:.0f}%")
        elif str(snap.get("gpu_state") or "") == "pending":
            # Not read yet, not missing. The GPU counters take seconds, and
            # a meter that says N/A while it is counting looks broken.
            self._g_gpu.set_value(0, "…")
        else:
            self._g_gpu.set_value(0, "N/A")

        tmp = snap["tmp"]
        if tmp >= 0:
            self._g_tmp.set_value(min(100.0, tmp), f"{tmp:.0f}°")
        elif str(snap.get("tmp_state") or "") == "pending":
            self._g_tmp.set_value(0, "…")
        else:
            self._g_tmp.set_value(0, "N/A")

        # NET in MB/s: the graph scales itself, the panel header carries the figure
        net = snap["net"]
        self._net_spark.push(net)
        rate = f"{net * 1024:.0f} KB/s" if net < 1.0 else f"{net:.1f} MB/s"
        self._net_panel.set_code(rate)

        try:
            elapsed = time.time() - psutil.boot_time()
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._row_uptime.set_value(f"{h:02d}:{m:02d}", "GREEN")
        except Exception:
            self._row_uptime.set_value("--:--", "GREEN")

        try:
            self._row_proc.set_value(str(len(psutil.pids())))
        except Exception:
            self._row_proc.set_value("--")

    def _build_header(self) -> QWidget:
        w = HeaderBar()
        w.setFixedHeight(58)
        self._header = w
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 6, 14, 8)
        lay.setSpacing(10)
        lay.addWidget(ArcLogo())

        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        display = self._assistant_name.upper()
        self._title_lbl = QLabel(display)
        self._title_lbl.setFont(px_font(19, QFont.Weight.Light, spacing=3.5))
        self._title_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._title_lbl.setToolTip(self._subtitle(display))
        name_col.addWidget(self._title_lbl)
        self._sub_lbl = QLabel(f"{APP_VERSION}  ·  {_t('window.personal_ai_system')}")
        self._sub_lbl.setFont(px_font(9, QFont.Weight.DemiBold, spacing=2.2))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        name_col.addWidget(self._sub_lbl)
        lay.addLayout(name_col)
        lay.addSpacing(8)

        # ONE DOOR, NOT TWO.
        #
        # The header had three buttons and the user was right about all
        # three: the gear and the sliders opened the SAME drawer on two
        # different pages, and the third one — the deck — opened a
        # column of cards that was already on the screen, so it looked
        # like a button that did nothing.
        #
        # Now there is one. It opens the drawer on the group that was
        # open last, which is the one the user is most likely to want,
        # and the rail inside the drawer carries the seven groups.
        self._drawer_btn = IconButton("gear", 32)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.setToolTip(_t("window.controls_drawer_tip"))
        self._drawer_btn.clicked.connect(self._toggle_controls)
        lay.addWidget(self._drawer_btn)
        # THE OLD NAME OF THE SAME BUTTON. There were two doors and now
        # there is one; the name the code used for the second one is
        # kept pointing at it, so nothing that reaches for it opens a
        # drawer that is not there.
        self._ctrl_btn = self._drawer_btn
        lay.addSpacing(8)
        lay.addStretch()

        self._state_chip = StateChip()
        lay.addWidget(self._state_chip)
        lay.addStretch()

        clock_col = QVBoxLayout()
        clock_col.setSpacing(0)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(px_font(22, QFont.Weight.Light, mono=True, spacing=1))
        self._clock_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(px_font(9, QFont.Weight.DemiBold, spacing=1.6))
        self._date_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._date_lbl)
        lay.addLayout(clock_col)
        lay.addSpacing(4)
        self._fs_btn = IconButton("expand", 32)
        self._fs_btn.setToolTip(_t("window.fullscreen_tip"))
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(self._fs_btn)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    @staticmethod
    def _subtitle(display: str) -> str:
        # The acronym expansion is a name, and a name is not translated. The
        # plain subtitle is an ordinary sentence, and that one is.
        return ("Just A Rather Very Intelligent System"
                if display.replace(".", "") == "TEEMO"
                else _t("window.subtitle_default"))

    def _fit_side_columns(self) -> None:
        """Side columns grow with the window, within limits, so the centre keeps
        the space a larger window was opened for."""
        W = self.width()
        if hasattr(self, "_left_panel"):
            self._left_panel.setFixedWidth(max(190, min(250, int(W * 0.19))))
        if hasattr(self, "_right_panel"):
            self._right_panel.setFixedWidth(max(290, min(400, int(W * 0.27))))
        self._fit_deck_width()
        # The network graph is the first thing to give up its space: below this
        # height the gauges and the task list need it more, and the rate itself
        # is still in STATUS.
        if hasattr(self, "_net_panel"):
            self._net_panel.setVisible(self.height() >= 820)

    def _fit_deck_width(self) -> None:
        """How wide the deck of cards can be without pushing anything.

        THE DECK IS A COLUMN AND NOT A PANEL ON TOP OF THE OTHER ONE. It
        takes its space from the middle, and the middle has a minimum:
        the face needs three hundred pixels to be a face, the handle of
        the splitter ten more, and thirty of air around it. In a narrow
        window the deck gives up the space it cannot have, so the three
        columns never overlap and nothing is drawn outside the window.
        """
        deck = getattr(self, "_blades", None)
        if deck is None:
            return
        try:
            central = self.centralWidget().width()
            left = self._left_panel.maximumWidth()
            right = self._right_panel.maximumWidth()
        except Exception:
            return
        centre_min = 340          # the face, the handle and the air
        spare = central - left - right - 30 - 20 - centre_min
        # THE DRAWER, WHEN IT IS A COLUMN, TAKES ITS WIDTH FROM THE SAME
        # PLACE. The deck gives up what the drawer needs, because the
        # drawer is the one the user opened on purpose.
        if getattr(self, "_drawer_in_column", False):
            spare -= self._DRAWER_W
        deck.setFixedWidth(max(200, min(self._DECK_W, spare)))

    def _refresh_identity_rows(self) -> None:
        """Engine and voice, read from config — on start and whenever they change."""
        try:
            from memory.config_manager import get_provider_mode, get_voice
            mode = str(get_provider_mode() or "gemini")
            engine = "GEMINI LIVE" if mode == "gemini" else f"LOCAL {mode.upper()}"
            voice = str(get_voice() or "--").upper()
        except Exception:
            engine, voice = "GEMINI LIVE", "--"
        self._engine_label = engine
        if hasattr(self, "_row_engine"):
            self._row_engine.set_value(engine, "PRI")
            self._row_voice.set_value(voice)
        self._refresh_status_strip()

    def _refresh_status_strip(self) -> None:
        if not hasattr(self, "_status_strip"):
            return
        mic = (_t("window.status_mic_muted"), "MUTED_C") if self._muted \
            else (_t("window.status_mic_live"), "GREEN")
        self._status_strip.set_items([
            (_t("window.status_engine",
                engine=getattr(self, "_engine_label", "GEMINI LIVE")), "GREEN"),
            mic,
            (_t("window.status_protocol", protocol=APP_PROTOCOL), "PRI"),
        ])

    def _build_tasks_panel(self) -> QWidget:
        """The work: the background tasks and the routines.

        It used to sit in the LEFT column, under the gauges, which is
        where nobody looks for it — and the right column had no room
        for it, because the log and the command line took all of it.
        It is a page of the right column now, next to the conversation
        it belongs to.
    """
        # Background tasks (core/tasks). Read from the engine every two seconds —
        # an in-memory list, no disk — and hidden rows cost nothing to paint.
        self._tasks_panel = HoloPanel(_t("panel.tasks"), "")
        rows_box = QWidget()
        rows_box.setStyleSheet("background: transparent;")
        rows_lay = QVBoxLayout(rows_box)
        rows_lay.setContentsMargins(0, 0, 4, 0)
        rows_lay.setSpacing(6)
        self._task_rows = []
        for _ in range(6):
            row = TaskRow()
            row.clicked.connect(self._open_task)
            row.hide()
            rows_lay.addWidget(row)
            self._task_rows.append(row)
        self._tasks_empty = QLabel(_t("window.no_tasks"))
        self._tasks_empty.setFont(px_font(10))
        self._tasks_empty.setWordWrap(True)
        self._tasks_empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        rows_lay.addWidget(self._tasks_empty)
        rows_lay.addStretch()
        # Scrolls rather than grows: the column must fit an 580-pixel-high
        # window, and a task list that pushes the gauges into each other is
        # worse than one that scrolls.
        scroll = QScrollArea()
        scroll.setWidget(rows_box)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_css())
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setMinimumHeight(24)

        # Routines (core/routines) share the panel, switched from its header: the
        # column has no room for another panel, and both lists answer the same
        # question — what is the assistant doing without being asked.
        from PyQt6.QtWidgets import QStackedWidget
        r_box = QWidget()
        r_box.setStyleSheet("background: transparent;")
        r_lay = QVBoxLayout(r_box)
        r_lay.setContentsMargins(0, 0, 4, 0)
        r_lay.setSpacing(6)
        self._routine_rows = []
        for _ in range(12):
            row = RoutineRow()
            row.clicked.connect(self._open_routine)
            row.toggled.connect(self._toggle_routine)
            row.hide()
            r_lay.addWidget(row)
            self._routine_rows.append(row)
        self._routines_empty = QLabel(_t("window.no_routines"))
        self._routines_empty.setFont(px_font(10))
        self._routines_empty.setWordWrap(True)
        self._routines_empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        r_lay.addWidget(self._routines_empty)
        r_lay.addStretch()
        r_scroll = QScrollArea()
        r_scroll.setWidget(r_box)
        r_scroll.setWidgetResizable(True)
        r_scroll.setFrameShape(QFrame.Shape.NoFrame)
        r_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        r_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_css())
        r_scroll.viewport().setStyleSheet("background: transparent;")
        r_scroll.setMinimumHeight(24)

        self._tasks_stack = QStackedWidget()
        self._tasks_stack.setStyleSheet("background: transparent;")
        self._tasks_stack.addWidget(scroll)
        self._tasks_stack.addWidget(r_scroll)
        self._tasks_panel.body.addWidget(self._tasks_stack)

        self._panel_switch = QPushButton(_t("panel.routines"))
        self._panel_switch.setFixedSize(76, 20)
        self._panel_switch.setFont(px_font(9, QFont.Weight.DemiBold))
        self._panel_switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._panel_switch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._panel_switch.setStyleSheet(btn_css("dim", align="center"))
        self._panel_switch.clicked.connect(self._flip_tasks_panel)
        self._tasks_panel.add_header_widget(self._panel_switch)
        self._tasks_tmr = QTimer(self)
        self._tasks_tmr.timeout.connect(self._refresh_tasks)
        self._tasks_tmr.start(2000)
        return self._tasks_panel


    def _build_quick_access(self) -> QWidget:
        """The switches that are used every day, in the column the eye goes to.

        The controls drawer holds forty things, and the ten that are
        touched every day were buried in it behind a group of five other
        groups. They are here now, one press away, and the drawer keeps
        the rest.

        THE BLOCK IS FOUR BY TWO
        ------------------------
        It used to be two by five, and the column was not tall enough for
        it: the panel wanted two hundred and forty-one pixels and had
        a hundred and ninety-two, so the last row of switches hung half
        outside the panel and the two bottom ones could not be pressed.
        Five across and two down fits the column whole, and a block of
        small squares in the corner of the eye reads like a keypad.

        AND THERE ARE EIGHT, NOT TEN
        -----------------------------
        The full screen, the maximize and the minimize were here as well,
        and they were the same three buttons that are in the header and
        in the VENTANA group of the drawer — where they are explained.
        Three buttons that say the same thing in three places is one
        button too many, and the user said there were icons too many.

        The eighth one is the language, which had no way in at all
        except through the drawer.
        """
        panel = HoloPanel(_t("panel.quick"), "")
        grid = QWidget()
        grid.setStyleSheet("background: transparent;")
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(6)

        def _icon(kind, tip, slot):
            b = IconButton(kind, 34)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        _buttons = (
            ("mic", _t("window.mute_tip"), self._toggle_mute),
            ("eye", _t("controls.hud_tip_face"), self._toggle_hud_style),
            ("hand", _t("controls.gesture_tip"), self._toggle_gesture),
            ("grid", _t("window.open_viewer_tip"), self._toggle_viewer),
            ("deck", _t("controls.deck_tip"), self._toggle_blades),
            ("sliders", _t("window.controls_drawer_tip"), self._open_controls),
            ("bell", _t("window.briefing_tip"), self._ask_briefing),
            ("globe", _t("group.language"), self._open_language),
        )
        _COLS = 4
        for _i, (_kind, _tip, _slot) in enumerate(_buttons):
            gl.addWidget(_icon(_kind, _tip, _slot), _i // _COLS, _i % _COLS)
        for _c in range(_COLS):
            gl.setColumnStretch(_c, 1)
        panel.body.addWidget(grid)
        self._quick_panel = panel
        return panel

    def _open_language(self) -> None:
        """The drawer on the language, from the quick access."""
        self._open_group("language")

    def _open_controls(self) -> None:
        """The drawer of the everyday switches, from the quick access."""
        self._open_group("voice")

    def _open_tools(self) -> None:
        """The drawer of the tools, from the quick access."""
        self._open_group("tools")

    def _ask_briefing(self) -> None:
        """The morning briefing, now.

        THE SWITCH WAS ON AND THE MORNING SAID NOTHING. Now the summary
        waits for the voice, and this is the other way in: the user asks
        for it whenever they want it, and the facts are read again at
        that moment — the weather of this minute, the agenda of today.
        """
        if self.on_briefing:
            try:
                self.on_briefing()
                return
            except Exception as exc:                   # noqa: BLE001
                print(f"[window] the briefing could not be asked for: {exc!r}")
        self._log.append_log(_t("window.briefing_not_ready"))


    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        w.setFixedWidth(200)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        sysp = HoloPanel(_t("panel.systems"), _t("label.live"))
        grid_w = QWidget()
        grid_w.setStyleSheet("background: transparent;")
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self._g_cpu = RingGauge("CPU")
        self._g_mem = RingGauge(_t("label.memory"))
        self._g_gpu = RingGauge("GPU")
        self._g_tmp = RingGauge(_t("label.temp"))
        for i, g in enumerate((self._g_cpu, self._g_mem, self._g_gpu, self._g_tmp)):
            grid.addWidget(g, i // 2, i % 2)
        sysp.body.addWidget(grid_w)
        lay.addWidget(sysp)
        self._systems_panel = sysp

        self._net_panel = HoloPanel(_t("panel.network"), "--")
        self._net_spark = Sparkline(samples=60, floor=0.25)
        self._net_panel.body.addWidget(self._net_spark)
        lay.addWidget(self._net_panel)

        stp = HoloPanel(_t("panel.status"), "")
        os_name = "WINDOWS"
        self._row_uptime = DataRow(_t("label.uptime"), "--:--", "GREEN")
        self._row_proc = DataRow(_t("label.processes"), "--")
        # THE NETWORK IS NOT HERE. The panel of the network, right above,
        # carries the rate in its own header and the graph under it; a
        # second line with the same number two centimetres below it was
        # the same thing twice, and the user read it as two different
        # things.
        self._row_os = DataRow(_t("label.platform"), os_name)
        self._row_engine = DataRow(_t("label.engine"), "--", "PRI")
        self._row_voice = DataRow(_t("settings.voice"), "--")
        for row in (self._row_uptime, self._row_proc,
                    self._row_os, self._row_engine, self._row_voice):
            stp.body.addWidget(row)
        lay.addWidget(stp)
        self._status_panel = stp

        # THE EVERYDAY SWITCHES, ON THE OUTSIDE.
        #
        # The controls drawer holds forty things. Most of them are set once
        # and then never touched again, and the ones that ARE touched every
        # day were buried in a drawer behind a group of five other groups.
        #
        # So the eight that are used every day are here, in the column the
        # eye already goes to, and the drawer keeps the rest.
        lay.addWidget(self._build_quick_access(), stretch=1)

        self._refresh_identity_rows()
        return w

    def _flip_tasks_panel(self) -> None:
        to_routines = self._tasks_stack.currentIndex() == 0
        self._tasks_stack.setCurrentIndex(1 if to_routines else 0)
        self._tasks_panel.set_title(_t("panel.routines") if to_routines
                                    else _t("panel.tasks"))
        self._panel_switch.setText(_t("panel.tasks") if to_routines
                                   else _t("panel.routines"))
        self._refresh_tasks()

    def _refresh_routines(self) -> None:
        import time as _time
        try:
            from core.routines import desktop
            eng = desktop.engine()
            items = eng.list() if eng else []
        except Exception:
            eng, items = None, []

        def when(r: dict) -> str:
            try:
                text = eng.describe(r)
            except Exception:
                text = ""
            if r["trigger"]["type"] == "time" and r.get("next_ts") and r.get("enabled"):
                text = f"next {_time.strftime('%a %H:%M', _time.localtime(r['next_ts']))} · {text}"
            return text

        def order(r: dict):
            timed = r["trigger"]["type"] == "time"
            return (not r.get("enabled"), timed, float(r.get("next_ts") or 0) if timed else 0.0)

        items.sort(key=order)
        for i, row in enumerate(self._routine_rows):
            r = dict(items[i], _when=when(items[i])) if i < len(items) else None
            row.set_routine(r)
            row.setVisible(r is not None)
        self._routines_empty.setVisible(not items)
        on = sum(1 for r in items if r.get("enabled"))
        self._tasks_panel.set_code(f"{on} {_t('label.enabled')}" if items else "")

    def _toggle_routine(self, rid: str, enabled: bool) -> None:
        try:
            from core.routines import desktop
            eng = desktop.engine()
            if eng is None:
                return
            r = eng.update(rid, enabled=bool(enabled))
            if r.get("builtin") == "briefing":
                # The MORNING BRIEF switch in the drawer tells the same story.
                desktop.mirror_brief_switch(r["enabled"])
                self._update_brief_btn(r["enabled"])
        except Exception as e:
            self._log.append_log(f"SYS: Routine not changed — {e}")
        self._refresh_routines()

    def _open_routine(self, rid: str) -> None:
        import time as _time
        try:
            from core.routines import desktop
            eng = desktop.engine()
            r = eng.get(rid) if eng else None
        except Exception:
            eng, r = None, None
        if not r:
            return
        stamp = lambda ts: _time.strftime("%Y-%m-%d %H:%M", _time.localtime(ts))  # noqa: E731
        lines = [r["name"], "", f"When: {eng.describe(r)}"]
        if r["trigger"]["type"] == "time" and r.get("next_ts") and r.get("enabled"):
            lines.append(f"Next: {stamp(r['next_ts'])}")
        what = {"say": "Says", "prompt": "Carries out", "task": "Background task"}.get(r["kind"])
        if what and r.get("text"):
            lines.append(f"{what}: {r['text']}")
        for key, value in (r.get("options") or {}).items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                lines += [f"  · {v}" for v in value]
            elif value not in ("", None):
                lines.append(f"{key}: {value}")
        state = "on" if r.get("enabled") else "off"
        lines.append(f"State: {state}{' · held back in quiet hours' if r.get('quiet') else ''}"
                     f" · ran {int(r.get('runs') or 0)} times")
        if r.get("last_run"):
            lines.append(f"Last: {stamp(r['last_run'])} — {r.get('last_result') or ''}")
        lines += ["", "Change it by voice — \"move the briefing to 08:30\", "
                      "\"turn the check-in off\", \"remind me every Friday at 17:00\"."]
        self._show_content("ROUTINE", "\n".join(lines)[:4000])

    def _refresh_tasks(self) -> None:
        if getattr(self, "_tasks_stack", None) is not None and self._tasks_stack.currentIndex() == 1:
            self._refresh_routines()
            return
        try:
            from core.tasks import desktop
            eng = desktop.engine()
            tasks = eng.list(12) if eng else []
        except Exception:
            tasks = []
        active = [t for t in tasks if t["status"] in ("running", "waiting_quota", "queued", "scheduled")]
        shown = (active + [t for t in tasks if t not in active])[:len(self._task_rows)]
        for i, row in enumerate(self._task_rows):
            task = shown[i] if i < len(shown) else None
            row.set_task(task)
            row.setVisible(task is not None)
        self._tasks_empty.setVisible(not shown)
        self._tasks_panel.set_code(f"{len(active)} {_t('label.active')}" if active else "")

    def _open_task(self, tid: str) -> None:
        try:
            from core.tasks import desktop
            eng = desktop.engine()
            t = eng.get(tid) if eng else None
        except Exception:
            t = None
        if not t:
            return
        if t["status"] == "done":
            body = f"{t['title']}\n\n{(t.get('result') or '').strip()}"
            if t.get("report"):
                body += f"\n\n— Full report: {t['report']}"
        else:
            head = (f"{t['title']}\n\n{t['status']} · {t['used']['seconds'] / 60:.1f}/"
                    f"{t['budget']['minutes']} min · {t['used']['rounds']} rounds")
            if t.get("error"):
                head += f"\n{t['error']}"
            lines = [f"· {e['text']}" for e in (t.get("events") or [])[-14:]
                     if e.get("kind") in ("progress", "tool", "note", "state", "finding")]
            body = head + "\n\n" + "\n".join(lines)
        self._show_content("BACKGROUND TASK", body[:4000])

    def _build_right_panel(self) -> QWidget:
        """The right column: the conversation, what you say to it, and the work.

        It used to hold the log and the command line, and the log took most
        of the column because a log is a thing that grows. The tasks had
        nowhere to go and ended up in the LEFT column, under the gauges.

        Now there are three pages and a rail — TALK, SAY IT and WORK. Each
        one gets the whole column while it is the one being used, and the
        column itself does not grow.
        """
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        w.setFixedWidth(300)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._right_rail = SegmentedRail([
            ("talk", _t("tab.conversation")),
            ("say", _t("tab.commands")),
            ("work", _t("tab.tasks")),
        ])
        self._right_rail.changed.connect(self._select_right_page)
        lay.addWidget(self._right_rail)

        self._right_stack = QStackedWidget()
        self._right_stack.setStyleSheet("background: transparent;")
        lay.addWidget(self._right_stack, stretch=1)

        # ── THE CONVERSATION ────────────────────────────────────────────────
        comm = HoloPanel(_t("panel.communications"), _t("label.log"))
        self._log = LogWidget()
        comm.body.addWidget(self._log)
        self._right_stack.addWidget(comm)
        self._comm_panel = comm

        # ── WHAT YOU SAY TO IT ──────────────────────────────────────────────
        cmd = HoloPanel(_t("panel.command"), _t("label.input"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        cmd.body.addWidget(self._drop_zone)

        self._file_hint = QLabel(_t("window.no_file_loaded"))
        self._file_hint.setFont(px_font(10))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._file_hint.setWordWrap(True)
        cmd.body.addWidget(self._file_hint)

        cmd.body.addWidget(self._build_input_row())

        row = QHBoxLayout()
        row.setSpacing(8)
        self._mute_btn = IconButton("mic", 38, _t("window.mic_active"), "GREEN")
        self._mute_btn.setToolTip(_t("window.mute_tip"))
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        row.addWidget(self._mute_btn, 1)
        self._interrupt_btn = IconButton("stop", 38, _t("window.interrupt"), "MUTED_C")
        self._interrupt_btn.setToolTip(_t("window.interrupt_tip"))
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        row.addWidget(self._interrupt_btn, 1)
        cmd.body.addLayout(row)
        self._right_stack.addWidget(cmd)
        self._cmd_panel = cmd

        # ── THE WORK ────────────────────────────────────────────────────────
        self._right_stack.addWidget(self._build_tasks_panel())

        self._select_right_page("talk")
        return w

    def _select_right_page(self, key: str) -> None:
        """One page of the right column at a time."""
        pages = {"talk": 0, "say": 1, "work": 2}
        idx = pages.get(str(key))
        if idx is None:
            return
        self._right_stack.setCurrentIndex(idx)
        rail = getattr(self, "_right_rail", None)
        if rail is not None:
            rail.set_current(str(key))

    # ── the controls drawer ──────────────────────────────────────────────────
    #
    # ONE DRAWER, SEVEN GROUPS.
    #
    # It used to be two drawers with two long lists in them, and the user
    # was right twice over: the button was duplicated, and what was inside
    # was a list of forty things with nothing to say which of them went
    # together.
    #
    # Now there is ONE drawer, a rail of seven groups at the top of it and
    # a stack of pages underneath. The rail is the index; the page is the
    # list. The two header buttons are two doors to the same drawer — the
    # sliders open the everyday switches, the gear opens the tools — so
    # there is nothing on screen that could be mistaken for a second copy.

    _GROUPS = ("voice", "language", "look", "hand", "session", "system", "tools")

    # HOW WIDE THE DRAWER IS.
    #
    # The column of names needs about a hundred pixels and the page needs
    # the rest: the widest group of switches asks for three hundred and
    # three, so the drawer is four hundred and fifty. Wide enough for the
    # longest sentence a control has to carry, and never wider than that.
    _DRAWER_W = 450

    # HOW WIDE THE DECK OF CARDS IS.
    #
    # It is a column of the interface now, between the face and the
    # conversation, so it takes its width from the middle and leaves
    # the other two columns alone. Wide enough for a sentence of news
    # and no wider: a card that is too wide has one very short line.
    _DECK_W = 340

    # HOW TALL THE ROOM UNDER THE HEAD IS.
    #
    # It is where the drawer of the controls stands while it is open and
    # where a report lands while it is being read. It is always there,
    # so the face does not change size every time something arrives.
    _ROOM_H = 300

    def _build_controls_drawer(self) -> QWidget:
        """The switches, in groups. One drawer, two doors."""
        self._BTN_PRI, self._BTN_DIM = btn_css("primary"), btn_css("dim")
        w = HoloPanel(_t("panel.controls"), "", parent=self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.hide()
        self._ctrl_panel = w
        lay = w.body
        lay.setSpacing(6)

        # THE RAIL, STANDING UP.
        #
        # It used to lie down: seven names in a row, and seven names do not
        # fit in three hundred pixels. Every one of them came out cut in
        # half — "IDIOMA" read as "I]OM", "HERRAMIENTAS" as "4MIE" — so the
        # index of the drawer was unreadable. A column has the height it
        # needs, and every name is written whole.
        self._group_rail = GroupRail([
            ("voice", _t("group.voice")),
            ("language", _t("group.language")),
            ("look", _t("group.look")),
            ("hand", _t("group.hand")),
            ("session", _t("group.session")),
            ("system", _t("group.system")),
            ("tools", _t("group.tools")),
        ])
        self._group_rail.changed.connect(self._select_group)

        self._group_stack = QStackedWidget()
        self._group_stack.setStyleSheet("background: transparent;")
        self._group_pages: dict[str, QWidget] = {}
        self._group_layouts: dict[str, QVBoxLayout] = {}
        for _key in self._GROUPS:
            _page = QWidget()
            _page.setStyleSheet("background: transparent;")
            _pl = QVBoxLayout(_page)
            _pl.setContentsMargins(0, 0, 0, 0)
            _pl.setSpacing(5)
            self._group_pages[_key] = _page
            self._group_layouts[_key] = _pl
            self._group_stack.addWidget(_page)

        # THE PAGE SCROLLS.
        #
        # The groups are not the same height, and the drawer is not as tall
        # as the tallest one: the voice group has more switches than the
        # language group has lines. A list that does not fit is a list that
        # is cut in half, and the half that is missing is the half nobody
        # complained about — which is how a setting goes missing for ever.
        # So the page scrolls instead of hiding.
        self._group_scroll = QScrollArea()
        self._group_scroll.setWidget(self._group_stack)
        self._group_scroll.setWidgetResizable(True)
        self._group_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._group_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # THE BACKGROUND OF THE PAGE IS THE BACKGROUND OF THE DRAWER.
        #
        # The viewport of a scroll area that has no sheet of its own keeps
        # the grey of the system palette, and it shows between the controls
        # like a stripe of dust across the panel. The sheet has to name the
        # widget and the viewport: a bare declaration is not a rule, and a
        # rule that does not parse leaves the grey exactly where it was.
        self._group_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }\n"
            "QScrollArea > QWidget > QWidget { background: transparent; }\n"
            + scroll_css())
        self._group_scroll.viewport().setStyleSheet(
            "background: transparent;")

        # The column on the left, the page on the right: the drawer reads
        # the way a settings dialog reads.
        _split = QHBoxLayout()
        _split.setContentsMargins(0, 0, 0, 0)
        _split.setSpacing(8)
        _split.addWidget(self._group_rail)
        _split.addWidget(self._group_scroll, stretch=1)
        lay.addLayout(_split, stretch=1)

        def _row(layout, button, style=None):
            button.setFixedHeight(30)
            button.setFont(px_font(11, QFont.Weight.DemiBold, spacing=1.2))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if style:
                button.setStyleSheet(style)
            layout.addWidget(button)
            return button

        def _btn(layout, text, slot, style=None):
            b = QPushButton(text)
            b.setFixedHeight(30)
            b.setFont(px_font(11, QFont.Weight.DemiBold, spacing=1.2))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(style or self._BTN_DIM)
            b.clicked.connect(slot)
            layout.addWidget(b)
            return b

        def _cap(layout, text):
            lbl = QLabel(text)
            lbl.setFont(px_font(9))
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            # THE TEXT OF THE CONTROLS CAN BE COPIED.
            #
            # The user asked to copy what the controls say and paste it to
            # the assistant: "no deja seleccionar el texto". A label that
            # cannot be selected is a sentence that has to be read aloud
            # and typed by hand, and a setting that cannot be copied
            # cannot be reported.
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction)
            layout.addWidget(lbl)
            return lbl

        _COMBO_CSS = f"""
            QComboBox {{
                background: {C.PANEL2}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
            }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL2}; color: {C.WHITE};
                selection-background-color: {C.PRI_DIM};
            }}
        """

        def _combo(layout, items, tip, slot=None):
            box = QComboBox()
            box.setFixedHeight(30)
            box.setFont(px_font(11))
            for _label, _value in items:
                box.addItem(_label, _value)
            box.setStyleSheet(_COMBO_CSS)
            box.setToolTip(tip)
            if slot is not None:
                box.currentIndexChanged.connect(slot)
            layout.addWidget(box)
            return box

        # ── VOZ ──────────────────────────────────────────────────────────────
        #
        # How the assistant listens and how it answers. The switches that get
        # flicked every day, all in one place, because they are the ones
        # that are used every day.
        voice = self._group_layouts["voice"]
        _cap(voice, _t("drawer.voice.explain"))

        self._wake_btn = _row(voice, QPushButton())
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        self._wake_sleep_btn = _row(voice, QPushButton())
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        # Neutral until the real state lands from the background warm-up.
        self._wake_btn.setText(_t("controls.wake_word"))
        self._wake_btn.setStyleSheet(self._BTN_DIM)
        self._wake_sleep_btn.hide()

        # The phrase the assistant answers to. It is a setting, not a constant:
        # renaming the assistant should not leave the wake word behind, and a
        # user who wants to call it something else must be able to. It is
        # stored with the rest of the config and read afresh wherever it is
        # shown, so a change is visible immediately.
        self._wake_phrase_edit = QLineEdit()
        self._wake_phrase_edit.setFixedHeight(30)
        self._wake_phrase_edit.setFont(px_font(11))
        self._wake_phrase_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
            }}
            QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
        """)
        self._wake_phrase_edit.setToolTip(_t("controls.wake_phrase_tip"))
        self._wake_phrase_edit.editingFinished.connect(self._save_wake_phrase)
        voice.addWidget(self._wake_phrase_edit)

        # HOW IT LISTENS: one phrase, or any voice.
        #
        # The bundled wake model was trained for ONE phrase, and it is not the
        # phrase this product is named after — it cannot be, because openwakeword
        # resolves a model by the exact file name it was published under. So the
        # product offered two ways out: train a model for your own phrase, or
        # stop looking for a phrase at all and wake on any speech, which is
        # what the voice-activity detector does and needs no training.
        #
        # The user was never offered that choice, and the interface simply
        # showed a phrase the microphone could not hear. That is the bug this
        # row exists to close.
        self._wake_mode_combo = QComboBox()
        self._wake_mode_combo.setFixedHeight(30)
        self._wake_mode_combo.setFont(px_font(11))
        self._wake_mode_combo.addItem(_t("wake.mode_phrase"), "phrase")
        self._wake_mode_combo.addItem(_t("wake.mode_voice"), "voice")
        self._wake_mode_combo.setStyleSheet(_COMBO_CSS)
        self._wake_mode_combo.setToolTip(_t("controls.wake_mode_tip"))
        self._wake_mode_combo.currentIndexChanged.connect(self._save_wake_mode)
        voice.addWidget(self._wake_mode_combo)

        # WHAT THE MICROPHONE IS ACTUALLY LISTENING FOR.
        #
        # The field above holds what the user WANTS to say. This line holds
        # what the machine can actually HEAR, and the two are not the same
        # unless a matching model is installed. Saying so is the difference
        # between a setting and a promise the product cannot keep.
        self._wake_effective_lbl = QLabel("")
        self._wake_effective_lbl.setFont(px_font(9))
        self._wake_effective_lbl.setWordWrap(True)
        self._wake_effective_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;")
        voice.addWidget(self._wake_effective_lbl)
        self._refresh_wake_phrase_field()

        self._ptt_btn = _row(voice, QPushButton())
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        self._refresh_talk_btns()

        self._hud_btn = _row(voice, QPushButton())
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        self._refresh_hud_btn()

        self._brief_btn = _row(voice, QPushButton())
        self._brief_btn.clicked.connect(self._toggle_brief)

        # ── IDIOMA ───────────────────────────────────────────────────────────
        #
        # Two switches that decide who the product is talking to: the
        # language it speaks, and the place it believes the user is in. Both
        # are stored, both are read back at startup, and both change the
        # interface the moment they are touched.
        #
        # The region box is EDITABLE on purpose: the table of cities is a
        # convenience, not a cage, and somebody who lives somewhere the
        # product has never heard of still has to be able to say so.
        language = self._group_layouts["language"]
        _cap(language, _t("drawer.language.explain"))

        self._language_caption = _cap(language, _t("settings.language"))
        self._language_combo = QComboBox()
        self._language_combo.setFixedHeight(30)
        self._language_combo.setFont(px_font(11))
        for _lang in localization.LANGUAGES:
            # The NATIVE name, because that is how a person asks for the
            # language they speak.
            self._language_combo.addItem(_lang.name_native, _lang.code)
        self._language_combo.setStyleSheet(_COMBO_CSS)
        self._language_combo.setToolTip(_t("selector.language_tip"))
        self._language_combo.currentIndexChanged.connect(self._save_language)
        language.addWidget(self._language_combo)

        self._region_caption = _cap(language, _t("settings.region"))
        self._region_combo = QComboBox()
        self._region_combo.setEditable(True)
        self._region_combo.setFixedHeight(30)
        self._region_combo.setFont(px_font(11))
        for _region in localization.regions():
            self._region_combo.addItem(localization.region_label(_region),
                                       (_region.city, _region.country))
        self._region_combo.setStyleSheet(_COMBO_CSS + f"""
            QLineEdit {{
                background: {C.PANEL2}; color: {C.WHITE};
                border: none; padding: 0px;
            }}
        """)
        self._region_combo.setToolTip(_t("selector.region_tip"))
        self._region_combo.currentIndexChanged.connect(self._save_region)
        # An editable combo box has no `editingFinished` of its own — that
        # signal belongs to the line edit inside it, which is where the user
        # types a city that is not on the list.
        _region_edit = self._region_combo.lineEdit()
        if _region_edit is not None:
            _region_edit.editingFinished.connect(self._save_region)
        language.addWidget(self._region_combo)

        # One sentence, two lines at most: what the region is for, and what
        # the language changes.
        self._selector_note = _cap(language, _t("selector.explain"))

        # ── ASPECTO ──────────────────────────────────────────────────────────
        #
        # The three faces of the assistant, the corner the core sits in, and
        # the four shapes the window can take. All of it is applied the
        # moment it is touched: a face that changes only on the next restart
        # is a setting the user cannot see.
        look = self._group_layouts["look"]
        _cap(look, _t("drawer.look.explain"))

        self._appearance_caption = _cap(look, _t("appearance.title"))
        self._appearance_combo = _combo(look, [
            (_t("appearance.dashboard"), "dashboard"),
            (_t("appearance.core"), "core"),
            (_t("appearance.tray"), "tray"),
        ], _t("appearance.mode_tip"), self._save_appearance)

        self._corner_caption = _cap(look, _t("appearance.corner"))
        self._corner_combo = _combo(look, [
            (_t("appearance.corner." + _c.replace("-", "_")), _c)
            for _c in ("bottom-right", "bottom-left", "top-right", "top-left")
        ], _t("appearance.corner_tip"), self._save_corner)

        # THE THREE SHAPES OF THE WINDOW, each one named.
        #
        # One button that toggled "the whole screen" left the user guessing
        # what the other half of the pair did, and there was no way at all to
        # ask for "make it big but keep the taskbar" without leaving the
        # drawer and hunting for the buttons of Windows. Three named buttons
        # answer the three real wishes.
        #
        # AND NONE OF THEM REPEATS ANOTHER. There were four: "maximizar",
        # "pantalla completa", "ventana" and "minimizar" — and the first
        # one turned into "ventana" when the window was maximized, so two
        # buttons said the same word and the user could not tell which was
        # which. Now the maximize button is the one that goes there and
        # comes back, and the word that tells it to is on it.
        #
        # AND THE THREE SAY WHAT THEY DO. The user asked what the difference
        # was between them, and a button that only says "MAXIMIZAR" does not
        # answer it: a line under them says that the maximized window keeps
        # the taskbar, the full screen does not, and the minimized one leaves
        # only the icon.
        _cap(look, _t("drawer.window_shapes.explain"))
        self._maximize_btn = _row(look, QPushButton(_t("window.maximize")),
                                  self._BTN_DIM)
        self._maximize_btn.clicked.connect(self._maximize_window)
        self._fullscreen_btn = _row(look, QPushButton(_t("controls.fullscreen")),
                                    self._BTN_DIM)
        self._fullscreen_btn.setToolTip(_t("window.fullscreen_tip"))
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        self._minimize_btn = _row(look, QPushButton(_t("window.minimize")),
                                  self._BTN_DIM)
        self._minimize_btn.clicked.connect(self._minimize_window)

        # ── MANO ─────────────────────────────────────────────────────────────
        #
        # The cursor you steer with: the camera watches, the skeleton of
        # the hand is drawn on the glass, the fingertip is the cursor and
        # the pinch is a click. ONE hand: with two, the pointer jumped
        # from one to the other.
        hand = self._group_layouts["hand"]
        _cap(hand, _t("drawer.hand.explain"))

        self._gesture_btn = _row(hand, QPushButton(_t("controls.gesture")),
                                 self._BTN_DIM)
        self._gesture_btn.setToolTip(_t("controls.gesture_tip"))
        self._gesture_btn.clicked.connect(self._toggle_gesture)

        self._gesture_pointer_btn = _row(
            hand, QPushButton(_t("controls.gesture_pointer")), self._BTN_DIM)
        self._gesture_pointer_btn.setToolTip(_t("controls.gesture_pointer_tip"))
        self._gesture_pointer_btn.clicked.connect(self._toggle_gesture_pointer)

        # THE GESTURES THAT TOUCH THE DESKTOP.
        #
        # These change the window, take a screenshot and answer what is
        # waiting on screen, so they are OFF until the user says so: a
        # palm that is pushed up while the hand is resting should not
        # maximise a window that the user was reading.
        self._gesture_advanced_btn = _row(
            hand, QPushButton(_t("controls.gesture_advanced")), self._BTN_DIM)
        self._gesture_advanced_btn.setToolTip(_t("controls.gesture_advanced_tip"))
        self._gesture_advanced_btn.clicked.connect(self._toggle_gesture_advanced)

        # THE LEGEND, because a gesture nobody can check is a guess.
        self._gesture_legend = QLabel(_t("gesture.legend"))
        self._gesture_legend.setFont(px_font(9))
        self._gesture_legend.setWordWrap(True)
        self._gesture_legend.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;")
        hand.addWidget(self._gesture_legend)

        # ── SESIÓN ───────────────────────────────────────────────────────────
        #
        # The deck is the session on one screen: every answer the assistant
        # gave, one card each. The floating panel is the same idea over
        # somebody else's window: it follows the assistant and can be dragged
        # anywhere with the mouse or with the hand.
        session = self._group_layouts["session"]
        _cap(session, _t("drawer.session.explain"))
        _cap(session, _t("drawer.deck.explain"))

        self._deck_btn_ctrl = _row(session, QPushButton(_t("controls.deck")),
                                   self._BTN_DIM)
        self._deck_btn_ctrl.setToolTip(_t("controls.deck_tip"))
        self._deck_btn_ctrl.clicked.connect(self._toggle_blades)

        _cap(session, _t("drawer.floating.explain"))
        self._floating_btn = _row(session, QPushButton(_t("controls.floating")),
                                  self._BTN_DIM)
        self._floating_btn.setToolTip(_t("controls.floating_tip"))
        self._floating_btn.clicked.connect(self._toggle_floating)

        # The ignition can be watched again. It is a show, and the user is
        # allowed to watch a show twice.
        self._boot_btn = _row(session, QPushButton(_t("controls.replay_boot")),
                              self._BTN_DIM)
        self._boot_btn.setToolTip(_t("controls.replay_boot_tip"))
        self._boot_btn.clicked.connect(self._replay_boot_clicked)

        # Which operator's tools the model is offered. Forty-five tools is a
        # lot of prompt for a conversation about one of them, and this is the
        # switch that narrows it — but the tool list is fixed when the session
        # connects, so the change takes effect on the next connection, which
        # the app arranges by reconnecting.
        self._ctx_combo = QComboBox()
        self._ctx_combo.setFixedHeight(30)
        self._ctx_combo.setFont(px_font(11))
        self._ctx_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL2}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 2px 7px;
            }}
            QComboBox:focus {{ border-color: {C.PRI_DIM}; }}
            QComboBox QAbstractItemView {{
                background: {C.PANEL2}; color: {C.WHITE};
                selection-background-color: {C.PRI_GHO};
            }}
        """)
        self._ctx_combo.setToolTip(_t("controls.context_tip"))
        self._ctx_combo.activated.connect(self._on_tool_context)
        session.addWidget(self._ctx_combo)
        self._refresh_context_combo()

        # ── SISTEMA ──────────────────────────────────────────────────────────
        #
        # Spotify or YouTube, and the user picks which one. Automatic means
        # "Spotify when it is really installed here, YouTube otherwise" — and
        # the tool used to try Spotify first on a machine where Spotify was
        # not installed at all, which cost half a minute and opened the
        # wrong player on the way to the right one.
        system = self._group_layouts["system"]
        _cap(system, _t("drawer.system.explain"))

        self._music_caption = _cap(system, _t("music.service"))
        self._music_combo = _combo(system, [
            (_t("music.service_auto"), "auto"),
            (_t("music.service_spotify"), "spotify"),
            (_t("music.service_youtube"), "youtube"),
        ], _t("music.service_tip"), self._save_music_service)
        self._load_music_service()

        # THE LEGEND OF THE STAR.
        #
        # A control marked with a star is there and works as far as it
        # goes, but the rest of it is not written yet. Saying so in the
        # panel is better than a control that quietly does nothing.
        self._pending_note = QLabel(_t("controls.pending"))
        self._pending_note.setFont(px_font(9))
        self._pending_note.setWordWrap(True)
        self._pending_note.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;")
        self._pending_note.setToolTip(_t("controls.pending_tip"))
        system.addWidget(self._pending_note)

        # ── HERRAMIENTAS ─────────────────────────────────────────────────────
        #
        # The things that are set once: the phone, the shortcut, the devices,
        # the memory, the plugins, the engine.
        #
        # EACH ONE SAYS WHAT IT IS. The group used to be nine buttons with
        # nine names and no explanation, and the user could not tell what
        # any of them was for — "no veo orden y diseño futurista ahí". A
        # line under each button says what it opens, and the group says
        # that each of them opens a window of its own, which is the thing
        # that made them look like loose buttons.
        tools = self._group_layouts["tools"]
        _cap(tools, _t("drawer.tools.explain"))

        _cap(tools, _t("drawer.tool.remote"))
        _btn(tools, _t("controls.remote"), self._open_remote, self._BTN_PRI)
        _cap(tools, _t("drawer.tool.shortcut"))
        _btn(tools, _t("controls.shortcut"), self._create_desktop_shortcut)
        _cap(tools, _t("drawer.tool.autostart"))
        self._autostart_btn = _btn(tools, _t("controls.autostart_off"),
                                   self._toggle_autostart)
        _cap(tools, _t("drawer.tool.customise"))
        _btn(tools, _t("controls.customise"), self._open_customize)
        _cap(tools, _t("drawer.tool.audio"))
        _btn(tools, _t("controls.audio_devices"), self._open_audio_devices)
        _cap(tools, _t("drawer.tool.memory"))
        _btn(tools, _t("controls.memory"), self._open_memory_panel)
        _cap(tools, _t("drawer.tool.plugins"))
        _btn(tools, _t("controls.plugins"), self._open_plugin_manager)
        _cap(tools, _t("drawer.tool.permissions"))
        _btn(tools, _t("controls.permissions"), self._open_permissions)
        _cap(tools, _t("drawer.tool.plugin_settings"))
        _btn(tools, _t("controls.plugin_settings"), self._open_plugin_settings)
        # The button is built here with the rest of the drawer; the panel behind
        # it — and the whole providers package — is imported on the click. The
        # drawer used to stutter for a measured 2.104 seconds because a state
        # check imported openwakeword while it was being built, and adding a
        # second thing that does that would undo the fix rather than repeat it.
        # "&&": a single & in a button label is Qt's shortcut marker, and it
        # was being drawn as an underscore — "ENGINE _PROVIDERS".
        _cap(tools, _t("drawer.tool.providers"))
        _btn(tools, _t("controls.engine_providers"), self._open_providers)

        for _page in self._group_pages.values():
            _page.layout().addStretch()

        self._refresh_language_combo()
        self._refresh_region_combo()
        self._refresh_appearance_combo()
        self._select_group("voice")

        # THE BUTTON THAT COPIES THE CONTROLS.
        #
        # The user wants to copy what the controls say and paste it to the
        # assistant. The labels can be selected one by one, but a whole
        # group is what he actually wants to report: "these are the
        # settings I have". So the group he is looking at is written to
        # the clipboard as text, with the name of every control and the
        # value it has.
        self._copy_btn = QPushButton(_t("drawer.copy"))
        self._copy_btn.setFont(px_font(8, QFont.Weight.Bold))
        self._copy_btn.setFixedHeight(20)
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setToolTip(_t("drawer.copy_tip"))
        self._copy_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 1px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        self._copy_btn.clicked.connect(self._copy_group_text)
        w.add_header_widget(self._copy_btn)

        w.adjustSize()
        return w

    # ── copying the controls ────────────────────────────────────────────────

    def _group_text(self, key: str) -> str:
        """The open group written out as text: what it is and what it holds."""
        page = self._group_pages.get(key)
        if page is None:
            return ""
        rows: list[str] = []
        for widget in page.findChildren(QWidget):
            try:
                if not widget.isVisible():
                    continue
                if isinstance(widget, QLabel):
                    text = widget.text().strip()
                    if text:
                        rows.append(text)
                elif isinstance(widget, QPushButton):
                    rows.append(widget.text().strip())
                elif isinstance(widget, QComboBox):
                    rows.append(f"{widget.currentText().strip()}")
                elif isinstance(widget, QLineEdit):
                    rows.append(f"{widget.text().strip()}")
            except Exception:
                continue
        title = ""
        rail = getattr(self, "_group_rail", None)
        if rail is not None:
            try:
                title = rail.text_of(key)
            except Exception:
                title = ""
        head = title or str(key)
        return "\n".join([head] + [f"- {row}" for row in rows])

    def _copy_group_text(self) -> None:
        """Put the open group on the clipboard, so it can be pasted and sent."""
        key = str(getattr(self, "_last_group", "") or "")
        text = self._group_text(key)
        if not text:
            return
        try:
            QApplication.clipboard().setText(text)
        except Exception:
            return
        try:
            self._copy_btn.setText(_t("drawer.copied"))
        except Exception:
            return
        QTimer.singleShot(1600, lambda: self._reset_copy_label())

    def _reset_copy_label(self) -> None:
        try:
            self._copy_btn.setText(_t("drawer.copy"))
        except Exception:
            pass

    def _select_group(self, key: str) -> None:
        """Open one group of the drawer.

        The rail and the stack are two views of the same thing, so this is
        the only place that decides which group is open. It never emits the
        change onwards: the rail is lit from here, not the other way round,
        or the two would talk to each other for ever.
        """
        key = str(key)
        page = self._group_pages.get(key)
        if page is None:
            return
        # THE LAST GROUP IS REMEMBERED, because the button of the header
        # opens the drawer on it: one door, and it opens on the group
        # the user was using last.
        self._last_group = key
        self._group_stack.setCurrentWidget(page)
        rail = getattr(self, "_group_rail", None)
        if rail is not None:
            rail.set_current(key)
        # THE DRAWER IS AS TALL AS THE GROUP IT SHOWS.
        #
        # The groups are not the same height — the one with the four
        # modes of the window is taller than the one with the language —
        # and a drawer with a fixed height clips the tallest one.
        drawer = getattr(self, "_ctrl_drawer", None)
        if drawer is not None and drawer.isVisible():
            self._position_ctrl_drawer()

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """
        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    # ── the one door of the drawer ──────────────────────────────────────────

    def _toggle_controls(self, checked: bool):
        """The gear: the drawer of the controls.

        ONE DOOR FOR SEVEN GROUPS. It used to be two doors — the gear
        for the tools and the sliders for the everyday switches — and the
        user could not tell them apart: two buttons, the same drawer. The
        drawer has a rail of seven groups inside it, so the button does
        not have to choose; it opens the group that was open last.

        WHERE IT OPENS DEPENDS ON THE WINDOW. When the window is wide
        enough the drawer is a COLUMN of the interface, right next to the
        face of the assistant — the same thing the deck of cards is, and
        it takes its width from the middle instead of hovering over it.
        The face does not move, nothing is covered, and the open group is
        seen whole from top to bottom.

        It used to drop to the bottom of the middle column, under the
        face, and the user could not tell what it belonged to: "abro
        controles y se va para abajo". In a narrow window there is no room
        for a third column, and the drawer goes back to standing in the
        room under the head, the way a menu does.
        """
        drawer = getattr(self, "_ctrl_drawer", None)
        if drawer is None:
            return
        if checked:
            self._refresh_wake_btns()   # cheap now: the state was warmed at boot
            self._select_group(getattr(self, "_last_group", "") or "voice")
            if self._drawer_fits_as_column():
                self._set_drawer_as_column(True)
            self._position_ctrl_drawer()
            drawer.show()
            drawer.raise_()
        else:
            drawer.hide()
            self._face_covered(False)

    def _open_group(self, key: str) -> None:
        """Open the drawer on one group in particular."""
        self._drawer_btn.setChecked(True)
        self._refresh_wake_btns()
        self._select_group(key)
        self._position_ctrl_drawer()
        drawer = getattr(self, "_ctrl_drawer", None)
        if drawer is not None:
            drawer.show()
            drawer.raise_()

    def _toggle_drawer(self, checked: bool):
        """The old name of the one door: the drawer of the controls.

        There is ONE door and it opens the group that was open last. This
        name is kept because other code reached for it; it is the same
        door, not a second one.
        """
        self._toggle_controls(checked)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        self._face_covered(False)
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _drawer_zone(self):
        """The empty room under the head, when it is empty.

        Under the face of the assistant there is a panel that stays blank
        until there is something to put in it: a notification, a search
        result, the viewer, the questions. While it is blank it is a room
        with nothing in it, and the drawer of the controls can stand there
        without covering a thing.

        The answer is None when that room is taken — the drawer then hangs
        from the button that opened it, the way a menu does.
        """
        stack = getattr(self, "_lower_stack", None)
        blank = getattr(self, "_lower_blank", None)
        central = self.centralWidget()
        if stack is None or blank is None or central is None:
            return None
        try:
            if stack.currentWidget() is not blank:
                return None
            if not stack.isVisible():
                return None
            top_left = stack.mapTo(central, QPoint(0, 0))
            rect = QRect(top_left.x(), top_left.y(),
                         stack.width(), stack.height())
        except Exception:
            return None
        # A room too small for the drawer is not a room for the drawer.
        if rect.height() < 260 or rect.width() < self._DRAWER_W + 16:
            return None
        return rect

    def _face_covered(self, covered: bool) -> None:
        """The core steps aside while the controls are in the center.

        THE FACE IS NOT HIDDEN FOR GOOD. The drawer takes the middle of
        the column because the controls belong where the core was, and
        the core is put back the moment the drawer is closed.
        """
        hud = getattr(self, "_hud_cam_stack", None)
        if hud is None:
            return
        covered = bool(covered)
        try:
            if hud.isHidden() != covered:
                hud.setVisible(not covered)
        except Exception:
            pass

    def _drawer_column(self):
        """The whole column of the middle, when the room under the head is free.

        THE CONTROLS TAKE THE CENTER OF THE COLUMN.

        The drawer used to stand in the room under the head, and the room
        is at the bottom of the window: the user opened the controls and
        they went down, far from the button that opened them, and the
        core of the assistant was left alone in the middle of the column
        with nothing else around it.

        When the room is empty there is nothing to cover, so the drawer
        takes the column — the same place where the core was, from the
        top of the face down to the bottom of the window. The controls
        come to the center, and they are as tall as the space there is,
        so the list of switches does not have to be scrolled to be read.
        """
        stack = getattr(self, "_lower_stack", None)
        blank = getattr(self, "_lower_blank", None)
        split = getattr(self, "_center_split", None)
        central = self.centralWidget()
        if stack is None or blank is None or split is None or central is None:
            return None
        try:
            if stack.currentWidget() is not blank:
                return None
            if not stack.isVisible():
                return None
            top_left = split.mapTo(central, QPoint(0, 0))
            rect = QRect(top_left.x(), top_left.y(), split.width(), split.height())
        except Exception:
            return None
        # A column too narrow for the drawer is not a column for the drawer.
        if rect.height() < 340 or rect.width() < self._DRAWER_W + 20:
            return None
        return rect

    def _drawer_fits_as_column(self) -> bool:
        """Is there room in the middle column for the drawer as a column?

        THE DRAWER IS A COLUMN WHEN THE WINDOW IS WIDE ENOUGH. It needs
        its four hundred and fifty pixels, the face needs three hundred
        and forty (the face, the handle of the splitter and the air), and
        the deck of cards needs two hundred more. When all three fit, the
        drawer stands next to the face. When they do not, the drawer goes
        back to the room under the head.
        """
        try:
            central = self.centralWidget().width()
            left = self._left_panel.maximumWidth()
            right = self._right_panel.maximumWidth()
        except Exception:
            return False
        centre_min = 340          # the face, the handle and the air
        spare = central - left - right - 30 - 20 - centre_min
        return spare >= self._DRAWER_W + 200

    def _set_drawer_as_column(self, on: bool) -> None:
        """The drawer is a column of the interface, or it is not.

        A widget is either in the layout or it floats over it, and the
        drawer does both depending on the window: as a column it is
        placed by the layout (full height, nothing covered), and as a
        panel it is placed by hand in the room under the head.
        """
        drawer = getattr(self, "_ctrl_drawer", None)
        body = getattr(self, "_body_layout", None)
        if drawer is None or body is None:
            return
        if on:
            if self._drawer_in_column:
                return
            try:
                at = body.indexOf(self._blades)
                body.insertWidget(at + 1 if at >= 0 else body.count(), drawer)
                drawer.setFixedWidth(self._DRAWER_W)
                drawer.setMaximumHeight(16777215)
                self._drawer_in_column = True
            except Exception:
                self._drawer_in_column = False
            return
        if not self._drawer_in_column:
            return
        self._drawer_in_column = False
        try:
            # Out of the layout and back to being a panel over the window.
            drawer.setParent(self.centralWidget())
            drawer.setFixedWidth(self._DRAWER_W)
        except Exception:
            pass

    def _position_ctrl_drawer(self):
        """Where the drawer hangs.

        THE EMPTY ROOM UNDER THE HEAD
        ------------------------------
        The drawer used to drop from the button in the header and land on
        the face of the assistant: the user asked for the controls and
        got the controls with the face in the way.

        So the drawer stands in the room under the head while that room is
        empty — the assistant stays visible, the controls have a place of
        their own, and nothing is covered. When the room is taken (the
        viewer is open, a notification is there), the drawer goes back to
        hanging under the button that opened it, the way a menu does.
        """
        drawer = getattr(self, "_ctrl_drawer", None)
        if drawer is None:
            return
        if self._drawer_in_column:
            # IT IS A COLUMN: the layout places it, and there is nothing
            # to place by hand.
            self._ctrl_in_zone = False
            self._face_covered(False)
            return

        # THE CENTER OF THE COLUMN FIRST.
        #
        # The controls come to where the core is, and the core steps
        # aside while they are there. The room under the head is for the
        # reports — a news item, a search result, the viewer — and when
        # there is one, the drawer hangs from its button instead.
        column = self._drawer_column()
        if column is not None:
            width = min(self._DRAWER_W, column.width())
            x = column.x() + (column.width() - width) // 2
            drawer.setFixedWidth(width)
            # IT IS AS TALL AS THE GROUP IT SHOWS. A panel of glass the
            # height of the whole column with five switches in it is a
            # panel with nothing in it; the drawer ends where the last
            # control ends, and it never grows taller than the column.
            try:
                drawer.adjustSize()
            except Exception:
                pass
            height = max(200, min(column.height(), drawer.height()))
            drawer.setGeometry(x, column.y(), width, height)
            self._ctrl_in_zone = True
            self._face_covered(True)
            return
        self._face_covered(False)

        zone = self._drawer_zone()
        self._ctrl_in_zone = zone is not None
        if zone is not None:
            # IT NEVER IS WIDER THAN THE ROOM IT STANDS IN. With the deck
            # of cards open the room is three hundred and forty pixels
            # wide, and a drawer of four hundred and fifty spilled over
            # the two columns next to it.
            width = max(280, min(self._DRAWER_W, zone.width()))
            drawer.setFixedWidth(width)
            x = zone.x() + (zone.width() - width) // 2
            drawer.setGeometry(x, zone.y(), width, zone.height())
            return

        left = 12
        button = self._drawer_btn
        try:
            left = max(12, button.mapTo(self.centralWidget(),
                                         QPoint(0, 0)).x() - 8)
        except Exception:
            pass
        drawer.setFixedWidth(self._DRAWER_W)
        drawer.adjustSize()
        top = self._header.height() + 2 if hasattr(self, "_header") else 60
        # IT NEVER GOES PAST THE FOOTER.
        #
        # The drawer is as tall as the group it shows, and the group
        # with the four modes of the window is the tallest of the seven.
        # Without a ceiling the drawer reached the bottom of the window
        # and covered the bar with the clock — the user called it "it
        # goes all the way down", and it was true.
        room = max(220, self.height() - top - 52)
        # AND IT NEVER COVERS THE REPORT.
        #
        # When the room under the head is taken, the drawer hangs from
        # its button, the way a menu does — and it hung over the report
        # that had arrived, so the first line of the report and its
        # title were under the glass. The drawer stops where the report
        # begins.
        ceiling = self._content_top()
        if ceiling is not None:
            room = min(room, max(220, ceiling - top))
        drawer.setGeometry(left, top, self._DRAWER_W,
                           min(drawer.sizeHint().height(), room))

    def _content_top(self) -> int | None:
        """Where the panel under the head begins, in the coordinates of the
        central widget — the same ones the drawer is placed in.

        None when there is no panel there: the room is empty and the
        drawer can be as tall as it wants.
        """
        stack = getattr(self, "_lower_stack", None)
        blank = getattr(self, "_lower_blank", None)
        central = self.centralWidget()
        if stack is None or blank is None or central is None:
            return None
        try:
            if stack.currentWidget() is blank:
                return None
            if not stack.isVisible():
                return None
            return int(stack.mapTo(central, QPoint(0, 0)).y())
        except Exception:
            return None

    def _build_input_row(self) -> QWidget:
        box = CommandFrame()
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 0, 4, 0)
        row.setSpacing(6)
        prompt = QLabel("›")
        prompt.setFont(px_font(20, QFont.Weight.Light))
        prompt.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        row.addWidget(prompt)

        self._input = QLineEdit()
        self._input.setPlaceholderText(_t("window.type_command"))
        self._input.setFont(px_font(13))
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; color: {C.WHITE};
                border: none; padding: 0;
                selection-background-color: {C.PRI_GHO};
            }}
        """)
        self._input.returnPressed.connect(self._send)
        # While the user writes, the clipboard panel gets out of the way: it
        # was designed for a moment when nothing else was happening.
        self._input.textChanged.connect(lambda *_: self._hide_clipboard_panel())
        row.addWidget(self._input, stretch=1)

        send = IconButton("send", 32)
        send.setToolTip(_t("window.send_tip"))
        send.clicked.connect(self._send)
        row.addWidget(send)
        return box

    def _build_content_panel(self) -> QWidget:
        """
        Panel below the HUD — shows search results, news, briefings, reviews.
        Hidden by default; appears when show_content() is called.

        IT IS A NOTIFICATION, NOT A PAGE.
        -------------------------------
        The user read the news and wanted it OUT OF THE WAY — closable with
        the mouse and gone on its own. So the panel carries a line along its
        bottom edge that runs out, and the panel goes when the line does.
        The clock stops while the pointer is over it, because a
        notification that vanishes while it is being read is worse than no
        notification at all.
        """
        w = HoloPanel(_t("panel.briefing"), "")
        w.setObjectName("ContentPanel")
        w.hide()
        # The panel paints its own title and time; these stand in for the
        # labels that used to hold them, so everything that sets them still does.
        self._content_title_lbl = PanelText(w, "title")
        self._content_ts_lbl = PanelText(w, "code")

        dismiss = IconButton("close", 22)
        dismiss.setToolTip(_t("window.dismiss"))
        dismiss.clicked.connect(self._hide_content)
        w.add_header_widget(dismiss)

        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(px_font(13))
        self._content_display.setMinimumHeight(60)
        self._content_display.setFrameShape(QFrame.Shape.NoFrame)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                selection-background-color: {C.PRI_GHO};
            }}
        """ + scroll_css())
        w.body.addWidget(self._content_display)

        # THE LINE THAT RUNS OUT.
        self._content_bar = CountdownBar(w)
        w.body.addWidget(self._content_bar)

        # Every piece of the panel watches the pointer, so that the clock
        # stops while the user reads the text and not only while the
        # pointer is on the frame around it.
        self._content_hover = (w, self._content_display, dismiss)
        for _part in self._content_hover:
            _part.installEventFilter(self)

        # How long a notification stays. Long enough to read a headline and
        # a couple of lines; short enough that it is a notification.
        self._content_default_life = 20.0
        self._content_life = self._content_default_life
        self._content_left = 0.0
        self._content_paused = False
        self._content_timer = QTimer(self)
        self._content_timer.setInterval(200)
        self._content_timer.timeout.connect(self._tick_content)
        return w

    def _set_content_paused(self, paused: bool) -> None:
        """Stop or restart the clock of the notification."""
        if bool(paused) == bool(self._content_paused):
            return
        self._content_paused = bool(paused)
        bar = getattr(self, "_content_bar", None)
        if bar is not None:
            bar.set_paused(self._content_paused)

    def _tick_content(self) -> None:
        """One fifth of a second of the notification's life."""
        if self._content_paused:
            return
        self._content_left -= 0.2
        if self._content_left <= 0.0:
            self._hide_content()
            return
        bar = getattr(self, "_content_bar", None)
        if bar is not None:
            bar.set_fraction(self._content_left / max(1.0, self._content_life))

    def _set_content_life(self, seconds: float) -> None:
        """How long the panel under the head stays.

        A NOTIFICATION goes on its own: news, a search result, a briefing.
        A thing that is being READ does not: a live job and a review of a
        document stay until the user closes them or another thing takes
        their place. The difference is the difference between a message and
        a page, and treating them the same made one of the two wrong.
        """
        self._content_life = float(seconds)
        bar = getattr(self, "_content_bar", None)
        if bar is not None:
            bar.setVisible(self._content_life > 0)
        if self._content_life > 0:
            self._content_left = self._content_life
            if bar is not None:
                bar.set_fraction(1.0)
                bar.set_paused(self._content_paused)
            self._content_timer.start()
        else:
            self._content_timer.stop()
            self._content_left = 0.0

    def _hide_content(self) -> None:
        """Take the notification away. The mouse can do it; so can the clock."""
        self._content_timer.stop()
        panel = getattr(self, "_content_panel", None)
        if panel is not None:
            panel.hide()
        stack = getattr(self, "_lower_stack", None)
        if stack is not None:
            blank = getattr(self, "_lower_blank", None)
            if blank is not None:
                stack.setCurrentWidget(blank)
            # THE SPACE GOES BACK TO THE HEAD.
            #
            # An empty page under the head is a hole in the interface: the
            # face is smaller than it was and nothing says why. The room
            # the notification was using goes back to the head.
            self._shrink_below_hud()
        # And the drawer goes back into the room it was standing in.
        self._reposition_drawer()

    def _reposition_drawer(self) -> None:
        """Put the drawer where it belongs after the room under the head
        has changed.

        The drawer stands in the empty room under the head while that
        room is empty, and hangs from its button when the room is
        taken. Whenever the room changes — a report, a review, the
        viewer — the drawer has to be told, or it keeps standing in a
        room that is no longer there.
        """
        drawer = getattr(self, "_ctrl_drawer", None)
        if drawer is None or not drawer.isVisible():
            return
        try:
            self._position_ctrl_drawer()
        except Exception:
            pass

    def _apply_room(self, height: int) -> None:
        """Give the room under the head a height, and the rest to the face.

        THE ROOM IS A ROOM, NOT A HOLE. It used to appear and disappear:
        when a report was shown the room grew, and when the report went
        away the room went with it — so the drawer of the controls had
        a home at the start and lost it after the first notification,
        and the face was bigger one minute and smaller the next. Now the
        room keeps its size and only its content changes.
        """
        split = getattr(self, "_center_split", None)
        stack = getattr(self, "_lower_stack", None)
        if split is None or stack is None:
            return
        idx = split.indexOf(stack)
        sizes = split.sizes()
        if idx < 0 or idx >= len(sizes):
            return
        total = sum(sizes) or split.height()
        if total <= 0:
            return
        room = max(160, min(int(height), max(160, total - 200)))
        if abs(sizes[idx] - room) < 8:
            return
        new = list(sizes)
        new[idx] = room
        new[0] = max(120, total - room)
        try:
            split.setSizes(new)
        except Exception:
            pass

    def _shrink_below_hud(self) -> None:
        """Give the room under the head back to the head."""
        self._apply_room(self._ROOM_H)

    def _reveal_below_hud(self, panel: QWidget, want: int) -> None:
        """Show one panel under the HUD at a usable height.

        The panels under the head are pages of one stack, so opening one
        closes the others. Checked every time, not only the first: the
        panels are given room by the splitter, and the next search result,
        review or finished task has to land in a panel that is really
        there.

        AND THE DRAWER GETS OUT OF THE WAY. The drawer of the controls
        stands in the empty room under the head while that room is
        empty; the moment a panel takes the room, the drawer has to go
        back to hanging under its button. It used to stay where it was,
        and the user was looking at the drawer drawn over the report —
        two panels on top of each other, and the one underneath was
        unreadable.
        """
        stack = getattr(self, "_lower_stack", None)
        if stack is None:
            panel.show()
            return
        stack.setCurrentWidget(panel)
        panel.show()
        split = self._center_split
        idx = split.indexOf(stack)
        sizes = split.sizes()
        if idx >= 0 and sizes[idx] < want:
            total = sum(sizes) or split.height()
            new = [0] * len(sizes)
            new[0] = max(total - want, 120)
            new[idx] = want
            split.setSizes(new)
        self._reposition_drawer()

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # AN EMPTY REPORT IS NOT A REPORT.
        #
        # The panel used to open with nothing in it: the user saw a big
        # empty box under the head, with a title and a cross, and no
        # text to read — and they were right to ask what that was. A
        # result that carries nothing says nothing, so nothing is shown.
        text = str(text or "")
        if not text.strip():
            self._hide_content()
            return
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Only the app's own label is upper-cased ("NEWS — …"); what follows the
        # dash is a search or a name in the user's language, and str.upper()
        # applies English rules to it (Turkish "istanbul" -> "ISTANBUL").
        label, dash, rest = str(title or "").partition(" — ")
        self._content_title_lbl.setText((label.upper() + dash + rest)[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        # THE CLOCK OF THE NOTIFICATION STARTS AGAIN.
        #
        # Every arrival is a new notification: the line runs out again from
        # the beginning, and the pointer on the panel still stops it.
        self._set_content_life(self._content_default_life)
        self._reveal_below_hud(self._content_panel, 220)
        # The same thing goes into the deck and onto the floating panel: the
        # card is the answer, and the little panel over the browser is the
        # answer too.
        self._push_blade(label or _t("panel.briefing"), text,
                         _time.strftime("%H:%M:%S"))
        try:
            if getattr(self, "_floating", None) is not None:
                self._floating.set_line(text.strip().splitlines()[0] if text.strip() else "",
                                        label)
        except Exception:
            pass

    # ── the coding agent, live ───────────────────────────────────────────────
    # The panel under the head shows a coding job while it runs: what was asked,
    # the plan, every file with its state, and the steps as they happen. Drawn
    # from a snapshot the job publishes on every step, so this slot never reads
    # anything a worker thread is still changing.
    _JOB_STATES = {
        "waiting": ("TEXT_DIM", "○"), "writing": ("PRI", "✎"),
        "done": ("GREEN", "✓"), "fixing": ("ACC2", "↻"),
        "skipped": ("TEXT_DIM", "–"), "failed": ("RED", "✕"),
        "broken": ("RED", "!"),
    }
    _JOB_PHASES = ("reading", "planning", "writing", "checking", "testing")

    def _show_code_job(self, job: dict):
        """Slot — Qt main thread. Draws the coding agent's progress."""
        import time as _time
        if not job:
            return
        e = self._esc
        col = lambda name: getattr(C, name, C.TEXT)
        status = job.get("status", "")
        finished = job.get("finished")
        took = (finished or _time.time()) - job.get("started", _time.time())

        # Phase strip: where in the job it is, readable at a glance.
        strip = []
        phases = list(job.get("phases") or self._JOB_PHASES)
        here = phases.index(status) if status in phases else (
            len(phases) if finished else -1)
        for i, ph in enumerate(phases):
            c = C.GREEN if i < here else (C.PRI if i == here else C.TEXT_DIM)
            mark = "●" if i <= here else "○"
            strip.append(f'<span style="color:{c};">{mark} {ph.upper()}</span>')
        if finished:
            fin = C.GREEN if status == "done" else (C.RED if status == "failed" else C.ACC2)
            strip.append(f'<span style="color:{fin};"><b>● {e(status.upper())}</b></span>')

        # The monospace face comes from gui/style.py, like every other one.
        # It used to be a literal "Courier New" here, which meant this panel
        # was the only place in the product rendering in a different typeface
        # from the rest of it.
        parts = [f'<div style="color:{C.TEXT}; font-family:{MONO_CSS};">',
                 f'<div style="color:{C.TEXT_DIM};">{"  ".join(strip)}'
                 f'&nbsp;&nbsp;·&nbsp;&nbsp;{took:.0f}s</div>',
                 f'<div style="margin-top:6px;"><span style="color:{C.TEXT_DIM};">'
                 f'REQUEST&nbsp;</span>{e(job.get("request", ""))}</div>']
        if job.get("summary"):
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin:8px 0;">{e(job["summary"])}</div>')
        files = job.get("files") or {}
        if files:
            parts.append(f'<div style="color:{C.TEXT_DIM}; margin-top:4px;">FILES</div>')
            for path, f in files.items():
                cname, mark = self._JOB_STATES.get(f.get("state", ""), ("TEXT_DIM", "·"))
                verb = "new " if f.get("action") == "create" else ""
                parts.append(
                    f'<div><span style="color:{col(cname)};">{mark}</span>&nbsp;'
                    f'<span style="color:{C.WHITE};">{e(path)}</span>&nbsp;'
                    f'<span style="color:{C.TEXT_DIM};">{verb}{e(f.get("what", ""))[:110]}'
                    f'</span></div>')
        # Newest first, so the latest step sits under the plan and the whole
        # state of the job is visible without scrolling in a 220-pixel panel.
        steps = job.get("steps") or []
        if steps:
            parts.append(f'<div style="color:{C.TEXT_DIM}; margin-top:8px;">'
                         f'STEPS — newest first</div>')
            for t, msg in reversed(steps[-14:]):
                parts.append(f'<div><span style="color:{C.TEXT_DIM};">{float(t):6.1f}s'
                             f'</span>&nbsp;{e(msg)}</div>')
        parts.append("</div>")

        # Only the app's own label is upper-cased — the folder name after the
        # dash is the user's, in their language (see _show_content).
        title = (f"CODE AGENT — {job.get('folder', '')} · {job.get('model', '')}")[:48]
        first = self._content_title_lbl.text() != title
        self._content_title_lbl.setText(title)
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        bar = self._content_display.verticalScrollBar()
        keep = 0 if first else bar.value()
        self._content_display.setHtml("".join(parts))
        # setHtml jumps to the top; put the reader back where they were.
        bar.setValue(keep)
        if first:
            self.hud.glance(0.0, -0.85, hold=1.3)
        # A LIVE JOB DOES NOT TIME OUT. It is not a notification: it is
        # work in progress, and a panel that vanished half way through
        # would be a panel the user could not read.
        self._set_content_life(0)
        self._reveal_below_hud(self._content_panel, 260)

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: the assistant says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:{DISPLAY_CSS}; font-size:13px;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        # A REVIEW IS READ, NOT NOTIFIED: it stays until it is closed.
        self._set_content_life(0)
        self._reveal_below_hud(self._content_panel, 260)

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message the assistant reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _build_viewer_panel(self) -> QWidget:
        """The viewer: camera on the left, the screen on the right, the hands below.

        The questionnaire used to sit alone under the head, asking questions
        that had nothing to do with the rest of the interface. The viewer
        takes that place: the questions are still asked, but the panel also
        shows the two things a user actually wants to CHECK — what the
        camera sees and what the machine can read on the screen — and the
        state of the hands, so a gesture can be verified instead of guessed.
        """
        panel = ViewerPanel(self, quiz=self._quiz_panel)
        self._viewer = panel
        return panel

    def _on_gesture(self, pose: str, action: str) -> None:
        """The hand says what it is doing, and the viewer shows it.

        This is the whole point of the strip under the two pictures: a
        gesture that cannot be checked is a guess, and the user is the only
        one who can say whether the guess was right.

        IN THE LANGUAGE OF THE INTERFACE
        --------------------------------
        The tracker names the poses in English — "pinch", "double click" —
        because those are the words its own vocabulary uses. The catalogue
        has every one of them in the five languages, so the reader says
        "clic derecho" to a person who reads Spanish. A word the catalogue
        does not know is shown as the tracker said it, never as a blank.
        """
        self._last_pose = str(pose or "")
        self._last_action = str(action or "")
        viewer = getattr(self, "_viewer", None)
        if viewer is None:
            return
        service = getattr(self, "_gesture", None)
        backend = service.backend if service is not None else "none"
        hands = service.hands_count if service is not None else 0
        # THE NAME OF THE TRACKER IN THE LANGUAGE OF THE USER.
        #
        # The reader said "none" in English in a Spanish interface, which
        # is the kind of leftover that makes a person think the product
        # does not speak their language.
        backend_text = _t(f"gesture.backend.{str(backend or 'none')}")
        if backend_text.startswith("gesture.backend."):
            backend_text = str(backend or "--")
        viewer.set_gesture(backend_text,
                           localization.gesture_label("pose", self._last_pose),
                           localization.gesture_label("action", self._last_action),
                           [
            (_t("viewer.hands_count"), str(hands), "TEXT"),
        ])

    def _on_lower_page_changed(self, _index: int) -> None:
        """The page under the head changed: the viewer follows.

        The camera and the screen are read only while the viewer is the
        page on screen. A viewer that is hidden and still reading the
        screen would be a program that watches the user's desktop with
        nobody watching it.

        AND THE DRAWER MOVES OUT OF THE WAY.
        -------------------------------------
        The drawer of the controls stands in the room under the head when
        that room is empty. The moment something else moves into it — a
        notification, the viewer — the drawer goes back to hanging from
        the button that opened it, so the two are never on top of each
        other.
        """
        viewer = getattr(self, "_viewer", None)
        if viewer is None:
            return
        stack = getattr(self, "_lower_stack", None)
        if stack is not None and stack.currentWidget() is viewer:
            viewer.start()
        else:
            viewer.stop()
        drawer = getattr(self, "_ctrl_drawer", None)
        if drawer is not None and drawer.isVisible():
            self._position_ctrl_drawer()

    def _hide_viewer(self) -> None:
        panel = getattr(self, "_viewer", None)
        if panel is not None:
            panel.stop()
        stack = getattr(self, "_lower_stack", None)
        if stack is not None:
            blank = getattr(self, "_lower_blank", None)
            if blank is not None:
                stack.setCurrentWidget(blank)

    def _toggle_viewer(self) -> None:
        """Open the viewer, or close it if it is already open."""
        stack = getattr(self, "_lower_stack", None)
        panel = getattr(self, "_viewer", None)
        if stack is None or panel is None:
            return
        if stack.currentWidget() is panel:
            self._hide_viewer()
            return
        # THE VISOR GETS THE ROOM IT NEEDS.
        #
        # The panels under the head are given room by the splitter, and the
        # viewer was asking for three hundred pixels when it wanted three
        # hundred and twenty: the third row of "what the hands are doing"
        # was painted below the edge of the panel.
        self._reveal_below_hud(panel, 320)
        panel.start()

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(px_font(12, QFont.Weight.DemiBold if primary else QFont.Weight.Normal,
                          spacing=1.0 if primary else 0.0))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(30)
        b.setStyleSheet(btn_css("primary" if primary else "dim"))
        return b

    def _build_quiz_panel(self) -> QWidget:
        """The questionnaire, as a tab of the viewer.

        It used to be a panel of its own under the head, asking questions
        in the middle of an interface that had nothing to do with it. The
        user was right: that space was better spent on what the machine
        can actually SEE.

        So the questionnaire is a tab of the viewer now — the camera on
        one side and the questions on the other, in one panel — and the
        user can watch the screen while the assistant asks.
        """
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._quiz_title_lbl = QLabel("")
        self._quiz_title_lbl.setFont(px_font(13, QFont.Weight.DemiBold,
                                             spacing=1.4))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent;")
        head.addWidget(self._quiz_title_lbl, 1)
        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(px_font(10, mono=True))
        self._quiz_count_lbl.setStyleSheet(
            f"color: {C.PRI_DIM}; background: transparent;")
        head.addWidget(self._quiz_count_lbl)
        lay.addLayout(head)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(px_font(15))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(5)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(px_font(12))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn(_t("quiz.next"), primary=True)
        self._quiz_next_btn.setFixedWidth(120)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        # The viewer is the page, and the questionnaire is a tab of it.
        viewer = getattr(self, "_viewer", None)
        if viewer is not None:
            viewer.show_quiz()
        self._reveal_below_hud(self._viewer_panel, 300)
        self._quiz_render()

    def _hide_quiz(self):
        """The questionnaire is done. The viewer stays, on its other tab.

        THE PANEL OF THE QUESTIONS IS NOT HIDDEN. It is a page of the
        stack of the viewer, and a page that is hidden while it is the
        current one leaves the stack showing nothing: the second
        questionnaire of the session would arrive at a panel that is not
        there. The stack decides what is visible, and the stack is told.
        """
        self._quiz = None
        viewer = getattr(self, "_viewer", None)
        if viewer is not None:
            viewer.show_view()
        # The stack does not go back to a blank page by itself: a page that
        # is hidden while it is the current one leaves an empty hole under
        # the head, and the hole reads as a panel that failed to open.
        stack = getattr(self, "_lower_stack", None)
        if stack is not None and stack.currentWidget() is self._viewer_panel:
            blank = getattr(self, "_lower_blank", None)
            if blank is not None:
                stack.setCurrentWidget(blank)
            # The room under the head goes back to the head.
            self._shrink_below_hud()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(hud_font(9))
            field.setPlaceholderText(_t("quiz.your_answer"))
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn(_t("quiz.answer"), primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = _t("quiz.correct"), C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are the assistant's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = _t("quiz.noted"), C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText(_t("quiz.finish") if last else _t("quiz.next"))
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to the assistant as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        self._status_strip = StatusStrip()
        self._status_strip.set_right(_t("window.footer_shortcuts"))
        self._refresh_status_strip()
        return self._status_strip

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        if cat == "folder":
            n = self._drop_zone._folder_files
            self._file_hint.setText(_t("window.file_hint_folder", icon=icon,
                                       name=p.name, count=n,
                                       assistant=self._assistant_name))
            self._log.append_log(f"FOLDER: {p.name} ({n} files) loaded")
            if self.on_text_command:
                msg = (
                    f"[FOLDER_UPLOADED] path={path} | name={p.name} | files={n} | "
                    f"Briefly tell the user the project folder '{p.name}' is loaded "
                    f"and that you can improve, fix or extend it with the coding "
                    f"model — ask what they want changed. When they say, call "
                    f"dev_agent with mode='improve' and their request as the "
                    f"description."
                )
                threading.Thread(target=self.on_text_command, args=(msg,),
                                 daemon=True).start()
            return
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(_t("window.file_hint_file", icon=icon,
                                   name=p.name, size=size,
                                   assistant=self._assistant_name))
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        # THE CONFIRMATION IS THE ONE MODAL THAT BLOCKS THE OTHERS.
        #
        # This panel is built by hand, so it does not go through
        # _show_overlay(); it has to ask the question itself. A panel that
        # lands on top of a question waiting for an answer is the deck of
        # cards the owner complained about, in a different suit.
        if self._confirm_pending():
            self._log.append_log("SYS: Answer the question on screen first.")
            return
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        berr   = result[4] if len(result) >= 5 else ""
        if self._remote_overlay:
            self._remote_overlay._do_close()
        # One modal at a time: the key panel does not land on top of the
        # memory panel and the plugins panel at the same time.
        self._close_overlays()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw,
                               bind_error=berr)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, "TEEMO_AI")
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent.parent / "main.py")
            import winreg
            reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
            if currently_on:
                winreg.DeleteValue(reg, "TEEMO_AI")
            else:
                pythonw = Path(sys.executable).parent / "pythonw.exe"
                exe = str(pythonw if pythonw.exists() else sys.executable)
                winreg.SetValueEx(reg, "TEEMO_AI", 0, winreg.REG_SZ,
                                  f'"{exe}" "{script}"')
            winreg.CloseKey(reg)
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        self._autostart_btn.setText(_t("controls.autostart_on") if enabled
                                    else _t("controls.autostart_off"))
        self._autostart_btn.setStyleSheet(btn_css("on" if enabled else "off"))

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)
        # The briefing is a routine now (core/routines); the switch drives it.
        try:
            from core.routines import desktop as _routines
            _routines.set_builtin_enabled("briefing", new_val)
        except Exception as e:
            print(f"[Routines] briefing switch not mirrored: {e}")

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before TeemoLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        # Kept so that a change of language can repaint the buttons without
        # asking the wake-word module again: that check imports a heavy
        # package, and a word must not cost the Qt thread two seconds.
        self._last_wake_state = st
        self._apply_wake_btn_texts(st)
        self._refresh_wake_phrase_field()

    def _apply_wake_btn_texts(self, st: dict):
        """The two wake buttons, in the language that is stored right now."""
        if not hasattr(self, '_wake_btn'):
            return
        _on, _off = btn_css("on"), btn_css("off")
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText(_t("controls.wake_download"))
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText(_t("controls.wake_on"))
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText(_t("controls.sleep_now") if st["awake"]
                                         else _t("controls.wake_now"))
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText(_t("controls.wake_off"))
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_wake_phrase_field(self):
        """Show the stored wake phrase in the field — but never over what the
        user is typing. A refresh that lands mid-edit would eat a keystroke.

        It also shows WHAT THE MICROPHONE IS LISTENING FOR, which is not
        necessarily what the field holds: the bundled wake model hears one
        particular phrase, and if that is not the phrase in the field, the
        user has to be told, because otherwise the field is a promise the
        machine cannot keep.
        """
        field = getattr(self, "_wake_phrase_edit", None)
        if field is None:
            return
        try:
            from core.wake_word import wake_phrase
            current = wake_phrase()
        except Exception:
            current = ""
        if not field.hasFocus() and field.text() != current:
            field.setText(current)
        field.setPlaceholderText(current or "Hey T.E.E.M.O")
        self._refresh_wake_mode_combo()
        self._refresh_wake_effective_label()

    def _refresh_wake_mode_combo(self):
        """Show the listening mode that is actually stored.

        The two WORDS follow the interface's language; the two VALUES behind
        them ("phrase" and "voice") are what is stored, and they never change.
        """
        combo = getattr(self, "_wake_mode_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            combo.setItemText(0, _t("wake.mode_phrase"))
            combo.setItemText(1, _t("wake.mode_voice"))
        finally:
            combo.blockSignals(False)
        try:
            from core.wake_word import wake_mode
            mode = wake_mode()
        except Exception:
            mode = "phrase"
        index = combo.findData(mode)
        if index >= 0 and combo.currentIndex() != index:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _save_wake_mode(self):
        """Persist the listening mode, and say what changed."""
        combo = getattr(self, "_wake_mode_combo", None)
        if combo is None:
            return
        mode = combo.currentData() or "phrase"
        try:
            from core.wake_word import wake_mode
            from memory.config_manager import save_wake_mode
            before = wake_mode()
            save_wake_mode(mode)
            after = wake_mode()
        except Exception as e:
            print(f"[Wake] mode not saved: {e}")
            return
        if after != before:
            detail = (_t("wake.detail_voice") if after == "voice"
                      else _t("wake.detail_phrase"))
            self._log_sig.emit(
                f"SYS: {_t('wake.mode_changed', mode=after, detail=detail)}")
        self._refresh_wake_effective_label()

    # ── Language and region ─────────────────────────────────────────────────
    #
    # The two selectors are the only place in the product where the user
    # says who the product is talking to: the language it speaks and the
    # place it believes the user is in.

    def _save_language(self, *_args):
        """Persist the language, and put the whole interface into it.

        The combo's SIGNAL fires while the items are being rebuilt too, so
        the stored language is the arbiter: when the combo already shows the
        stored language, nothing happened and nothing is written.
        """
        combo = getattr(self, "_language_combo", None)
        if combo is None:
            return
        code = combo.currentData() or ""
        try:
            before = localization.get_language()
        except Exception:
            before = ""
        if not code or code == before:
            return
        try:
            localization.set_language(code)
        except Exception as e:
            print(f"[L10n] language not saved: {e}")
            return
        self._apply_language(before, code)
        try:
            native = next((l.name_native for l in localization.LANGUAGES
                           if l.code == code), code)
        except Exception:
            native = code
        self._log_sig.emit(
            f"SYS: {_t('selector.language_changed', language=native)}")

    def _save_region(self, *_args):
        """Persist the region — the place the user lives.

        The box is editable, so the text may be a city that is not in the
        table. That is allowed on purpose: refusing it would leave the
        product with a region that is not the user's.
        """
        combo = getattr(self, "_region_combo", None)
        if combo is None:
            return
        data = combo.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            city, country = str(data[0]), str(data[1])
        else:
            city = str(combo.currentText() or "").strip()
            country = ""
        if not city:
            return
        try:
            region = localization.set_region(city, country or None)
        except Exception as e:
            print(f"[L10n] region not saved: {e}")
            return
        self._refresh_region_combo()
        self._log_sig.emit(
            f"SYS: {_t('selector.region_changed', region=localization.region_label(region))}")

    def _save_appearance(self, *_args):
        """Change the face the assistant shows, and apply it at once.

        A face that only changed on the next restart would be a setting the
        user could not see, and seeing it is the whole point of the three
        of them.
        """
        combo = getattr(self, "_appearance_combo", None)
        if combo is None:
            return
        mode = str(combo.currentData() or "dashboard")
        if mode == self.appearance_mode():
            return
        self.apply_appearance(mode)
        if mode in ("core", "tray"):
            # The drawers belong to the dashboard: in the corner there is
            # nothing to open them from.
            for drawer in ("_quick_drawer", "_ctrl_drawer"):
                panel = getattr(self, drawer, None)
                if panel is not None:
                    try:
                        panel.hide()
                    except Exception:
                        pass
        self._refresh_appearance_combo()

    def _save_corner(self, *_args):
        """Move the core to another corner of the screen."""
        combo = getattr(self, "_corner_combo", None)
        if combo is None:
            return
        corner = str(combo.currentData() or "")
        if not corner or corner == self._appearance.corner():
            return
        self.set_core_corner(corner)

    def _load_music_service(self):
        """Show the player that is really in force."""
        combo = getattr(self, "_music_combo", None)
        if combo is None:
            return
        try:
            from memory.config_manager import get_music_service
            current = get_music_service()
        except Exception:
            current = "auto"
        index = combo.findData(current)
        if index >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _save_music_service(self, *_args):
        """Where a song is opened from now on."""
        combo = getattr(self, "_music_combo", None)
        if combo is None:
            return
        service = str(combo.currentData() or "auto")
        try:
            from memory.config_manager import save_music_service
            save_music_service(service)
        except Exception as exc:
            self._log.append_log(f"ERR: the music player could not be saved "
                                 f"({type(exc).__name__})")
            return
        try:
            self._log.append_log(
                f"SYS: Music player: {_t('music.service_' + service)}.")
        except Exception:
            pass

    def _refresh_appearance_combo(self):
        """Show the appearance that is actually in force.

        The corner box is only alive in the core mode: in the dashboard
        there is no corner to speak of, and in the tray nothing is on
        screen to put in one.
        """
        combo = getattr(self, "_appearance_combo", None)
        if combo is None:
            return
        mode = self.appearance_mode()
        index = combo.findData(mode)
        if index >= 0 and combo.currentIndex() != index:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
        corner_combo = getattr(self, "_corner_combo", None)
        if corner_combo is not None:
            corner_index = corner_combo.findData(self._appearance.corner())
            if corner_index >= 0 and corner_combo.currentIndex() != corner_index:
                corner_combo.blockSignals(True)
                corner_combo.setCurrentIndex(corner_index)
                corner_combo.blockSignals(False)
            corner_combo.setEnabled(mode == "core")
        caption = getattr(self, "_corner_caption", None)
        if caption is not None:
            caption.setVisible(mode == "core")

    def _toggle_gesture(self):
        """Start or stop watching the hands."""
        service = getattr(self, "_gesture", None)
        if service is None:
            return
        message = service.toggle()
        self._log_sig.emit(f"SYS: {message}")
        self._refresh_gesture_btn()

    def _toggle_gesture_pointer(self):
        """Whether the hand takes the real pointer."""
        service = getattr(self, "_gesture", None)
        if service is None:
            return
        if not service.drives_pointer and not service.enabled:
            # The pointer cannot be driven by hands that nobody is
            # watching. Letting the switch say "ON" anyway would be a
            # button that claims something that is not happening.
            self._log_sig.emit(f"SYS: {_t('controls.gesture_pointer_needs_watch')}")
            self._refresh_gesture_btn()
            return
        service.set_drives_pointer(not service.drives_pointer)
        self._refresh_gesture_btn()

    def _refresh_gesture_btn(self):
        """Say what the hands are doing, in the button itself."""
        service = getattr(self, "_gesture", None)
        button = getattr(self, "_gesture_btn", None)
        if service is None or button is None:
            return
        button.setText(_t("controls.gesture")
                       + (" · ON" if service.enabled else ""))
        pointer = getattr(self, "_gesture_pointer_btn", None)
        if pointer is not None:
            pointer.setText(_t("controls.gesture_pointer")
                            + (" · ON" if service.drives_pointer else ""))
        advanced = getattr(self, "_gesture_advanced_btn", None)
        if advanced is not None:
            advanced.setText(_t("controls.gesture_advanced")
                             + (" · ON" if service.advanced else ""))

    def _toggle_gesture_advanced(self):
        """The gestures that change the desktop: armed, or disarmed.

        THE SWITCH IS THE USER'S. It is off when the program starts, and
        the button says which way it is, because a gesture that is armed
        behind the user's back is a gesture that can close a window.
        """
        service = getattr(self, "_gesture", None)
        if service is None:
            return
        service.set_advanced(not service.advanced)
        self._log_sig.emit(
            f"SYS: {_t('controls.gesture_advanced_on' if service.advanced
                      else 'controls.gesture_advanced_off')}")
        self._refresh_gesture_btn()

    # ── what the hand says ────────────────────────────────────────────────

    def _on_gesture_confirm(self, accepted: bool) -> None:
        """The thumb, or the fist, has answered what was on screen.

        WHEN SOMETHING IS WAITING FOR AN ANSWER, the gesture is the
        answer. When nothing is waiting, the fist closes the drawer of
        the controls, which is what a closed hand means everywhere else.
        """
        from core import confirm as confirm_gate

        if confirm_gate.pending_title():
            confirm_gate.resolve(bool(accepted))
            self._log_sig.emit(
                f"SYS: {_t('gesture.confirmed' if accepted else 'gesture.rejected')}")
            return
        if accepted:
            self._close_controls()

    def _on_gesture_wake(self) -> None:
        """The L: wake T.E.E.M.O. up, or put it back to sleep."""
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception as exc:                   # noqa: BLE001
                self._log_sig.emit(f"SYS: {type(exc).__name__}")
            return
        self._do_interrupt()

    def _on_gesture_sleep(self) -> None:
        """The clap: T.E.E.M.O. goes to sleep and the hands are let go.

        The tracking is stopped, because a hand that is asleep should
        not keep the camera open: the HUD is locked until the user asks
        for the hands again.
        """
        service = getattr(self, "_gesture", None)
        if service is not None and service.enabled:
            service.stop()
        self._log_sig.emit(f"SYS: {_t('gesture.asleep')}")
        self._refresh_gesture_btn()

    def _refresh_language_combo(self):
        """Show the language that is actually stored."""
        combo = getattr(self, "_language_combo", None)
        if combo is None:
            return
        try:
            code = localization.get_language()
        except Exception:
            return
        index = combo.findData(code)
        if index >= 0 and combo.currentIndex() != index:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _refresh_region_combo(self):
        """Show the region that is actually stored."""
        combo = getattr(self, "_region_combo", None)
        if combo is None:
            return
        try:
            region = localization.get_region()
            label = localization.region_label(region)
        except Exception:
            return
        combo.blockSignals(True)
        try:
            index = combo.findText(label)
            if index >= 0:
                combo.setCurrentIndex(index)
            elif combo.currentText().strip() != label:
                combo.setEditText(label)
        finally:
            combo.blockSignals(False)

    def _apply_language(self, old_lang: str = "", new_lang: str = ""):
        """Put every translated string on screen into the new language.

        HOW IT WORKS WITHOUT A REGISTRY OF EVERY WIDGET
            Every translatable string came from the catalogue through
            `_t("key")`, and the catalogue knows that same key in every
            language. So the text a widget is showing right now identifies
            its key, and the same key in the new language gives the new
            text. A widget that was never translated — a file name, a
            number, a tool name — matches nothing in the catalogue and is
            left exactly as it was.

        It is not perfect and it does not pretend to be: two catalogue keys
        that share the same English wording keep the wording of the last
        one read, and a string that was built by gluing a catalogue value to
        a decoration ("▶  VIDEO") keeps its decoration and changes its
        word. The next launch builds every string from the new language
            outright.
        """
        if not old_lang or not new_lang:
            try:
                old_lang = localization.get_language()
                new_lang = old_lang
            except Exception:
                return
        mapping = self._translation_map(old_lang, new_lang)
        if not mapping:
            return
        for widget in self.findChildren(QWidget):
            try:
                if isinstance(widget, QComboBox):
                    for index in range(widget.count()):
                        text = widget.itemText(index)
                        replacement = self._replace(text, mapping)
                        if replacement and replacement != text:
                            widget.setItemText(index, replacement)
                    continue
                if isinstance(widget, (QLabel, QPushButton)):
                    text = widget.text()
                    replacement = self._replace(text, mapping)
                    if replacement and replacement != text:
                        widget.setText(replacement)
            except Exception:
                continue
        # The pieces that are built from more than a word.
        self._refresh_wake_mode_combo()
        self._refresh_wake_effective_label()
        try:
            caption = getattr(self, "_language_caption", None)
            if caption is not None:
                caption.setText(_t("settings.language"))
            caption = getattr(self, "_region_caption", None)
            if caption is not None:
                caption.setText(_t("settings.region"))
            note = getattr(self, "_selector_note", None)
            if note is not None:
                note.setText(_t("selector.explain"))
        except Exception:
            pass
        # THE READER OF THE HANDS IS DRAWN, NOT A LABEL.
        #
        # The text replacement above walks the widgets that hold a word; the
        # strip with the pose and the action is painted on a canvas, so it
        # does not follow. It is repainted here with the last thing the
        # tracker read, in the new language.
        try:
            pose = str(getattr(self, "_last_pose", "") or "")
            action = str(getattr(self, "_last_action", "") or "")
            if pose or action:
                self._on_gesture(pose, action)
        except Exception:
            pass

    @staticmethod
    def _translation_map(old_lang: str, new_lang: str) -> dict:
        """{old wording: new wording} for every catalogue key."""
        mapping: dict = {}
        try:
            keys = localization.catalog_keys()
        except Exception:
            return mapping
        for key in keys:
            entry = localization.CATALOG.get(key) or {}
            old = entry.get(old_lang)
            new = entry.get(new_lang)
            if old and new and old != new:
                mapping[str(old)] = str(new)
        return mapping

    @staticmethod
    def _replace(text: str, mapping: dict) -> str:
        """The same sentence in the new language, decorations and all."""
        if not text:
            return ""
        out = text
        for old, new in mapping.items():
            if old and old in out:
                out = out.replace(old, new)
        return out

    def _refresh_wake_effective_label(self):
        """The truth about what the microphone hears.

        Three cases, and each one gets its own sentence:

          * a model of the user's own is installed -> it hears the phrase
            they configured, and there is nothing to warn about;
          * the listening mode is "any voice" -> there is no phrase at all,
            and the honest thing to say is that any speech wakes it up;
          * the bundled model -> it hears the phrase it was trained for, NOT
            the one in the field, and that is said out loud rather than left
            for the user to discover by failing to wake it up.
        """
        label = getattr(self, "_wake_effective_lbl", None)
        if label is None:
            return
        try:
            from core.wake_word import (
                effective_wake_phrase, wake_mode, wake_phrase,
            )
            mode = wake_mode()
            effective = effective_wake_phrase()
            wanted = wake_phrase()
        except Exception:
            label.setText("")
            return

        if mode == "voice":
            text = _t("wake.effective_voice")
        elif effective and effective.casefold() == wanted.casefold():
            text = _t("wake.effective_phrase", phrase=effective)
        elif effective:
            text = _t("wake.effective_model", effective=effective,
                      wanted=wanted, mode=_t("wake.mode_voice"))
        else:
            text = _t("wake.effective_none", wanted=wanted,
                      mode=_t("wake.mode_voice"))
        label.setText(text)
        label.setToolTip(text)

    def _save_wake_phrase(self):
        """Persist the wake phrase typed into the CONTROLS drawer.

        An empty field restores the default rather than saving an empty
        phrase: a wake word made of nothing would leave the button looking
        enabled and the assistant asleep forever, with nothing to point at.
        """
        field = getattr(self, "_wake_phrase_edit", None)
        if field is None:
            return
        typed = field.text().strip()
        try:
            from core.wake_word import wake_phrase
            from memory.config_manager import save_wake_phrase
            before = wake_phrase()
            save_wake_phrase(typed)
            after = wake_phrase()
        except Exception as e:
            print(f"[Wake] phrase not saved: {e}")
            return
        if after != before:
            self._log_sig.emit(f"SYS: {_t('wake.phrase_changed', phrase=after)}")
        self._refresh_wake_phrase_field()

    def _refresh_context_combo(self):
        """Fill the context selector with the contexts that exist.

        Called when the drawer is built and whenever the set changes. The
        signals are blocked while it fills: the app sets the item itself
        after the user picks one, and a signal firing on that would ask for
        another reconnect for a change the user did not make.

        AN EMPTY DROPDOWN SAYS NOTHING.
        ------------------------------
        When there are no contexts — the engine has not offered any yet —
        the selector was left with nothing in it, and a dropdown with
        nothing in it looks like a control that broke. Now it says that
        there are no tools yet, and it cannot be opened.
        """
        combo = getattr(self, "_ctx_combo", None)
        if combo is None:
            return
        contexts: list[str] = []
        if self.get_tool_contexts:
            try:
                contexts = list(self.get_tool_contexts() or [])
            except Exception:
                contexts = []
        current = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            if not contexts:
                combo.addItem(_t("controls.context_none"))
                combo.setEnabled(False)
                return
            combo.setEnabled(True)
            combo.addItems(contexts)
            index = combo.findText(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(False)

    def _on_tool_context(self, index: int):
        """The user picked a different set of tools for the assistant."""
        combo = getattr(self, "_ctx_combo", None)
        if combo is None or not self.on_set_tool_context:
            return
        context = combo.itemText(index)
        if not context:
            return
        chosen = self.on_set_tool_context(context)
        if chosen and chosen != context:
            # The app refused it (an unknown context keeps the previous one);
            # put the selector back where it was.
            self._refresh_context_combo()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(_t("controls.ptt_on", chord=chord_label()) if ptt
                              else _t("controls.ptt_off"))
        self._ptt_btn.setStyleSheet(btn_css("on" if ptt else "off"))
        self._ptt_btn.setToolTip(
            _t("controls.ptt_tip_on") if ptt
            else _t("controls.ptt_tip_off"))

    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        face = get_hud_style() == "face"
        # Neither state is "off", so both read as active — this is a choice
        # between two things, not a switch with a disabled side.
        self._hud_btn.setText(_t("controls.hud_face") if face
                              else _t("controls.hud_core"))
        self._hud_btn.setStyleSheet(btn_css("choice"))
        self._hud_btn.setToolTip(
            _t("controls.hud_tip_face") if face
            else _t("controls.hud_tip_core"))

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style
        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face." if want == "face"
            else "SYS: HUD switched to the reactor core.")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        The global hook is the preferred route — it works while another
        application has focus. This is the fallback, and it is live whenever
        this window has focus. Qt gives no key-release for a QShortcut, so a
        press latches the mic open and a short timer closes it; held down,
        auto-repeat keeps pushing that timer out, which behaves like holding a
        key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText(_t("controls.downloading"))
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through TeemoLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        self._brief_btn.setText(_t("controls.brief_on") if enabled
                                else _t("controls.brief_off"))
        self._brief_btn.setStyleSheet(btn_css("on" if enabled else "off"))

    def _open_customize(self):
        cfg = _read_full_config()
        self._close_overlays()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "T.E.E.M.O.") or "T.E.E.M.O.",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        self._show_overlay(ov, "_customize_overlay")

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_to_ui(self, name: str) -> str:
        """Put a name on screen. Returns the display form actually used.

        Split out of `_apply_name_update` because a rename can arrive from two
        directions: the CUSTOMISE overlay (which also persists it) and the
        config file being edited behind the app's back (which must NOT be
        written back — it is already the truth).
        """
        self._assistant_name = (name or "").strip() or "T.E.E.M.O."
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        self._title_lbl.setToolTip(self._subtitle(display))
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display
        return display

    def _watch_identity(self) -> None:
        """A rename made straight in config/api_keys.json has to show up live.

        The CUSTOMISE overlay is the usual way to rename the assistant, but the
        config file is a plain file and a user may well edit it with a text
        editor while the app is running. The window title and the header must
        follow it without a restart — and without the app writing the file
        back over the change it has just read.

        The file is only re-read when its timestamp moved, so this costs a
        stat() every couple of seconds and nothing at all the rest of the time.
        """
        try:
            # The file the configuration manager watches — the same one it
            # writes, so the window never watches a path that is not the
            # one in use.
            from memory.config_manager import CONFIG_FILE as _cfg_file
            mtime = Path(_cfg_file).stat().st_mtime
        except Exception:
            return
        if mtime == getattr(self, "_identity_mtime", None):
            return
        self._identity_mtime = mtime
        try:
            cfg = _read_full_config()
        except Exception:
            return
        name = (cfg.get("assistant_name") or "T.E.E.M.O.").strip()
        if not name or name == self._assistant_name:
            return
        display = self._apply_name_to_ui(name)
        self._refresh_identity_rows()
        try:
            self._log.append_log(f"SYS: Identity updated from the config file — {display}")
        except Exception:
            pass

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        display = self._apply_name_to_ui(name)

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            from memory.config_manager import update_config
            fields = {"assistant_name": self._assistant_name, "user_name": user_name.strip()}
            if ui_color:
                fields["ui_color"] = ui_color.strip().lower()
            update_config(**fields)
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        self._refresh_identity_rows()

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── the rule about modals ────────────────────────────────────────────────
    #
    # ONE AT A TIME.
    #
    # Two modals on the screen at once is a deck of cards laid on top of
    # each other: the user cannot tell which one is on top, the one
    # underneath keeps eating the clicks, and closing the wrong one leaves
    # the other one there for ever. So opening a modal closes the one that
    # was open.
    #
    # The confirmation is the exception, and it is an exception on purpose:
    # it asks a question that has to be answered before anything else
    # happens — usually before the machine is shut down. While it is open,
    # nothing else opens over it.
    _OVERLAY_ATTRS = ("_audio_overlay", "_providers_overlay", "_memory_overlay",
                       "_customize_overlay", "_plugin_manager_overlay",
                       "_plugin_settings_overlay", "_remote_overlay",
                       "_permissions_overlay")

    def _close_overlays(self, keep: str = "") -> None:
        """Close every modal except the one named."""
        for name in self._OVERLAY_ATTRS:
            if name == keep:
                continue
            ov = getattr(self, name, None)
            if ov is None:
                continue
            try:
                ov.hide()
                ov.deleteLater()
            except Exception:
                pass
            setattr(self, name, None)

    def _confirm_pending(self) -> bool:
        """Is there a question waiting for an answer?"""
        ov = getattr(self, "_confirm_overlay", None)
        try:
            return ov is not None and ov.isVisible()
        except Exception:
            return False

    def _show_overlay(self, ov, name: str = "") -> bool:
        """Show one modal, and only one.

        `name` is the attribute the window keeps it under, and it is set
        HERE and not by the caller: the closing of the others empties
        every one of those attributes, so an overlay that was filed away
        before the closing was filed away again.

        Returns False when the modal was refused — which happens only when
        a confirmation is waiting for an answer.
        """
        if self._confirm_pending():
            try:
                ov.hide()
                ov.deleteLater()
            except Exception:
                pass
            try:
                self._log.append_log("SYS: Answer the question on screen first.")
            except Exception:
                pass
            return False
        self._close_overlays()
        self._centre_overlay(ov)
        if name:
            setattr(self, name, ov)
        return True

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._show_overlay(ov, "_audio_overlay")   # kept so it is not GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Permissions ──────────────────────────────────────────────────────────

    def _open_permissions(self):
        """The permissions panel: what the assistant may do, and what is allowed.

        The panel is imported on the click, like the providers panel: it pulls
        in the policy module and it must not slow the drawer down while it is
        being built.
        """
        try:
            from gui.overlays import PermissionsOverlay
        except Exception as exc:
            self._log.append_log(f"ERR: Permissions panel unavailable — {exc}")
            return
        try:
            ov = PermissionsOverlay(parent=self.centralWidget())
            self._show_overlay(ov, "_permissions_overlay")   # kept so it is not GC'd
        except Exception as exc:
            self._log.append_log(f"ERR: Permissions panel unavailable — {exc}")

    # ── Engine & providers ───────────────────────────────────────────────────

    def _open_providers(self):
        """Open the engine panel. Everything it needs is imported on this call.

        A failure here must not take the drawer with it — the folder can be
        deleted, which is the point of it being a folder, and the honest answer
        to that is a line in the log rather than a traceback.
        """
        try:
            ov = ProvidersOverlay(parent=self.centralWidget())
        except ImportError:
            self._log.append_log(
                "SYS: The providers folder is not installed — "
                "the assistant runs on Gemini Live.")
            return
        except Exception as e:
            self._log.append_log(f"SYS: Provider settings unavailable: {e}")
            return
        ov.changed.connect(self._on_providers_applied)
        self._show_overlay(ov, "_providers_overlay")   # kept so it is not GC'd

    def _on_providers_applied(self):
        """Rebuild the session so the new engine takes over.

        Reuses the audio-device callback rather than adding a second one: both
        mean 'the conversation has to be rebuilt', main.py already keeps the
        transcript across such a rebuild through session resumption, and one
        path that is known to work is better than two that look alike."""
        self._refresh_identity_rows()
        self._log.append_log("SYS: Engine settings saved — reconnecting.")
        if self.on_audio_device_change:
            self.on_audio_device_change()
        # The engine panel the user has just used is the one overlay that
        # must not stop the assistant from saying hello.
        QTimer.singleShot(600, lambda: self._start_boot(ignore=("_providers_overlay",)))

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._show_overlay(ov, "_memory_overlay")

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        # The confirmation is the one modal that does not close the others:
        # it is the one that asks the question, so it IS the modal.
        self._close_overlays()
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        self._show_overlay(ov, "_plugin_manager_overlay")

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        self._show_overlay(ov, "_plugin_settings_overlay")

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    @staticmethod
    def _current_clipboard_text() -> str:
        """The text of the clipboard, or an empty string."""
        try:
            return QApplication.clipboard().text().strip()
        except Exception:
            return ""

    def _on_clipboard_changed(self):
        """The clipboard changed: the panel offers what to do with it.

        IT COMES BACK ONLY FOR SOMETHING NEW. The same text used to raise the
        panel again and again — a copy anywhere in Windows and another window
        appeared out of nowhere, over and over, and the user had to close each
        one.

        AND IT STAYS OUT OF THE WAY. While the user is typing an order, a
        panel that pops up over the field they are typing in is worse than no
        panel at all.
        """
        text = self._current_clipboard_text()
        if len(text) < 10:
            return
        if text == getattr(self, "_last_clip_text", ""):
            return                      # the same thing, already offered
        # THE CLIPBOARD THAT ARRIVES WITH THE PROGRAM IS NOT A COPY.
        if (time.monotonic() - getattr(self, "_started_at", 0.0)) < 5.0:
            self._last_clip_text = text
            return                      # it is remembered, not offered
        field = getattr(self, "_input", None)
        if field is not None and field.hasFocus():
            return                      # the user is typing: leave them alone
        self._last_clip_text = text
        self._clipboard_sig.emit(text)

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _hide_clipboard_panel(self) -> None:
        """The clipboard panel goes away when the user starts writing."""
        panel = getattr(self, "_clipboard_panel", None)
        if panel is None:
            return
        try:
            panel.hide()
        except Exception:
            pass

    def _position_clipboard_panel(self):
        """The panel goes where there is nothing to click.

        IT USED TO GO AT THE BOTTOM OF THE MIDDLE COLUMN, over the
        questionnaire and over the order field, and it ate the clicks there:
        a copy anywhere in Windows and the buttons underneath stopped
        answering, which is what made the whole interface look dead.

        It now goes in the top right of the face, which is empty space: a
        panel that is up never stands between the user and a control.
        """
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        area = None
        hud = getattr(self, "hud", None)
        if hud is not None:
            try:
                area = hud.geometry()
            except Exception:
                area = None
        if area is None or area.width() <= 0:
            area = cw.rect()
        x = max(8, area.right() - pw - 14)
        y = area.top() + 12
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        if muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted." + (f" {note}" if note else ""))
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active." + (f" {note}" if note else ""))

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.set_look("mic_off", _t("window.mic_muted"), "MUTED_C")
        else:
            self._mute_btn.set_look("mic", _t("window.mic_active"), "GREEN")
        self._refresh_status_strip()

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        if hasattr(self, "_state_chip"):
            self._state_chip.set_state(state)
        # THE ACCENT FOLLOWS THE STATE.
        #
        # One colour for the whole interface: the scene, the rings around
        # the head, the brackets, the hands. It changes with what the
        # assistant is doing, and everything else inherits it.
        try:
            from gui import fx
            accent = fx.accent_for(state)
            if getattr(self, "_scene", None) is not None:
                self._scene.state = state
            if getattr(self, "_gesture_overlay", None) is not None:
                self._gesture_overlay.set_accent(accent)
            if getattr(self, "_blades", None) is not None:
                self._blades.set_accent(accent)
        except Exception:
            pass
        # The tray icon carries the state too, because in the tray mode the
        # icon is the only thing the user can see of the assistant.
        if getattr(self, "_tray", None) is not None:
            try:
                self._tray.set_state(_state_text(state))
            except Exception:
                pass
        # And so does the floating panel: it is over somebody else's
        # window, so it is the only part of the interface that is visible
        # while the user is doing something else.
        if getattr(self, "_floating", None) is not None:
            try:
                self._floating.set_state(state)
            except Exception:
                pass

    # ── the ignition ────────────────────────────────────────────────────────

    def _apply_emotional_state(self, name: str, prompt: str = "", colour: str = ""):
        """The user chose an affective state: the interface adopts it.

        THREE THINGS MOVE TOGETHER, and that is the whole point of the state:
        the palette travels toward the state's colour BY INTERPOLATION, the
        face takes on the state's micro-animation, and the model is told the
        new tone.

        The colour glides rather than snapping, and the reason is visible: a
        colour that jumps reads as a mistake, a colour that settles reads as
        a mood changing. The same rule is why the state is a named machine
        state and not a word in a prompt — one thing, three outputs, and they
        never disagree.
        """
        name = str(name or "")
        if not name:
            return

        # 1. The palette, by interpolation.
        target = str(colour or "")
        if target:
            try:
                from gui import theme as _theme
                _theme.animate_accent_to(target)
            except Exception:
                pass

        # 2. The face and the ring.
        try:
            if getattr(self, "hud", None) is not None:
                self.hud.set_emotional_state(name)
        except Exception:
            pass

        # 3. The model. main.py pushes the tone into the live session, so the
        #    change takes effect on the next sentence and not at the next
        #    reconnect.
        if self.on_emotional_state:
            try:
                self.on_emotional_state(name, str(prompt or ""))
            except Exception:
                pass

    def _apply_resource_state(self, state) -> None:
        """The regulator says how the machine is doing: the HUD slows down with it.

        THE PRESENTATION GOES FIRST, THE CAPACITY NEVER. A busy machine makes
        the interface breathe more slowly — 60 frames a second becomes 45, then
        30, then 15 — so that the assistant keeps thinking while the picture
        gets worse. The other way round, a machine that is too slow to draw
        also stops being able to answer.
        """
        try:
            if getattr(self, "hud", None) is not None:
                self.hud.apply_resource_state(state)
        except Exception:
            pass

    def _start_boot(self, ignore: tuple = ()) -> None:
        """Put the ignition up.

        It does not run in the corner mode: a checklist of subsystems is
        for the moment the assistant comes up in front of you, and in the
        corner there is nothing to come up in front of.

        And it does not run while ANYTHING ELSE is on screen. The
        ignition covers the whole interface and swallows what is typed
        into it, so a user who is being asked for a key, or who is
        choosing an engine, would be typing into a splash screen. The
        form is the priority; the ignition waits for it.

        `ignore` names the overlays that do not count as "something else
        is on screen": the engine panel the user has just used is the one
        thing that should not stop the assistant from saying hello.
        """
        if self._appearance.mode() != "dashboard":
            return
        for name in ("_overlay", "_providers_overlay", "_remote_overlay",
                     "_customize_overlay", "_audio_overlay", "_memory_overlay"):
            if name in ignore:
                continue
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                if widget.isVisible():
                    return
            except Exception:
                continue
        try:
            central = self.centralWidget()
            if central is not None:
                self._boot.setGeometry(central.rect())
            self._boot.start()
        except Exception as exc:                               # noqa: BLE001
            print(f"[window] the ignition did not start: {type(exc).__name__}")

    def _on_boot_finished(self) -> None:
        """The ignition is over: the interface is ready."""
        try:
            self._log.append_log(_t("window.boot_done"))
        except Exception:
            pass

    def replay_boot(self) -> str:
        """Show the ignition again — for the user who wants to see it."""
        self._start_boot()
        return _t("window.boot_replayed")

    def _replay_boot_clicked(self) -> None:
        """The button in the controls panel."""
        try:
            self._log.append_log(self.replay_boot())
        except Exception:
            pass

    # ── the deck ────────────────────────────────────────────────────────────

    def _toggle_blades(self) -> None:
        """Open or close the deck."""
        if not self._blades.count:
            return
        if self._blades.is_open():
            self._blades.close_deck()
        else:
            self._blades.open_deck()
            self._blades.show()
            self._blades.raise_()
        self._deck_seen = True
        # The columns share what is left, and the deck has just taken a
        # piece of it or given it back.
        self._fit_side_columns()

    def _push_blade(self, title: str, text: str, code: str = "") -> None:
        """Put a card in the deck, and open the deck the first time.

        The first time only: after that the deck follows whatever the
        user did with it, because a panel that opens itself every time
        something happens is a panel the user learns to close — and a
        card that comes back on its own is a card that cannot be thrown
        away.
        """
        if not getattr(self, "_blades", None):
            return
        try:
            # AN EMPTY DECK OPENS AGAIN. The deck that the user emptied
            # by hand is a deck with nothing in it, and the next card is
            # a reason to open it once more.
            was_empty = self._blades.count == 0
            self._blades.push(title, text, code)
            if was_empty or not self._deck_seen:
                self._deck_seen = True
                self._blades.open_deck()
                self._blades.show()
                self._blades.raise_()
        except Exception:
            pass

    # ── the thing that stays on top ─────────────────────────────────────────

    def _toggle_floating(self) -> None:
        """Show or hide the floating panel."""
        panel = getattr(self, "_floating", None)
        if panel is None:
            return
        if panel.isVisible():
            panel.hide()
        else:
            panel.move_to_default()
            panel.show()
            panel.raise_()

    # ── the keys ────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        """The letters of the deck.

        They only work while the deck is open, and a key event only
        reaches the window when no text field has the focus — a QLineEdit
        swallows the letters it needs for writing. So the shortcut cannot
        eat what the user is typing, and the user does not have to think
        about which of the two is happening.
        """
        try:
            deck = getattr(self, "_blades", None)
            if deck is not None and deck.is_open() and deck.count:
                key = event.key()
                if key == Qt.Key.Key_E:
                    deck.fold()
                    event.accept()
                    return
                if key == Qt.Key.Key_X:
                    deck.close_deck()
                    event.accept()
                    return
                if key == Qt.Key.Key_BracketLeft:
                    deck.prev_blade()
                    event.accept()
                    return
                if key == Qt.Key.Key_BracketRight:
                    deck.next_blade()
                    event.accept()
                    return
        except Exception:
            pass
        super().keyPressEvent(event)

    def _check_config(self) -> bool:
        # A key in the Credential Locker counts, even though the settings file
        # does not hold it — the file is not where the key lives. Reading only
        # the file asked the user for a key they had already given: the form
        # appeared on every single start.
        try:
            from memory.config_manager import has_gemini_key
            keyed = has_gemini_key()
        except Exception:
            keyed = False
        d = _read_full_config()
        providers = d.get("providers") if isinstance(d.get("providers"), dict) else {}
        # A Gemini key, or a choice to run without one: a local engine needs no key.
        return bool(keyed or d.get("local_only")
                    or str(providers.get("mode") or "").lower() == "pipeline")

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        ov.done.connect(self._on_setup_done)
        self._overlay = ov
        self._centre_setup_overlay()
        ov.show()
        ov.raise_()
        # THE FIELD GETS THE FOCUS.
        #
        # Nobody wants to click a box before they can write in it, and a
        # form whose field is not focused does not even show the cursor
        # in it: the user types and the letters go to whatever else had
        # the focus — a scroll area of the panel behind, which swallows
        # them without a sound. The field is focused here, when the form
        # appears, so the first key the user presses is already in it.
        try:
            ov._key_input.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:
            pass

    def _on_setup_done(self, key: str):
        # The key goes to the Credential Locker, never to the settings file: a
        # key in a JSON file next to the program is one backup away from being
        # exposed, and a key that never reaches the locker is a key the model
        # never gets.
        #
        # Everything else is merged into the settings, never written over them.
        # This used to replace the whole file with these two keys, so re-entering
        # a key after an "API key invalid" error erased every other setting — the
        # wake word, the local engine, plugin credentials, the assistant's name.
        from memory.config_manager import save_api_keys, update_config
        key = (key or "").strip()
        if key:
            try:
                save_api_keys(key)
            except Exception as exc:
                # Nothing is lost: the form stays on screen with the key still
                # typed into it, and the user can try again. Swallowing this
                # and carrying on would leave them with an app that has no key
                # and no way to know it.
                try:
                    self._log.append_log(
                        f"ERR: Gemini key not saved ({type(exc).__name__}) - "
                        "type it again"
                    )
                except Exception:
                    pass
                return
        else:
            # Running without Gemini is a choice, not a deletion: a key that may
            # already be in the locker stays there.
            update_config(local_only=True)
        try:
            from core import gemini as _gemini
            _gemini.api_key(refresh=True)
        except Exception:
            pass
        if not key:
            QTimer.singleShot(400, self._open_providers)
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        # THE IGNITION PLAYS NOW.
        #
        # The form was the priority while it was on screen; now that the
        # assistant is really coming up, this is the moment the ignition
        # was written for.
        QTimer.singleShot(500, self._start_boot)
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "T.E.E.M.O.") or "T.E.E.M.O."
        self._log.append_log(f"SYS: Initialised. Windows. {self._assistant_name} online.")


