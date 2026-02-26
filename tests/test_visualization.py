#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
可视化模块单元测试
"""

import pytest
import tempfile
import os
from pathlib import Path

from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope
from core.visualization import plot_3d_layout, plot_thermal_heatmap
from core.exceptions import VisualizationError


class TestVisualization:
    """可视化模块测试类"""

    @pytest.fixture
    def sample_design_state(self):
        """创建示例设计状态"""
        return DesignState(
            iteration=1,
            envelope=Envelope(
                outer_size=Vector3D(x=1000, y=800, z=600)
            ),
            components=[
                ComponentGeometry(
                    id="battery_01",
                    position=Vector3D(x=100, y=100, z=50),
                    dimensions=Vector3D(x=200, y=150, z=100),
                    mass=5.0,
                    power=50.0,
                    category="power"
                ),
                ComponentGeometry(
                    id="payload_01",
                    position=Vector3D(x=400, y=200, z=100),
                    dimensions=Vector3D(x=180, y=180, z=120),
                    mass=3.5,
                    power=30.0,
                    category="payload"
                )
            ]
        )

    def test_plot_3d_layout(self, sample_design_state):
        """测试3D布局图生成"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_3d.png")
            plot_3d_layout(sample_design_state, output_path)

            assert os.path.exists(output_path)
            assert os.path.getsize(output_path) > 0

    def test_plot_thermal_heatmap(self, sample_design_state):
        """测试热图生成"""
        thermal_data = {
            "battery_01": 55.3,
            "payload_01": 42.7
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_heatmap.png")
            plot_thermal_heatmap(sample_design_state, thermal_data, output_path)

            assert os.path.exists(output_path)
            assert os.path.getsize(output_path) > 0

    def test_plot_3d_layout_empty_components(self):
        """测试空组件列表的3D布局图"""
        design_state = DesignState(
            iteration=1,
            envelope=Envelope(
                outer_size=Vector3D(x=1000, y=800, z=600)
            ),
            components=[]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_empty.png")
            plot_3d_layout(design_state, output_path)

            assert os.path.exists(output_path)

    def test_plot_thermal_heatmap_empty_data(self, sample_design_state):
        """测试空热数据的热图"""
        thermal_data = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test_empty_thermal.png")
            plot_thermal_heatmap(sample_design_state, thermal_data, output_path)

            assert os.path.exists(output_path)

    def test_plot_3d_layout_invalid_path(self, sample_design_state):
        """测试无效输出路径"""
        invalid_path = "/invalid/path/that/does/not/exist/test.png"

        with pytest.raises(VisualizationError):
            plot_3d_layout(sample_design_state, invalid_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
