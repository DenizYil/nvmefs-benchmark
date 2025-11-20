import os
import signal
import subprocess
import time

from database import QuackDatabase 

TPCH_INPUT_DIR = "."
SCALE_FACTOR = 5
DB_PATH = "bench.duckdb"

THREADS = 16
MEMORY_MB = 32768

ENABLE_FLAMEGRAPHS = True
FLAMEGRAPH_DIR = "./FlameGraph"
PERF_FREQ = 500
PERF_DATA_DIR = "perf_record"
FLAMEGRAPH_OUT_DIR = "flamegraphs"

PERF_EVENTS = [
    "cycles,instructions",
    "cache-references,cache-misses",
    "L1-dcache-loads,L1-dcache-load-misses",
    "LLC-loads,LLC-load-misses",
    "branch-instructions,branch-misses",
    "context-switches,cpu-migrations",
    "page-faults",
    "dTLB-load-misses,iTLB-load-misses",
]


def ensure_dirs():
    os.makedirs("perf2", exist_ok=True)
    if ENABLE_FLAMEGRAPHS:
        os.makedirs(PERF_DATA_DIR, exist_ok=True)
        os.makedirs(FLAMEGRAPH_OUT_DIR, exist_ok=True)


def start_perf_stat(pid: int, q: int, events):
    output_file = f"perf2/perf_stat_q{q:02d}.txt"

    perf_cmd = [
        "perf", "stat",
        "-d",
        "-o", output_file,
        "-p", str(pid),
    ]

    for ev in events:
        perf_cmd += ["-e", ev]

    proc = subprocess.Popen(perf_cmd)
    print(proc)
    print(f"Started perf stat for Q{q:02d}: PID={proc.pid}")
    return proc, output_file


def start_perf_record(pid: int, q: int):
    perf_data = os.path.join(PERF_DATA_DIR, f"perf_q{q:02d}.data")

    cmd = [
        "perf", "record",
        "-F", str(PERF_FREQ),
        "--call-graph", "dwarf",
        "-o", perf_data,
        "-p", str(pid),
    ]

    proc = subprocess.Popen(cmd)
    print(f"Started perf record for Q{q:02d}: PID={proc.pid}, data={perf_data}")
    return proc, perf_data


def stop_proc(proc: subprocess.Popen, name: str):
    if proc is None:
        return
    try:
        proc.send_signal(signal.SIGINT)
    except ProcessLookupError:
        # It might already be gone; ignore.
        return
    proc.wait()
    print(f"{name} stopped (pid={proc.pid})")

def generate_flamegraph(perf_data_path: str, svg_out_path: str, title: str):
    stackcollapse = os.path.join(FLAMEGRAPH_DIR, "stackcollapse-perf.pl")
    flamegraph = os.path.join(FLAMEGRAPH_DIR, "flamegraph.pl")

    p1 = subprocess.Popen(
        ["perf", "script", "-i", perf_data_path],
        stdout=subprocess.PIPE,
    )

    p2 = subprocess.Popen(
        [stackcollapse],
        stdin=p1.stdout,
        stdout=subprocess.PIPE,
    )

    with open(svg_out_path, "wb") as svg_file:
        p3 = subprocess.Popen(
            [flamegraph, "--title", title],
            stdin=p2.stdout,
            stdout=svg_file,
        )

        p3.wait()

    print(f"Generated flamegraph → {svg_out_path}")

def wait_for_perf_ready(perf_pid, timeout=3):
    """Wait until perf has opened its perf_event file descriptors."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            fds = os.listdir(f"/proc/{perf_pid}/fd")
        except FileNotFoundError:
            return False 

        if len(fds) > 3:
            return True
        time.sleep(0.01)
    return False

def main():
    ensure_dirs()

    db = QuackDatabase(
        db_path=DB_PATH,
        threads=THREADS,
        memory=MEMORY_MB,
    )

    # db.setup_tpch(
    #     input_dir_path=TPCH_INPUT_DIR,
    #     scale_factor=SCALE_FACTOR,
    # )

    pid = os.getpid()
    results = []

    events = [ev + ":u" for ev in PERF_EVENTS]

    try:
        for q in range(1, 23):

            if ENABLE_FLAMEGRAPHS:
                perf_rec_proc, perf_data_path = start_perf_record(pid, q)
                
                time.sleep(1.0)
            else:
                perf_rec_proc, perf_data_path = None, None

            perf_stat_proc, stat_out = start_perf_stat(pid, q, events)

            try:
                start = time.perf_counter()
                db.tpch(q)
                end = time.perf_counter()

                elapsed_ms = (end - start) * 1000.0
                results.append((q, elapsed_ms))
                print(f"Q{q:02d}: {elapsed_ms:.3f} ms")

                total_s = sum(ms for _, ms in results) / 1000.0
                print(f"Total TPC-H time so far: {total_s:.3f} s")

            finally:
                stop_proc(perf_stat_proc, f"perf stat for Q{q:02d}")
                if ENABLE_FLAMEGRAPHS and perf_rec_proc is not None:
                    stop_proc(perf_rec_proc, f"perf record for Q{q:02d}")

            if perf_data_path is not None:
                svg_path = os.path.join(FLAMEGRAPH_OUT_DIR, f"flame_q{q:02d}.svg")
                generate_flamegraph(
                    perf_data_path,
                    svg_path,
                    title=f"TPC-H Q{q:02d}",
                )

            break

    finally:
        db.close()

    with open("tpch_timings.csv", "w") as f:
        f.write("query;elapsed_ms\n")
        for q, ms in results:
            f.write(f"{q};{ms:.3f}\n")


if __name__ == "__main__":
    main()
