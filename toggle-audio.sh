#!/usr/bin/env bash

# Find PipeWire IDs dynamically
DAC_ID=$(wpctl status | sed -n '/Sinks:/,/Sources:/p' | grep -E "ddHiFi" | grep -oE "[0-9]+" | head -n1)
SINK_ID=$(wpctl status | sed -n '/Filters:/,/Streams:/p' | grep -E "dual_monitor_sink" | grep -oE "[0-9]+" | head -n1)

if [ -z "$DAC_ID" ] || [ -z "$SINK_ID" ]; then
    echo "Error: Could not locate DAC or Custom Sink IDs."
    exit 1
fi

# Find which one is currently set as default
CURRENT_DEFAULT=$(wpctl status | grep -E "\*" | head -n1)

if echo "$CURRENT_DEFAULT" | grep -q "$DAC_ID"; then
    echo "Switching to Custom Speakers (dual_monitor_sink: ID $SINK_ID)..."
    wpctl set-default "$SINK_ID"
else
    echo "Switching to IEMs/DAC (ddHiFi: ID $DAC_ID)..."
    wpctl set-default "$DAC_ID"
fi
