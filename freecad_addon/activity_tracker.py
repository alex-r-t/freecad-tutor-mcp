"""Lightweight Document & GUI observer recording recent student actions.

Captures a bounded ring buffer (max 50 events) of high-signal FreeCAD events
(documents created/closed, objects created/deleted, meaningful property edits,
undo/redo actions, and entering/leaving Sketcher or feature Task panels) so
the AI tutor can see what the student just did without guessing.
"""

from collections import deque
import threading
import time
from typing import Any

import FreeCAD

MAX_ACTIVITY_EVENTS = 50
_NOISY_PROPERTIES = frozenset({
    "Shape",
    "AddSubShape",
    "SuppressedShape",
    "PreviewShape",
    "_ElementMapVersion",
    "Proxy",
    "ExpressionEngine",
    "State",
    "BoundingBox",
    "MemSize",
    "Geometry",  # Handled at high level; individual drag updates are noisy
    "Constraints",
    "ExternalGeo",
    "ExternalGeometry",
})


class _AppDocumentObserver:
    """FreeCAD App::DocumentObserver slots recording model-tree changes."""

    def __init__(self, tracker: "ActivityTracker") -> None:
        self._tracker = tracker

    def slotCreatedDocument(self, doc: Any) -> None:
        self._tracker.record("document_created", getattr(doc, "Name", None))

    def slotDeletedDocument(self, doc: Any) -> None:
        self._tracker.record("document_closed", getattr(doc, "Name", None))

    def slotUndoDocument(self, doc: Any) -> None:
        self._tracker.record("document_undo", getattr(doc, "Name", None))

    def slotRedoDocument(self, doc: Any) -> None:
        self._tracker.record("document_redo", getattr(doc, "Name", None))

    def slotCreatedObject(self, obj: Any) -> None:
        doc_name = getattr(getattr(obj, "Document", None), "Name", None)
        self._tracker.record(
            "object_created",
            doc_name,
            object_name=getattr(obj, "Name", None),
            detail=getattr(obj, "TypeId", None),
        )

    def slotDeletedObject(self, obj: Any) -> None:
        doc_name = getattr(getattr(obj, "Document", None), "Name", None)
        self._tracker.record(
            "object_deleted",
            doc_name,
            object_name=getattr(obj, "Name", None),
            detail=getattr(obj, "TypeId", None),
        )

    def slotChangedObject(self, obj: Any, prop: str) -> None:
        if not prop or prop.startswith("_") or prop in _NOISY_PROPERTIES:
            return
        # Suppress automatic initial Label assignment before creation completes
        if prop == "Label" and getattr(obj, "Name", "") == getattr(obj, "Label", ""):
            return
        doc_name = getattr(getattr(obj, "Document", None), "Name", None)
        self._tracker.record(
            "property_changed",
            doc_name,
            object_name=getattr(obj, "Name", None),
            detail=prop,
            coalesce_seconds=1.0,
        )


class _GuiDocumentObserver:
    """FreeCADGui DocumentObserver slots recording edit-mode transitions."""

    def __init__(self, tracker: "ActivityTracker") -> None:
        self._tracker = tracker

    def slotInEdit(self, view_provider: Any) -> None:
        obj = getattr(view_provider, "Object", None)
        doc_name = getattr(getattr(obj, "Document", None), "Name", None) if obj is not None else None
        self._tracker.record(
            "edit_entered",
            doc_name,
            object_name=getattr(obj, "Name", None) if obj is not None else None,
            detail=getattr(obj, "TypeId", None) if obj is not None else None,
        )

    def slotResetEdit(self, view_provider: Any) -> None:
        obj = getattr(view_provider, "Object", None)
        doc_name = getattr(getattr(obj, "Document", None), "Name", None) if obj is not None else None
        self._tracker.record(
            "edit_exited",
            doc_name,
            object_name=getattr(obj, "Name", None) if obj is not None else None,
        )


class ActivityTracker:
    """Thread-safe ring buffer of recent FreeCAD App and GUI events."""

    def __init__(self, maxlen: int = MAX_ACTIVITY_EVENTS) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._app_observer: _AppDocumentObserver | None = None
        self._gui_observer: _GuiDocumentObserver | None = None

    @property
    def is_installed(self) -> bool:
        return self._app_observer is not None

    def install(self) -> None:
        """Attach App and GUI DocumentObservers cleanly (idempotent)."""
        if self._app_observer is None and hasattr(FreeCAD, "addDocumentObserver"):
            app_obs = _AppDocumentObserver(self)
            FreeCAD.addDocumentObserver(app_obs)
            self._app_observer = app_obs

        if self._gui_observer is None and getattr(FreeCAD, "GuiUp", False):
            try:
                import FreeCADGui as Gui

                if hasattr(Gui, "addDocumentObserver"):
                    gui_obs = _GuiDocumentObserver(self)
                    Gui.addDocumentObserver(gui_obs)
                    self._gui_observer = gui_obs
            except Exception:
                pass

    def uninstall(self) -> None:
        """Detach observers cleanly when the RPC server stops."""
        if self._app_observer is not None and hasattr(FreeCAD, "removeDocumentObserver"):
            try:
                FreeCAD.removeDocumentObserver(self._app_observer)
            except Exception:
                pass
            self._app_observer = None

        if self._gui_observer is not None:
            try:
                import FreeCADGui as Gui

                if hasattr(Gui, "removeDocumentObserver"):
                    Gui.removeDocumentObserver(self._gui_observer)
            except Exception:
                pass
            self._gui_observer = None

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def record(
        self,
        event: str,
        document: str | None,
        *,
        object_name: str | None = None,
        detail: str | None = None,
        coalesce_seconds: float = 0.0,
    ) -> None:
        """Record an event into the ring buffer, optionally coalescing slider/spinbox drags."""
        now = time.time()
        entry = {
            "timestamp": round(now, 3),
            "event": event,
            "document": document,
            "object": object_name,
            "detail": detail,
        }
        with self._lock:
            if coalesce_seconds > 0 and self._events:
                prev = self._events[-1]
                if (
                    prev["event"] == event
                    and prev["document"] == document
                    and prev["object"] == object_name
                    and prev["detail"] == detail
                    and (now - prev["timestamp"]) <= coalesce_seconds
                ):
                    self._events[-1] = entry
                    return
            self._events.append(entry)

    def recent(self, limit: int = 25, doc_name: str = "") -> dict[str, Any]:
        """Return up to `limit` most recent events (newest first)."""
        clamped = max(1, min(MAX_ACTIVITY_EVENTS, int(limit)))
        with self._lock:
            items = list(self._events)
        if doc_name:
            items = [e for e in items if e.get("document") == doc_name]
        newest_first = list(reversed(items[-clamped:]))
        return {
            "observer_active": self.is_installed,
            "count": len(newest_first),
            "total_buffered": len(items),
            "events": newest_first,
        }


_TRACKER = ActivityTracker()


def install_activity_observer() -> None:
    _TRACKER.install()


def uninstall_activity_observer() -> None:
    _TRACKER.uninstall()


def get_activity_tracker() -> ActivityTracker:
    return _TRACKER


def get_recent_activity(limit: int = 25, doc_name: str = "") -> dict[str, Any]:
    """Return recent student/document actions captured by the FreeCAD observer."""
    return _TRACKER.recent(limit=limit, doc_name=doc_name)
