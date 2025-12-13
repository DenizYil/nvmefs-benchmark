#!/bin/bash

# ================= Configuration =================

DEFAULT_DEVICE="/dev/nvme0n1"
export NVME_DEVICE_PATH="$DEFAULT_DEVICE"

# Paths
SECRET_FILE="$HOME/.duckdb/stored_secrets/nvmefs.duckdb_secret"
SCRIPT_DIR="/home/group01/nvmefs/scripts/nvme"
BUILD_DIR="/home/group01/nvmefs/build"

# Experiment Settings
threads_list=(1 2 4 8 16 32)
# We will set RESULTS_PARENT dynamically inside the loop
backend_targets=("io_uring")

# ================= Functions =================

# Function to switch the build folder based on the desired version ("before" or "after")
switch_build() {
    local desired_version="$1"  # "before" or "after"
    
    echo "=== Switching Build to: $desired_version ==="
    
    cd "$BUILD_DIR" || { echo "Error: Cannot access build dir $BUILD_DIR"; exit 1; }

    # LOGIC MAP:
    # State A (Current is "Before"): Exists: [release] (old code) AND [release.latest] (new code stashed)
    # State B (Current is "After"):  Exists: [release] (new code) AND [release.old] (old code stashed)

    if [[ "$desired_version" == "before" ]]; then
        # We want "Before" (No Sync) code in release/
        if [[ -d "release.old" ]]; then
            echo "Current state detected: AFTER (Sync). Swapping to BEFORE..."
            mv release release.latest   # Stash the current "After" code
            mv release.old release      # Activate the "Before" code
        elif [[ -d "release.latest" ]]; then
            echo "Current state detected: BEFORE. No change needed."
        else
            echo "Error: Ambiguous state in $BUILD_DIR. Could not find release.old or release.latest."
            exit 1
        fi

    elif [[ "$desired_version" == "after" ]]; then
        # We want "After" (Sync) code in release/
        if [[ -d "release.latest" ]]; then
            echo "Current state detected: BEFORE (No Sync). Swapping to AFTER..."
            mv release release.old      # Stash the current "Before" code
            mv release.latest release   # Activate the "After" code
        elif [[ -d "release.old" ]]; then
            echo "Current state detected: AFTER. No change needed."
        else
            echo "Error: Ambiguous state in $BUILD_DIR. Could not find release.old or release.latest."
            exit 1
        fi
    fi

    # Verify the switch resulted in a valid release folder
    if [[ ! -d "release" ]]; then
        echo "Critical Error: 'release' folder missing after switch operation."
        exit 1
    fi
    
    # Return to script dir
    cd - > /dev/null
}

secret_cleanup() {
    if [[ -f "$SECRET_FILE" ]]; then
        rm -f "$SECRET_FILE"
    fi
}

disk_cleanup() {
    if [[ -f "$SCRIPT_DIR/device_dealloc.sh" ]]; then
        bash "$SCRIPT_DIR/device_dealloc.sh"
    else
        echo "Warning: Dealloc script not found in $SCRIPT_DIR"
    fi
}

setup_environment() {
    local current_target="$1"
    echo "=== Setting up environment for target: $current_target ==="

    if command -v nvme &> /dev/null; then
        if command -v jq &> /dev/null; then
            found=$(nvme list -o json | jq -r --arg DEV "$DEFAULT_DEVICE" \
                '.Devices[] | select(.DevicePath == $DEV or .GenericPath == $DEV) | .DevicePath' || true)
        else
            if nvme list | grep -q "$DEFAULT_DEVICE"; then
                found="$DEFAULT_DEVICE"
            else
                found=""
            fi
        fi

        if [[ -z "${found:-}" ]]; then
            echo "Warning: NVMe device $DEFAULT_DEVICE not found."
        else
            echo "NVMe device found: $found"
        fi
    fi

    echo "Performing disk cleanup..."
    disk_cleanup
    secret_cleanup
    echo "=== Setup complete for $current_target ==="
}

