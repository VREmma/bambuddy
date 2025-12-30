#!/bin/bash
#
# Start Bambuddy with DEBUG logging enabled
# Use this script to debug issues like FTP/MQTT connection problems
#
# All debug messages are prefixed with:
#   [FTP-DEBUG]     - FTP/TLS connection and transfer details
#   [MQTT-DEBUG]    - MQTT connection, commands, and responses
#   [REPRINT-DEBUG] - Reprint endpoint flow
#

set -e

# Enable debug logging
export DEBUG=true
export LOG_LEVEL=DEBUG

# Optional: Log to file as well
export LOG_TO_FILE=true

echo "=== Bambuddy DEBUG Mode ==="
echo "DEBUG=$DEBUG"
echo "LOG_LEVEL=$LOG_LEVEL"
echo "LOG_TO_FILE=$LOG_TO_FILE"
echo ""
echo "Debug prefixes to look for:"
echo "  [FTP-DEBUG]     - FTP/TLS details"
echo "  [MQTT-DEBUG]    - MQTT commands/responses"
echo "  [REPRINT-DEBUG] - Reprint flow"
echo "==========================="
echo ""

# Start the application
cd "$(dirname "$0")"

# Check if running in Docker or native
if [ -f /.dockerenv ]; then
    # Running inside Docker container
    exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
else
    # Running native - activate venv if present
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    fi
    uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
fi
