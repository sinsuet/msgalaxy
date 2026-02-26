#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BOM (Bill of Materials) 文件解析器

支持多种格式：
- JSON格式
- CSV格式
- YAML格式
"""

import json
import csv
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from core.exceptions import BOMParseError
from core.logger import get_logger

logger = get_logger("bom_parser")


@dataclass
class BOMComponent:
    """BOM组件定义"""
    id: str
    name: str
    dimensions: Dict[str, float]  # x, y, z in mm
    mass: float  # kg
    power: float  # W
    category: str
    quantity: int = 1
    material: Optional[str] = None
    thermal_conductivity: Optional[float] = None
    max_temp: Optional[float] = None
    notes: Optional[str] = None


class BOMParser:
    """BOM文件解析器"""

    @staticmethod
    def parse(file_path: str) -> List[BOMComponent]:
        """
        解析BOM文件

        Args:
            file_path: BOM文件路径

        Returns:
            组件列表

        Raises:
            BOMParseError: 文件格式不支持或解析失败
        """
        path = Path(file_path)

        if not path.exists():
            error_msg = f"BOM文件不存在: {file_path}"
            logger.error(error_msg)
            raise BOMParseError(error_msg)

        # 根据文件扩展名选择解析器
        suffix = path.suffix.lower()
        logger.info(f"解析BOM文件: {file_path} (格式: {suffix})")

        try:
            if suffix == '.json':
                components = BOMParser._parse_json(file_path)
            elif suffix == '.csv':
                components = BOMParser._parse_csv(file_path)
            elif suffix in ['.yaml', '.yml']:
                components = BOMParser._parse_yaml(file_path)
            else:
                error_msg = f"不支持的文件格式: {suffix}"
                logger.error(error_msg)
                raise BOMParseError(error_msg)

            logger.info(f"成功解析 {len(components)} 个组件")
            return components

        except BOMParseError:
            raise
        except Exception as e:
            error_msg = f"解析BOM文件失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise BOMParseError(error_msg) from e

    @staticmethod
    def _parse_json(file_path: str) -> List[BOMComponent]:
        """解析JSON格式BOM"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            error_msg = f"JSON格式错误: {str(e)}"
            logger.error(error_msg)
            raise BOMParseError(error_msg) from e
        except Exception as e:
            error_msg = f"读取JSON文件失败: {str(e)}"
            logger.error(error_msg)
            raise BOMParseError(error_msg) from e

        components = []

        # 支持两种格式：
        # 1. {"components": [...]}
        # 2. [...]
        if isinstance(data, dict) and 'components' in data:
            items = data['components']
        elif isinstance(data, list):
            items = data
        else:
            error_msg = "JSON格式错误：需要components数组或直接数组"
            logger.error(error_msg)
            raise BOMParseError(error_msg)

        for i, item in enumerate(items):
            try:
                comp = BOMParser._create_component(item)
                components.append(comp)
            except Exception as e:
                error_msg = f"解析第 {i+1} 个组件失败: {str(e)}"
                logger.error(error_msg)
                raise BOMParseError(error_msg) from e

        return components

    @staticmethod
    def _parse_csv(file_path: str) -> List[BOMComponent]:
        """解析CSV格式BOM"""
        components = []

        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)

            for row in reader:
                # 转换数据类型
                item = {
                    'id': row['id'],
                    'name': row['name'],
                    'dimensions': {
                        'x': float(row['dim_x']),
                        'y': float(row['dim_y']),
                        'z': float(row['dim_z'])
                    },
                    'mass': float(row['mass']),
                    'power': float(row['power']),
                    'category': row['category'],
                    'quantity': int(row.get('quantity', 1))
                }

                # 可选字段
                if 'material' in row and row['material']:
                    item['material'] = row['material']
                if 'thermal_conductivity' in row and row['thermal_conductivity']:
                    item['thermal_conductivity'] = float(row['thermal_conductivity'])
                if 'max_temp' in row and row['max_temp']:
                    item['max_temp'] = float(row['max_temp'])
                if 'notes' in row and row['notes']:
                    item['notes'] = row['notes']

                comp = BOMParser._create_component(item)
                components.append(comp)

        return components

    @staticmethod
    def _parse_yaml(file_path: str) -> List[BOMComponent]:
        """解析YAML格式BOM"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        components = []

        # 支持两种格式
        if isinstance(data, dict) and 'components' in data:
            items = data['components']
        elif isinstance(data, list):
            items = data
        else:
            raise ValueError("YAML格式错误：需要components数组或直接数组")

        for item in items:
            comp = BOMParser._create_component(item)
            components.append(comp)

        return components

    @staticmethod
    def _create_component(item: Dict[str, Any]) -> BOMComponent:
        """从字典创建组件对象"""
        # 必需字段
        required_fields = ['id', 'name', 'dimensions', 'mass', 'power', 'category']
        for field in required_fields:
            if field not in item:
                error_msg = f"缺少必需字段: {field}"
                logger.error(error_msg)
                raise BOMParseError(error_msg)

        # 验证dimensions
        dims = item['dimensions']
        if not all(k in dims for k in ['x', 'y', 'z']):
            error_msg = "dimensions必须包含x, y, z"
            logger.error(error_msg)
            raise BOMParseError(error_msg)

        try:
            return BOMComponent(
                id=item['id'],
                name=item['name'],
                dimensions=item['dimensions'],
                mass=item['mass'],
                power=item['power'],
                category=item['category'],
                quantity=item.get('quantity', 1),
                material=item.get('material'),
                thermal_conductivity=item.get('thermal_conductivity'),
                max_temp=item.get('max_temp'),
                notes=item.get('notes')
            )
        except Exception as e:
            error_msg = f"创建组件对象失败: {str(e)}"
            logger.error(error_msg)
            raise BOMParseError(error_msg) from e

    @staticmethod
    def validate(components: List[BOMComponent]) -> List[str]:
        """
        验证BOM组件列表

        Args:
            components: 组件列表

        Returns:
            错误信息列表（空列表表示验证通过）
        """
        errors = []

        if not components:
            errors.append("组件列表为空")
            return errors

        # 检查ID唯一性
        ids = [c.id for c in components]
        if len(ids) != len(set(ids)):
            errors.append("组件ID不唯一")

        # 检查每个组件
        for comp in components:
            # 检查尺寸
            if any(v <= 0 for v in comp.dimensions.values()):
                errors.append(f"{comp.id}: 尺寸必须大于0")

            # 检查质量
            if comp.mass <= 0:
                errors.append(f"{comp.id}: 质量必须大于0")

            # 检查功率
            if comp.power < 0:
                errors.append(f"{comp.id}: 功率不能为负")

            # 检查数量
            if comp.quantity <= 0:
                errors.append(f"{comp.id}: 数量必须大于0")

        return errors

    @staticmethod
    def create_template(output_path: str, format: str = 'json'):
        """
        创建BOM模板文件

        Args:
            output_path: 输出文件路径
            format: 文件格式 (json, csv, yaml)
        """
        template_data = [
            {
                'id': 'battery_01',
                'name': '锂电池组',
                'dimensions': {'x': 200, 'y': 150, 'z': 100},
                'mass': 5.0,
                'power': 50.0,
                'category': 'power',
                'quantity': 1,
                'material': 'aluminum',
                'thermal_conductivity': 237.0,
                'max_temp': 60.0,
                'notes': '主电源'
            },
            {
                'id': 'payload_01',
                'name': '科学载荷',
                'dimensions': {'x': 180, 'y': 180, 'z': 120},
                'mass': 3.5,
                'power': 30.0,
                'category': 'payload',
                'quantity': 1,
                'material': 'aluminum',
                'thermal_conductivity': 237.0,
                'max_temp': 50.0,
                'notes': '主要任务载荷'
            }
        ]

        if format == 'json':
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump({'components': template_data}, f, indent=2, ensure_ascii=False)

        elif format == 'csv':
            with open(output_path, 'w', encoding='utf-8', newline='') as f:
                fieldnames = ['id', 'name', 'dim_x', 'dim_y', 'dim_z', 'mass', 'power',
                            'category', 'quantity', 'material', 'thermal_conductivity',
                            'max_temp', 'notes']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for item in template_data:
                    row = {
                        'id': item['id'],
                        'name': item['name'],
                        'dim_x': item['dimensions']['x'],
                        'dim_y': item['dimensions']['y'],
                        'dim_z': item['dimensions']['z'],
                        'mass': item['mass'],
                        'power': item['power'],
                        'category': item['category'],
                        'quantity': item['quantity'],
                        'material': item.get('material', ''),
                        'thermal_conductivity': item.get('thermal_conductivity', ''),
                        'max_temp': item.get('max_temp', ''),
                        'notes': item.get('notes', '')
                    }
                    writer.writerow(row)

        elif format == 'yaml':
            with open(output_path, 'w', encoding='utf-8') as f:
                yaml.dump({'components': template_data}, f, allow_unicode=True, default_flow_style=False)

        else:
            raise ValueError(f"不支持的格式: {format}")


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == 'template':
            format = sys.argv[2] if len(sys.argv) > 2 else 'json'
            output = sys.argv[3] if len(sys.argv) > 3 else f'bom_template.{format}'
            BOMParser.create_template(output, format)
            print(f"模板已创建: {output}")

        elif command == 'parse':
            if len(sys.argv) < 3:
                print("用法: python bom_parser.py parse <file>")
                sys.exit(1)

            file_path = sys.argv[2]
            components = BOMParser.parse(file_path)

            print(f"\n解析成功: {len(components)} 个组件")
            print("-" * 60)
            for comp in components:
                print(f"{comp.id}: {comp.name}")
                print(f"  尺寸: {comp.dimensions['x']}x{comp.dimensions['y']}x{comp.dimensions['z']} mm")
                print(f"  质量: {comp.mass} kg, 功率: {comp.power} W")
                print(f"  类别: {comp.category}, 数量: {comp.quantity}")
                print()

            # 验证
            errors = BOMParser.validate(components)
            if errors:
                print("验证错误:")
                for error in errors:
                    print(f"  - {error}")
            else:
                print("[OK] 验证通过")

        else:
            print(f"未知命令: {command}")
    else:
        print("BOM文件解析器")
        print("\n用法:")
        print("  python bom_parser.py template [format] [output]  - 创建模板")
        print("  python bom_parser.py parse <file>                - 解析BOM文件")
        print("\n示例:")
        print("  python bom_parser.py template json bom.json")
        print("  python bom_parser.py parse bom.json")
