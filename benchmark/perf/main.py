import os
import time

from database import QuackDatabase 
from perf_monitor import PerfMonitor
from args import parse_args

TPCH_INPUT_DIR = "."
SCALE_FACTOR = 1

DB_PATH = f"bench-sf{SCALE_FACTOR}.duckdb"

THREADS = 16
MEMORY_MB = 32768

def main():
    args = parse_args()

    db = QuackDatabase(
        db_path=DB_PATH,
        threads=THREADS,
        memory=MEMORY_MB,
    )

    # see if tpch was setup previously
    try:
        db.query("SELECT COUNT(*) FROM customer")
        print("TPC-H data already exists; skipping setup.")
    except Exception:
        print("Setting up TPC-H data...  This may take a while depending on scaling factor.")

        db.setup_tpch(
            input_dir_path=TPCH_INPUT_DIR,
            scale_factor=SCALE_FACTOR,
        )
                
    perf = None
    if args.monitor_perf:
        perf = PerfMonitor(scale_factor=SCALE_FACTOR)
    
    if args.monitor_default:
        os.makedirs("results/query_profiling", exist_ok=True)
        db.enable_profiling()

    pid = os.getpid()
    results = []

    for q in range(1, 23):
        # TODO: clean
        if perf:
            with perf.profile(pid, q):
                start = time.perf_counter()
                db.tpch(q)
                end = time.perf_counter()
        else:
            start = time.perf_counter()
            db.tpch(q)
            end = time.perf_counter()

        elapsed_ms = (end - start) * 1000.0
        results.append((q, elapsed_ms))
        print(f"Q{q:02d}: {elapsed_ms:.3f} ms")

        total_s = sum(ms for _, ms in results) / 1000.0
        print(f"Total TPC-H time so far: {total_s:.3f} s")

        if args.monitor_default:
            with open("./profile.json", "r") as f:
                profile_data = f.read()

            with open(f"results/query_profiling/tpch-sf{SCALE_FACTOR}-q{q}-profile.json", "w") as f:
                f.write(profile_data)

    with open("results/tpch_timings.csv", "w") as f:
        f.write("query;elapsed_ms\n")
        for q, ms in results:
            f.write(f"{q};{ms:.3f}\n")

    if os.path.exists("./profile.json"):
        os.remove("./profile.json")

if __name__ == "__main__":
    main()