run_benchmark_set() {
    local sf="$1"
    local mem="$2"
    local target="$3"
    local parent_dir="$4" # Pass parent dir dynamically

    echo ">>> Starting Benchmarks: Target=$target | SF=$sf | Mem=${mem}MB"

    for t in "${threads_list[@]}"
    do
        SUB_FOLDER="results-${target}-sf${sf}-mem${mem}-t${t}"
        FULL_PATH="$parent_dir/$SUB_FOLDER"

        echo "Running: Threads=$t (Out: $SUB_FOLDER)"

        if command -v python3 &> /dev/null; then
            python3 -u main.py \
                --sf="$sf" \
                --target="$target" \
                --threads="$t" \
                --memory="$mem" \
                --folder="$FULL_PATH" \
                --monitor-perf
        else
            echo "Error: python3 not found."
            exit 1
        fi
        echo "Finished run."
    done
    echo "-----------------------------------"
}

# ================= Main =================

echo "Starting Experiment Suite..."

# We iterate over the two versions: Before (No Sync) and After (Sync)
build_versions=("before" "after")

for build_ver in "${build_versions[@]}"
do
    # 1. Switch the binary
    switch_build "$build_ver"
    
    # 2. Define a unique parent folder for this build version
    # Example: "results-before" vs "results-after"
    CURRENT_RESULTS_PARENT="results-${build_ver}"
    
    if [[ ! -d "$CURRENT_RESULTS_PARENT" ]]; then
        mkdir -p "$CURRENT_RESULTS_PARENT"
    fi
    
    echo "=========================================================="
    echo " STARTING EXPERIMENTS FOR BUILD: $build_ver"
    echo " Output Directory: $CURRENT_RESULTS_PARENT"
    echo "=========================================================="

    for target in "${backend_targets[@]}"
    do
        # ==========================================
        # SCENARIO 1: SF 10
        # ==========================================
        setup_environment "$target"

        sf=10
        echo "--- [SF $sf] SEEDING PHASE (Target=$target) ---"
        SETUP_FOLDER="$CURRENT_RESULTS_PARENT/results-${target}-sf${sf}-SETUP"
        
        python3 -u main.py --sf="$sf" --target="$target" --threads="1" --folder="$SETUP_FOLDER" --monitor-perf
        run_benchmark_set "$sf" "1000" "$target" "$CURRENT_RESULTS_PARENT"
        run_benchmark_set "$sf" "2000" "$target" "$CURRENT_RESULTS_PARENT"

        # ==========================================
        # SCENARIO 2: SF 100
        # ==========================================
        setup_environment "$target" # CLEAN before SF100

        sf=100
        echo "--- [SF $sf] SEEDING PHASE (Target=$target) ---"
        SETUP_FOLDER="$CURRENT_RESULTS_PARENT/results-${target}-sf${sf}-SETUP"
        
        python3 -u main.py --sf="$sf" --target="$target" --threads="1" --folder="$SETUP_FOLDER" --monitor-perf
        
        # Run both memory limits
        run_benchmark_set "$sf" "4000" "$target" "$CURRENT_RESULTS_PARENT"
        run_benchmark_set "$sf" "8000" "$target" "$CURRENT_RESULTS_PARENT"

        #==========================================
        # SCENARIO 3: SF 100 (with default mem limit 2000MB)
        #==========================================
        setup_environment "$target" # CLEAN before SF100
        sf=100
        echo "--- [SF $sf] SEEDING PHASE (Target=$target) ---"
        SETUP_FOLDER="$CURRENT_RESULTS_PARENT/results-${target}-sf${sf}-SETUP"
        python3 -u main.py --sf="$sf" --target="$target" --threads="1" --folder="$SETUP_FOLDER" --monitor-perf
        run_benchmark_set "$sf" "2000" "$target" "$CURRENT_RESULTS_PARENT

        echo "--- Finished Target: $target ---"
    done
done

echo "All experiments (Before/After) completed."