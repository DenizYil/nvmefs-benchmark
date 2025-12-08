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

    parser.add_argument(
        "--target",
        type=str,
        default="nvmefs",
        help="Target filesystem for benchmarking (default: nvmefs)",
        choices=["io_uring", "posix", "default"],
        required=True,
    )

    parser.add_argument(
        "--sf",
        type=int,
        help="TPC-H Scale Factor",
        required=True,
    )

    parser.add_argument(
        "--folder",
        type=str,
        help="Output folder for benchmark data",
        default="results",
        required=True,
    )

    args = parser.parse_args()

    if args.monitor_perf and args.monitor_default:
        print("You should not enable both --monitor-perf and --monitor-default as they can interfere with the results of each other. Run each one separately.")
        exit(1)

    if not args.monitor_perf and not args.monitor_default:
        print("No monitoring enabled. Use --monitor-perf or --monitor-default to enable monitoring.")
        exit(1)

    if not args.target:
        print('Please specify a target filesystem using --target ["io_uring", "posix", "default"].')
        exit(1)

    return args