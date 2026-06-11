import sqlite3
from unittest.mock import Mock, patch

import pytest

from olms import discover
from olms.discover import (
    DiscoveryConfig,
    bisect_max_assigned,
    extract_sr_num,
    fetch,
    fetch_assigned,
    is_assigned,
    watermark,
)


def response(status=200, content_type="text/html", body=b"ng-app="):
    r = Mock()
    r.status_code = status
    r.headers = {"Content-Type": content_type}
    r.content = body
    r.__enter__ = Mock(return_value=r)
    r.__exit__ = Mock(return_value=False)
    return r


def test_fetch_backs_off_then_succeeds():
    session = Mock()
    session.get.side_effect = [response(403), response(403), response()]
    with patch.object(discover.time, "sleep") as sleep:
        content_type, body = fetch(session, 1, "LM10Form")
    assert content_type == "text/html"
    assert body == b"ng-app="
    assert [c.args[0] for c in sleep.call_args_list] == [5, 10]


def test_fetch_raises_after_persistent_block():
    session = Mock()
    session.get.side_effect = [response(403)] * 6
    with patch.object(discover.time, "sleep"):
        with pytest.raises(RuntimeError, match="blocked"):
            fetch(session, 1, "LM10Form")


def test_fetch_skips_non_html_bodies():
    session = Mock()
    session.get.side_effect = [response(content_type="application/pdf", body=b"%PDF")]
    content_type, body = fetch(session, 1, "LM10Form")
    assert content_type == "application/pdf"
    assert body == b""  # never read the PDF


def test_fetch_assigned():
    session = Mock()
    session.get.side_effect = [
        response(content_type="application/pdf"),
        response(body=b"<html>not found stub</html>"),
        response(body=b'<html ng-app="LM20App">'),
    ]
    assert fetch_assigned(session, 1, "LM2Form") is True
    assert fetch_assigned(session, 2, "LM2Form") is False
    assert fetch_assigned(session, 3, "LM2Form") is True


def test_is_assigned_short_circuits():
    session = Mock()
    session.get.side_effect = [response(content_type="application/pdf")]
    assert is_assigned(session, 1) is True
    assert session.get.call_count == 1  # not all five probe forms


def test_bisect_finds_boundary():
    assigned_up_to = 1234
    with patch.object(
        discover, "is_assigned", side_effect=lambda s, r: r <= assigned_up_to
    ):
        assert bisect_max_assigned(None, low=1000, initial_step=10) == assigned_up_to


HTML = """
<span class="i-label">1.a. File Number: C-</span>
<span class="i-value"> 297 </span>
"""


def test_extract_sr_num_tries_labels_in_order():
    labels = ("1. File Number: E-", "1.a. File Number: C-")
    assert extract_sr_num(HTML.encode(), labels) == 297
    assert extract_sr_num(b"<html></html>", labels) is None


def test_watermark(tmp_path):
    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE filing (rptId INTEGER, formFiled TEXT)")
    conn.execute("INSERT INTO filing VALUES (7, 'LM-10'), (9, 'LM-2')")
    conn.commit()
    assert watermark(db, "SELECT max(rptId) FROM filing") == 9
    assert (
        watermark(db, "SELECT max(rptId) FROM filing WHERE formFiled = 'LM-10'") == 7
    )
    assert (
        watermark(db, "SELECT max(rptId) FROM filing WHERE formFiled = 'LM-30'") == 0
    )


def test_config_defaults():
    config = DiscoveryConfig(
        watermark_sql="SELECT 1",
        scan_forms=("LM10Form",),
        sr_num_labels=("x",),
    )
    assert config.scan_concurrency == 4
    assert config.user_agent.startswith("Mozilla/5.0")


def test_fetch_hit_html_falls_through_mismatched_forms():
    # OLMS only server-renders a filing's data when rptForm matches its
    # actual type; a mismatch returns the bare Angular shell with no
    # "Signature" (verified live: an electronic LM-21 under LM20Form is
    # the ~8.5K shell, under LM21Form the ~21K rendered form). The scan
    # must therefore fall through to the next configured form.
    from olms.discover import fetch_hit_html

    session = Mock()
    session.get.side_effect = [
        response(body=b'<html ng-app="LM20App">bare shell</html>'),
        response(body=b"<html>Signature ... rendered LM-21 ...</html>"),
    ]
    body = fetch_hit_html(session, 941283, ("LM20Form", "LM21Form"))
    assert b"rendered LM-21" in body
    assert session.get.call_count == 2
