#!/bin/bash

# QR Rental System Startup Script
# This script activates the virtual environment and starts the Flask application

# Set the application directory
APP_DIR="/var/www/qr-rental-system"

# Change to the application directory
cd "$APP_DIR"

# Log startup attempt
echo "$(date): Starting QR Rental System..." >> "$APP_DIR/logs/startup.log"

# Activate the virtual environment
# This ensures all Python dependencies are available
source "$APP_DIR/venv/bin/activate"

# Verify virtual environment is activated
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "$(date): Virtual environment activated: $VIRTUAL_ENV" >> "$APP_DIR/logs/startup.log"
else
    echo "$(date): ERROR: Failed to activate virtual environment" >> "$APP_DIR/logs/startup.log"
    exit 1
fi

# Set environment variables (backup in case .env doesn't load)
export PYTHONPATH="$APP_DIR:$PYTHONPATH"

# Start the Flask application
# The python interpreter from the venv will be used automatically
echo "$(date): Starting Flask application..." >> "$APP_DIR/logs/startup.log"
exec python3 app.py
