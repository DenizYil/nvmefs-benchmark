import os
import subprocess
import signal
import time
import statistics
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
         "-e", "syscalls:sys_exit_futex",
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
      data_filename = f"perf-lock-{self.target}-sf{self.scale_factor}-q{q:02d}.data"
      input_file = os.path.join(self.perf_data_dir, data_filename)

      if not os.path.exists(input_file):
            print(f"[ERROR] Data file missing: {input_file}")
            return

      report_filename = f"perf-lock-report-{self.target}-sf{self.scale_factor}-q{q:02d}.txt"
      output_txt = os.path.join(self.perf_lock_dir, report_filename)
      
      print(f"Generating latency report (ms) for Q{q:02d}...")

      # -F fields: comm, pid, tid, time, event, ip, sym
      cmd = ["perf", "script", "-i", input_file, "--demangle", "-F", "comm,pid,tid,time,event,ip,sym"]
      
      TARGET_CLASS = "TemporaryFileMetadataManager"
      
      inflight_waits = {} # { tid: start_timestamp_seconds }
      thread_blame = {}   # { tid: "FunctionName" }
      results = {}        # { func_name: [duration_ms, duration_ms, ...] }

      try:
          # bufsize=1 enables line buffering for memory efficiency
          process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
          
          current_tid = None
          current_event = None
          current_timestamp = 0.0
          
          for line in process.stdout:
              line = line.strip()
              if not line: continue

              # 1. HEADER LINE DETECTION (Contains timestamp and event type)
              if "syscalls:" in line:
                  parts = line.split()
                  try:
                      # Find timestamp (looks like 12345.6789:)
                      time_str = next(s for s in parts if ":" in s and "." in s).strip(":")
                      current_timestamp = float(time_str)
                      
                      # Find TID (looks like 1234/5678 or just 5678)
                      tid_part = next(s for s in parts if s.replace('/','').isdigit())
                      current_tid = tid_part.split('/')[-1]
                      
                      if "sys_enter_futex" in line:
                          current_event = "ENTER"
                          inflight_waits[current_tid] = current_timestamp
                          
                      elif "sys_exit_futex" in line:
                          current_event = "EXIT"
                          # Calculate Duration if we were tracking this thread
                          if current_tid in inflight_waits and current_tid in thread_blame:
                              start_time = inflight_waits.pop(current_tid)
                              func_name = thread_blame.pop(current_tid)
                              
                              # CONVERSION: Seconds -> Milliseconds
                              duration_ms = (current_timestamp - start_time) * 1_000.0
                              
                              if func_name not in results: results[func_name] = []
                              results[func_name].append(duration_ms)
                          
                          # Clean up if it wasn't our target class
                          if current_tid in inflight_waits: del inflight_waits[current_tid]
                          
                  except StopIteration:
                      continue

              # 2. STACK LINE DETECTION (Contains function names)
              elif current_event == "ENTER" and TARGET_CLASS in line:
                  # We found the function causing the wait!
                  
                  # Extract just the function name (e.g., "TemporaryFileMetadataManager::GetLBA")
                  start_idx = line.find(TARGET_CLASS)
                  end_idx = line.find("(", start_idx)
                  if end_idx == -1: end_idx = len(line)
                  
                  func_name = line[start_idx:end_idx].strip()
                  
                  # Blame this function for the current wait on this thread
                  thread_blame[current_tid] = func_name
                  
                  # Stop parsing stack for this event
                  current_event = None 

          process.wait()

      except Exception as e:
          print(f"[ERROR] Parsing failed: {e}")
          return

      # 3. WRITE REPORT
      with open(output_txt, "w") as f:
          f.write(f"Lock Latency Report: {TARGET_CLASS}\n")
          f.write(f"Query: Q{q:02d}\n")
          f.write("=" * 110 + "\n")
          # Header adjusted for ms
          header = f"{'Function':<55} | {'Count':<6} | {'Avg (ms)':<10} | {'Min (ms)':<10} | {'Max (ms)':<10}"
          f.write(header + "\n")
          f.write("-" * 110 + "\n")
          
          if not results:
              f.write("No contention detected for this class.\n")
          else:
              for func, durations in results.items():
                  count = len(durations)
                  avg_wait = statistics.mean(durations)
                  min_wait = min(durations)
                  max_wait = max(durations)
                  
                  # Formatted to 3 decimal places (e.g., 0.125 ms)
                  f.write(f"{func:<55} | {count:<6} | {avg_wait:<10.3f} | {min_wait:<10.3f} | {max_wait:<10.3f}\n")

      print(f"Report saved to: {output_txt}")
      
      # 4. CLEANUP
      if os.path.exists(input_file):
          os.remove(input_file)
          print(f"[CLEANUP] Deleted raw data file.")

   
 
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



