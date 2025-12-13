import os
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import json

# --- 1. CONFIGURATION ---
BASE_DIR = '.'

# We check both experiment version folders
VERSIONS = ["results-before", "results-after"]

# Specific configurations: (Scale Factor, Memory Limit in MB)
CONFIGS = [
    (10, 1000),
    (10, 2000),
    (100, 4000),
    (100, 8000)
]

BACKENDS = ['io_uring']

THREADS = [1, 2, 4, 8, 16, 32]
TARGET_QUERIES = [6, 9, 13, 18]

PLOT_OUTPUT_DIR = "final_plots_iouring"
METRICS_DIR = os.path.join(PLOT_OUTPUT_DIR, 'metrics')
PERF_DIR = os.path.join(PLOT_OUTPUT_DIR, 'perf')
LOCK_DIR = os.path.join(PLOT_OUTPUT_DIR, 'lock-info')

# --- 2. ACADEMIC STYLE ---
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif", "serif"],
    "font.size": 11,
    "axes.labelsize": 12,
    "legend.fontsize": 10,
    "svg.fonttype": "none",
    "figure.autolayout": False
})

# --- 3. DATA LOADERS ---

def load_csv_data():
    all_data = []
    print(f"Scanning for Timing CSVs in: {os.path.abspath(BASE_DIR)}")

    for version in VERSIONS:
        ver_label = "Before" if "before" in version else "After"
        
        for backend in BACKENDS:
            for (sf, mem) in CONFIGS:
                for t in THREADS:
                    subfolder = f"results-{backend}-sf{sf}-mem{mem}-t{t}"
                    folder_path = os.path.join(BASE_DIR, version, subfolder)
                    path = os.path.join(folder_path, "timings.csv")

                    if os.path.exists(path):
                        try:
                            df = pd.read_csv(path, sep=';')
                            df['backend'] = backend
                            df['version'] = ver_label
                            df['sf'] = sf
                            df['memory'] = mem
                            df['config_label'] = f"SF{sf} ({mem}MB)"
                            df['threads'] = t
                            all_data.append(df)
                        except Exception as e:
                            print(f"  [!] CSV Error {folder_path}: {e}")

    if not all_data:
        return pd.DataFrame()
    
    df = pd.concat(all_data, ignore_index=True)
    df['Label'] = df.apply(lambda x: f"{x['backend']} ({x['version']})", axis=1)
    return df

def load_perf_data():
    all_perf = []
    print(f"Scanning Perf logs...")

    for version in VERSIONS:
        ver_label = "Before" if "before" in version else "After"

        for backend in BACKENDS:
            for (sf, mem) in CONFIGS:
                for t in THREADS:
                    subfolder = f"results-{backend}-sf{sf}-mem{mem}-t{t}"
                    folder_path = os.path.join(BASE_DIR, version, subfolder)
                    perf_dir = os.path.join(folder_path, "perf")
                    
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
                                            metrics[event] = max(metrics.get(event, 0), val)
                                        except ValueError:
                                            continue
                                
                                row = metrics
                                row['backend'] = backend
                                row['version'] = ver_label
                                row['sf'] = sf
                                row['memory'] = mem
                                row['config_label'] = f"SF{sf} ({mem}MB)"
                                row['threads'] = t
                                row['query'] = q_id
                                all_perf.append(row)

                            except Exception: pass

    if not all_perf:
        return pd.DataFrame()

    df = pd.DataFrame(all_perf)
    
    if 'instructions:u' in df and 'cycles' in df:
        df['ipc'] = df['instructions:u'] / df['cycles']
    if 'L1-dcache-load-misses' in df and 'L1-dcache-loads' in df:
        df['l1_miss_ratio'] = (df['L1-dcache-load-misses'] / df['L1-dcache-loads']) * 100
    if 'LLC-load-misses' in df and 'LLC-loads' in df:
        df['llc_miss_ratio'] = (df['LLC-load-misses'] / df['LLC-loads']) * 100

    df['Label'] = df.apply(lambda x: f"{x['backend']} ({x['version']})", axis=1)
    return df

