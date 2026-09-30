"""Bounded disposable CPU worker for the controlled VM benchmark only."""
import argparse
import math
import os
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-seconds', type=float, required=True)
    parser.add_argument('--max-wall-seconds', type=float, default=60)
    parser.add_argument('--cpu', type=int)
    args = parser.parse_args()
    if not math.isfinite(args.cpu_seconds) or not 0 < args.cpu_seconds <= 300:
        parser.error('cpu-seconds must be finite in (0, 300]')
    if not math.isfinite(args.max_wall_seconds) or not 0 < args.max_wall_seconds <= 600:
        parser.error('max-wall-seconds must be finite in (0, 600]')
    if args.cpu is not None:
        os.sched_setaffinity(0, {args.cpu})
    started_cpu = time.process_time()
    deadline = time.monotonic() + args.max_wall_seconds
    value = 1
    while time.process_time() - started_cpu < args.cpu_seconds:
        if time.monotonic() > deadline:
            return 2
        for _ in range(10000):
            value = (value * 1664525 + 1013904223) & 0xFFFFFFFF
    print(value)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
