import os
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import json
import re

# --- 1. CONFIGURATION ---
BASE_DIR = '.'  

METRICS_DIR = os.path.join('plots', 'metrics')
PERF_DIR = os.path.join('plots', 'perf')
LOCK_DIR = os.path.join('plots', 'lock-info')

BACKENDS = ['posix', 'io_uring']
# Updated to include 5 based on your filenames
SCALING_FACTORS = [1, 5, 10, 100] 
THREADS = [2, 4, 8, 16]
TARGET_QUERIES = [6, 9, 13, 18]

# --- 2. ACADEMIC STYLE ---
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif", "serif"],
    "font.size": 11,
    "axes.labelsize": 12,
    "legend.fontsize": 11,
    "svg.fonttype": "none",
    "figure.autolayout": False
})

# --- 3. DATA LOADERS ---

def load_csv_data():
    all_data = []
    print(f"Scanning CSVs in: {os.path.abspath(BASE_DIR)}")

    for backend in BACKENDS:
        for sf in SCALING_FACTORS:
            for t in THREADS:
                folder = f"results-{backend}-sf{sf}-t{t}"
                path = os.path.join(BASE_DIR, folder, "timings.csv")

                if os.path.exists(path):
                    try:
                        df = pd.read_csv(path, sep=';')
                        df['backend'] = backend
                        df['sf'] = sf
                        df['threads'] = t
                        all_data.append(df)
                    except Exception as e:
                        print(f"  [!] CSV Error {folder}: {e}")

    if not all_data:
        return pd.DataFrame()
    
    df = pd.concat(all_data, ignore_index=True)
    df['Backend'] = df['backend'].replace({'posix': 'Posix', 'io_uring': 'io_uring'})
    return df

def load_perf_data():
    all_perf = []
    print(f"Scanning Perf logs in: {os.path.abspath(BASE_DIR)}")

    for backend in BACKENDS:
        for sf in SCALING_FACTORS:
            for t in THREADS:
                folder_name = f"results-{backend}-sf{sf}-t{t}"
                perf_dir = os.path.join(BASE_DIR, folder_name, "perf")
                
                if not os.path.exists(perf_dir):
                    continue

                for q_id in TARGET_QUERIES:
                    q_str = f"q{q_id:02d}" 
                    filename = f"perf-{backend}-sf{sf}-{q_str}.txt"
                    filepath = os.path.join(perf_dir, filename)

                    if os.path.exists(filepath):
                        try:
                            metrics = {}
                            with open(filepath, 'r') as f:
                                for line in f:
                                    if not line.strip(): continue
                                    try:
                                        entry = json.loads(line)
                                        event = entry.get('event')
                                        val = float(entry.get('counter-value', 0))
                                        if event in metrics:
                                            metrics[event] = max(metrics[event], val)
                                        else:
                                            metrics[event] = val
                                    except ValueError:
                                        continue
                            
                            row = metrics
                            row['backend'] = backend
                            row['sf'] = sf
                            row['threads'] = t
                            row['query'] = q_id
                            all_perf.append(row)

                        except Exception as e:
                            print(f"  [!] Perf Error {filepath}: {e}")

    if not all_perf:
        return pd.DataFrame()

    df = pd.DataFrame(all_perf)
    df['Backend'] = df['backend'].replace({'posix': 'Posix', 'io_uring': 'io_uring'})

    if 'instructions:u' in df and 'cycles' in df:
        df['ipc'] = df['instructions:u'] / df['cycles']
    if 'L1-dcache-load-misses' in df and 'L1-dcache-loads' in df:
        df['l1_miss_ratio'] = (df['L1-dcache-load-misses'] / df['L1-dcache-loads']) * 100
    if 'LLC-load-misses' in df and 'LLC-loads' in df:
        df['llc_miss_ratio'] = (df['LLC-load-misses'] / df['LLC-loads']) * 100

    return df

