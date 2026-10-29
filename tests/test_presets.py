"""Preset API tests: the FFmpeg pane's 'Save' button freezes one
successful generation — instruction, inputs, outputs, generated command,
and the result pane's metadata — into a JSON file named by md5 over
instruction + inputs + command. The Info/Log pane's PRESETS block lists
them, previews one's full JSON, and loads it back into the FFmpeg pane.
Storage is $FASS_PRESETS_DIR (tests) or <config dir>/presets (deployments,
/config/presets in the container — persisted by the /config volume).

Run under pytest from the repo root (no server needed: fastapi TestClient).
"""

from __future__ import annotations

import hashlib
import json
import re

from fastapi.testclient import TestClient

from fass.api import app

client = TestClient(app)

BODY = {
    "instruction": "resize to 720p and mix in the background song",
    "assets": {"main": "clip.mp4", "in1": {"path": "song.mp3", "kind": "audio"}},
    "command": "ffmpeg -i clip.mp4 -i song.mp3 -vf scale=-2:720 "
               "-c:v libx264 -c:a aac out.mp4",
    "output_filename": "out.mp4",
    "adapter": "rules",
    "preflight": False,
    "verbose": False,
    "servehttp": False,
    "arguments": ["ffmpeg", "-i", "clip.mp4", "-i", "song.mp3",
                  "-vf", "scale=-2:720", "-c:v", "libx264", "-c:a", "aac",
                  "out.mp4"],
    "summary": ["resize to 720p", "replace the audio with song.mp3"],
    "defaults": ["scale=-2:720 keeps the aspect ratio"],
    "warnings": [],
    "validation_level": "static",
    "checks": [{"ok": True, "name": "output", "detail": "out.mp4"}],
    "evidence": None,
}


def expected_name(body: dict) -> str:
    """The server-side naming: md5 over instruction + canonical inputs +
    command (key order in the assets map does not change the name)."""
    basis = body["instruction"] + "\n" + json.dumps(
        body["assets"], sort_keys=True, separators=(",", ":")) + "\n" + body["command"]
    return hashlib.md5(basis.encode("utf-8")).hexdigest() + ".json"


def save(body: dict, **kw):
    return client.post("/v1/presets", json={**BODY, **body, **kw})


# ------------------------------------------------------------- storage

def test_presets_list_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path / "presets"))
    r = client.get("/v1/presets")
    assert r.status_code == 200
    d = r.json()
    assert d["presets"] == []
    assert d["dir"] == str(tmp_path / "presets")


def test_preset_save_and_list_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path))
    r = save({})
    assert r.status_code == 200
    d = r.json()
    name = d["name"]
    assert re.fullmatch(r"[0-9a-f]{32}\.json", name)
    assert name == expected_name(BODY)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", d["saved_at"])
    assert d["dir"] == str(tmp_path)

    # the file carries the whole generation, assets in the sent order
    doc = json.loads((tmp_path / name).read_text(encoding="utf-8"))
    assert doc["schema"] == 1
    assert doc["name"] == name
    assert doc["instruction"] == BODY["instruction"]
    assert list(doc["assets"].keys()) == ["main", "in1"]
    assert doc["assets"]["in1"] == {"path": "song.mp3", "kind": "audio"}
    assert doc["command"] == BODY["command"]
    assert doc["output_filename"] == "out.mp4"
    assert doc["adapter"] == "rules"
    assert doc["preflight"] is False and doc["verbose"] is False
    assert doc["arguments"] == BODY["arguments"]
    assert doc["summary"] == BODY["summary"]
    assert doc["defaults"] == BODY["defaults"]
    assert doc["checks"] == BODY["checks"]
    assert doc["evidence"] is None

    # and the list offers it back, newest first, with a truncated command
    lst = client.get("/v1/presets").json()
    assert [p["name"] for p in lst["presets"]] == [name]
    row = lst["presets"][0]
    assert row["instruction"] == BODY["instruction"]
    assert row["command"] == BODY["command"][:120]
    assert row["saved_at"] == d["saved_at"]
    assert row["size"] == (tmp_path / name).stat().st_size


