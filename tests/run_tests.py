"""
测试运行器

自动运行所有测试并生成报告
"""

import sys
import io
import subprocess
import os
from pathlib import Path
from datetime import datetime

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


class TestRunner:
    """测试运行器"""

    def __init__(self):
        self.results = []
        self.start_time = None
        self.end_time = None

    def print_header(self):
        """打印标题"""
        print("=" * 60)
        print("MsGalaxy 测试套件")
        print("=" * 60)
        print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()

    def check_dependencies(self):
        """检查依赖"""
        print("[1/3] 检查Python版本")
        print(f"  Python: {sys.version}")

        print("\n[2/3] 检查关键依赖")
        deps = ['numpy', 'pydantic', 'openai', 'yaml', 'scipy']
        for dep in deps:
            try:
                if dep == 'yaml':
                    import yaml
                    print(f"  [OK] pyyaml: {yaml.__version__}")
                else:
                    mod = __import__(dep)
                    version = getattr(mod, '__version__', 'unknown')
                    print(f"  [OK] {dep}: {version}")
            except ImportError:
                print(f"  [FAIL] {dep}: 未安装")

        print("\n[3/3] 检查环境变量")
        if os.path.exists('.env'):
            print("  [OK] .env 文件存在")
        else:
            print("  [WARN] .env 文件不存在")
        print()

    def run_test(self, test_name: str, test_file: str, *args) -> bool:
        """运行单个测试"""
        print(f"[TEST] {test_name}")
        print("-" * 60)

        try:
            # 运行测试
            cmd = [sys.executable, test_file] + list(args)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',  # 替换无法解码的字符
                timeout=120
            )

            # 打印输出
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)

            # 检查结果
            success = result.returncode == 0

            if success:
                print(f"[OK] {test_name} 通过")
            else:
                print(f"[FAIL] {test_name} 失败 (退出码: {result.returncode})")

            self.results.append({
                'name': test_name,
                'success': success,
                'returncode': result.returncode
            })

            print()
            return success

        except subprocess.TimeoutExpired:
            print(f"[FAIL] {test_name} 超时")
            self.results.append({
                'name': test_name,
                'success': False,
                'returncode': -1
            })
            print()
            return False

        except Exception as e:
            print(f"[FAIL] {test_name} 异常: {e}")
            self.results.append({
                'name': test_name,
                'success': False,
                'returncode': -1
            })
            print()
            return False

    def print_summary(self):
        """打印总结"""
        print("=" * 60)
        print("测试总结")
        print("=" * 60)

        total = len(self.results)
        passed = sum(1 for r in self.results if r['success'])
        failed = total - passed

        print(f"\n总计: {total} 个测试")
        print(f"通过: {passed} 个")
        print(f"失败: {failed} 个")

        if failed > 0:
            print("\n失败的测试:")
            for r in self.results:
                if not r['success']:
                    print(f"  - {r['name']}")

        duration = (self.end_time - self.start_time).total_seconds()
        print(f"\n总耗时: {duration:.2f} 秒")

        print("\n" + "=" * 60)
        if failed == 0:
            print("[SUCCESS] 所有测试通过！")
            print("=" * 60)
            return 0
        else:
            print(f"[FAIL] {failed} 个测试失败")
            print("=" * 60)
            return 1

    def run_all(self):
        """运行所有测试"""
        self.start_time = datetime.now()

        self.print_header()
        self.check_dependencies()

        print("=" * 60)
        print("开始运行测试")
        print("=" * 60)
        print()

        # 测试1: 几何模块
        self.run_test("几何模块测试", "test_geometry.py")

        # 测试2: 仿真模块
        self.run_test("仿真模块测试", "test_simulation.py")

        # 测试3: 集成测试
        self.run_test("系统集成测试", "test_integration.py")

        # 测试4: Qwen API测试（如果有API key）
        api_key = None
        if os.path.exists('.env'):
            with open('.env', 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('OPENAI_API_KEY='):
                        api_key = line.split('=', 1)[1].strip().strip('"\'')
                        break

        if api_key:
            self.run_test("Qwen API测试", "test_qwen.py", api_key)
        else:
            print("[SKIP] Qwen API测试 - 未找到API密钥")
            print()

        self.end_time = datetime.now()

        return self.print_summary()


def main():
    """主函数"""
    runner = TestRunner()
    exit_code = runner.run_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
