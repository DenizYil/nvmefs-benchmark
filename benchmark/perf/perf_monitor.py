import os
import subprocess
import signal
import time
import re
from contextlib import contextmanager

ENABLE_LOCK_PROFILING = True
PERF_FREQ = 500
STRICT_PERF_FREQ = 49

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

SSD_EVENTS = [
   "block:block_rq_issue",
   "block:block_rq_complete",
   "nvme:nvme_async_event",
   "nvme:nvme_setup_cmd",
   "nvme:nvme_complete_rq",
   "nvme:nvme_sq",
]

class PerfMonitor:
   def __init__(self, scale_factor: int, target: str, folder: str):
      self.scale_factor = scale_factor
      self.target = target
      self.folder = os.path.abspath(folder)

      self.perf_stat_dir = os.path.join(self.folder, "perf")
      self.perf_data_dir = os.path.join(self.folder, "perf_record")
      self.perf_lock_dir = os.path.join(self.folder, "perf_lock")

      self.perf_stat = None
      self.perf_record = None
      self.perf_record_data_path = None
   
      self.ensure_dirs()

   def ensure_dirs(self):
      os.makedirs(self.folder, exist_ok=True)
      os.makedirs(self.perf_stat_dir, exist_ok=True)
      os.makedirs(self.perf_data_dir, exist_ok=True)
      if ENABLE_LOCK_PROFILING:
         os.makedirs(self.perf_lock_dir, exist_ok=True)
      
   # ---------------------------------------------------------
   # 1. PERF STAT (CPU & SSD Counters)
   # ---------------------------------------------------------

   def start_perf_stat(self, pid: int, q: int) -> None:
      output_file = f"{self.perf_stat_dir}/perf-{self.target}-sf{self.scale_factor}-q{q:02d}.txt"
      
      perf_cmd = [
         "perf", "stat",
         "-d",
         "-o", output_file,
         "--json",
         "-p", str(pid),
      ]

      for ev in PERF_EVENTS:
         perf_cmd += ["-e", ev + ":u"]
      
      for ev in SSD_EVENTS: 
         perf_cmd += ["-e", ev]

      self.perf_stat = subprocess.Popen(perf_cmd)
      print(f"Started perf stat for Q{q:02d}: PID={self.perf_stat.pid}")
   
   def stop_perf_stat(self) -> None:
      self.stop_proc(self.perf_stat, "perf stat")
   
   # ---------------------------------------------------------
   # 2. PERF RECORD 
   # ---------------------------------------------------------

   def start_perf_record(self, pid: int, q: int) -> None:
      if not ENABLE_LOCK_PROFILING:
         return
      filename = f"perf-lock-{self.target}-sf{self.scale_factor}-q{q:02d}.data"
      output_file = os.path.join(self.perf_data_dir, filename)

      self.perf_record_data_path = output_file

      perf_cmd = [
         "perf", "record",
         "-p", str(pid),
         "-o", output_file,
         "-e", "syscalls:sys_enter_futex",
         "--call-graph", "dwarf,8192",
         "-m", "16M",
         "-F", str(PERF_FREQ),
      ]

      self.perf_record = subprocess.Popen(perf_cmd, stderr=subprocess.PIPE)
      
      time.sleep(1.0)
      if self.perf_record.poll() is not None:
          # It crashed immediately
          err = self.perf_record.stderr.read().decode()
          print(f"\n[CRITICAL ERROR] perf record failed to start for Q{q:02d}!")
          print(f"Command: {' '.join(perf_cmd)}")
          print(f"Error: {err}\n")
          self.perf_record = None
          return

      print(f"Started perf record (Lock Profiling) for Q{q:02d}: PID={self.perf_record.pid}")
   
   def stop_perf_record(self) -> None:
      if not self.perf_record:
         return

      print(f"Stopping perf record (PID={self.perf_record.pid})...")
      
      # 1. Polite Stop
      self.perf_record.send_signal(signal.SIGINT)
      
      stdout_data, stderr_data = None, None
      
      # 2. Wait for flush
      try:
          stdout_data, stderr_data = self.perf_record.communicate(timeout=5)
      except subprocess.TimeoutExpired:
          print("[WARNING] Perf hung during flush, forcing kill...")
          self.perf_record.kill()
          stdout_data, stderr_data = self.perf_record.communicate()
      
      print("perf record stopped.")

      # 3. Print Errors (Now this will actually work because we piped stderr above)
      if stderr_data:
          err_msg = stderr_data.decode('utf-8', errors='ignore')
          if "Error" in err_msg or "failed" in err_msg or "denied" in err_msg:
              print(f"----------------------------------------")
              print(f"[PERF ERROR DETAILS]:\n{err_msg}")
              print(f"----------------------------------------")

      # 4. Generate Report logic...
      if self.perf_record_data_path and os.path.exists(self.perf_record_data_path):
          size = os.path.getsize(self.perf_record_data_path)
          print(f"Perf data captured: {size/1024:.2f} KB")
   
   def generate_lock_report(self, q: int):
      """
      1. dumps raw stack traces using 'perf script'
      2. filters for TemporaryFileMetadataManager functions
      3. counts contention events per function
      4. writes a clean report
      5. DELETES the raw binary file to save space
      """
      # 1. Setup Paths
      data_filename = f"perf-lock-{self.target}-sf{self.scale_factor}-q{q:02d}.data"
      input_file = os.path.join(self.perf_data_dir, data_filename)

      if not os.path.exists(input_file):
            print(f"[ERROR] Data file missing: {input_file}")
            return

      report_filename = f"perf-lock-report-{self.target}-sf{self.scale_factor}-q{q:02d}.txt"
      output_txt = os.path.join(self.perf_lock_dir, report_filename)

      print(f"Generating clean lock report for Q{q:02d}...")

      # 2. Run 'perf script' to stream raw text data
      #    We use a PIPE to process line-by-line in memory (no massive intermediate text file)
      cmd = ["perf", "script", "-i", input_file, "--demangle"]
      
      from collections import Counter
      func_counts = Counter()
      
      # The class we want to focus on
      TARGET_CLASS = "TemporaryFileMetadataManager"

      try:
          # bufsize=1 enables line buffering
          process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
          
          current_event_handled = False

          # 3. Parse output line by line
          for line in process.stdout:
              # Lines starting with non-whitespace are Events (headers)
              # Lines starting with whitespace are Stack Frames
              if line and line[0].isalnum(): 
                  current_event_handled = False # New event started, reset flag
                  continue
              
              # If we haven't found the culprit for this event yet...
              if not current_event_handled:
                  # Check if this stack frame belongs to our Manager
                  if TARGET_CLASS in line:
                      # EXTRACT CLEAN FUNCTION NAME
                      # Example Line: "    7f... duckdb::TemporaryFileMetadataManager::GetLBA(std::string...)+0x1a ..."
                      
                      # 1. Find start of the class name
                      start_idx = line.find(TARGET_CLASS)
                      
                      # 2. Find end (usually at '(' for args or '+' for offset or end of line)
                      end_idx = line.find("(", start_idx)
                      if end_idx == -1: end_idx = line.find("+", start_idx)
                      if end_idx == -1: end_idx = len(line)
                      
                      # 3. Extract and Clean
                      func_name = line[start_idx:end_idx].strip()
                      
                      # Count it!
                      func_counts[func_name] += 1
                      
                      # Stop looking at this stack (we want the deepest call in the manager)
                      current_event_handled = True

          process.wait()
          if process.returncode != 0:
              print(f"[WARNING] perf script exited with error: {process.stderr.read()}")

      except Exception as e:
          print(f"[ERROR] Failed to parse perf data: {e}")
          return

      # 4. Write the Clean Report
      with open(output_txt, "w") as f:
          f.write(f"Lock Contention Report: {TARGET_CLASS}\n")
          f.write(f"Query: Q{q:02d}\n")
          f.write("=" * 60 + "\n")
          f.write(f"{'Count':<8} | {'Function'}\n")
          f.write("-" * 60 + "\n")
          
          if not func_counts:
              f.write("No lock contention detected for this class.\n")
          else:
              for func, count in func_counts.most_common():
                  f.write(f"{count:<8} | {func}\n")
      
      print(f"Report saved to: {output_txt}")

      # 5. AUTO-CLEANUP: Delete the massive binary file
      try:
          os.remove(input_file)
          print(f"[CLEANUP] Deleted raw data file: {input_file}")
      except OSError as e:
          print(f"[WARNING] Failed to delete data file: {e}")

   # def generate_lock_report(self, q: int):
   #    """Converts the binary perf data into a readable text report."""
   #    filename = f"perf-lock-{self.target}-sf{self.scale_factor}-q{q:02d}.data"

   #    input_file = os.path.join(self.perf_data_dir, filename)

   #    if not os.path.exists(input_file):
   #          print(f"[ERROR] Data file missing: {input_file}")
   #          return

   #    output_filename = f"perf-lock-report-{self.target}-sf{self.scale_factor}-q{q:02d}.txt"
   #    output_txt = os.path.join(self.perf_lock_dir, output_filename)

   #    print(f"Generating lock report for Q{q:02d}...")
   #    print(f"Reading: {input_file}")
   #    print(f"Writing: {output_txt}")

   #    with open(output_txt, "w") as f:
   #       # EXPLANATION OF FLAGS:
   #       # --stdio : Print to text file
   #       # --demangle : Convert weird C++ symbols to human names
   #       # -n : Show the exact count of samples (how many times it waited)
   #       # -g graph,0.0,caller : 
   #       #     graph  = Use a tree view
   #       #     0.0    = Show EVERYTHING (don't hide small events)
   #       #     caller = Invert the tree. Show the lock first, then indent the function that called it.
   #       subprocess.run(
   #          ["perf", "report", "-i", input_file, "--stdio", "-n", "--demangle", "-g", "graph,0.0,caller"], 
   #          stdout=f, 
   #          stderr=subprocess.STDOUT
   #       )
   #    print(f"Report saved.")
   
 
   # ---------------------------------------------------------
   # UTILS & CONTEXT MANAGER
   # ---------------------------------------------------------

   def stop_proc(self, proc: subprocess.Popen, name: str):
      if proc is None:
         return
      
      print(f"[PerfMonitor] Stopping {name} (pid={proc.pid})...")
      try:
         # SIGINT (Ctrl+C) usually best for perf to flush buffers
         proc.send_signal(signal.SIGINT)
         proc.wait(timeout=10)
      except subprocess.TimeoutExpired:
         print(f"[PerfMonitor] {name} hung, forcing kill...")
         proc.kill()
         proc.wait()
      except ProcessLookupError:
         pass # Process already died
         
      print(f"[PerfMonitor] {name} stopped.")

   @contextmanager
   def profile(self, pid: int, q: int):
      try:
         self.start_perf_record(pid, q)
         self.start_perf_stat(pid, q)
         yield
      finally:
         self.stop_perf_stat()
         self.stop_perf_record()

         if ENABLE_LOCK_PROFILING:
            self.generate_lock_report(q) 
         
         # # --- CRITICAL SAFETY FEATURE ---
         # # Delete the massive raw data file immediately
         # if self.perf_record_data_path and os.path.exists(self.perf_record_data_path):
         #    print(f"[PerfMonitor] Cleaning up raw data: {self.perf_record_data_path}")
         #    os.remove(self.perf_record_data_path)



