#!/usr/bin/env bash

# Read the output list once and only use audio nodes, not devices or sources.
if ! STATUS=$(wpctl status); then
    echo "Error: Could not read PipeWire audio status." >&2
    exit 1
fi

find_output_id() {
    LC_ALL=C awk -v name="$1" -v filters_allowed="$2" '
        /^Audio[[:space:]]*$/ { audio = 1; next }
        audio && /^[^[:space:]]/ { audio = 0 }
        !audio { next }
        /Sinks:[[:space:]]*$/ { section = "sinks"; next }
        /Filters:[[:space:]]*$/ { section = "filters"; next }
        /(Devices|Sources|Streams):[[:space:]]*$/ { section = ""; next }
        section == "sinks" || (filters_allowed && section == "filters") {
            if (match($0, /[0-9]+[.][[:space:]]+/) && index(substr($0, RSTART + RLENGTH), name)) {
                id = substr($0, RSTART, RLENGTH)
                sub(/[.].*/, "", id)
                seen[id] = 1
            }
        }
        END {
            for (id in seen) { result = id; count++ }
            if (count == 1) print result
        }
    ' <<< "$STATUS"
}

DAC_ID=$(find_output_id ddHiFi 0)
SINK_ID=$(find_output_id dual_monitor_sink 1)
if [[ -z "$DAC_ID" || -z "$SINK_ID" || "$DAC_ID" == "$SINK_ID" ]]; then
    echo "Error: Could not uniquely find both audio outputs. No change made." >&2
    exit 1
fi

# Inspect the canonical default instead of using the first '*' anywhere in status.
if ! DEFAULT_INFO=$(wpctl inspect '@DEFAULT_AUDIO_SINK@'); then
    echo "Error: Could not read the current default audio output. No change made." >&2
    exit 1
fi
if [[ "$DEFAULT_INFO" =~ ^id[[:space:]]+([0-9]+), ]]; then
    DEFAULT_ID="${BASH_REMATCH[1]}"
else
    echo "Error: Could not identify the current default audio output. No change made." >&2
    exit 1
fi

if [[ "$DEFAULT_ID" == "$DAC_ID" ]]; then
    echo "Switching to Custom Speakers (dual_monitor_sink: ID $SINK_ID)..."
    wpctl set-default "$SINK_ID"
else
    echo "Switching to IEMs/DAC (ddHiFi: ID $DAC_ID)..."
    wpctl set-default "$DAC_ID"
fi
