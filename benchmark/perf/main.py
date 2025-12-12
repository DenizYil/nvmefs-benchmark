import os
import time

from database import QuackDatabase, SPDKDatabase, ConnectionConfig, NvmeDatabase
from perf_monitor import PerfMonitor
from args import parse_args

TPCH_INPUT_DIR = "."


# Queries to test: 6 (baseline), others (heavy)
TARGET_QUERIES = [6, 9, 13, 18]
MEMORY_MB = 2000

DEFAULT_DEVICE = "/dev/nvme0n1"
NG_DEVICE = "*dev/ng0n1"
DEFAULT_BACKEND = "posix"

BACKEND_DEVICE_MAP = {
    "posix": DEFAULT_DEVICE,
    "io_uring": NG_DEVICE
}

def get_disk_stats(device_name):
    """
    Reads cumulative sectors read/written from /proc/diskstats.
    Returns tuple (read_bytes, write_bytes).
    
    Handles 'nvme0n1' correctly.
    """
    # Clean the device name (e.g., "/dev/nvme0n1" -> "nvme0n1")
    if "/" in device_name:
        device_name = device_name.split("/")[-1]
        print(f"Monitoring device: {device_name}")
    
    # 2. Handle wildcard if present
    if "*" in device_name:
        device_name = device_name.replace("*", "")

    # Map 'ng0n1' (Char Device) -> 'nvme0n1' (Block Device)
    # The kernel only reports stats for the block device.
    if "ng0n" in device_name:
        device_name = device_name.replace("ng", "nvme")

    try:
        with open("/proc/diskstats", "r") as f:
            for line in f:
                parts = line.split()
    
                if parts[2] == device_name:  # Field 3 (index 2) is the device name
                    sectors_read = int(parts[5]) # Field 6 (index 5): sectors read
                    sectors_written = int(parts[9]) # Field 10 (index 9): sectors written
                    return sectors_read * 512, sectors_written * 512 # Sector size is 512 bytes
    except Exception as e:
        print(f"Error reading diskstats: {e}")
        pass
    return 0, 0

def main():
    args = parse_args()

    target_device_path = BACKEND_DEVICE_MAP.get(args.target, DEFAULT_DEVICE)

    DB_PATH = "nvmefs://bench.db"

    db = NvmeDatabase(
            db_path=DB_PATH,
            threads=args.threads,
            memory=MEMORY_MB,
            config=ConnectionConfig(
                device=DEFAULT_DEVICE,
                backend=args.target,
                memory=MEMORY_MB,
                use_fdp=False,
                threads=args.threads,
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

    print(f"{'Query':<5} {'Time(ms)':<10} {'Read(MB/s)':<12} {'Write(MB/s)':<12}")

    pid = os.getpid()
    results = []

    for q in TARGET_QUERIES:
        try:
            # 1. Measure Bytes Before
            start_r, start_w = get_disk_stats(target_device_path)

            # 2. Start Timer & Perf
            if perf:
                with perf.profile(pid, q):
                    start = time.perf_counter()
                    db.tpch(q)
                    end = time.perf_counter()
            else:
                start = time.perf_counter()
                db.tpch(q)
                end = time.perf_counter()

            # 3. Measure Bytes After
            end_r, end_w = get_disk_stats(target_device_path)

            # 4. Calculate Metrics
            seconds = end - start
            elapsed_ms = seconds * 1000.0

           # Calculate Throughput (Diff / Time)
            read_diff = end_r - start_r
            write_diff = end_w - start_w

            read_mb_sec = (read_diff / (1024 * 1024)) / seconds if seconds > 0 else 0
            write_mb_sec = (write_diff / (1024 * 1024)) / seconds if seconds > 0 else 0
            
            results.append((q, elapsed_ms, read_mb_sec, write_mb_sec))

            # Print formatted row
            print(f"Q{q:02d}    {elapsed_ms:8.3f}   {read_mb_sec:8.2f}     {write_mb_sec:8.2f}")

            # Update total time
            total_s = sum(ms for _, ms, _, _ in results) / 1000.0
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
        f.write("query;elapsed_ms;read_mb_s;write_mb_s\n")
        for q, ms, r_tput, w_tput in results:
            f.write(f"{q};{ms:.3f};{r_tput:.2f};{w_tput:.2f}\n")

    if os.path.exists("./profile.json"):
        os.remove("./profile.json")

if __name__ == "__main__":
    main()
