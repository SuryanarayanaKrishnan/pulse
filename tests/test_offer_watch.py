import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import offer_watch as ow  # noqa: E402

HTML = """<html><script>var x='AED 99 cashback'</script><body><h1>Cashback Card</h1>
<p>Annual fee: AED 199, waived with AED 10,000 spend</p><p>Welcome offer valid until 31 December 2026</p>
<p>Totally unrelated marketing sentence here.</p></body></html>"""


def test_signal_lines_skip_scripts_and_noise():
    lines = ow.signal_lines(HTML)
    assert any("Annual fee" in x for x in lines)
    assert not any("unrelated" in x for x in lines)
    assert not any("var x" in x for x in lines)


def test_diff_and_expiry():
    d = ow.diff_lines(["a 5%", "b 3%"], ["a 5%", "c 7%"])
    assert d == {"added": ["c 7%"], "removed": ["b 3%"]}
    hits = ow.expiring(ow.signal_lines(HTML), date(2026, 12, 20))
    assert hits and hits[0]["days_left"] == 11


def test_parse_dates_formats():
    assert ow.parse_dates("until 5th Jan 2027") == [date(2027, 1, 5)]
    assert ow.parse_dates("ends December 31, 2026") == [date(2026, 12, 31)]
    assert ow.parse_dates("2026-11-30") == [date(2026, 11, 30)]
