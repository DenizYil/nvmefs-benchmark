import os
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# --- 1. CONFIGURATION ---
BASE_DIR = '.'  
OUTPUT_FOLDER = 'plots'
BACKENDS = ['posix', 'io_uring']
SCALING_FACTORS = [1, 10, 100]
THREADS = [2, 4, 8, 16]
TARGET_QUERIES = [6, 9, 13, 18]

# --- 2. ACADEMIC STYLE ---
plt.rcParams.update({
    "font.family": "serif",
    # We add 'DejaVu Serif' (built-in) and 'serif' (system default) to prevent crashes
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif", "serif"],
    "font.size": 11,
    "axes.labelsize": 12,
    "legend.fontsize": 11,
    "svg.fonttype": "none",  # Text stays as text (good for Overleaf)
    "figure.autolayout": False
})

def load_csv_data():
    """Loads all CSVs into a single DataFrame."""
    all_data = []
    print(f"Scanning directory: {os.path.abspath(BASE_DIR)}")

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
                        print(f"  [!] Error reading {folder}: {e}")

    if not all_data:
        return pd.DataFrame()
    
    # Capitalize backend names for better looking legends
    combined_df = pd.concat(all_data, ignore_index=True)
    combined_df['Backend'] = combined_df['backend'].replace({'posix': 'Posix', 'io_uring': 'io_uring'})
    
    return combined_df

def _create_academic_plot(df, y_col, y_label, title_suffix, filename_prefix):
    """
    Private helper function that handles the academic styling, 
    legend placement, and saving.
    """
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

    # High-contrast, colorblind-friendly palette
    sns.set_palette(["#4c72b0", "#dd8452"]) 

    for q_id in TARGET_QUERIES:
        query_df = df[df['query'] == q_id]

        # Skip if no data or if all values are 0 (common for Writes)
        if query_df.empty or query_df[y_col].sum() == 0:
            continue

        # Plotting
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
        
        # --- NEW FIX: Force Y-axis to start at 0 ---
        # (0, None) means start at 0, but let the top auto-scale to the data max
        g.set(ylim=(0, None)) 

        # Title & Legend
        g.fig.suptitle(f"TPC-H Query {q_id}: {title_suffix}", y=1.1, fontsize=14, weight='bold')
        
        handles, labels = g.axes[0][0].get_legend_handles_labels()
        g.fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.05), ncol=2, frameon=False)

        # Formatting & Save
        g.despine(left=True)
        plt.subplots_adjust(top=0.85)
        
        filename = f"{filename_prefix}_query_{q_id}.svg"
        save_path = os.path.join(OUTPUT_FOLDER, filename)
        g.savefig(save_path, format='svg', bbox_inches='tight')
        print(f"Generated: {save_path}")

# --- 4. PUBLIC PLOTTING FUNCTIONS ---

def plot_latency(df):
    print("\n--- Generating Latency Plots ---")
    _create_academic_plot(
        df, 
        y_col='elapsed_ms', 
        y_label='Latency (ms)', 
        title_suffix='Latency', 
        filename_prefix='latency'
    )

def plot_read_throughput(df):
    print("\n--- Generating Read Throughput Plots ---")
    _create_academic_plot(
        df, 
        y_col='read_mb_s', 
        y_label='Read Throughput (MB/s)', 
        title_suffix='Read Throughput', 
        filename_prefix='read_throughput'
    )

def plot_write_throughput(df):
    print("\n--- Generating Write Throughput Plots ---")
    _create_academic_plot(
        df, 
        y_col='write_mb_s', 
        y_label='Write Throughput (MB/s)', 
        title_suffix='Write Throughput', 
        filename_prefix='write_throughput'
    )

# --- 5. EXECUTION ---
if __name__ == "__main__":
    data = load_csv_data()
    
    if not data.empty:
        plot_latency(data)
        plot_read_throughput(data)
        plot_write_throughput(data)
        print("\nAll plots saved to 'plots/' folder.")
    else:
        print("No data found.")