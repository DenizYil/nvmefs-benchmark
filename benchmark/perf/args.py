import argparse

def parse_args():
    parser = argparse.ArgumentParser(
    )

    parser.add_argument(
        "--monitor-perf",
        action="store_true",
        help="Enable performance monitoring with perf",
    )

    parser.add_argument(
        "--monitor-default",
        action="store_true",
        help="Enable default performance monitoring",
    )

    args = parser.parse_args()

    if args.monitor_perf and args.monitor_default:
        print("You should not enable both --monitor-perf and --monitor-default as they can interfere with the results of each other. Run each one separately.")
        exit(1)

    if not args.monitor_perf and not args.monitor_default:
        print("No monitoring enabled. Use --monitor-perf or --monitor-default to enable monitoring.")
        exit(1)

    return args