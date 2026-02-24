"""Tests for roam watch command -- file watcher with debouncing."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from roam.commands.cmd_watch import (
    DebounceAccumulator,
    WebhookBridge,
    detect_changes,
    scan_disk_mtimes,
    load_tracked_files,
    poll_loop,
)


class TestDetectChanges:
    def test_no_changes(self):
        tracked = {"a.py": 1000.0, "b.py": 2000.0}
        disk = {"a.py": 1000.0, "b.py": 2000.0}
        added, modified, removed = detect_changes(tracked, disk)
        assert added == []
        assert modified == []
        assert removed == []

    def test_new_file_detected(self):
        tracked = {"a.py": 1000.0}
        disk = {"a.py": 1000.0, "b.py": 2000.0}
        added, modified, removed = detect_changes(tracked, disk)
        assert added == ["b.py"]
        assert modified == []
        assert removed == []

    def test_deleted_file_detected(self):
        tracked = {"a.py": 1000.0, "b.py": 2000.0}
        disk = {"a.py": 1000.0}
        added, modified, removed = detect_changes(tracked, disk)
        assert added == []
        assert modified == []
        assert removed == ["b.py"]

    def test_modified_file_detected(self):
        tracked = {"a.py": 1000.0}
        disk = {"a.py": 1001.5}
        added, modified, removed = detect_changes(tracked, disk)
        assert added == []
        assert modified == ["a.py"]
        assert removed == []

    def test_mtime_within_tolerance_not_modified(self):
        tracked = {"a.py": 1000.0}
        disk = {"a.py": 1000.0005}
        _, modified, _ = detect_changes(tracked, disk)
        assert modified == []

    def test_mtime_at_tolerance_boundary_not_modified(self):
        tracked = {"a.py": 1000.0}
        disk = {"a.py": 1000.001}
        _, modified, _ = detect_changes(tracked, disk)
        assert modified == []

    def test_all_three_change_types(self):
        tracked = {"old.py": 1.0, "kept.py": 2.0}
        disk = {"new.py": 3.0, "kept.py": 99.0}
        added, modified, removed = detect_changes(tracked, disk)
        assert added == ["new.py"]
        assert modified == ["kept.py"]
        assert removed == ["old.py"]

    def test_empty_tracked_all_added(self):
        tracked = {}
        disk = {"a.py": 1.0, "b.py": 2.0}
        added, modified, removed = detect_changes(tracked, disk)
        assert sorted(added) == ["a.py", "b.py"]
        assert modified == []
        assert removed == []

    def test_empty_disk_all_removed(self):
        tracked = {"a.py": 1.0, "b.py": 2.0}
        disk = {}
        added, modified, removed = detect_changes(tracked, disk)
        assert added == []
        assert modified == []
        assert sorted(removed) == ["a.py", "b.py"]

    def test_results_are_sorted(self):
        tracked = {"z.py": 1.0}
        disk = {"a.py": 1.0, "m.py": 1.0, "b.py": 1.0}
        added, _, removed = detect_changes(tracked, disk)
        assert added == sorted(added)
        assert removed == sorted(removed)


class TestScanDiskMtimes:
    def test_existing_file_returns_mtime(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x=1")
        result = scan_disk_mtimes(["a.py"], tmp_path)
        assert "a.py" in result
        assert result["a.py"] > 0

    def test_missing_file_omitted(self, tmp_path):
        result = scan_disk_mtimes(["nonexistent.py"], tmp_path)
        assert result == {}

    def test_mixed_existing_and_missing(self, tmp_path):
        f = tmp_path / "exists.py"
        f.write_text("pass")
        result = scan_disk_mtimes(["exists.py", "gone.py"], tmp_path)
        assert "exists.py" in result
        assert "gone.py" not in result

    def test_empty_paths_returns_empty(self, tmp_path):
        result = scan_disk_mtimes([], tmp_path)
        assert result == {}


class TestDebounceAccumulator:
    def test_initially_no_pending(self):
        acc = DebounceAccumulator(window=1.0)
        assert not acc.has_pending()

    def test_add_sets_pending(self):
        acc = DebounceAccumulator(window=1.0)
        acc.add(["src/foo.py"])
        assert acc.has_pending()

    def test_add_empty_list_no_pending(self):
        acc = DebounceAccumulator(window=1.0)
        acc.add([])
        assert not acc.has_pending()

    def test_flush_returns_sorted_paths(self):
        acc = DebounceAccumulator(window=1.0)
        acc.add(["z.py", "a.py"])
        result = acc.flush()
        assert result == ["a.py", "z.py"]

    def test_flush_clears_pending(self):
        acc = DebounceAccumulator(window=1.0)
        acc.add(["a.py"])
        acc.flush()
        assert not acc.has_pending()

    def test_multiple_adds_accumulate(self):
        acc = DebounceAccumulator(window=1.0)
        acc.add(["a.py"])
        acc.add(["b.py"])
        result = acc.flush()
        assert result == ["a.py", "b.py"]

    def test_duplicate_paths_deduplicated(self):
        acc = DebounceAccumulator(window=1.0)
        acc.add(["a.py"])
        acc.add(["a.py"])
        result = acc.flush()
        assert result == ["a.py"]

    def test_should_fire_false_before_window(self):
        acc = DebounceAccumulator(window=5.0)
        now = time.monotonic()
        acc.add(["a.py"])
        acc._last_change = now
        assert not acc.should_fire(now + 0.5)

    def test_should_fire_true_after_window(self):
        acc = DebounceAccumulator(window=1.0)
        now = time.monotonic()
        acc.add(["a.py"])
        acc._last_change = now - 2.0
        assert acc.should_fire(now)

    def test_should_fire_false_when_no_pending(self):
        acc = DebounceAccumulator(window=0.0)
        assert not acc.should_fire(time.monotonic() + 999)

    def test_flush_after_fire_resets_timer(self):
        acc = DebounceAccumulator(window=1.0)
        acc.add(["a.py"])
        acc.flush()
        now = time.monotonic()
        assert not acc.should_fire(now + 999)

    def test_window_zero_fires_immediately(self):
        acc = DebounceAccumulator(window=0.0)
        acc.add(["a.py"])
        acc._last_change = time.monotonic() - 0.001
        assert acc.should_fire(time.monotonic())


class TestWebhookBridge:

    def _post(self, url: str, body: dict | None = None, headers: dict | None = None):
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=2)

    def test_health_endpoint(self):
        bridge = WebhookBridge(host="127.0.0.1", port=0)
        bridge.start()
        try:
            url = f"http://127.0.0.1:{bridge.port}/health"
            with urllib.request.urlopen(url, timeout=2) as resp:
                assert resp.status == 200
                payload = json.loads(resp.read().decode("utf-8"))
            assert payload["ok"] is True
            assert payload["path"] == "/roam/reindex"
        finally:
            bridge.stop()

    def test_reindex_post_enqueues_event(self):
        bridge = WebhookBridge(host="127.0.0.1", port=0, path="/hook")
        bridge.start()
        try:
            url = f"http://127.0.0.1:{bridge.port}/hook"
            with self._post(url, body={"event": "push", "force": True}) as resp:
                assert resp.status == 202
            events = bridge.drain_events()
            assert len(events) == 1
            assert events[0]["event"] == "push"
            assert events[0]["force"] is True
        finally:
            bridge.stop()

    def test_secret_required(self):
        bridge = WebhookBridge(host="127.0.0.1", port=0, secret="s3cr3t")
        bridge.start()
        try:
            url = f"http://127.0.0.1:{bridge.port}/roam/reindex"
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                self._post(url, body={"event": "push"})
            assert excinfo.value.code == 401

            with self._post(url, body={"event": "push"}, headers={"X-Roam-Secret": "s3cr3t"}) as resp:
                assert resp.status == 202
            events = bridge.drain_events()
            assert len(events) == 1
            assert events[0]["event"] == "push"
        finally:
            bridge.stop()




class TestWatchCommand:

    def test_help_text(self):
        from click.testing import CliRunner
        from roam.commands.cmd_watch import watch
        runner = CliRunner()
        result = runner.invoke(watch, ["--help"])
        assert result.exit_code == 0
        assert "interval" in result.output
        assert "debounce" in result.output
        assert "quiet" in result.output
        assert "webhook-port" in result.output
        assert "webhook-host" in result.output
        assert "webhook-path" in result.output
        assert "webhook-secret" in result.output
        assert "webhook-only" in result.output
        assert "webhook-force" in result.output
        assert "guardian" in result.output
        assert "guardian-report" in result.output
        assert "guardian-health-gate" in result.output
        assert "guardian-drift-threshold" in result.output

    def _invoke_watch_with_flags(self, flags):
        from click.testing import CliRunner
        from roam.commands.cmd_watch import watch
        runner = CliRunner()
        mock_loop = MagicMock(side_effect=KeyboardInterrupt)
        with patch("roam.commands.cmd_watch.ensure_index"):
            with patch("roam.commands.cmd_watch.find_project_root", return_value=Path("/tmp")):
                with patch("roam.commands.cmd_watch.poll_loop", mock_loop):
                    with patch("roam.commands.cmd_watch.WebhookBridge") as bridge_cls:
                        bridge = MagicMock()
                        bridge.port = 12345
                        bridge_cls.return_value = bridge
                        runner.invoke(watch, flags, catch_exceptions=False, obj={})
        return mock_loop

    def test_interval_flag_parsing(self):
        mock = self._invoke_watch_with_flags(["--interval", "5"])
        _, kwargs = mock.call_args
        assert kwargs["interval"] == 5.0

    def test_debounce_flag_parsing(self):
        mock = self._invoke_watch_with_flags(["--debounce", "3"])
        _, kwargs = mock.call_args
        assert kwargs["debounce"] == 3.0

    def test_quiet_flag_parsing(self):
        mock = self._invoke_watch_with_flags(["--quiet"])
        _, kwargs = mock.call_args
        assert kwargs["quiet"] is True

    def test_default_flags(self):
        mock = self._invoke_watch_with_flags([])
        _, kwargs = mock.call_args
        assert kwargs["interval"] == 2.0
        assert kwargs["debounce"] == 1.0
        assert kwargs["quiet"] is False
        assert kwargs["webhook_only"] is False
        assert kwargs["webhook_force"] is False
        assert kwargs["guardian"] is False
        assert kwargs["guardian_report"] == ""
        assert kwargs["guardian_health_gate"] == 70.0
        assert kwargs["guardian_drift_threshold"] == 0.5

    def test_short_i_flag(self):
        mock = self._invoke_watch_with_flags(["-i", "10"])
        _, kwargs = mock.call_args
        assert kwargs["interval"] == 10.0

    def test_short_d_flag(self):
        mock = self._invoke_watch_with_flags(["-d", "0.5"])
        _, kwargs = mock.call_args
        assert kwargs["debounce"] == 0.5

    def test_short_q_flag(self):
        mock = self._invoke_watch_with_flags(["-q"])
        _, kwargs = mock.call_args
        assert kwargs["quiet"] is True

    def test_webhook_flags_parsing(self):
        mock = self._invoke_watch_with_flags([
            "--webhook-port", "9000",
            "--webhook-host", "0.0.0.0",
            "--webhook-path", "/hook",
            "--webhook-secret", "token",
            "--webhook-only",
            "--webhook-force",
        ])
        _, kwargs = mock.call_args
        assert kwargs["webhook_only"] is True
        assert kwargs["webhook_force"] is True

    def test_guardian_flags_parsing(self):
        mock = self._invoke_watch_with_flags([
            "--guardian",
            "--guardian-report", ".roam/guardian.jsonl",
            "--guardian-health-gate", "75",
            "--guardian-drift-threshold", "0.4",
        ])
        _, kwargs = mock.call_args
        assert kwargs["guardian"] is True
        assert kwargs["guardian_report"] == ".roam/guardian.jsonl"
        assert kwargs["guardian_health_gate"] == 75.0
        assert kwargs["guardian_drift_threshold"] == 0.4

    def test_webhook_bridge_constructed_when_port_set(self):
        from click.testing import CliRunner
        from roam.commands.cmd_watch import watch

        runner = CliRunner()
        with patch("roam.commands.cmd_watch.ensure_index"), \
                patch("roam.commands.cmd_watch.find_project_root", return_value=Path("/tmp")), \
                patch("roam.commands.cmd_watch.poll_loop", side_effect=KeyboardInterrupt), \
                patch("roam.commands.cmd_watch.WebhookBridge") as bridge_cls:
            bridge = MagicMock()
            bridge.port = 7777
            bridge_cls.return_value = bridge
            runner.invoke(
                watch,
                [
                    "--webhook-port", "7777",
                    "--webhook-host", "0.0.0.0",
                    "--webhook-path", "/hook",
                    "--webhook-secret", "token",
                ],
                obj={},
            )
            bridge_cls.assert_called_once_with(
                host="0.0.0.0",
                port=7777,
                path="/hook",
                secret="token",
            )

    def test_keyboard_interrupt_shows_stopped(self):
        from click.testing import CliRunner
        from roam.commands.cmd_watch import watch
        runner = CliRunner()
        with patch("roam.commands.cmd_watch.ensure_index"):
            with patch("roam.commands.cmd_watch.find_project_root", return_value=Path("/tmp")):
                with patch("roam.commands.cmd_watch.poll_loop", side_effect=KeyboardInterrupt):
                    result = runner.invoke(watch, [], obj={})
        assert "Watch stopped" in result.output


class TestLoadTrackedFiles:

    def test_returns_empty_when_no_db(self, tmp_path):
        with patch("roam.commands.cmd_watch.db_exists", return_value=False):
            result = load_tracked_files(tmp_path)
        assert result == {}

    def test_returns_empty_on_exception(self, tmp_path):
        with patch("roam.commands.cmd_watch.db_exists", return_value=True):
            with patch("roam.commands.cmd_watch.open_db", side_effect=Exception("db error")):
                result = load_tracked_files(tmp_path)
        assert result == {}


class TestPollLoopWebhook:

    def test_webhook_event_triggers_reindex(self, tmp_path):
        calls: list[bool] = []
        first = True

        def _sleep(_seconds):
            nonlocal first
            if first:
                first = False
                return None
            raise KeyboardInterrupt

        def _external_events():
            if first:
                return []
            return [{"event": "push", "force": False}]

        def _reindex(force=False):
            calls.append(bool(force))

        with pytest.raises(KeyboardInterrupt):
            poll_loop(
                project_root=tmp_path,
                interval=0.01,
                debounce=0.0,
                quiet=True,
                webhook_only=True,
                _sleep=_sleep,
                _external_events=_external_events,
                _reindex=_reindex,
            )
        assert calls == [False]

    def test_webhook_event_reindexes_force_when_flag_enabled(self, tmp_path):
        calls: list[bool] = []
        first = True

        def _sleep(_seconds):
            nonlocal first
            if first:
                first = False
                return None
            raise KeyboardInterrupt

        def _external_events():
            if first:
                return []
            return [{"event": "push", "force": False}]

        def _reindex(force=False):
            calls.append(bool(force))

        with pytest.raises(KeyboardInterrupt):
            poll_loop(
                project_root=tmp_path,
                interval=0.0,
                debounce=0.0,
                quiet=True,
                webhook_only=True,
                webhook_force=True,
                _sleep=_sleep,
                _external_events=_external_events,
                _reindex=_reindex,
            )
        assert calls == [True]

    def test_guardian_collection_and_report_write(self, tmp_path):
        reindex_calls: list[bool] = []
        guardian_snapshots: list[dict] = []
        writes: list[dict] = []
        first = True

        def _sleep(_seconds):
            nonlocal first
            if first:
                first = False
                return None
            raise KeyboardInterrupt

        def _external_events():
            if first:
                return []
            return [{"event": "push", "force": False}]

        def _reindex(force=False):
            reindex_calls.append(bool(force))

        def _guardian_collect():
            payload = {
                "current": {"health_score": 88},
                "trend": {"verdict": "stable"},
                "drift": {"drift_files": 1},
                "gates": {"health_gate_pass": True},
            }
            guardian_snapshots.append(payload)
            return payload

        def _guardian_write(payload):
            writes.append(payload)

        with pytest.raises(KeyboardInterrupt):
            poll_loop(
                project_root=tmp_path,
                interval=0.0,
                debounce=0.0,
                quiet=True,
                webhook_only=True,
                guardian=True,
                guardian_report=".roam/guardian.jsonl",
                _sleep=_sleep,
                _external_events=_external_events,
                _reindex=_reindex,
                _guardian_collect=_guardian_collect,
                _guardian_write=_guardian_write,
            )

        assert reindex_calls == [False]
        assert len(guardian_snapshots) == 1
        assert len(writes) == 1


class TestWatchRegistration:

    def test_watch_registered_in_cli(self):
        from roam.cli import _COMMANDS
        assert "watch" in _COMMANDS

    def test_watch_in_category(self):
        from roam.cli import _CATEGORIES
        assert any("watch" in v for v in _CATEGORIES.values())

    def test_watch_module_importable(self):
        from roam.commands.cmd_watch import watch
        assert callable(watch)

    def test_poll_loop_importable(self):
        from roam.commands.cmd_watch import poll_loop
        assert callable(poll_loop)

    def test_debounce_accumulator_importable(self):
        from roam.commands.cmd_watch import DebounceAccumulator
        assert issubclass(DebounceAccumulator, object)

    def test_detect_changes_importable(self):
        from roam.commands.cmd_watch import detect_changes
        assert callable(detect_changes)

    def test_scan_disk_mtimes_importable(self):
        from roam.commands.cmd_watch import scan_disk_mtimes
        assert callable(scan_disk_mtimes)

