#!/usr/bin/env python3
"""工具入口：python3 -I gaz.py <子命令> ...

用 -I（隔离模式）运行时 cwd 不在 sys.path 中，这里显式加入本文件所在目录。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gazetteer.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
