#!/usr/bin/env python3
"""
EWY 信号 — launchd 调度入口。

用法:
  python ewy_orchestrator.py                  # 正常: 更新数据 + 信号检查
  python ewy_orchestrator.py --no-update      # 跳过数据更新
  python ewy_orchestrator.py --test           # 测试模式
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from qbot import signal_bus, notifier, order_placer, db
from qbot.log_util import get_logger
from qbot.safety import check_is_weekday

log = get_logger("orchestrator", "EWY")


def run(do_update: bool = True):
    log.info("=== EWY Signal Check ===")

    wd = check_is_weekday()
    if not wd.passed:
        log.info(f"Skip: {wd.detail}")
        return

    from ewy_strategy import EWYStrategy
    strategy = EWYStrategy(do_update=do_update)

    results = signal_bus.run_strategy(
        strategy,
        notifier=notifier,
        order_placer=order_placer,
    )

    if not results:
        log.info("No signals today")
    else:
        for r in results:
            sig = r["signal"]
            passed = r["all_passed"]
            status = "SENT" if passed else "BLOCKED"
            log.info(f"{sig.symbol} {sig.direction} [{sig.data.get('sub_strategy','')}] "
                     f"{status}: {sig.data.get('reason', '')}")



def main():
    parser = argparse.ArgumentParser(description="EWY Orchestrator")
    parser.add_argument("--no-update", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    try:
        do_update = not (args.no_update or args.test)
        run(do_update=do_update)
    except Exception as e:
        log.error(f"Orchestrator error: {e}", exc_info=True)
        notifier.send_alert("EWY Orchestrator Error", str(e), level="ERROR")
        raise


if __name__ == "__main__":
    main()
