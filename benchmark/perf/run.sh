#!/bin/bash

# ================= Configuration =================

DEFAULT_DEVICE="/dev/nvme0n1"
export NVME_DEVICE_PATH="$DEFAULT_DEVICE"

# Paths
SECRET_FILE="$HOME/.duckdb/stored_secrets/nvmefs.duckdb_secret"
SCRIPT_DIR="/home/group01/nvmefs/scripts/nvme"

# ================= Functions =================

secret_cleanup() {
    if [[ -f "$SECRET_FILE" ]]; then
        rm -f "$SECRET_FILE"
    fi
}

disk_cleanup() {
    # Ensure scripts exist before running
    if [[ -f "$SCRIPT_DIR/device_dealloc.sh" ]]; then
        bash "$SCRIPT_DIR/device_dealloc.sh"
    else
        echo "Warning: Dealloc script not found in $SCRIPT_DIR"
    fi
}

setup_environment() {
    local current_target="$1"
    echo "=== Setting up environment for target: $current_target ==="

    # Check NVMe device presence (works with or without jq)
    if command -v nvme &> /dev/null; then
        if command -v jq &> /dev/null; then
            found=$(nvme list -o json | jq -r --arg DEV "$DEFAULT_DEVICE" \
                '.Devices[] | select(.DevicePath == $DEV or .GenericPath == $DEV) | .DevicePath' || true)
        else
            # Fallback to text parsing if jq not available
            if nvme list | grep -q "$DEFAULT_DEVICE"; then
                found="$DEFAULT_DEVICE"
            else
                found=""
            fi
        fi

        if [[ -z "${found:-}" ]]; then
            echo "Warning: NVMe device $DEFAULT_DEVICE not found or not visible to `nvme` command."
            echo "Continuing — ensure the device is correct or drivers are loaded if you expect it to be present."
        else
            echo "NVMe device found: $found"
        fi
    else
        echo "Warning: 'nvme' tool not found; skipping device presence check."
    fi

    # Cleanup before starting
    echo "Performing disk cleanup..."
    disk_cleanup
    secret_cleanup

    echo "=== Setup complete for $current_target ==="
}

# ================= Main =================

scale_factors=(1 10 100)
threads_list=(1 2 4 8 16)
backend_targets=("posix" "io_uring")

echo "Starting Experiment Suite..."



for target in "${backend_targets[@]}"
do
    for sf in "${scale_factors[@]}"
    do
        setup_environment "$target"

        echo "--- STARTING SEEDING PHASE for Target=$target SF=$sf ---"

        # 2. RUN ONCE to setup secrets (Data Seeding)
        python3 -u main.py \
            --sf="$sf" \
            --target="$target" \
            --threads="1" \
            --folder="results-${target}-sf${sf}-SETUP" \
            --monitor-perf

        echo "--- SEEDING COMPLETE. STARTING BENCHMARKS ---"

        for t in "${threads_list[@]}"
        do
            OUTPUT_FOLDER="results-${target}-sf${sf}-t${t}"

            echo "Running: Target=$target | SF=$sf | Threads=$t"    

            if command -v python3 &> /dev/null; then
                echo "Executing: python3 main.py --sf=$sf --target=$target --threads="$t" --folder=results-${OUTPUT_FOLDER}-sf${sf} --monitor-perf"
                python3 -u main.py \
                    --sf="$sf" \
                    --target="$target" \
                    --threads="$t" \
                    --folder="$OUTPUT_FOLDER" \
                    --monitor-perf
            else
                echo "Error: python3 not found."
                exit 1
            fi

            echo "Finished run with sf=$sf threads=$t"
            echo "-----------------------------------"
        done
    done
done

echo "All experiments completed."