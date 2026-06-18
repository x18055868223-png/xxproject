# -*- coding: utf-8 -*-
"""无 pytest 依赖的极简测试运行器：发现并运行 tests/test_*.py 内的 test_* 函数。"""
import importlib.util
import glob
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(path):
    name = "t_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    passed = failed = 0
    failures = []
    for f in files:
        mod = _load(f)
        for attr in sorted(dir(mod)):
            if not attr.startswith("test_"):
                continue
            fn = getattr(mod, attr)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print("  PASS %s::%s" % (os.path.basename(f), attr))
            except Exception:
                failed += 1
                failures.append((f, attr, traceback.format_exc()))
                print("  FAIL %s::%s" % (os.path.basename(f), attr))
    print("\n%d passed, %d failed" % (passed, failed))
    for f, attr, tb in failures:
        print("\n=== %s::%s ===\n%s" % (os.path.basename(f), attr, tb))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
