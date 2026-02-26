#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FFD模块单元测试
"""

import pytest
import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from geometry.ffd import FFDDeformer, create_simple_deformation, create_taper_deformation
from core.protocol import ComponentGeometry, Vector3D
from core.exceptions import GeometryError


class TestFFDDeformer:
    """FFD变形器测试"""

    def test_initialization(self):
        """测试FFD变形器初始化"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        assert deformer.nx == 3
        assert deformer.ny == 3
        assert deformer.nz == 3
        assert deformer.lattice is None

    def test_invalid_initialization(self):
        """测试无效的初始化参数"""
        with pytest.raises(GeometryError):
            FFDDeformer(nx=1, ny=3, nz=3)  # nx < 2

        with pytest.raises(GeometryError):
            FFDDeformer(nx=3, ny=1, nz=3)  # ny < 2

    def test_create_lattice(self):
        """测试创建FFD网格"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)

        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])

        lattice = deformer.create_lattice(bbox_min, bbox_max, margin=0.1)

        assert lattice is not None
        assert lattice.nx == 3
        assert lattice.ny == 3
        assert lattice.nz == 3
        assert lattice.control_points.shape == (3, 3, 3, 3)

        # 检查网格边界扩展
        expected_origin = bbox_min - 0.1 * (bbox_max - bbox_min)
        np.testing.assert_array_almost_equal(lattice.origin, expected_origin)

    def test_world_to_parametric(self):
        """测试世界坐标到参数空间转换"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max, margin=0.0)

        # 测试中心点
        center = np.array([[50.0, 50.0, 50.0]])
        parametric = deformer.world_to_parametric(center)
        np.testing.assert_array_almost_equal(parametric, [[0.5, 0.5, 0.5]])

        # 测试角点
        corner = np.array([[0.0, 0.0, 0.0]])
        parametric = deformer.world_to_parametric(corner)
        np.testing.assert_array_almost_equal(parametric, [[0.0, 0.0, 0.0]])

    def test_parametric_to_world(self):
        """测试参数空间到世界坐标转换"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max, margin=0.0)

        # 测试中心点
        parametric = np.array([[0.5, 0.5, 0.5]])
        world = deformer.parametric_to_world(parametric)
        np.testing.assert_array_almost_equal(world, [[50.0, 50.0, 50.0]], decimal=1)

    def test_deform_identity(self):
        """测试无变形情况(恒等变换)"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max, margin=0.0)

        # 测试点
        points = np.array([
            [25.0, 25.0, 25.0],
            [50.0, 50.0, 50.0],
            [75.0, 75.0, 75.0]
        ])

        # 无位移
        displacements = {}
        deformed = deformer.deform(points, displacements)

        # 应该保持不变
        np.testing.assert_array_almost_equal(deformed, points, decimal=1)

    def test_deform_with_displacement(self):
        """测试带位移的变形"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max, margin=0.0)

        # 测试点
        points = np.array([[50.0, 50.0, 50.0]])

        # 移动中心控制点
        displacements = {
            (1, 1, 1): np.array([10.0, 0.0, 0.0])  # 中心控制点向x正方向移动10
        }

        deformed = deformer.deform(points, displacements)

        # 中心点应该也向x正方向移动
        assert deformed[0, 0] > points[0, 0]

    def test_get_set_control_point(self):
        """测试获取和设置控制点"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max, margin=0.0)

        # 获取控制点
        cp = deformer.get_control_point(1, 1, 1)
        assert cp.shape == (3,)

        # 设置控制点
        new_pos = np.array([60.0, 60.0, 60.0])
        deformer.set_control_point(1, 1, 1, new_pos)

        # 验证
        cp_new = deformer.get_control_point(1, 1, 1)
        np.testing.assert_array_almost_equal(cp_new, new_pos)

    def test_get_lattice_info(self):
        """测试获取网格信息"""
        deformer = FFDDeformer(nx=4, ny=3, nz=2)

        # 未初始化
        info = deformer.get_lattice_info()
        assert info["initialized"] == False

        # 初始化后
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max)

        info = deformer.get_lattice_info()
        assert info["initialized"] == True
        assert info["nx"] == 4
        assert info["ny"] == 3
        assert info["nz"] == 2
        assert info["total_control_points"] == 24


class TestBernsteinFunctions:
    """Bernstein函数测试"""

    def test_bernstein_basis(self):
        """测试Bernstein基函数"""
        # B_0^2(0) = 1
        assert FFDDeformer._bernstein(2, 0, 0.0) == 1.0

        # B_2^2(1) = 1
        assert FFDDeformer._bernstein(2, 2, 1.0) == 1.0

        # B_1^2(0.5) = 0.5
        assert abs(FFDDeformer._bernstein(2, 1, 0.5) - 0.5) < 1e-10

    def test_binomial_coefficient(self):
        """测试二项式系数"""
        assert FFDDeformer._binomial_coefficient(5, 0) == 1
        assert FFDDeformer._binomial_coefficient(5, 1) == 5
        assert FFDDeformer._binomial_coefficient(5, 2) == 10
        assert FFDDeformer._binomial_coefficient(5, 3) == 10
        assert FFDDeformer._binomial_coefficient(5, 5) == 1

    def test_bernstein_partition_of_unity(self):
        """测试Bernstein基函数的单位分解性质"""
        n = 3
        t = 0.3

        # Σ B_i^n(t) = 1
        total = sum(FFDDeformer._bernstein(n, i, t) for i in range(n + 1))
        assert abs(total - 1.0) < 1e-10


class TestDeformationHelpers:
    """变形辅助函数测试"""

    def test_create_simple_deformation(self):
        """测试创建简单变形"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max)

        # 创建z轴变形
        displacements = create_simple_deformation(deformer, axis='z', magnitude=10.0)

        # 应该有9个控制点(顶层3x3)
        assert len(displacements) == 9

        # 检查位移方向
        for (i, j, k), disp in displacements.items():
            assert k == 2  # 顶层
            assert disp[2] == 10.0  # z方向位移

    def test_create_taper_deformation(self):
        """测试创建锥形变形"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max)

        # 创建锥形变形
        displacements = create_taper_deformation(deformer, axis='z', taper_ratio=0.8)

        # 应该有27个控制点
        assert len(displacements) == 27


class TestComponentDeformation:
    """组件变形测试"""

    def test_deform_component(self):
        """测试组件变形"""
        deformer = FFDDeformer(nx=3, ny=3, nz=3)

        # 创建组件
        component = ComponentGeometry(
            id="test_comp",
            position=Vector3D(x=10.0, y=10.0, z=10.0),
            dimensions=Vector3D(x=50.0, y=50.0, z=50.0),
            mass=1.0,
            power=10.0,
            category="test"
        )

        # 保存原始尺寸
        original_z = component.dimensions.z

        # 创建FFD网格
        bbox_min = np.array([0.0, 0.0, 0.0])
        bbox_max = np.array([100.0, 100.0, 100.0])
        deformer.create_lattice(bbox_min, bbox_max)

        # 应用变形
        displacements = {
            (1, 1, 2): np.array([0.0, 0.0, 20.0])  # 顶部中心向上移动
        }

        deformed_component = deformer.deform_component(component, displacements)

        # 验证组件被修改
        assert deformed_component.id == "test_comp"
        # 尺寸应该有变化(z方向应该增大)
        assert deformed_component.dimensions.z > original_z


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
