#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CAD导出模块测试
"""

import pytest
from pathlib import Path
import sys
import tempfile
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent))

from geometry.cad_export import (
    STEPExporter,
    IGESExporter,
    CADExportOptions,
    export_design
)
from core.protocol import DesignState, ComponentGeometry, Vector3D, Envelope
from core.exceptions import GeometryError


def create_test_design() -> DesignState:
    """创建测试设计状态"""
    components = [
        ComponentGeometry(
            id="battery_01",
            position=Vector3D(x=10.0, y=10.0, z=10.0),
            dimensions=Vector3D(x=100.0, y=80.0, z=50.0),
            mass=5.0,
            power=50.0,
            category="power"
        ),
        ComponentGeometry(
            id="payload_01",
            position=Vector3D(x=120.0, y=10.0, z=10.0),
            dimensions=Vector3D(x=80.0, y=80.0, z=60.0),
            mass=3.0,
            power=30.0,
            category="payload"
        )
    ]

    envelope = Envelope(
        outer_size=Vector3D(x=300.0, y=200.0, z=200.0)
    )

    return DesignState(
        iteration=1,
        components=components,
        envelope=envelope
    )


class TestSTEPExporter:
    """STEP导出器测试"""

    def test_initialization(self):
        """测试初始化"""
        exporter = STEPExporter()
        assert exporter.options is not None
        assert exporter.options.unit == "mm"

    def test_initialization_with_options(self):
        """测试带选项初始化"""
        options = CADExportOptions(
            unit="cm",
            precision=2,
            author="Test Author"
        )
        exporter = STEPExporter(options)
        assert exporter.options.unit == "cm"
        assert exporter.options.precision == 2
        assert exporter.options.author == "Test Author"

    def test_export_step(self):
        """测试STEP导出"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.step"

            exporter = STEPExporter()
            success = exporter.export(design, str(output_path))

            assert success
            assert output_path.exists()

            # 验证文件内容
            content = output_path.read_text(encoding='utf-8')
            assert "ISO-10303-21" in content
            assert "HEADER" in content
            assert "DATA" in content
            assert "battery_01" in content
            assert "payload_01" in content

    def test_export_step_with_metadata(self):
        """测试带元数据的STEP导出"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_metadata.step"

            options = CADExportOptions(
                include_metadata=True,
                author="Test User",
                description="Test satellite design"
            )

            exporter = STEPExporter(options)
            success = exporter.export(design, str(output_path))

            assert success

            content = output_path.read_text(encoding='utf-8')
            assert "Test User" in content
            assert "Test satellite design" in content

    def test_export_creates_directory(self):
        """测试自动创建目录"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "subdir" / "test.step"

            exporter = STEPExporter()
            success = exporter.export(design, str(output_path))

            assert success
            assert output_path.exists()
            assert output_path.parent.exists()


class TestIGESExporter:
    """IGES导出器测试"""

    def test_initialization(self):
        """测试初始化"""
        exporter = IGESExporter()
        assert exporter.options is not None

    def test_export_iges(self):
        """测试IGES导出"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.iges"

            exporter = IGESExporter()
            success = exporter.export(design, str(output_path))

            assert success
            assert output_path.exists()

            # 验证文件内容
            content = output_path.read_text(encoding='utf-8')
            assert "MsGalaxy" in content
            assert "S      1" in content  # Start段
            assert "G" in content  # Global段
            assert "D" in content  # Directory段
            assert "T      1" in content  # Terminate段


class TestExportDesign:
    """导出设计函数测试"""

    def test_export_step_format(self):
        """测试STEP格式导出"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.step"

            success = export_design(design, str(output_path), format="step")

            assert success
            assert output_path.exists()

    def test_export_iges_format(self):
        """测试IGES格式导出"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.iges"

            success = export_design(design, str(output_path), format="iges")

            assert success
            assert output_path.exists()

    def test_export_with_options(self):
        """测试带选项导出"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.step"

            options = CADExportOptions(
                precision=2,
                author="Custom Author"
            )

            success = export_design(
                design,
                str(output_path),
                format="step",
                options=options
            )

            assert success

            content = output_path.read_text(encoding='utf-8')
            assert "Custom Author" in content

    def test_export_unsupported_format(self):
        """测试不支持的格式"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.obj"

            with pytest.raises(ValueError, match="Unsupported format"):
                export_design(design, str(output_path), format="obj")

    def test_export_case_insensitive(self):
        """测试格式大小写不敏感"""
        design = create_test_design()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.step"

            success = export_design(design, str(output_path), format="STEP")
            assert success

            output_path2 = Path(tmpdir) / "test.iges"
            success = export_design(design, str(output_path2), format="IGES")
            assert success


class TestCADExportOptions:
    """CAD导出选项测试"""

    def test_default_options(self):
        """测试默认选项"""
        options = CADExportOptions()
        assert options.include_metadata == True
        assert options.unit == "mm"
        assert options.precision == 3
        assert options.author == "MsGalaxy"

    def test_custom_options(self):
        """测试自定义选项"""
        options = CADExportOptions(
            include_metadata=False,
            unit="cm",
            precision=5,
            author="Custom",
            description="Custom description"
        )

        assert options.include_metadata == False
        assert options.unit == "cm"
        assert options.precision == 5
        assert options.author == "Custom"
        assert options.description == "Custom description"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
