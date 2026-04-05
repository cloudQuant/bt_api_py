"""Tests for analysis_log module."""

import tempfile
from pathlib import Path

import pytest

from bt_api_py.functions.analysis_log import (
    TIME_PATTERN,
    _require_package_path,
    build_duration_series,
    extract_slam_times,
    render_report,
    time_subtraction,
)


class TestTimePattern:
    """Tests for TIME_PATTERN regex."""

    def test_matches_valid_timestamp(self):
        """Test matching valid timestamp."""
        line = "deal trade_data, time = 2024-01-15 10:30:00.123456"
        matches = TIME_PATTERN.findall(line)

        assert len(matches) == 1
        assert matches[0] == "2024-01-15 10:30:00.123456"

    def test_matches_multiple_timestamps(self):
        """Test matching multiple timestamps in one line."""
        line = "deal trade_data, time = 2024-01-15 10:30:00.123456 deal trade_data, time = 2024-01-15 10:30:01.654321"
        matches = TIME_PATTERN.findall(line)

        assert len(matches) == 2

    def test_no_match_for_invalid_format(self):
        """Test no match for invalid format."""
        line = "some other log message"
        matches = TIME_PATTERN.findall(line)

        assert len(matches) == 0


class TestTimeSubtraction:
    """Tests for time_subtraction function."""

    def test_subtraction_same_second(self):
        """Test subtraction within same second."""
        result = time_subtraction("2024-01-15 10:30:00.100000", "2024-01-15 10:30:00.200000")

        assert result == 100.0  # 100 milliseconds

    def test_subtraction_different_seconds(self):
        """Test subtraction across different seconds."""
        result = time_subtraction("2024-01-15 10:30:00.000000", "2024-01-15 10:30:01.000000")

        assert result == 1000.0  # 1 second = 1000 milliseconds

    def test_subtraction_different_minutes(self):
        """Test subtraction across different minutes."""
        result = time_subtraction("2024-01-15 10:30:00.000000", "2024-01-15 10:31:00.000000")

        assert result == 60000.0  # 1 minute = 60000 milliseconds


class TestExtractSlamTimes:
    """Tests for extract_slam_times function."""

    def test_extract_from_file(self):
        """Test extracting timestamps from file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.write("deal trade_data, time = 2024-01-15 10:30:00.123456\n")
            f.write("some other line\n")
            f.write("deal trade_data, time = 2024-01-15 10:30:01.654321\n")
            f.flush()

            times = extract_slam_times(Path(f.name))

            assert len(times) == 2
            assert times[0] == "2024-01-15 10:30:00.123456"
            assert times[1] == "2024-01-15 10:30:01.654321"

    def test_extract_empty_file(self):
        """Test extracting from empty file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.flush()

            times = extract_slam_times(Path(f.name))

            assert len(times) == 0

    def test_extract_no_matches(self):
        """Test extracting from file with no matches."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.write("some log line\n")
            f.write("another log line\n")
            f.flush()

            times = extract_slam_times(Path(f.name))

            assert len(times) == 0


class TestAnalysisLogHelpers:
    def test_require_package_path_returns_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "bt_api_py.functions.analysis_log.get_package_path", lambda package_name: str(tmp_path)
        )

        assert _require_package_path("lv") == tmp_path

    def test_require_package_path_raises_when_missing(self, monkeypatch):
        monkeypatch.setattr(
            "bt_api_py.functions.analysis_log.get_package_path", lambda package_name: None
        )

        with pytest.raises(RuntimeError):
            _require_package_path("lv")

    def test_build_duration_series_pairs_timestamps(self, tmp_path):
        log_file = tmp_path / "slam.log"
        log_file.write_text(
            "\n".join(
                [
                    "deal trade_data, time = 2024-01-15 10:30:00.000000",
                    "deal trade_data, time = 2024-01-15 10:30:00.100000",
                    "deal trade_data, time = 2024-01-15 10:30:01.000000",
                    "deal trade_data, time = 2024-01-15 10:30:01.250000",
                ]
            ),
            encoding="utf-8",
        )

        assert build_duration_series(log_file) == [100.0, 250.0]

    def test_build_duration_series_ignores_unpaired_tail(self, tmp_path):
        log_file = tmp_path / "slam.log"
        log_file.write_text(
            "\n".join(
                [
                    "deal trade_data, time = 2024-01-15 10:30:00.000000",
                    "deal trade_data, time = 2024-01-15 10:30:00.100000",
                    "deal trade_data, time = 2024-01-15 10:30:01.000000",
                ]
            ),
            encoding="utf-8",
        )

        assert build_duration_series(log_file) == [100.0]

    def test_render_report_writes_html_and_returns_dataframe(self, tmp_path):
        output = tmp_path / "report.html"
        result = render_report([10.0, 20.0, 30.0], output)

        assert list(result.columns) == ["consume_time"]
        assert len(result) == 3
        assert output.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
