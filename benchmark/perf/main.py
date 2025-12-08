import os
import time

from database import QuackDatabase, SPDKDatabase, ConnectionConfig, NvmeDatabase
from perf_monitor import PerfMonitor
from args import parse_args

TPCH_INPUT_DIR = "."

THREADS = 1
MEMORY_MB = 2000

DEFAULT_DEVICE = "/dev/nvme0n1"
NG_DEVICE = "*dev/ng0n1"
DEFAULT_BACKEND = "posix"

BACKEND_DEVICE_MAP = {
    "posix": DEFAULT_DEVICE,
    "io_uring": NG_DEVICE
}

def main():
    args = parse_args()

    DB_PATH = "nvmefs://bench.db"

    db = NvmeDatabase(
            db_path=DB_PATH,
            threads=THREADS,
            memory=MEMORY_MB,
            config=ConnectionConfig(
                device=DEFAULT_DEVICE,
                backend=args.target,
                memory=MEMORY_MB,
                use_fdp=False,
                threads=THREADS,
            )
    )

    # see if tpch was setup previously
    try:
        db.query("SELECT COUNT(*) FROM customer")
        print("TPC-H data already exists; skipping setup.")
    except Exception:
        print("Setting up TPC-H data...  This may take a while depending on scaling factor.")

        db.setup_tpch(
            input_dir_path=TPCH_INPUT_DIR,
            scale_factor=args.sf,
        )
                
    perf = None
    if args.monitor_perf:
        perf = PerfMonitor(scale_factor=args.sf, target=args.target, folder=args.folder)
    
    if args.monitor_default:
        os.makedirs("results/query_profiling", exist_ok=True)
        db.enable_profiling()

    pid = os.getpid()
    results = []

    for q in range(1, 23):
        try:
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

                with open(f"{args.folder}/query_profiling/tpch-{args.target}-sf{args.sf}-q{q}-profile.json", "w") as f:
                    f.write(profile_data)
        except Exception as e:
            print(f"Q{q:02d} failed: {e}")

            import traceback
            traceback.print_exc()

    with open(f"{args.folder}/timings.csv", "w") as f:
        f.write("query;elapsed_ms\n")
        for q, ms in results:
            f.write(f"{q};{ms:.3f}\n")

    if os.path.exists("./profile.json"):
        os.remove("./profile.json")

if __name__ == "__main__":
    main()
