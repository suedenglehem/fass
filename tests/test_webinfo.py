"""M10.7 web-layer tests: the verbose-log toggle (argv + log placement),
the media-info / log endpoints, the 10000-line log cap, and the Info / Log
tab in the served UI.

Run under pytest from the repo root (fastapi TestClient, no server).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fass import execute
from fass.api import app
from fass.probe.ffprobe import ffprobe_available

REPO = Path(__file__).resolve().parents[1]
TITAN = REPO / "media" / "titan.mp4"
FFMPEG = shutil.which("ffmpeg") is not None

client = TestClient(app)


def _dirs(tmp_path, monkeypatch):
    """Isolated input / output / run dirs (the docker deployment's layout)."""
    in_dir, out_dir, run_dir = (tmp_path / "in", tmp_path / "out", tmp_path / "runs")
    for d in (in_dir, out_dir, run_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(execute.INPUT_DIR_ENV, str(in_dir))
    monkeypatch.setenv(execute.OUTPUT_DIR_ENV, str(out_dir))
    monkeypatch.setenv(execute.RUN_DIR_ENV, str(run_dir))
    monkeypatch.delenv(execute.ALLOW_EXECUTE_ENV, raising=False)
    return in_dir, out_dir, run_dir


def _post(instruction: str, assets: dict, **kw):
    body = {"instruction": instruction, "assets": assets, "adapter": "rules"}
    body.update(kw)
    return client.post("/v1/commands", json=body)


def _lavfi_argv(out_name: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "verbose",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
            "-f", "mp4", out_name]


def _wait(run_id: str, timeout: float = 40.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = execute.get_run(run_id)
        if st and st["status"] != "running":
            return st
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} still running after {timeout}s")


# ------------------------------------------------------------- verbose mode

def test_verbose_true_renders_verbose_loglevel():
    d = _post("resize to 720p", {"main": "clip.mp4"}, verbose=True).json()
    assert d["status"] == "ready"
    # argv index 3 is the -loglevel value (ffmpeg -hide_banner -loglevel X)
    assert d["arguments"][:4] == ["ffmpeg", "-hide_banner", "-loglevel", "verbose"]
    assert "error" not in d["arguments"]
    assert "-loglevel verbose" in d["command"]


def test_verbose_true_applies_to_every_pass_of_a_multipass_plan():
    d = _post("stabilize the video", {"main": "clip.mp4"}, verbose=True).json()
    assert d["status"] == "ready"
    steps = d["steps"]
    assert len(steps) >= 2, "stabilize is a two-pass plan"
    for s in steps:
        assert s["arguments"][:4] == ["ffmpeg", "-hide_banner", "-loglevel", "verbose"], s
    assert all("error" not in s["arguments"] for s in steps)


def test_verbose_default_keeps_the_error_loglevel():
    d = _post("resize to 720p", {"main": "clip.mp4"}).json()
    assert d["status"] == "ready"
    assert d["arguments"][:4] == ["ffmpeg", "-hide_banner", "-loglevel", "error"]


def test_run_request_accepts_the_verbose_field():
    # execution is disabled in the test env → 403 (the field is accepted,
    # not a 422 from the extra='forbid' model)
    r = client.post("/v1/runs", json={
        "arguments": _lavfi_argv("x.mp4"), "verbose": True})
    assert r.status_code == 403


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_verbose_run_log_lands_in_output_log_dir(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    run = execute.start_run(_lavfi_argv("demo.mp4"), verbose=True)
    st = _wait(run.id)
    assert st["status"] == "success", st
    log = out_dir / "log" / f"{run.id}.log"
    assert log.is_file(), f"verbose log must live at {log}"
    assert not (run_dir / f"{run.id}.log").exists()
    assert st["log_path"] == str(log)
    assert st["verbose"] is True
    # listed as a verbose log, fetchable by name, and via the run endpoint
    names = {x["name"]: x["kind"] for x in execute.list_logs()}
    assert names.get(f"{run.id}.log") == "verbose"
    r = client.get(f"/v1/logs/{run.id}.log")
    assert r.status_code == 200 and "Output" in r.text
    r = client.get(f"/v1/runs/{run.id}/log")
    assert r.status_code == 200 and "Output" in r.text
    assert (out_dir / "demo.mp4").is_file()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_non_verbose_run_log_stays_in_run_dir(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    monkeypatch.setenv(execute.ALLOW_EXECUTE_ENV, "1")
    run = execute.start_run(_lavfi_argv("demo.mp4"))
    st = _wait(run.id)
    assert st["status"] == "success", st
    assert (run_dir / f"{run.id}.log").is_file()
    assert not (out_dir / "log" / f"{run.id}.log").exists()
    assert st["verbose"] is False
    names = {x["name"]: x["kind"] for x in execute.list_logs()}
    assert names.get(f"{run.id}.log") == "run"


def test_verbose_run_log_falls_back_to_run_dir_without_output_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(execute.RUN_DIR_ENV, str(tmp_path / "runs"))
    monkeypatch.setattr(execute, "_output_dir", lambda: "")
    assert execute._log_file("r9", verbose=True) == tmp_path / "runs" / "r9.log"


# ---------------------------------------------------------------- log list

def test_logs_lists_and_fetches_a_seeded_log(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    (run_dir / "20260101-000000-abc123.log").write_text("hello log\nsecond line\n")
    logs = client.get("/v1/logs").json()["logs"]
    entry = next(x for x in logs if x["name"] == "20260101-000000-abc123.log")
    assert entry["kind"] == "run"
    assert entry["run_id"] == "20260101-000000-abc123"
    assert entry["size"] > 0 and entry["mtime"]
    r = client.get("/v1/logs/20260101-000000-abc123.log")
    assert r.status_code == 200
    assert r.text == "hello log\nsecond line\n"
    assert "text/plain" in r.headers["content-type"]


def test_logs_reports_verbose_kind_and_sorts_newest_first(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    (out_dir / "log").mkdir(parents=True, exist_ok=True)
    (out_dir / "log" / "old.log").write_text("old\n")
    time.sleep(0.01)
    (out_dir / "log" / "new.log").write_text("new\n")
    (run_dir / "runside.log").write_text("run\n")
    logs = client.get("/v1/logs").json()["logs"]
    kinds = {x["name"]: x["kind"] for x in logs}
    assert kinds["old.log"] == "verbose"
    assert kinds["new.log"] == "verbose"
    assert kinds["runside.log"] == "run"
    mtimes = [x["mtime"] for x in logs]
    assert mtimes == sorted(mtimes, reverse=True)


def test_log_fetch_rejects_unknown_and_traversing_names(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    (run_dir / "ok.log").write_text("ok\n")
    assert client.get("/v1/logs/nope.log").status_code == 404
    assert client.get("/v1/logs/.hidden.log").status_code == 404
    assert client.get("/v1/logs/notalog.txt").status_code == 404
    # a path is not a basename: the route cannot match it
    assert client.get("/v1/logs/../../etc/passwd").status_code == 404
    assert client.get("/v1/logs/sub/ok.log").status_code == 404


# ------------------------------------------------------------------ log cap

def _seed_big_log(path: Path, lines: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i in range(1, lines + 1):
            f.write(f"line {i}\n")


def test_log_over_10000_lines_is_served_as_last_1000_with_banner(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    _seed_big_log(run_dir / "big.log", 10001)
    r = client.get("/v1/logs/big.log")
    assert r.status_code == 200
    body = r.text.splitlines()
    assert body[0] == "[truncated: showing last 1000 of 10001 lines]"
    assert len(body) == 1001
    assert body[1] == "line 9002"
    assert body[-1] == "line 10001"


def test_log_at_exactly_10000_lines_is_served_whole(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    _seed_big_log(run_dir / "edge.log", 10000)
    body = client.get("/v1/logs/edge.log").text.splitlines()
    assert len(body) == 10000
    assert body[0] == "line 1" and body[-1] == "line 10000"


def test_run_log_endpoint_resolves_log_path_and_honors_the_cap(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    log = out_dir / "log" / "vrun.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    _seed_big_log(log, 25000)
    run = execute.Run(
        id="vrun", argv=["ffmpeg", "-i", "a", "o.mp4"], command="ffmpeg -i a o.mp4",
        started="2026-01-01T00:00:00", status="success", exit_code=0,
        log="vrun.log", log_path=str(log), verbose=True)
    execute._save(run)
    r = client.get("/v1/runs/vrun/log")
    assert r.status_code == 200
    body = r.text.splitlines()
    assert body[0] == "[truncated: showing last 1000 of 25000 lines]"
    assert len(body) == 1001
    assert body[-1] == "line 25000"


def test_run_log_endpoint_falls_back_to_run_dir_log(tmp_path, monkeypatch):
    _in, out_dir, run_dir = _dirs(tmp_path, monkeypatch)
    (run_dir / "plain.log").write_text("plain log text\n")
    run = execute.Run(
        id="plain", argv=["ffmpeg", "-i", "a", "o.mp4"], command="ffmpeg -i a o.mp4",
        started="2026-01-01T00:00:00", status="success", exit_code=0,
        log="plain.log")
    execute._save(run)
    r = client.get("/v1/runs/plain/log")
    assert r.status_code == 200
    assert r.text == "plain log text\n"


def test_run_log_endpoint_unknown_run_is_404(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    assert client.get("/v1/runs/no-such-run/log").status_code == 404


# ------------------------------------------------------------- media info

@pytest.mark.parametrize("bad", ["a/b", "sub/dir/x.mp4", "..hidden", "../up.mp4"])
def test_mediainfo_rejects_non_basename_names(bad):
    r = client.get("/v1/mediainfo", params={"target": "input", "file": bad})
    assert r.status_code == 400


def test_mediainfo_rejects_bad_target_and_empty_file(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    assert client.get("/v1/mediainfo", params={"target": "bogus", "file": "x.mp4"}).status_code == 400
    assert client.get("/v1/mediainfo", params={"target": "input", "file": ""}).status_code == 400


def test_mediainfo_missing_file_is_404(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    r = client.get("/v1/mediainfo", params={"target": "input", "file": "nope.mp4"})
    assert r.status_code == 404


def test_mediainfo_strips_the_i_prefix(tmp_path, monkeypatch):
    # the web UI writes '-i clip.mp4'; the endpoint accepts the same convention
    in_dir, _out, _runs = _dirs(tmp_path, monkeypatch)
    (in_dir / "clip.mp4").write_bytes(b"not really media")
    r = client.get("/v1/mediainfo", params={"target": "input", "file": "-i clip.mp4"})
    # the file exists, so the request is not a 400/404 — ffprobe on a fake
    # file fails, which is the documented 502 path
    assert r.status_code in (200, 502)
    if r.status_code == 502:
        assert len(r.json()["detail"]) <= 520


@pytest.mark.skipif(
    not ffprobe_available() or not TITAN.is_file(),
    reason="ffprobe or media/titan.mp4 not available — needs real media",
)
def test_mediainfo_real_media_returns_ffprobe_json(tmp_path, monkeypatch):
    in_dir, out_dir, _runs = _dirs(tmp_path, monkeypatch)
    shutil.copy(TITAN, in_dir / "titan.mp4")
    shutil.copy(TITAN, out_dir / "done.mp4")
    r = client.get("/v1/mediainfo", params={"target": "input", "file": "titan.mp4"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "format" in d and "streams" in d
    assert d["format"]["filename"].endswith("titan.mp4")
    codecs = {s.get("codec_type") for s in d["streams"]}
    assert "video" in codecs
    # the output volume is reachable through target=output
    r = client.get("/v1/mediainfo", params={"target": "output", "file": "done.mp4"})
    assert r.status_code == 200
    assert r.json()["format"]["filename"].endswith("done.mp4")


# ------------------------------------------------------------- tsanalyze

@pytest.mark.parametrize("bad", ["a/b", "sub/dir/x.ts", "..hidden", "../up.ts"])
def test_tsanalyze_rejects_non_basename_names(bad):
    r = client.get("/v1/tsanalyze", params={"target": "input", "file": bad})
    assert r.status_code == 400


def test_tsanalyze_rejects_bad_target_and_empty_file(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    assert client.get("/v1/tsanalyze", params={"target": "bogus", "file": "x.ts"}).status_code == 400
    assert client.get("/v1/tsanalyze", params={"target": "input", "file": ""}).status_code == 400


def test_tsanalyze_missing_file_is_404(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    assert client.get("/v1/tsanalyze", params={"target": "input", "file": "nope.ts"}).status_code == 404


def test_tsanalyze_strips_the_i_prefix(tmp_path, monkeypatch):
    in_dir, _out, _runs = _dirs(tmp_path, monkeypatch)
    (in_dir / "clip.ts").write_bytes(b"not really a TS stream")
    r = client.get("/v1/tsanalyze", params={"target": "input", "file": "-i clip.ts"})
    # the file exists, so the request is not a 400/404 — ffprobe on the
    # fake file fails the mpegts gate, which is the documented 400 path
    assert r.status_code == 400
    assert "not an MPEG-TS" in r.json()["detail"]


def test_tsanalyze_reaches_the_output_volume(tmp_path, monkeypatch):
    # target=output resolves the OUTPUT folder (the Files-tab right-click
    # offers tsanalyze in both panes) — a fake file there gets the mpegts
    # gate 400, not a 404
    _in, out_dir, _runs = _dirs(tmp_path, monkeypatch)
    (out_dir / "clip.ts").write_bytes(b"not really a TS stream")
    r = client.get("/v1/tsanalyze", params={"target": "output", "file": "clip.ts"})
    assert r.status_code == 400
    assert "not an MPEG-TS" in r.json()["detail"]


@pytest.mark.skipif(
    not ffprobe_available() or not TITAN.is_file(),
    reason="ffprobe or media/titan.mp4 not available — needs real media",
)
def test_tsanalyze_rejects_a_non_mpegts_container(tmp_path, monkeypatch):
    # titan.mp4 is an MP4 container: the mpegts gate must refuse it
    in_dir, _out, _runs = _dirs(tmp_path, monkeypatch)
    shutil.copy(TITAN, in_dir / "titan.mp4")
    r = client.get("/v1/tsanalyze", params={"target": "input", "file": "titan.mp4"})
    assert r.status_code == 400
    assert "not an MPEG-TS" in r.json()["detail"]


@pytest.mark.skipif(
    not ffprobe_available() or not TITAN.is_file() or not FFMPEG,
    reason="ffmpeg/ffprobe or media/titan.mp4 not available — needs real media",
)
def test_tsanalyze_on_a_real_ts_file(tmp_path, monkeypatch):
    in_dir, _out, _runs = _dirs(tmp_path, monkeypatch)
    ts = in_dir / "clip.ts"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(TITAN),
                    "-c", "copy", str(ts)], check=True, timeout=60)
    r = client.get("/v1/tsanalyze", params={"target": "input", "file": "clip.ts"})
    if shutil.which("tsanalyze"):
        # the deployment image ships the tool: a plain-text TS report
        assert r.status_code == 200, r.text
        assert r.text.strip()
    else:
        # a dev machine without tsduck: the documented 502 path
        assert r.status_code == 502
        assert "tsanalyze" in r.json()["detail"]


# ------------------------------------------------------------------- the UI

def test_ui_has_the_verbose_toggle_and_the_info_tab():
    html = client.get("/").text
    assert 'id="verbose"' in html and "verbose log" in html
    assert 'id="tab-info"' in html and "showTab('info')" in html
    assert 'id="pane-info"' in html
    assert "['cmd','runs','info','cfg']" in html
    for needle in ("mi-target", "mi-file", "log-run", "Show media info", "Show log"):
        assert needle in html
    # the toggles are wired into both POSTs
    assert "verbose: $('verbose').checked" in html
    assert html.count("verbose: $('verbose').checked") == 2  # submit() + runCmd()


def test_ui_mediainfo_ticker_on_the_command_tab():
    # Mediainfo is now a BUTTON on the FFmpeg tab (next to Generate), not a
    # ticker: the old #mi checkbox is gone and the three option checkboxes
    # (preflight / verbose / serve over http) are independent. The real
    # media-info controls stay in the Info / Log tab.
    html = client.get("/").text
    cmd_pane = html.split('<div id="pane-cmd">')[1].split('<div id="pane-runs"')[0]
    # the old exclusive ticker checkbox is gone
    assert '<input type="checkbox" id="mi">' not in cmd_pane
    # the three real options remain, in order, and are no longer exclusive
    assert cmd_pane.index('id="pf"') < cmd_pane.index('id="verbose"')
    assert cmd_pane.index('id="verbose"') < cmd_pane.index('id="servehttp"')
    assert "['mi', 'pf', 'verbose', 'servehttp']" not in html
    # the Mediainfo button sits next to Generate (between Generate and Clear)
    assert 'id="genbtn"' in cmd_pane and 'id="mi-btn"' in cmd_pane
    assert 'onclick="runMediaInfo()"' in cmd_pane
    assert cmd_pane.index('id="genbtn"') < cmd_pane.index('id="mi-btn"')
    assert "showMediaInfo" not in cmd_pane
    assert "mi-target" not in cmd_pane and "mi-file" not in cmd_pane
    assert "log-run" not in cmd_pane
    # exactly one media-info control, inside the info pane
    assert html.count("Show media info") == 1
    info_pane = html.split('<div id="pane-info"')[1].split('<div id="pane-cfg"')[0]
    assert "Show media info" in info_pane and "log-run" in info_pane
    # the TSAnalyze button sits next to Show media info, disabled until a
    # probed file proves it is an MPEG-TS container
    assert 'id="tsbtn"' in info_pane and "TSAnalyze" in info_pane
    assert 'disabled onclick="showTSAnalyze()"' in info_pane
    # 'Look in' options are bare 'input' / 'output'
    assert '<option value="input">input</option>' in info_pane
    assert '<option value="output">output</option>' in info_pane


def test_ui_mediainfo_button_independent_of_instruction():
    # the Mediainfo button is a peek that ignores the instruction: it does
    # NOT go through the form submit, so the Instruction field keeps its
    # native `required` always (the old miSet required-toggling is gone).
    # While any instruction is present the button is disabled, so a real
    # command is never silently replaced by a report.
    html = client.get("/").text
    # the old required-toggling helper and the exclusive-row listener are gone
    assert "function miSet" not in html
    assert "$('instr').removeAttribute('required')" not in html
    assert "for (const id of ['mi', 'pf', 'verbose', 'servehttp'])" not in html
    # the button is wired to a standalone probe, not the form submit
    assert 'id="mi-btn"' in html
    assert 'onclick="runMediaInfo()"' in html
    assert "async function runMediaInfo()" in html
    # the Instruction field stays required (the peek no longer needs to lift it)
    cmd_pane = html.split('<div id="pane-cmd">')[1].split('<div id="pane-runs"')[0]
    assert '<textarea id="instr" required' in cmd_pane
    # syncMiBtn disables the button while any instruction is present
    assert "function syncMiBtn()" in html
    assert "$('mi-btn').disabled = $('instr').value.trim() !== ''" in html
    # and it runs on every keystroke (and once at load) alongside compare mode
    assert "setCompareMode(); syncMiBtn()" in html


def test_ui_command_tab_clear_button():
    # the button row is Generate | Mediainfo | Clear; Clear resets the whole
    # FFmpeg pane: every field, the option checkboxes, the adapter, the extra
    # inputs, the result area, and the two derived states (the compare labels
    # and the Mediainfo button)
    html = client.get("/").text
    cmd_pane = html.split('<div id="pane-cmd">')[1].split('<div id="pane-runs"')[0]
    assert 'id="genbtn"' in cmd_pane and 'id="mi-btn"' in cmd_pane and 'id="clearbtn"' in cmd_pane
    assert cmd_pane.index('id="genbtn"') < cmd_pane.index('id="mi-btn"') < cmd_pane.index('id="clearbtn"')
    assert 'onclick="clearCmdPane()"' in cmd_pane
    assert "function clearCmdPane()" in html
    for needle in ("$('instr').value = ''", "$('inp').value = ''",
                   "$('out').value = ''", "$('wm').value = ''",
                   "$('ad').value = 'auto'", "$('extra').innerHTML = ''",
                   "$('pf').checked = false", "$('verbose').checked = false",
                   "$('servehttp').checked = false", "setCompareMode()",
                   "syncMiBtn()"):
        assert needle in html
    # the old miSet helper is fully gone
    assert "miSet" not in html


def test_ui_ffmpeg_tab_label_and_prompts_view():
    # the Command pane is renamed FFmpeg (the internal id stays 'cmd'),
    # and the Info/Log pane gains a 'prompts' selector under 'run log'
    html = client.get("/").text
    bar = html.split('<div class="tabs">')[1].split("</div>")[0]
    assert ">FFmpeg<" in bar and ">Command<" not in bar
    info_pane = html.split('<div id="pane-info"')[1].split('<div id="pane-cfg"')[0]
    assert 'id="sel-prompt"' in info_pane
    assert "showInfo('prompts')" in info_pane
    assert 'id="info-prompts"' in info_pane
    # under 'run log'
    assert info_pane.index('id="sel-log"') < info_pane.index('id="sel-prompt"')
    # showInfo toggles the section + refreshes it; the tab-show hook
    # respects the third selector
    assert "$('info-prompts').hidden = (which !== 'prompts')" in html
    assert "if (which === 'prompts') loadPrompts()" in html
    assert "$('sel-prompt').checked ? 'prompts'" in html
    # the list itself: fetch + clickable rows that fill the instruction
    assert "fetch('/v1/prompts')" in html
    assert "function loadPrompts()" in html
    assert "function usePrompt(text)" in html
    assert "$('instr').value = text" in html
    # a programmatic fill never fires the input listener, so usePrompt
    # re-derives both states (the compare labels + the Mediainfo button)
    assert "compare labels and the Mediainfo button (which disables on an instruction)" in html
    assert "syncMiBtn();" in html


def test_ui_run_cmd_sends_the_instruction():
    # the NL instruction that generated the command is posted with the run
    # (run log footer: '# instruction:' above the command line)
    html = client.get("/").text
    assert "instruction: $('instr').value.trim() || null" in html


def test_ui_shows_the_raw_llm_output_block():
    # the Command tab renders the model's verbatim reply (the pre-processing
    # LLM output) as a collapsible block appended to every generation result
    html = client.get("/").text
    assert "rawLlmHtml" in html
    assert "raw LLM output" in html
    assert "d.raw_llm" in html
    # appended in render() for every status (ready / clarify / unsupported /
    # error), not just the ready path
    assert "html += rawLlmHtml(d)" in html


def test_ui_tab_bar_order_files_second_config_last():
    # user-requested order: Command, Files, Runs, Player, Info / Log, RAG,
    # Config — the tab bar only (the showTab pane list is order-independent
    # and keeps its own asserted shape)
    html = client.get("/").text
    bar = html.split('<div class="tabs">')[1].split("</div>")[0]
    assert re.findall(r"showTab\('([a-z]+)'\)", bar) == [
        "cmd", "files", "runs", "player", "info", "rag", "cfg"]


def test_ui_files_tab_right_click_actions_and_free_space():
    html = client.get("/").text
    # the three pane-dependent context-menu items (hidden until fileMenuAt
    # decides they apply)
    for needle in ("fileAction('as-input')", "fileAction('to-input')",
                   "fileAction('tsanalyze')",
                   'id="fm-asinput"', 'id="fm-toinput"', 'id="fm-tsanalyze"'):
        assert needle in html
    # tsanalyze is offered in BOTH panes for TS-ish container extensions
    # (case-insensitive — .TS/.MPG/etc. match the same class); the gate is
    # extension-only, no pane condition
    assert r"\.(ts|mpg|mpeg|ps)$" in html
    assert "TS_EXT_RX.test(row.dataset.name) ? '' : 'none'" in html
    assert "isInput && TS_EXT_RX" not in html
    # and the action probes the pane the file was right-clicked in
    # (input OR output volume), not a hardcoded 'input'
    assert "$('mi-target').value = dir" in html
    assert "$('mi-target').value = 'input'" not in html
    # the free-space line under each pane, one fetch for both
    for needle in ('id="files-disk-input"', 'id="files-disk-output"',
                   "loadDisk", "fetch('/v1/files/disk')"):
        assert needle in html
    # the actions themselves: 'as input' writes the Command tab's input
    # field; 'to input' posts the server copy; tsanalyze reuses the
    # Info / Log tab's TSAnalyze display
    assert "$('inp').value = name" in html
    assert "fetch('/v1/files/copy-to-input'" in html
    # mi-file is assigned only after showInfo('mi') — fillMi() would
    # otherwise overwrite it from the Command tab's fields
    assert "$('mi-file').value = name" in html
    assert html.index("$('mi-file').value = name") > \
        html.index("if ($('info-mi').hidden) showInfo('mi');")