def load_lock_data():
    all_locks = []
    print(f"Scanning Lock reports...")

    for version in VERSIONS:
        ver_label = "Before" if "before" in version else "After"

        for backend in BACKENDS:
            for (sf, mem) in CONFIGS:
                for t in THREADS:
                    subfolder = f"results-{backend}-sf{sf}-mem{mem}-t{t}"
                    folder_path = os.path.join(BASE_DIR, version, subfolder)
                    lock_dir = os.path.join(folder_path, "perf_lock")
                    
                    if not os.path.exists(lock_dir):
                        continue

                    for q_id in TARGET_QUERIES:
                        q_str = f"q{q_id:02d}"
                        filename = f"perf-lock-report-{backend}-sf{sf}-{q_str}.txt"
                        filepath = os.path.join(lock_dir, filename)

                        if os.path.exists(filepath):
                            try:
                                with open(filepath, 'r') as f:
                                    for line in f:
                                        line = line.strip()
                                        if "No contention detected" in line:
                                            all_locks.append({
                                                'backend': backend, 'version': ver_label,
                                                'sf': sf, 'memory': mem, 'config_label': f"SF{sf} ({mem}MB)",
                                                'threads': t, 'query': q_id, 'function': 'None',
                                                'count': 0, 'avg_ms': 0.0
                                            })
                                            break 

                                        if "|" in line:
                                            parts = [p.strip() for p in line.split('|') if p.strip()]
                                            if len(parts) >= 5:
                                                try:
                                                    count = int(parts[1])
                                                    if count > 0:
                                                        all_locks.append({
                                                            'backend': backend,
                                                            'version': ver_label,
                                                            'sf': sf,
                                                            'memory': mem,
                                                            'config_label': f"SF{sf} ({mem}MB)",
                                                            'threads': t,
                                                            'query': q_id,
                                                            'function': parts[0],
                                                            'count': count,
                                                            'avg_ms': float(parts[2])
                                                        })
                                                except ValueError:
                                                    continue
                            except: pass

    if not all_locks:
        return pd.DataFrame()

    df = pd.DataFrame(all_locks)
    df['Label'] = df.apply(lambda x: f"{x['backend']} ({x['version']})", axis=1)
    return df

# --- 4. PLOTTING HELPER ---

def _create_academic_plot(df, y_col, y_label, title_suffix, filename_prefix, target_folder):
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    # Simplified Palette
    custom_palette = {
        "io_uring (Before)": "#4c72b0",  # Standard Blue
        "io_uring (After)":  "#dd8452"   # Standard Orange
    }

    # UPDATED: Column Order to include SF10 (2000MB)
    CONFIG_ORDER = [
        "SF10 (1000MB)", 
        "SF10 (2000MB)", 
        "SF100 (4000MB)", 
        "SF100 (8000MB)"
    ]

    for q_id in TARGET_QUERIES:
        query_df = df[df['query'] == q_id]

        if query_df.empty or y_col not in query_df:
             continue
        if query_df[y_col].sum() == 0:
            continue

        g = sns.catplot(
            data=query_df,
            kind="bar",
            x="threads",
            y=y_col,
            hue="Label",
            col="config_label",
            col_order=CONFIG_ORDER, # Enforce the logical sort order
            sharey=False,
            height=3.5,
            aspect=0.9,
            legend=True,
            edgecolor="black",
            linewidth=0.8,
            palette=custom_palette
        )

        g.set_axis_labels("Number of Threads", y_label)
        g.set_titles("{col_name}")
        g.set(ylim=(0, None)) 

        g.fig.suptitle(f"TPC-H Query {q_id}: {title_suffix}", y=1.1, fontsize=14, weight='bold')
        
        filename = f"{filename_prefix}_query_{q_id}.svg"
        save_path = os.path.join(target_folder, filename)
        g.savefig(save_path, format='svg', bbox_inches='tight')
        print(f"Generated: {save_path}")

def plot_lock_metrics(df):
    if df.empty: return
    print(f"\n--- Generating Lock Info Plots ---")
    
    agg_df = df.groupby(['Label', 'config_label', 'threads', 'query'], as_index=False).agg({
        'count': 'sum',
        'avg_ms': 'max'
    })

    _create_academic_plot(agg_df, 'count', 'Contention Count', 'Lock Contention Count', 'lock_count', LOCK_DIR)
    _create_academic_plot(agg_df, 'avg_ms', 'Avg Wait (ms)', 'Avg Lock Wait Time', 'lock_wait_avg', LOCK_DIR)

# --- 5. EXECUTION ---
if __name__ == "__main__":
    
    # 1. METRICS
    csv_df = load_csv_data()
    if not csv_df.empty:
        print(f"\n--- Generating Standard Metrics ---")
        _create_academic_plot(csv_df, 'elapsed_ms', 'Latency (ms)', 'Latency', 'latency', METRICS_DIR)
        _create_academic_plot(csv_df, 'read_mb_s', 'Read Tput (MB/s)', 'Read Throughput', 'read_throughput', METRICS_DIR)
        _create_academic_plot(csv_df, 'write_mb_s', 'Write Tput (MB/s)', 'Write Throughput', 'write_throughput', METRICS_DIR)

    # 2. PERF
    perf_df = load_perf_data()
    if not perf_df.empty:
        print(f"\n--- Generating Perf Metrics ---")
        _create_academic_plot(perf_df, 'ipc', 'IPC', 'Instructions Per Cycle', 'perf_ipc', PERF_DIR)
        _create_academic_plot(perf_df, 'context-switches', 'Count', 'Context Switches', 'perf_cs', PERF_DIR)
        _create_academic_plot(perf_df, 'block:block_rq_complete', 'Count', 'Block IO Completions', 'perf_block', PERF_DIR)
        _create_academic_plot(perf_df, 'nvme:nvme_complete_rq', 'Count', 'NVMe Completions', 'perf_nvme', PERF_DIR)

    # 3. LOCKS
    lock_df = load_lock_data()
    if not lock_df.empty:
        plot_lock_metrics(lock_df)
    
    print("\nProcessing Complete.")