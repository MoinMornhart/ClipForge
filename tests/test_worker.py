import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from clipforge.core.errors import Cancelled, ClipForgeError
from clipforge.ui.worker import Task, running_tasks


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def run(task: Task, timeout=5000) -> dict:
    out: dict = {"progress": [], "threads": set()}
    loop = QEventLoop()
    main = threading.get_ident()

    def note(key):
        def f(*a):
            out[key] = a
            out["threads"].add(threading.get_ident() == main)
            QTimer.singleShot(50, loop.quit)
        return f

    task.progress.connect(lambda *a: out["progress"].append(a))
    task.finished.connect(note("finished"))
    task.failed.connect(note("failed"))
    task.cancelled.connect(note("cancelled"))
    QTimer.singleShot(timeout, loop.quit)
    task.start()
    loop.exec()
    return out


def test_finished_in_gui_thread(app):
    def work(progress, cancel, confirm):
        progress("x", 0.5, "halb")
        return 42

    out = run(Task(work))
    assert out["finished"] == (42,) and out["progress"] == [("x", 0.5, "halb")]
    assert out["threads"] == {True}  # Signale kommen im Fenster-Thread an
    assert running_tasks() == 0


def test_errors_and_cancel(app):
    def fail(*a):
        raise ClipForgeError("Kaputt", "Tipp")

    assert run(Task(fail))["failed"] == ("Kaputt", "Tipp")

    def crash(*a):
        raise ZeroDivisionError("teilen")

    msg, hint = run(Task(crash))["failed"]
    assert "unerwarteter" in msg and "ZeroDivisionError" in hint

    def cancel(*a):
        raise Cancelled()

    assert "cancelled" in run(Task(cancel))


def test_confirm_roundtrip(app):
    t = Task(lambda p, c, confirm: confirm("Weiter?"))
    t.confirm_request.connect(lambda text: t.answer(text == "Weiter?"))
    assert run(t)["finished"] == (True,)


def test_cancel_unblocks_confirm(app):
    t = Task(lambda p, c, confirm: confirm("Weiter?"))
    t.confirm_request.connect(lambda text: t.cancel.set())
    assert run(t)["finished"] == (False,)