def load_lock_data():
    """
    Parses 'perf-lock-report-*.txt' files.
    Reads Count, Avg, Min, Max directly from the table columns.
    """
    all_locks = []
    print(f"Scanning Lock reports in: {os.path.abspath(BASE_DIR)}")
    
    debug_printed = False

    for backend in BACKENDS:
        for sf in SCALING_FACTORS:
            for t in THREADS:
                folder_name = f"results-{backend}-sf{sf}-t{t}"
                lock_dir = os.path.join(BASE_DIR, folder_name, "perf_lock")
                
                if not os.path.exists(lock_dir):
                    continue

                for q_id in TARGET_QUERIES:
                    q_str = f"q{q_id:02d}"
                    filename = f"perf-lock-report-{backend}-sf{sf}-{q_str}.txt"
                    filepath = os.path.join(lock_dir, filename)

                    # Debug: verify path for specific file
                    if not debug_printed and sf == 5 and q_id == 6:
                        print(f"  [DEBUG] Checking: {filepath}")
                        debug_printed = True

                    if os.path.exists(filepath):
                        try:
                            with open(filepath, 'r') as f:
                                for line in f:
                                    line = line.strip()
                                    
                                    # CASE A: Explicit "No contention"
                                    if "No contention detected" in line:
                                        all_locks.append({
                                            'backend': backend, 'sf': sf, 'threads': t, 'query': q_id,
                                            'function': 'None',
                                            'count': 0, 'avg_ms': 0.0, 'min_ms': 0.0, 'max_ms': 0.0
                                        })
                                        break 

                                    # CASE B: Standard Table Row (look for pipe)
                                    if "|" in line:
                                        parts = [p.strip() for p in line.split('|') if p.strip()]
                                        
                                        # Expecting: Func | Count | Avg | Min | Max
                                        if len(parts) >= 5:
                                            try:
                                                # Parse numbers directly
                                                count = int(parts[1])
                                                avg_ms = float(parts[2])
                                                min_ms = float(parts[3])
                                                max_ms = float(parts[4])
                                                func_name = parts[0]

                                                if count > 0:
                                                    all_locks.append({
                                                        'backend': backend,
                                                        'sf': sf,
                                                        'threads': t,
                                                        'query': q_id,
                                                        'function': func_name,
                                                        'count': count,
                                                        'avg_ms': avg_ms,
                                                        'min_ms': min_ms,
                                                        'max_ms': max_ms
                                                    })
                                            except ValueError:
                                                continue # Header row
                                
                        except Exception as e:
                            print(f"  [!] Lock Parse Error {filepath}: {e}")

    if not all_locks:
        print("  [Warning] No lock data found.")
        return pd.DataFrame()
    else:
        print(f"  [Success] Parsed {len(all_locks)} lock events.")

    df = pd.DataFrame(all_locks)
    df['Backend'] = df['backend'].replace({'posix': 'Posix', 'io_uring': 'io_uring'})
    return df

# --- 4. PLOTTING HELPER ---

def _create_academic_plot(df, y_col, y_label, title_suffix, filename_prefix, target_folder):
    """Generates and saves a faceted bar chart."""
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    sns.set_palette(["#4c72b0", "#dd8452"]) 

    for q_id in TARGET_QUERIES:
        query_df = df[df['query'] == q_id]

        if query_df.empty or y_col not in query_df or query_df[y_col].sum() == 0:
             continue

        g = sns.catplot(
            data=query_df,
            kind="bar",
            x="threads",
            y=y_col,
            hue="Backend",
            col="sf",
            sharey=False,
            height=3.5,
            aspect=0.85,
            legend=True,
            edgecolor="black",
            linewidth=0.8
        )

        g.set_axis_labels("Number of Threads", y_label)
        g.set_titles("SF {col_name}")
        g.set(ylim=(0, None)) 

        g.fig.suptitle(f"TPC-H Query {q_id}: {title_suffix}", y=1.1, fontsize=14, weight='bold')
        
        handles, labels = g.axes[0][0].get_legend_handles_labels()
        g.fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.05), ncol=2, frameon=False)

        g.despine(left=True)
        plt.subplots_adjust(top=0.85)
        
        filename = f"{filename_prefix}_query_{q_id}.svg"
        save_path = os.path.join(target_folder, filename)
        g.savefig(save_path, format='svg', bbox_inches='tight')
        print(f"Generated: {save_path}")

