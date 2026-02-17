"""Tests for anomaly detection module and trend command extensions.

Covers:
- modified_z_score: point anomaly detection with MAD-based Z-scores
- theil_sen_slope: robust trend estimation
- mann_kendall_test: non-parametric trend significance
- western_electric_rules: control chart pattern detection
- cusum: cumulative sum change detection
- forecast: linear projection to target
- trend command --anomalies, --forecast, --analyze, --fail-on-anomaly, --sensitivity
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit, index_in_process

from roam.graph.anomaly import (
    modified_z_score,
    theil_sen_slope,
    mann_kendall_test,
    western_electric_rules,
    cusum,
    forecast,
)


# ============================================================================
# modified_z_score
# ============================================================================

class TestModifiedZScore:
    """Tests for modified_z_score point anomaly detection."""

    def test_known_anomaly_detected(self):
        """A clear outlier like 100 in [1,2,3,4,5,100] should be flagged."""
        values = [1, 2, 3, 4, 5, 100]
        results = modified_z_score(values)
        assert len(results) == len(values)
        # The last value (100) should be flagged as anomaly
        anomalies = [r for r in results if r["is_anomaly"]]
        assert len(anomalies) >= 1, "Expected at least one anomaly"
        anomaly_indices = [r["index"] for r in anomalies]
        assert 5 in anomaly_indices, "Index 5 (value=100) should be anomalous"

    def test_no_anomalies_in_uniform_data(self):
        """A smoothly varying series should have no anomalies."""
        values = [10, 11, 12, 13, 14]
        results = modified_z_score(values)
        anomalies = [r for r in results if r["is_anomaly"]]
        assert len(anomalies) == 0, f"Expected no anomalies, got {anomalies}"

    def test_threshold_sensitivity_low(self):
        """A lower threshold should detect more anomalies."""
        values = [1, 2, 3, 4, 5, 20]
        results_strict = modified_z_score(values, threshold=5.0)
        results_loose = modified_z_score(values, threshold=1.0)
        strict_count = sum(1 for r in results_strict if r["is_anomaly"])
        loose_count = sum(1 for r in results_loose if r["is_anomaly"])
        assert loose_count >= strict_count, (
            f"Lower threshold should detect >= anomalies: loose={loose_count}, strict={strict_count}"
        )

    def test_all_equal_values_mad_zero(self):
        """When all values are identical, MAD=0 and no anomalies should be flagged."""
        values = [5, 5, 5, 5, 5]
        results = modified_z_score(values)
        assert len(results) == 5
        # All z-scores should be 0 and no anomalies
        for r in results:
            assert r["z_score"] == 0.0
            assert r["is_anomaly"] is False

    def test_mostly_equal_with_outlier_mad_zero(self):
        """When MAD=0 but one value differs, that value gets inf z-score."""
        values = [5, 5, 5, 5, 5, 99]
        results = modified_z_score(values)
        # The outlier at index 5 should have inf z-score and be flagged
        outlier = results[5]
        assert outlier["is_anomaly"] is True

    def test_insufficient_data_returns_empty(self):
        """Less than 5 data points should return an empty list."""
        assert modified_z_score([1, 2, 3, 4]) == []
        assert modified_z_score([1, 2, 3]) == []
        assert modified_z_score([1]) == []
        assert modified_z_score([]) == []

    def test_result_structure(self):
        """Each result should have index, value, z_score, is_anomaly keys."""
        values = [1, 2, 3, 4, 5]
        results = modified_z_score(values)
        for r in results:
            assert "index" in r
            assert "value" in r
            assert "z_score" in r
            assert "is_anomaly" in r

    def test_negative_outlier_detected(self):
        """A very negative outlier should also be flagged."""
        values = [50, 51, 52, 53, 54, -100]
        results = modified_z_score(values)
        anomalies = [r for r in results if r["is_anomaly"]]
        anomaly_indices = [r["index"] for r in anomalies]
        assert 5 in anomaly_indices, "Negative outlier should be detected"


# ============================================================================
# theil_sen_slope
# ============================================================================

class TestTheilSenSlope:
    """Tests for Theil-Sen robust trend estimator."""

    def test_ascending_slope(self):
        """[1,2,3,4,5] should give slope=1.0 and direction=increasing."""
        result = theil_sen_slope([1, 2, 3, 4, 5])
        assert result is not None
        assert result["slope"] == pytest.approx(1.0, abs=0.01)
        assert result["direction"] == "increasing"

    def test_descending_slope(self):
        """[5,4,3,2,1] should give slope=-1.0 and direction=decreasing."""
        result = theil_sen_slope([5, 4, 3, 2, 1])
        assert result is not None
        assert result["slope"] == pytest.approx(-1.0, abs=0.01)
        assert result["direction"] == "decreasing"

    def test_flat_values(self):
        """Constant values should give slope~0 and direction=stable."""
        result = theil_sen_slope([7, 7, 7, 7, 7])
        assert result is not None
        assert abs(result["slope"]) <= 0.01
        assert result["direction"] == "stable"

    def test_insufficient_data(self):
        """Less than 4 data points should return None."""
        assert theil_sen_slope([1, 2, 3]) is None
        assert theil_sen_slope([1, 2]) is None
        assert theil_sen_slope([1]) is None
        assert theil_sen_slope([]) is None

    def test_result_keys(self):
        """Result should contain slope, intercept, direction."""
        result = theil_sen_slope([1, 2, 3, 4])
        assert result is not None
        assert "slope" in result
        assert "intercept" in result
        assert "direction" in result

    def test_intercept_value(self):
        """For [1,2,3,4,5], intercept should be ~1.0 (y=x+1 at index 0)."""
        result = theil_sen_slope([1, 2, 3, 4, 5])
        assert result is not None
        assert result["intercept"] == pytest.approx(1.0, abs=0.1)

    def test_robust_to_single_outlier(self):
        """Theil-Sen should still detect an increasing trend despite one outlier."""
        values = [1, 2, 3, 100, 5, 6, 7]
        result = theil_sen_slope(values)
        assert result is not None
        # Median of slopes should still be near 1.0
        assert result["direction"] == "increasing"
        assert result["slope"] == pytest.approx(1.0, abs=0.5)


# ============================================================================
# mann_kendall_test
# ============================================================================

class TestMannKendallTest:
    """Tests for Mann-Kendall trend significance test."""

    def test_strong_ascending_significant(self):
        """A monotonically increasing series should be significant."""
        values = [1, 2, 3, 4, 5, 6, 7, 8]
        result = mann_kendall_test(values)
        assert result is not None
        assert result["significant"] is True
        assert result["direction"] == "increasing"
        assert result["p_value"] < 0.05

    def test_strong_descending_significant(self):
        """A monotonically decreasing series should be significant and decreasing."""
        values = [8, 7, 6, 5, 4, 3, 2, 1]
        result = mann_kendall_test(values)
        assert result is not None
        assert result["significant"] is True
        assert result["direction"] == "decreasing"

    def test_flat_not_significant(self):
        """Constant values should not be significant."""
        values = [5, 5, 5, 5, 5, 5, 5, 5]
        result = mann_kendall_test(values)
        assert result is not None
        assert result["significant"] is False
        assert result["direction"] == "stable"

    def test_insufficient_data(self):
        """Less than 8 data points should return None."""
        assert mann_kendall_test([1, 2, 3, 4, 5, 6, 7]) is None
        assert mann_kendall_test([1, 2, 3]) is None
        assert mann_kendall_test([]) is None

    def test_result_keys(self):
        """Result should have S, z_score, p_value, significant, direction."""
        result = mann_kendall_test([1, 2, 3, 4, 5, 6, 7, 8])
        assert result is not None
        assert "S" in result
        assert "z_score" in result
        assert "p_value" in result
        assert "significant" in result
        assert "direction" in result

    def test_s_stat_positive_for_increasing(self):
        """S statistic should be positive for increasing series."""
        result = mann_kendall_test([1, 2, 3, 4, 5, 6, 7, 8])
        assert result is not None
        assert result["S"] > 0

    def test_s_stat_negative_for_decreasing(self):
        """S statistic should be negative for decreasing series."""
        result = mann_kendall_test([8, 7, 6, 5, 4, 3, 2, 1])
        assert result is not None
        assert result["S"] < 0


# ============================================================================
# western_electric_rules
# ============================================================================

class TestWesternElectricRules:
    """Tests for Western Electric control chart rules."""

    def test_rule1_single_point_beyond_3_sigma(self):
        """A single extreme point beyond 3 sigma should trigger Rule 1."""
        # Start with values centered around 10, add a massive outlier
        values = [10, 10, 11, 10, 9, 10, 10, 100]
        results = western_electric_rules(values)
        rule1_violations = [v for v in results if v["rule"] == 1]
        assert len(rule1_violations) >= 1, (
            f"Expected Rule 1 violation for extreme point, got rules: "
            f"{[v['rule'] for v in results]}"
        )

    def test_rule4_eight_consecutive_same_side(self):
        """8+ consecutive points on the same side of center should trigger Rule 4."""
        # 6 low values + 9 high values; median lands among the highs,
        # so the first 6 form a run below center.  Alternatively we need
        # values clearly on one side.  Use a design where the median is
        # in the middle and 8+ consecutive values lie above it.
        # Median of this list is 10. The last 9 values (11-19) are above.
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19]
        results = western_electric_rules(values)
        rule4_violations = [v for v in results if v["rule"] == 4]
        assert len(rule4_violations) >= 1, (
            f"Expected Rule 4 violation for 8+ same-side run, got rules: "
            f"{[v['rule'] for v in results]}"
        )

    def test_rule5_six_consecutive_increasing(self):
        """6+ consecutive increasing points should trigger Rule 5."""
        values = [1, 2, 3, 4, 5, 6, 7]
        results = western_electric_rules(values)
        rule5_violations = [v for v in results if v["rule"] == 5]
        assert len(rule5_violations) >= 1, (
            f"Expected Rule 5 violation for 6+ consecutive increases, got rules: "
            f"{[v['rule'] for v in results]}"
        )

    def test_no_violations_for_stable_data(self):
        """Noisy data centered around the mean should have few/no violations."""
        values = [10, 10, 10, 10, 10]
        results = western_electric_rules(values)
        # All identical values => MAD=0 and sigma=0, so empty
        assert len(results) == 0

    def test_insufficient_data(self):
        """Less than 3 data points should return an empty list."""
        assert western_electric_rules([1, 2]) == []
        assert western_electric_rules([1]) == []
        assert western_electric_rules([]) == []

    def test_result_structure(self):
        """Each violation should have rule, description, indices keys."""
        values = [1, 2, 3, 4, 5, 6, 7]
        results = western_electric_rules(values)
        for v in results:
            assert "rule" in v
            assert "description" in v
            assert "indices" in v


# ============================================================================
# cusum
# ============================================================================

class TestCusum:
    """Tests for CUSUM change detection."""

    def test_step_change_detected(self):
        """A clear step change should be detected by CUSUM."""
        # Need a large enough shift relative to MAD-based sigma.
        # Use many baseline points + large shift to accumulate cusum.
        values = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50]
        signals = cusum(values)
        assert len(signals) >= 1, "Expected CUSUM to detect step change"
        # Should detect an upward shift
        up_signals = [s for s in signals if s["direction"] == "up"]
        assert len(up_signals) >= 1, "Expected upward shift detection"

    def test_gradual_drift_detected(self):
        """A gradual drift away from baseline should eventually be detected."""
        # Many stable baseline values, then progressive shift upward.
        # The median stays near 0, so the drifting values accumulate cusum.
        values = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10, 20, 30, 40, 50, 60, 70, 80]
        signals = cusum(values)
        assert len(signals) >= 1, "Expected CUSUM to detect gradual drift"

    def test_stable_no_signals(self):
        """Constant values should produce no signals."""
        values = [5, 5, 5, 5, 5, 5, 5, 5]
        signals = cusum(values)
        assert len(signals) == 0, f"Expected no signals for constant data, got {signals}"

    def test_insufficient_data(self):
        """Less than 5 data points should return an empty list."""
        assert cusum([1, 2, 3, 4]) == []
        assert cusum([1, 2]) == []
        assert cusum([]) == []

    def test_result_structure(self):
        """Each signal should have index, direction, cusum_value keys."""
        values = [0, 0, 0, 0, 10, 10, 10, 10]
        signals = cusum(values)
        for s in signals:
            assert "index" in s
            assert "direction" in s
            assert "cusum_value" in s

    def test_downward_step_change(self):
        """A downward step change should be detected with direction=down."""
        values = [50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 0, 0, 0, 0, 0]
        signals = cusum(values)
        assert len(signals) >= 1, "Expected CUSUM to detect downward step"
        down_signals = [s for s in signals if s["direction"] == "down"]
        assert len(down_signals) >= 1, "Expected downward shift detection"

    def test_custom_threshold(self):
        """A lower threshold should detect more or equal signals than a higher threshold."""
        values = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50]
        signals_loose = cusum(values, threshold=2.0)
        signals_strict = cusum(values, threshold=10.0)
        assert len(signals_loose) >= len(signals_strict), (
            "Lower threshold should detect >= signals than higher threshold"
        )


# ============================================================================
# forecast
# ============================================================================

class TestForecast:
    """Tests for trend forecasting."""

    def test_linear_growth_correct_steps(self):
        """Linear growth [1,2,3,4,5] heading to target=10 should take ~5 steps."""
        result = forecast([1, 2, 3, 4, 5], target=10)
        assert result is not None
        assert result["current"] == 5
        assert result["target"] == 10
        assert result["slope"] == pytest.approx(1.0, abs=0.01)
        assert result["steps_until"] == 5
        assert result["direction"] == "increasing"

    def test_no_trend_stable(self):
        """Flat data heading to a target should give steps_until=None."""
        result = forecast([5, 5, 5, 5, 5], target=10)
        assert result is not None
        assert result["direction"] == "stable"
        assert result["steps_until"] is None

    def test_wrong_direction_returns_none_steps(self):
        """If slope moves away from target, steps_until should be None."""
        # Decreasing series targeting a higher value
        result = forecast([5, 4, 3, 2, 1], target=10)
        assert result is not None
        assert result["steps_until"] is None

    def test_already_at_target(self):
        """If current value equals target, steps_until should be 0."""
        result = forecast([1, 2, 3, 4, 5], target=5)
        assert result is not None
        assert result["steps_until"] == 0

    def test_insufficient_data(self):
        """Less than 4 data points should return None."""
        assert forecast([1, 2, 3], target=10) is None
        assert forecast([1], target=10) is None
        assert forecast([], target=10) is None

    def test_result_keys(self):
        """Result should have current, target, slope, steps_until, direction."""
        result = forecast([1, 2, 3, 4, 5], target=10)
        assert result is not None
        assert "current" in result
        assert "target" in result
        assert "slope" in result
        assert "steps_until" in result
        assert "direction" in result

    def test_decreasing_toward_lower_target(self):
        """Decreasing series heading to a lower target should compute steps."""
        result = forecast([10, 9, 8, 7, 6], target=1)
        assert result is not None
        assert result["direction"] == "decreasing"
        assert result["steps_until"] is not None
        assert result["steps_until"] == 5


# ============================================================================
# Integration tests: trend command with anomaly flags
# ============================================================================

class TestTrendCommandAnomalies:
    """Integration tests for trend command anomaly detection flags.

    These tests require an indexed project with multiple snapshots.
    """

    @pytest.fixture(scope="class")
    def trend_project(self, tmp_path_factory):
        """Create a project with multiple snapshots for trend analysis."""
        proj = tmp_path_factory.mktemp("trend_anomaly")

        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "app.py").write_text(
            'def main():\n'
            '    """Main entry point."""\n'
            '    return 0\n'
        )
        git_init(proj)

        # Index and create initial snapshot
        out, rc = index_in_process(proj)
        assert rc == 0, f"roam index failed:\n{out}"

        # Create additional snapshots with file growth
        for i in range(2, 7):
            (proj / f"module_{i}.py").write_text(
                f'def func_{i}():\n'
                f'    """Function {i}."""\n'
                f'    return {i}\n'
                f'\n'
                f'def helper_{i}():\n'
                f'    """Helper {i}."""\n'
                f'    return func_{i}() + 1\n'
            )
            git_commit(proj, f"add module_{i}")
            out, rc = index_in_process(proj)
            assert rc == 0, f"roam index (snapshot {i}) failed:\n{out}"
            # Also try to create an explicit snapshot
            roam("snapshot", "--tag", f"v{i}", cwd=proj)

        return proj

    def test_trend_analyze_produces_output(self, trend_project):
        """roam trend --analyze should produce analysis output."""
        out, rc = roam("trend", "--analyze", cwd=trend_project)
        assert rc == 0, f"trend --analyze failed (exit {rc}):\n{out}"
        # Should contain analysis-related text (verdict, trends, etc.)
        out_lower = out.lower()
        assert any(word in out_lower for word in [
            "verdict", "trend", "anomal", "pattern", "score", "date"
        ]), f"Expected analysis-related output, got:\n{out}"

    def test_trend_json_anomalies_has_array(self, trend_project):
        """roam --json trend --anomalies should have anomalies array in JSON."""
        out, rc = roam("--json", "trend", "--anomalies", cwd=trend_project)
        assert rc == 0, f"trend --anomalies JSON failed (exit {rc}):\n{out}"
        # Parse the JSON output
        data = json.loads(out)
        assert "anomalies" in data, (
            f"Expected 'anomalies' key in JSON output, got keys: {list(data.keys())}"
        )
        assert isinstance(data["anomalies"], list)

    def test_trend_json_forecast_has_arrays(self, trend_project):
        """roam --json trend --forecast should have trends and forecasts arrays."""
        out, rc = roam("--json", "trend", "--forecast", cwd=trend_project)
        assert rc == 0, f"trend --forecast JSON failed (exit {rc}):\n{out}"
        data = json.loads(out)
        assert "trends" in data, (
            f"Expected 'trends' key in JSON output, got keys: {list(data.keys())}"
        )
        assert isinstance(data["trends"], list)
        assert "forecasts" in data, (
            f"Expected 'forecasts' key in JSON output, got keys: {list(data.keys())}"
        )
        assert isinstance(data["forecasts"], list)

    def test_trend_json_analyze_has_all_fields(self, trend_project):
        """roam --json trend --analyze should have all analysis fields."""
        out, rc = roam("--json", "trend", "--analyze", cwd=trend_project)
        assert rc == 0, f"trend --analyze JSON failed (exit {rc}):\n{out}"
        data = json.loads(out)
        assert "anomalies" in data, f"Missing 'anomalies' in: {list(data.keys())}"
        assert "trends" in data, f"Missing 'trends' in: {list(data.keys())}"
        assert "forecasts" in data, f"Missing 'forecasts' in: {list(data.keys())}"
        assert "patterns" in data, f"Missing 'patterns' in: {list(data.keys())}"
        # Summary should have verdict and anomaly_count
        summary = data.get("summary", {})
        assert "verdict" in summary, f"Missing 'verdict' in summary: {summary}"
        assert "anomaly_count" in summary, f"Missing 'anomaly_count' in summary: {summary}"

    def test_trend_sensitivity_high_vs_low(self, trend_project):
        """Higher sensitivity should detect >= anomalies compared to lower."""
        out_high, rc_high = roam(
            "--json", "trend", "--analyze", "--sensitivity=high",
            cwd=trend_project,
        )
        out_low, rc_low = roam(
            "--json", "trend", "--analyze", "--sensitivity=low",
            cwd=trend_project,
        )
        assert rc_high == 0, f"trend --sensitivity=high failed:\n{out_high}"
        assert rc_low == 0, f"trend --sensitivity=low failed:\n{out_low}"
        data_high = json.loads(out_high)
        data_low = json.loads(out_low)
        anomalies_high = len(data_high.get("anomalies", []))
        anomalies_low = len(data_low.get("anomalies", []))
        assert anomalies_high >= anomalies_low, (
            f"High sensitivity ({anomalies_high}) should detect >= anomalies "
            f"than low ({anomalies_low})"
        )
