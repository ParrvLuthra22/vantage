"""scripts/collect_judge_traces.py: the reporting helpers and, more importantly,
the loop's stop conditions — the part that decides whether a multi-hour job
notices it is doing nothing.

The loop tests run the real main() against a fake `vantage` executable, so no
network, agent, or judge is involved.
"""

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "scripts" / "collect_judge_traces.py"


@pytest.fixture(scope="module")
def collect():
    spec = importlib.util.spec_from_file_location("collect_judge_traces", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(prompt, response="r", score=4, category="clear"):
    return json.dumps(
        {
            "input_prompt": prompt,
            "raw_response": response,
            "parsed_score": score,
            "category": category,
        }
    )


def test_summarize_counts_what_is_actually_there(collect, tmp_path):
    log = tmp_path / "t.jsonl"
    log.write_text(
        "\n".join(
            [
                _entry("A", "r1", 4),
                _entry("A", "r1", 4),  # exact duplicate
                _entry("A", "r2", 5),  # same prompt, different judgment
                _entry("B", "r3", 1, "adversarial"),
                '{"input_prompt": "torn", "raw_res',  # killed mid-write
                "",  # blank line: ignored, not counted
            ]
        )
    )

    s = collect.summarize(log)

    assert s["lines"] == 5
    assert s["invalid"] == 1
    assert s["unique_prompts"] == 2
    assert s["unique_examples"] == 3
    assert s["scores"] == {1: 1, 4: 2, 5: 1}
    assert s["categories"] == {"adversarial": 1, "clear": 3}


def _write_scores(path, scores):
    path.write_text("\n".join(_entry(f"p{i}", "r", sc) for i, sc in enumerate(scores)) + "\n")


def test_report_warns_when_scores_are_skewed(collect, tmp_path, capsys):
    """The pilot's real data was 14/19 fives; the failure examples a judge most
    needs are the scarce ones, and the report should say so rather than let a
    big line count imply a balanced dataset."""
    log = tmp_path / "t.jsonl"
    _write_scores(log, [5] * 14 + [1, 1, 2, 3, 4])

    collect.print_report(log, started=collect.time.monotonic())

    assert "74% of judgments are a 5" in capsys.readouterr().out


def test_report_is_quiet_about_skew_when_scores_are_spread(collect, tmp_path, capsys):
    log = tmp_path / "t.jsonl"
    _write_scores(log, [1, 2, 3, 4, 5] * 4)

    collect.print_report(log, started=collect.time.monotonic())

    assert "of judgments are a" not in capsys.readouterr().out


def test_summarize_of_a_missing_file_is_empty_not_an_error(collect, tmp_path):
    s = collect.summarize(tmp_path / "nope.jsonl")
    assert (s["lines"], s["unique_prompts"], s["invalid"]) == (0, 0, 0)


def test_command_skips_the_database_by_default(collect, tmp_path):
    cmd = collect.build_command(
        "vantage", "suite", "vesper", "groq", tmp_path / "o.jsonl", False, False
    )
    assert "--no-persist" in cmd
    assert cmd[cmd.index("--judge-provider") + 1] == "groq"
    assert cmd[cmd.index("--adapter") + 1] == "vesper"
    assert cmd[cmd.index("--collect-judge-traces") + 1] == str(tmp_path / "o.jsonl")

    persisting = collect.build_command(
        "vantage", "suite", "vesper", "groq", tmp_path / "o", True, False
    )
    assert "--no-persist" not in persisting


def test_caffeinate_wraps_only_on_macos(collect, tmp_path, monkeypatch):
    monkeypatch.setattr(collect.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(collect.sys, "platform", "darwin")
    wrapped = collect.build_command("vantage", "s", "vesper", "groq", tmp_path / "o", False, True)
    assert wrapped[:2] == ["caffeinate", "-i"]

    monkeypatch.setattr(collect.sys, "platform", "linux")
    assert (
        collect.build_command("vantage", "s", "vesper", "groq", tmp_path / "o", False, True)[0]
        == "vantage"
    )


# --- the loop, against a fake `vantage` -----------------------------------


@pytest.fixture
def fake_vantage(collect, tmp_path, monkeypatch):
    """A stand-in executable: on the iterations listed in FAKE_YIELD_ON (comma
    separated, 1-based) it appends one valid line to the --collect-judge-traces
    file; it exits FAKE_EXIT (default 1, like a normal run with failing
    scenarios — or an uncaught crash)."""
    script = tmp_path / "fake_vantage"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "out = args[args.index('--collect-judge-traces') + 1]\n"
        "counter = os.environ['FAKE_COUNTER']\n"
        "n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1\n"
        "open(counter, 'w').write(str(n))\n"
        "if str(n) in os.environ['FAKE_YIELD_ON'].split(','):\n"
        "    with open(out, 'a') as f:\n"
        "        f.write(json.dumps({'input_prompt': f'p{n}', 'raw_response': 'r',\n"
        "                            'parsed_score': 4, 'category': 'clear'}) + '\\n')\n"
        "sys.exit(int(os.environ.get('FAKE_EXIT', '1')))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(collect, "resolve_vantage_executable", lambda: str(script))
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    monkeypatch.setenv("FAKE_COUNTER", str(tmp_path / "counter"))
    return script


def _run(collect, monkeypatch, tmp_path, *extra):
    argv = [
        "collect_judge_traces.py",
        "--suite",
        "s",
        "--output",
        str(tmp_path / "out" / "t.jsonl"),
        "--judge-provider",
        "groq",
        "--no-caffeinate",
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return collect.main(), tmp_path / "out" / "t.jsonl"


def test_a_healthy_run_appends_every_iteration(
    collect, fake_vantage, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_YIELD_ON", "1,2,3,4,5")

    rc, out = _run(collect, monkeypatch, tmp_path, "--iterations", "3")

    assert rc == 0
    assert len(out.read_text().splitlines()) == 3
    assert "Iteration 3/3" in capsys.readouterr().out


def test_stops_when_iterations_stop_producing_anything(
    collect, fake_vantage, tmp_path, monkeypatch
):
    """The exit code can't tell a crash from 'some scenarios failed' (both are 1),
    so only the missing output can — without this guard, 40 crashing iterations
    would look like a completed run."""
    monkeypatch.setenv("FAKE_YIELD_ON", "1")  # one good iteration, then nothing

    rc, out = _run(collect, monkeypatch, tmp_path, "--iterations", "10", "--max-zero-yield", "2")

    assert rc == 1
    assert len(out.read_text().splitlines()) == 1
    assert int((tmp_path / "counter").read_text()) == 3  # 1 good + 2 empty, not all 10


def test_a_productive_iteration_resets_the_zero_yield_streak(
    collect, fake_vantage, tmp_path, monkeypatch
):
    """Empty iterations that aren't consecutive must not add up to an abort:
    yield, empty, yield, empty, yield never has two empties in a row."""
    monkeypatch.setenv("FAKE_YIELD_ON", "1,3,5")

    rc, out = _run(collect, monkeypatch, tmp_path, "--iterations", "5", "--max-zero-yield", "2")

    assert rc == 0
    assert len(out.read_text().splitlines()) == 3


def test_an_unexpected_exit_code_aborts(collect, fake_vantage, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_YIELD_ON", "1,2,3,4,5")
    monkeypatch.setenv("FAKE_EXIT", "2")  # e.g. click usage error / adapter unavailable

    rc, _ = _run(collect, monkeypatch, tmp_path, "--iterations", "5")

    assert rc == 1
    assert int((tmp_path / "counter").read_text()) == 1


def test_missing_judge_key_fails_before_running_anything(
    collect, fake_vantage, tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("GROQ_API_KEY")

    rc, out = _run(collect, monkeypatch, tmp_path, "--iterations", "5")

    assert rc == 2
    assert "GROQ_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "counter").exists()  # the fake was never invoked
    assert not out.exists()


def test_mock_adapter_is_allowed_but_warned_about(
    collect, fake_vantage, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_YIELD_ON", "1,2,3,4,5")

    rc, _ = _run(collect, monkeypatch, tmp_path, "--iterations", "1", "--adapter", "mock")

    assert rc == 0
    assert "deterministic" in capsys.readouterr().err