def plot_lock_metrics(df):
    """
    Creates 4 separate plots for Count, Avg, Min, and Max.
    Aggregates per run (in case multiple functions contended) using standard logic.
    """
    if df.empty: return
    
    print(f"\n--- Generating Lock Info Plots in '{LOCK_DIR}' ---")
    
    # Aggregate data so we have 1 row per experiment run.
    # Count -> Sum
    # Avg -> Max (We show the worst average wait seen)
    # Min -> Min (Best case)
    # Max -> Max (Worst case)
    agg_df = df.groupby(['backend', 'Backend', 'sf', 'threads', 'query'], as_index=False).agg({
        'count': 'sum',
        'avg_ms': 'max', 
        'min_ms': 'min',
        'max_ms': 'max'
    })

    # 1. Lock Contention Count
    _create_academic_plot(agg_df, 'count', 'Contention Count', 'Lock Contention Count', 'lock_count', LOCK_DIR)

    # 2. Average Wait Time
    _create_academic_plot(agg_df, 'avg_ms', 'Avg Wait Time (ms)', 'Avg Lock Wait Time', 'lock_wait_avg', LOCK_DIR)

    # 3. Minimum Wait Time
    _create_academic_plot(agg_df, 'min_ms', 'Min Wait Time (ms)', 'Min Lock Wait Time', 'lock_wait_min', LOCK_DIR)

    # 4. Maximum Wait Time
    _create_academic_plot(agg_df, 'max_ms', 'Max Wait Time (ms)', 'Max Lock Wait Time', 'lock_wait_max', LOCK_DIR)

# --- 5. EXECUTION ---
if __name__ == "__main__":
    
    # 1. METRICS
    csv_df = load_csv_data()
    if not csv_df.empty:
        print(f"\n--- Generating Standard Metrics in '{METRICS_DIR}' ---")
        _create_academic_plot(csv_df, 'elapsed_ms', 'Latency (ms)', 'Latency', 'latency', METRICS_DIR)
        _create_academic_plot(csv_df, 'read_mb_s', 'Read Throughput (MB/s)', 'Read Throughput', 'read_throughput', METRICS_DIR)
        _create_academic_plot(csv_df, 'write_mb_s', 'Write Throughput (MB/s)', 'Write Throughput', 'write_throughput', METRICS_DIR)

    # 2. PERF
    perf_df = load_perf_data()
    if not perf_df.empty:
        print(f"\n--- Generating Perf Metrics in '{PERF_DIR}' ---")
        _create_academic_plot(perf_df, 'ipc', 'IPC (Ins/Cycle)', 'Instructions Per Cycle', 'perf_ipc', PERF_DIR)
        _create_academic_plot(perf_df, 'context-switches', 'Count', 'Context Switches', 'perf_context_switches', PERF_DIR)
        _create_academic_plot(perf_df, 'l1_miss_ratio', 'Miss Ratio (%)', 'L1 Cache Miss Ratio', 'perf_l1_misses', PERF_DIR)
        _create_academic_plot(perf_df, 'llc_miss_ratio', 'Miss Ratio (%)', 'LLC Cache Miss Ratio', 'perf_llc_misses', PERF_DIR)
        _create_academic_plot(perf_df, 'block:block_rq_complete', 'Count', 'Block Layer Completions', 'perf_block_complete', PERF_DIR)
        _create_academic_plot(perf_df, 'nvme:nvme_complete_rq', 'Count', 'NVMe Driver Completions', 'perf_nvme_complete', PERF_DIR)
        _create_academic_plot(perf_df, 'nvme:nvme_sq', 'Count', 'NVMe Submission Queue Updates', 'perf_nvme_sq', PERF_DIR)

    # 3. LOCKS
    lock_df = load_lock_data()
    if not lock_df.empty:
        plot_lock_metrics(lock_df)
    
    print("\nDone.")