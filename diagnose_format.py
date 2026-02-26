#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断格式化错误
"""

import sys
sys.path.insert(0, '.')

from optimization.protocol import ThermalMetrics

# 创建测试数据
metrics = ThermalMetrics(
    max_temp=221735840.05,
    min_temp=0.0,
    avg_temp=69048346.99,
    temp_gradient=0.0
)

print("Testing format strings...")

try:
    result = f"Max temp: {metrics.max_temp:.1f}"
    print(f"[OK] Direct format: {result}")
except Exception as e:
    print(f"[FAIL] Direct format failed: {e}")

try:
    result = f"Max temp: {float(metrics.max_temp):.1f}"
    print(f"[OK] Float cast format: {result}")
except Exception as e:
    print(f"[FAIL] Float cast format failed: {e}")

# 测试所有字段
for field in ['max_temp', 'min_temp', 'avg_temp', 'temp_gradient']:
    value = getattr(metrics, field)
    print(f"\n{field}:")
    print(f"  Type: {type(value)}")
    print(f"  Value: {value}")
    try:
        formatted = f"{value:.2f}"
        print(f"  [OK] Format OK: {formatted}")
    except Exception as e:
        print(f"  [FAIL] Format failed: {e}")
