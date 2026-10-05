# loglens 常用命令（Windows 上可用 Git Bash / WSL，或直接执行等价的 python 命令）
PY ?= python
DATA := data/HDFS_2k.log
OUT := results/HDFS

.PHONY: help install run run-all selftest bench clean

help:
	@echo "make install    安装运行依赖 (numpy, pandas)"
	@echo "make run        对 data/HDFS_2k.log 跑一遍完整流水线"
	@echo "make run-all    对 data/ 下四个样例全部跑一遍并生成对比"
	@echo "make selftest   运行 45 个单元测试"
	@echo "make bench      运行注入式基准评测 (精确率/召回率/F1)"
	@echo "make clean      清理临时目录与缓存"

install:
	$(PY) -m pip install -r requirements.txt

run:
	$(PY) -m loglens run -i $(DATA) -o $(OUT)

run-all:
	$(PY) scripts/run_all.py

selftest:
	$(PY) -m loglens selftest

bench:
	$(PY) -m loglens bench -o results/bench

clean:
	$(PY) -c "import shutil,pathlib;[shutil.rmtree(p, ignore_errors=True) for p in list(pathlib.Path('.').rglob('__pycache__'))+[pathlib.Path('build_tmp')]]"
