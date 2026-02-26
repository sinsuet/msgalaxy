#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BOM解析器单元测试
"""

import pytest
import json
import csv
import tempfile
import os
from pathlib import Path

from core.bom_parser import BOMParser, BOMComponent
from core.exceptions import BOMParseError


class TestBOMParser:
    """BOM解析器测试类"""

    def test_parse_json_with_components_key(self):
        """测试解析带components键的JSON格式"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump({
                "components": [
                    {
                        "id": "test_01",
                        "name": "测试组件",
                        "dimensions": {"x": 100, "y": 100, "z": 100},
                        "mass": 1.0,
                        "power": 10.0,
                        "category": "test"
                    }
                ]
            }, f)
            temp_path = f.name

        try:
            components = BOMParser.parse(temp_path)
            assert len(components) == 1
            assert components[0].id == "test_01"
            assert components[0].name == "测试组件"
            assert components[0].mass == 1.0
        finally:
            os.unlink(temp_path)

    def test_parse_json_direct_array(self):
        """测试解析直接数组的JSON格式"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump([
                {
                    "id": "test_01",
                    "name": "测试组件",
                    "dimensions": {"x": 100, "y": 100, "z": 100},
                    "mass": 1.0,
                    "power": 10.0,
                    "category": "test"
                }
            ], f)
            temp_path = f.name

        try:
            components = BOMParser.parse(temp_path)
            assert len(components) == 1
            assert components[0].id == "test_01"
        finally:
            os.unlink(temp_path)

    def test_parse_csv(self):
        """测试解析CSV格式"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'id', 'name', 'dim_x', 'dim_y', 'dim_z', 'mass', 'power', 'category', 'quantity'
            ])
            writer.writeheader()
            writer.writerow({
                'id': 'test_01',
                'name': '测试组件',
                'dim_x': 100,
                'dim_y': 100,
                'dim_z': 100,
                'mass': 1.0,
                'power': 10.0,
                'category': 'test',
                'quantity': 1
            })
            temp_path = f.name

        try:
            components = BOMParser.parse(temp_path)
            assert len(components) == 1
            assert components[0].id == "test_01"
            assert components[0].dimensions['x'] == 100
        finally:
            os.unlink(temp_path)

    def test_parse_nonexistent_file(self):
        """测试解析不存在的文件"""
        with pytest.raises(BOMParseError):
            BOMParser.parse("nonexistent_file.json")

    def test_parse_unsupported_format(self):
        """测试解析不支持的文件格式"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("test")
            temp_path = f.name

        try:
            with pytest.raises(BOMParseError):
                BOMParser.parse(temp_path)
        finally:
            os.unlink(temp_path)

    def test_parse_invalid_json(self):
        """测试解析无效的JSON"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{invalid json")
            temp_path = f.name

        try:
            with pytest.raises(BOMParseError):
                BOMParser.parse(temp_path)
        finally:
            os.unlink(temp_path)

    def test_parse_missing_required_field(self):
        """测试缺少必需字段"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump({
                "components": [
                    {
                        "id": "test_01",
                        "name": "测试组件",
                        # 缺少dimensions
                        "mass": 1.0,
                        "power": 10.0,
                        "category": "test"
                    }
                ]
            }, f)
            temp_path = f.name

        try:
            with pytest.raises(BOMParseError):
                BOMParser.parse(temp_path)
        finally:
            os.unlink(temp_path)

    def test_validate_empty_list(self):
        """测试验证空列表"""
        errors = BOMParser.validate([])
        assert len(errors) > 0
        assert "组件列表为空" in errors[0]

    def test_validate_duplicate_ids(self):
        """测试验证重复ID"""
        components = [
            BOMComponent(
                id="test_01",
                name="组件1",
                dimensions={"x": 100, "y": 100, "z": 100},
                mass=1.0,
                power=10.0,
                category="test"
            ),
            BOMComponent(
                id="test_01",  # 重复ID
                name="组件2",
                dimensions={"x": 100, "y": 100, "z": 100},
                mass=1.0,
                power=10.0,
                category="test"
            )
        ]
        errors = BOMParser.validate(components)
        assert len(errors) > 0
        assert any("ID不唯一" in e for e in errors)

    def test_validate_invalid_dimensions(self):
        """测试验证无效尺寸"""
        components = [
            BOMComponent(
                id="test_01",
                name="组件1",
                dimensions={"x": -100, "y": 100, "z": 100},  # 负数尺寸
                mass=1.0,
                power=10.0,
                category="test"
            )
        ]
        errors = BOMParser.validate(components)
        assert len(errors) > 0
        assert any("尺寸必须大于0" in e for e in errors)

    def test_validate_invalid_mass(self):
        """测试验证无效质量"""
        components = [
            BOMComponent(
                id="test_01",
                name="组件1",
                dimensions={"x": 100, "y": 100, "z": 100},
                mass=-1.0,  # 负数质量
                power=10.0,
                category="test"
            )
        ]
        errors = BOMParser.validate(components)
        assert len(errors) > 0
        assert any("质量必须大于0" in e for e in errors)

    def test_create_template_json(self):
        """测试创建JSON模板"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "template.json")
            BOMParser.create_template(output_path, format='json')

            assert os.path.exists(output_path)

            # 验证可以解析
            components = BOMParser.parse(output_path)
            assert len(components) == 2

    def test_create_template_csv(self):
        """测试创建CSV模板"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "template.csv")
            BOMParser.create_template(output_path, format='csv')

            assert os.path.exists(output_path)

            # 验证可以解析
            components = BOMParser.parse(output_path)
            assert len(components) == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