def test_preset_save_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path))
    first = save({}).json()
    second = save({}).json()
    assert second["name"] == first["name"]  # same generation, same file
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_preset_name_tracks_instruction_inputs_command(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path))
    base = save({}).json()["name"]
    # a changed command is a new preset
    assert save({"command": BODY["command"] + " -y"}).json()["name"] != base
    # a changed instruction is a new preset
    assert save({"instruction": BODY["instruction"] + " (25fps)"}).json()["name"] != base
    # a changed input is a new preset
    assert save({"assets": {"main": "other.mp4"}}).json()["name"] != base
    # key order in the assets map does NOT change the name (canonical basis)
    reordered = {"assets": {"in1": BODY["assets"]["in1"], "main": "clip.mp4"}}
    assert save(reordered).json()["name"] == base
    # the first save was overwritten by the reordered one, not duplicated
    assert len(list(tmp_path.glob("*.json"))) == 4


def test_preset_get_returns_the_raw_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path))
    name = save({}).json()["name"]
    r = client.get("/v1/presets/" + name)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # the raw text — the Info/Log pane renders it line by line
    assert r.text == (tmp_path / name).read_text(encoding="utf-8")
    assert json.loads(r.text)["command"] == BODY["command"]


def test_preset_get_rejects_non_preset_names(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path))
    name = save({}).json()["name"]
    # wrong shape → 404 (the filename is the path-traversal guard)
    assert client.get("/v1/presets/notes.json").status_code == 404
    assert client.get("/v1/presets/" + "0" * 32).status_code == 404  # no .json
    assert client.get("/v1/presets/" + "g" * 32 + ".json").status_code == 404
    # right shape but never saved → 404
    assert client.get("/v1/presets/" + "f" * 32 + ".json").status_code == 404
    # the saved one still answers
    assert client.get("/v1/presets/" + name).status_code == 200


def test_preset_list_skips_corrupt_and_foreign_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path))
    (tmp_path / "notes.txt").write_text("not a preset", encoding="utf-8")
    (tmp_path / ("a" * 32 + ".json")).write_text("[1, 2]", encoding="utf-8")
    (tmp_path / ("b" * 32 + ".json")).write_text("{corrupt", encoding="utf-8")
    name = save({}).json()["name"]
    lst = client.get("/v1/presets").json()
    assert [p["name"] for p in lst["presets"]] == [name]
    # a corrupt preset file is a 500 on direct read, never a crash
    r = client.get("/v1/presets/" + "b" * 32 + ".json")
    assert r.status_code == 500


# ------------------------------------------------------------- validation

def test_preset_save_rejects_malformed_bodies(tmp_path, monkeypatch):
    monkeypatch.setenv("FASS_PRESETS_DIR", str(tmp_path))
    assert save({"command": ""}).status_code == 422       # no command to save
    assert save({"instruction": ""}).status_code == 422
    assert save({"bogus": 1}).status_code == 422          # extra fields
    r = save({})  # a valid body still passes → exactly one file
    assert r.status_code == 200
    assert len(list(tmp_path.glob("*.json"))) == 1


# ------------------------------------------------------------- the UI

def test_save_button_is_in_the_ui():
    html = client.get("/").text
    # Save sits next to Generate, disabled until a generation is ready
    assert 'id="savebtn" class="ghost" disabled onclick="savePreset()"' in html
    assert 'id="save-status"' in html
    # the Save state follows the rendered response (lastReady) and Clear
    # resets it
    assert "function savePreset()" in html
    assert "lastReady" in html
    # submit() and savePreset() share the exact asset map
    assert "function collectAssets()" in html


def test_presets_block_is_in_the_ui():
    html = client.get("/").text
    # the fourth radio of the Info/Log 'What to look at' selector
    assert 'id="sel-presets"' in html
    assert "onchange=\"showInfo('presets')\"" in html
    assert 'id="info-presets"' in html
    # selector + refresh, the line-by-line preview pane, and the Load button
    assert 'id="preset-sel"' in html and "onchange=\"previewPreset()\"" in html
    assert "onclick=\"loadPresets()\"" in html
    assert 'id="preset-preview"' in html
    assert 'id="preset-loadbtn" class="ghost" disabled onclick="loadPreset()"' in html
    # showInfo toggles the block and lazy-loads it, like prompts
    assert "if (which === 'presets') loadPresets();" in html
    assert "presetCurrent" in html
