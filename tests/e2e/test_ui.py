"""Drive the real Streamlit UI headlessly: publish through the normal UI, then resume in a new session."""
import os
from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parents[2] / "app" / "main.py")


@pytest.fixture
def ui_env(services, test_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    from streamlit.testing.v1 import AppTest
    return AppTest


def click(at, label_start: str):
    btn = next(b for b in at.button if b.label.startswith(label_start))
    btn.click().run(timeout=60)
    return at


def text(at) -> str:
    parts = [m.value for m in at.markdown] + [m.value for m in at.success] + [m.value for m in at.error] + \
            [m.value for m in at.info] + [m.value for m in at.warning] + [m.value for m in at.caption]
    return "\n".join(str(p) for p in parts)


def new_session(AppTest, run_id=None, ws="demo"):
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["ws"] = ws
    at.query_params["page"] = "journey"
    if run_id:
        at.query_params["run"] = run_id
    return at.run()


def test_publish_through_ui_then_lost_response_resume(ui_env):
    AppTest = ui_env
    at = new_session(AppTest)
    assert not at.exception, at.exception
    at.selectbox(key="pick_scenario").set_value("lost_response_after_write").run()
    click(at, "▶ Start run")
    run_id = at.query_params["run"][0] if isinstance(at.query_params["run"], list) else at.query_params["run"]
    click(at, "💬 Send task")
    click(at, "🔎 Verify sign-in")
    assert "VERIFIED" in text(at)
    click(at, "✔ Approve")
    click(at, "🚀 Publish")
    assert "UNKNOWN" in text(at)
    # A different browser session: only the URL carries over; state comes from PostgreSQL.
    other = new_session(AppTest, run_id)
    assert "UNKNOWN" in text(other)
    click(other, "🔍 Check publication status")
    body = text(other)
    assert "CONFIRMED" in body and "reconciliation" in body
    assert "Releases in the target for this operation: **1**" in body


def test_help_center_and_results_pages_render(ui_env):
    AppTest = ui_env
    for page in ("help", "results", "operations"):
        at = AppTest.from_file(APP, default_timeout=60)
        at.query_params["page"] = page
        at.run()
        assert not at.exception, (page, at.exception)
    assert os.environ["DATABASE_URL"].rsplit("/", 1)[1].startswith("lab_test_")
