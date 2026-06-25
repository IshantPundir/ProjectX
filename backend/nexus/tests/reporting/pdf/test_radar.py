"""Unit tests for build_radar_geometry (Task C2)."""
import math
import pytest
from app.modules.reporting.pdf.context import build_radar_geometry


def _make_radar(n: int, score: float = 7.0) -> list[dict]:
    return [{"name": f"Signal {i}", "score": score} for i in range(n)]


class TestBuildRadarGeometry:
    def test_returns_none_for_zero(self):
        assert build_radar_geometry([]) is None

    def test_returns_none_for_one(self):
        assert build_radar_geometry(_make_radar(1)) is None

    def test_returns_none_for_two(self):
        assert build_radar_geometry(_make_radar(2)) is None

    def test_returns_dict_for_three(self):
        result = build_radar_geometry(_make_radar(3))
        assert result is not None

    def test_vertex_count_matches_n(self):
        for n in [3, 4, 5, 6, 8]:
            geom = build_radar_geometry(_make_radar(n))
            assert geom is not None
            # count vertices in grid_points string
            verts = geom["grid_points"].strip().split()
            assert len(verts) == n, f"Expected {n} vertices for n={n}"

    def test_data_polygon_at_full_radius_for_score_10(self):
        """Score=10 means the data polygon vertex is at the full grid radius."""
        geom = build_radar_geometry(_make_radar(3, score=10.0))
        assert geom is not None
        assert geom["data_points"] == geom["grid_points"]

    def test_data_polygon_at_center_for_score_0(self):
        """Score=0 means the data polygon vertex is at the center."""
        geom = build_radar_geometry(_make_radar(3, score=0.0))
        assert geom is not None
        cx, cy = geom["cx"], geom["cy"]
        for pt in geom["data_points"].strip().split():
            x, y = pt.split(",")
            assert abs(float(x) - cx) < 0.01
            assert abs(float(y) - cy) < 0.01

    def test_axes_count_matches_n(self):
        geom = build_radar_geometry(_make_radar(5))
        assert geom is not None
        assert len(geom["axes"]) == 5

    def test_labels_count_matches_n(self):
        geom = build_radar_geometry(_make_radar(4))
        assert geom is not None
        assert len(geom["labels"]) == 4

    def test_label_names_preserved(self):
        radar = [{"name": "SQL", "score": 7}, {"name": "Python", "score": 8}, {"name": "Design", "score": 6}]
        geom = build_radar_geometry(radar)
        assert geom is not None
        names = [lb["name"] for lb in geom["labels"]]
        assert names == ["SQL", "Python", "Design"]

    def test_size_in_result(self):
        geom = build_radar_geometry(_make_radar(3), size=300)
        assert geom is not None
        assert geom["size"] == 300

    def test_score_5_halfway(self):
        """Score=5 means vertex is at 50% of full radius."""
        geom = build_radar_geometry(_make_radar(3, score=5.0))
        assert geom is not None
        grid_verts = [tuple(map(float, p.split(","))) for p in geom["grid_points"].split()]
        data_verts = [tuple(map(float, p.split(","))) for p in geom["data_points"].split()]
        cx, cy = geom["cx"], geom["cy"]
        for (gx, gy), (dx, dy) in zip(grid_verts, data_verts):
            # data point should be halfway between center and grid point
            expected_x = cx + (gx - cx) * 0.5
            expected_y = cy + (gy - cy) * 0.5
            assert abs(dx - expected_x) < 0.1
            assert abs(dy - expected_y) < 0.1
