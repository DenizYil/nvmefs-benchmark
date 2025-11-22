import os
import subprocess
import signal
import time
from contextlib import contextmanager

ENABLE_FLAMEGRAPHS = True
FLAMEGRAPH_DIR = "./FlameGraph"
PERF_FREQ = 500

PERF_EVENTS = [
    "cycles,instructions",
    "cache-references,cache-misses",
    "L1-dcache-loads,L1-dcache-load-misses",
    "LLC-loads,LLC-load-misses",
    "branch-instructions,branch-misses",
    "context-switches,cpu-migrations",
    "page-faults",
    "dTLB-load-misses,iTLB-load-misses",

    "mem_load_retired.l3_miss,mem_load_retired.local_dram",
]

# DIRECTORIES
PERF_STAT_OUTPUT_DIR = "results/perf"
PERF_DATA_DIR = "results/perf_record"
FLAMEGRAPH_OUT_DIR = "results/flamegraphs"

class PerfMonitor:
    def __init__(self, scale_factor: int):
        self.scale_factor = scale_factor
        self.perf_stat = None
        self.perf_record = None
        self.perf_record_data_path = None
        self.ensure_dirs()

    def ensure_dirs(self):
        os.makedirs("results", exist_ok=True)
        os.makedirs(PERF_STAT_OUTPUT_DIR, exist_ok=True)
        if ENABLE_FLAMEGRAPHS:
            os.makedirs(PERF_DATA_DIR, exist_ok=True)
            os.makedirs(FLAMEGRAPH_OUT_DIR, exist_ok=True)


    def start_perf_stat(self, pid: int, q: int) -> None:
        output_file = f"{PERF_STAT_OUTPUT_DIR}/perf-sf{self.scale_factor}-q{q:02d}.txt"

        perf_cmd = [
            "perf", "stat",
            "-d",
            "-o", output_file,
            "--json",
            "-p", str(pid),
        ]

        events = [ev + ":u" for ev in PERF_EVENTS]

        for ev in events:
            perf_cmd += ["-e", ev]

        self.perf_stat = subprocess.Popen(perf_cmd)
        print(f"Started perf stat for Q{q:02d}: PID={self.perf_stat.pid}")

    def start_perf_record(self, pid: int, q: int) -> None:
        self.perf_record_data_path = os.path.join(PERF_DATA_DIR, f"perf-sf{self.scale_factor}-q{q:02d}.data")

        cmd = [
            "perf", "record",
            "-F", str(PERF_FREQ),
            "--call-graph", "dwarf",
            "-o", self.perf_record_data_path,
            "-p", str(pid),
        ]

        self.perf_record = subprocess.Popen(cmd)

        # must sleep 1 second, otherwise perf record doesn't start and queries finish first
        # maybe we can figure out a better way, but this works for now.
        time.sleep(1.0)

        print(f"Started perf record for Q{q:02d}: PID={self.perf_record.pid}, data={self.perf_record_data_path}")
    
    def stop_perf_stat(self) -> None:
        self.stop_proc(self.perf_stat, "perf stat")

    def stop_perf_record(self) -> None:
        self.stop_proc(self.perf_record, "perf record")

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


    @contextmanager
    def profile(self, pid: int, q: int):
        try:
            if ENABLE_FLAMEGRAPHS:
                self.start_perf_record(pid, q)

            self.start_perf_stat(pid, q)
            yield
        finally:
            self.stop_perf_stat()
            if ENABLE_FLAMEGRAPHS:
                self.stop_perf_record()
                self.generate_flamegraph(q)


    def generate_flamegraph(self, q: int):
        stackcollapse = os.path.join(FLAMEGRAPH_DIR, "stackcollapse-perf.pl")
        flamegraph = os.path.join(FLAMEGRAPH_DIR, "flamegraph.pl")
        svg_path = os.path.join(FLAMEGRAPH_OUT_DIR, f"flame_q{q:02d}.svg")
        title = f"TPC-H Q{q:02d}"

        p1 = subprocess.Popen(
            ["perf", "script", "-i", self.perf_record_data_path],
            stdout=subprocess.PIPE,
        )

        p2 = subprocess.Popen(
            [stackcollapse],
            stdin=p1.stdout,
            stdout=subprocess.PIPE,
        )

        with open(svg_path, "wb") as svg_file:
            p3 = subprocess.Popen(
                [flamegraph, "--title", title],
                stdin=p2.stdout,
                stdout=svg_file,
            )

            p3.wait()

        print(f"Generated flamegraph → {svg_path}")