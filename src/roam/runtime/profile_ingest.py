"""Deterministic profiler-trace parsing and source-span mapping."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProfileFrame:
    """One frame from the speedscope shared-frame table."""

    name: str
    file: str | None
    line: int | None


@dataclass(frozen=True)
class ProfileSample:
    """One weighted sampled stack."""

    frame_indexes: tuple[int, ...]
    weight: float


@dataclass(frozen=True)
class SpeedscopeProfile:
    """Normalized sampled speedscope data."""

    frames: tuple[ProfileFrame, ...]
    samples: tuple[ProfileSample, ...]
    total_weight: float
    unit: str
    profile_count: int


def parse_speedscope(path: str | Path) -> SpeedscopeProfile:
    """Parse sampled py-spy/speedscope JSON into a deterministic model."""
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("speedscope document must be a JSON object")

    shared = data.get("shared")
    raw_frames = shared.get("frames") if isinstance(shared, dict) else None
    if not isinstance(raw_frames, list):
        raise ValueError("speedscope document is missing shared.frames")

    frames: list[ProfileFrame] = []
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise ValueError(f"speedscope frame {index} is missing a string name")
        file_path = raw.get("file")
        if file_path is not None and not isinstance(file_path, str):
            raise ValueError(f"speedscope frame {index} has a non-string file")
        line = raw.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise ValueError(f"speedscope frame {index} has an invalid line")
        frames.append(ProfileFrame(name=raw["name"], file=file_path, line=line))

    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("speedscope document contains no profiles")

    samples: list[ProfileSample] = []
    units: set[str] = set()
    for profile_index, raw_profile in enumerate(raw_profiles):
        if not isinstance(raw_profile, dict) or raw_profile.get("type") != "sampled":
            raise ValueError("only sampled speedscope profiles are supported")
        raw_samples = raw_profile.get("samples")
        if not isinstance(raw_samples, list):
            raise ValueError(f"speedscope profile {profile_index} is missing samples")
        raw_weights = raw_profile.get("weights")
        if raw_weights is None:
            raw_weights = [1.0] * len(raw_samples)
        if not isinstance(raw_weights, list) or len(raw_weights) != len(raw_samples):
            raise ValueError(f"speedscope profile {profile_index} weights do not match samples")

        unit = raw_profile.get("unit", "samples")
        if not isinstance(unit, str):
            raise ValueError(f"speedscope profile {profile_index} has an invalid unit")
        units.add(unit)

        for sample_index, (stack, weight) in enumerate(zip(raw_samples, raw_weights, strict=True)):
            if not isinstance(stack, list) or any(
                not isinstance(frame_index, int)
                or isinstance(frame_index, bool)
                or frame_index < 0
                or frame_index >= len(frames)
                for frame_index in stack
            ):
                raise ValueError(f"speedscope profile {profile_index} sample {sample_index} has invalid frame indexes")
            if (
                not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or not math.isfinite(float(weight))
                or weight < 0
            ):
                raise ValueError(f"speedscope profile {profile_index} sample {sample_index} has invalid weight")
            samples.append(ProfileSample(tuple(stack), float(weight)))

    if len(units) != 1:
        raise ValueError("speedscope profiles use incompatible units")
    total_weight = sum(sample.weight for sample in samples)
    if total_weight <= 0:
        raise ValueError("speedscope profiles contain no positive sample weight")
    return SpeedscopeProfile(
        frames=tuple(frames),
        samples=tuple(samples),
        total_weight=total_weight,
        unit=next(iter(units)),
        profile_count=len(raw_profiles),
    )


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _load_index_spans(conn: sqlite3.Connection) -> tuple[dict[str, list[dict]], dict[int, list[dict]]]:
    files_by_basename: dict[str, list[dict]] = {}
    for row in conn.execute("SELECT id, path FROM files ORDER BY path"):
        normalized = _normalize_path(row["path"])
        files_by_basename.setdefault(normalized.rsplit("/", 1)[-1], []).append(
            {"id": row["id"], "path": row["path"], "normalized": normalized}
        )

    spans_by_file: dict[int, list[dict]] = {}
    rows = conn.execute(
        "SELECT id, file_id, name, qualified_name, kind, line_start, line_end "
        "FROM symbols WHERE line_start IS NOT NULL AND line_end IS NOT NULL "
        "ORDER BY file_id, (line_end - line_start) ASC, line_start DESC, id ASC"
    )
    for row in rows:
        spans_by_file.setdefault(row["file_id"], []).append(dict(row))
    return files_by_basename, spans_by_file


def _map_frame(
    frame: ProfileFrame,
    files_by_basename: dict[str, list[dict]],
    spans_by_file: dict[int, list[dict]],
) -> tuple[dict | None, str | None]:
    if not frame.file:
        return None, "missing_file"
    if frame.line is None:
        return None, "missing_line"

    trace_path = _normalize_path(frame.file)
    basename = trace_path.rsplit("/", 1)[-1]
    candidates = [
        row
        for row in files_by_basename.get(basename, [])
        if trace_path == row["normalized"]
        or trace_path.endswith(f"/{row['normalized']}")
        or row["normalized"].endswith(f"/{trace_path}")
    ]
    if not candidates:
        return None, "file_not_indexed"
    if len(candidates) > 1:
        return None, "ambiguous_file"

    file_id, indexed_path = candidates[0]["id"], candidates[0]["path"]
    spans = [span for span in spans_by_file.get(file_id, []) if span["line_start"] <= frame.line <= span["line_end"]]
    if not spans:
        return None, "no_indexed_span"

    span = spans[0]
    return (
        {
            "symbol_id": span["id"],
            "symbol_name": span["name"],
            "qualified_name": span["qualified_name"],
            "kind": span["kind"],
            "file": indexed_path,
            "line_start": span["line_start"],
            "line_end": span["line_end"],
        },
        None,
    )


def rank_hot_spans(conn: sqlite3.Connection, profile: SpeedscopeProfile) -> dict:
    """Map frames to indexed spans and rank spans by cumulative sample share."""
    files_by_basename, spans_by_file = _load_index_spans(conn)
    mappings = [_map_frame(frame, files_by_basename, spans_by_file) for frame in profile.frames]
    span_totals: dict[int, dict] = {}
    unmapped_totals: dict[tuple[str, str | None, int | None, str], dict] = {}

    for sample in profile.samples:
        sample_span_ids: set[int] = set()
        sample_unmapped: set[tuple[str, str | None, int | None, str]] = set()
        for frame_index in set(sample.frame_indexes):
            frame = profile.frames[frame_index]
            span, reason = mappings[frame_index]
            if span is not None:
                symbol_id = span["symbol_id"]
                sample_span_ids.add(symbol_id)
                entry = span_totals.setdefault(symbol_id, {**span, "cumulative_weight": 0.0, "profiler_frames": set()})
                entry["profiler_frames"].add((frame.name, frame.file, frame.line))
            else:
                key = (frame.name, frame.file, frame.line, reason or "unmapped")
                sample_unmapped.add(key)
                unmapped_totals.setdefault(
                    key,
                    {
                        "name": frame.name,
                        "file": frame.file,
                        "line": frame.line,
                        "reason": reason or "unmapped",
                        "cumulative_weight": 0.0,
                    },
                )

        for symbol_id in sample_span_ids:
            span_totals[symbol_id]["cumulative_weight"] += sample.weight
        for key in sample_unmapped:
            unmapped_totals[key]["cumulative_weight"] += sample.weight

    def finalize(entry: dict, *, mapped: bool) -> dict:
        result = dict(entry)
        frames = result.pop("profiler_frames", None)
        result["cumulative_weight"] = round(result["cumulative_weight"], 6)
        result["runtime_share_pct"] = round(result["cumulative_weight"] / profile.total_weight * 100, 2)
        if mapped:
            result["profiler_frame_count"] = len(frames or ())
        return result

    spans = [finalize(entry, mapped=True) for entry in span_totals.values()]
    spans.sort(key=lambda item: (-item["cumulative_weight"], item["file"], item["line_start"], item["symbol_name"]))
    unmapped = [finalize(entry, mapped=False) for entry in unmapped_totals.values()]
    unmapped.sort(
        key=lambda item: (
            -item["cumulative_weight"],
            item["file"] or "",
            item["line"] or 0,
            item["name"],
        )
    )
    return {"spans": spans, "unmapped_frames": unmapped}
