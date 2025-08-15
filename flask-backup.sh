#!/bin/bash

# =============================================================================
# Database Connection Fix Script
# =============================================================================
# This script diagnoses and fixes database connection issues for the backup system
# =============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# =============================================================================
# Step 1: Clean up leftover directories
# =============================================================================
cleanup_directories() {
    log "Cleaning up leftover directories..."
    
    # Remove old scirpts directory with sudo if needed
    if [ -d "$HOME/scirpts" ]; then
        sudo rm -rf "$HOME/scirpts"
        log "Removed old 'scirpts' directory"
    fi
    
    # Ensure proper ownership
    if [ -d "$HOME/scripts" ]; then
        sudo chown -R $(whoami):$(whoami) "$HOME/scripts"
    fi
    
    if [ -d "$HOME/flask-backups" ]; then
        sudo chown -R $(whoami):$(whoami) "$HOME/flask-backups"
    fi
    
    log "Directory cleanup completed"
}

# =============================================================================
# Step 2: Diagnose MySQL/MariaDB Service
# =============================================================================
diagnose_mysql() {
    log "Diagnosing MySQL/MariaDB service..."
    
    # Check if MySQL service is running
    if systemctl is-active --quiet mysql; then
        log "✓ MySQL service is running"
    elif systemctl is-active --quiet mariadb; then
        log "✓ MariaDB service is running"
    else
        warn "✗ MySQL/MariaDB service is not running"
        
        # Try to start MySQL
        if sudo systemctl start mysql 2>/dev/null; then
            log "✓ Started MySQL service"
        elif sudo systemctl start mariadb 2>/dev/null; then
            log "✓ Started MariaDB service"
        else
            error "Could not start database service"
            return 1
        fi
    fi
    
    # Check if MySQL port is listening
    if netstat -tuln 2>/dev/null | grep -q ":3306 "; then
        log "✓ MySQL port 3306 is listening"
    else
        warn "✗ MySQL port 3306 is not listening"
    fi
    
    # Check MySQL client tools
    if command -v mysql &> /dev/null; then
        log "✓ MySQL client is installed"
    else
        error "✗ MySQL client is not installed"
        info "Install with: sudo apt update && sudo apt install mysql-client"
        return 1
    fi
    
    if command -v mysqldump &> /dev/null; then
        log "✓ mysqldump is available"
    else
        error "✗ mysqldump is not available"
        return 1
    fi
}

# =============================================================================
# Step 3: Test Database Connection with Different Methods
# =============================================================================
test_database_connection() {
    log "Testing database connection with different methods..."
    
    # Load credentials from .env file
    ENV_FILE="/var/www/qr-rental-system/.env"
    if [ ! -r "$ENV_FILE" ]; then
        error "Cannot read .env file"
        return 1
    fi
    
    DB_NAME=$(grep "^DB_NAME=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    DB_USER=$(grep "^DB_USER=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    DB_PASSWORD=$(grep "^DB_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    DB_HOST=$(grep "^DB_HOST=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"' | tr -d "'" || echo "localhost")
    
    info "Database credentials:"
    info "  Host: $DB_HOST"
    info "  Database: $DB_NAME"
    info "  User: $DB_USER"
    info "  Password: [HIDDEN]"
    
    # Test 1: Try with localhost
    log "Test 1: Connecting to localhost..."
    if mysql -h localhost -u "$DB_USER" -p"$DB_PASSWORD" -e "USE $DB_NAME;" 2>/dev/null; then
        log "✓ Connection successful with localhost"
        WORKING_HOST="localhost"
    else
        warn "✗ Connection failed with localhost"
        
        # Test 2: Try with 127.0.0.1
        log "Test 2: Connecting to 127.0.0.1..."
        if mysql -h 127.0.0.1 -u "$DB_USER" -p"$DB_PASSWORD" -e "USE $DB_NAME;" 2>/dev/null; then
            log "✓ Connection successful with 127.0.0.1"
            WORKING_HOST="127.0.0.1"
        else
            warn "✗ Connection failed with 127.0.0.1"
            
            # Test 3: Try without host (socket connection)
            log "Test 3: Connecting via socket..."
            if mysql -u "$DB_USER" -p"$DB_PASSWORD" -e "USE $DB_NAME;" 2>/dev/null; then
                log "✓ Connection successful via socket"
                WORKING_HOST=""
            else
                error "✗ All connection methods failed"
                return 1
            fi
        fi
    fi
    
    log "Database connection test completed"
}

# =============================================================================
# Step 4: Update Backup Script with Working Connection
# =============================================================================
update_backup_script() {
    log "Updating backup script with working database connection..."
    
    if [ -z "$WORKING_HOST" ]; then
        error "No working database connection found"
        return 1
    fi
    
    BACKUP_SCRIPT="$HOME/scripts/flask-backup.sh"
    
    # Create updated backup script
    cat > "$BACKUP_SCRIPT" << EOF
#!/bin/bash

# =============================================================================
# Flask Application & Database Backup Script (Fixed Version)
# =============================================================================

# Configuration Variables
APP_DIR="/var/www/qr-rental-system"
BACKUP_BASE_DIR="\$HOME/flask-backups"
RETENTION_DAYS=30
TIMESTAMP=\$(date '+%Y-%m-%d_%H-%M-%S')
BACKUP_DIR="\$BACKUP_BASE_DIR/backup_\$TIMESTAMP"
LOG_FILE="\$BACKUP_BASE_DIR/backup.log"

# Create backup base directory
mkdir -p "\$BACKUP_BASE_DIR"

# Logging function
log() {
    echo "[\$(date '+%Y-%m-%d %H:%M:%S')] \$1" | tee -a "\$LOG_FILE"
}

# Error handling
set -e
handle_error() {
    log "ERROR: Backup failed at line \$1"
    if [ -d "\$BACKUP_DIR" ]; then
        rm -rf "\$BACKUP_DIR"
    fi
    exit 1
}
trap 'handle_error \$LINENO' ERR

# Load database credentials
load_db_credentials() {
    log "Loading database credentials..."
    
    if [ ! -f "\$APP_DIR/.env" ]; then
        log "ERROR: .env file not found at \$APP_DIR/.env"
        exit 1
    fi
    
    DB_NAME=\$(grep "^DB_NAME=" "\$APP_DIR/.env" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    DB_USER=\$(grep "^DB_USER=" "\$APP_DIR/.env" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    DB_PASSWORD=\$(grep "^DB_PASSWORD=" "\$APP_DIR/.env" | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    
    # Use working host connection
    DB_HOST="$WORKING_HOST"
    
    if [ -z "\$DB_NAME" ] || [ -z "\$DB_USER" ] || [ -z "\$DB_PASSWORD" ]; then
        log "ERROR: Missing database credentials"
        exit 1
    fi
    
    log "Database credentials loaded (Host: \${DB_HOST:-socket})"
}

# Pre-backup checks
pre_backup_checks() {
    log "Running pre-backup checks..."
    
    # Check application directory
    if [ ! -d "\$APP_DIR" ]; then
        log "ERROR: Application directory not found: \$APP_DIR"
        exit 1
    fi
    
    # Check mysqldump
    if ! command -v mysqldump &> /dev/null; then
        log "ERROR: mysqldump not found"
        exit 1
    fi
    
    # Test database connection
    if [ -n "\$DB_HOST" ]; then
        if ! mysql -h "\$DB_HOST" -u "\$DB_USER" -p"\$DB_PASSWORD" -e "USE \$DB_NAME;" 2>/dev/null; then
            log "ERROR: Database connection test failed"
            exit 1
        fi
    else
        if ! mysql -u "\$DB_USER" -p"\$DB_PASSWORD" -e "USE \$DB_NAME;" 2>/dev/null; then
            log "ERROR: Database connection test failed"
            exit 1
        fi
    fi
    
    log "Pre-backup checks passed"
}

# Backup application files
backup_application() {
    log "Starting application backup..."
    
    mkdir -p "\$BACKUP_DIR/application"
    
    cd "\$APP_DIR"
    BACKUP_ITEMS=("*.py" "templates/" "static/" ".env" "requirements.txt" "ssl/" "*.md" "*.txt" "*.json")
    
    for item in "\${BACKUP_ITEMS[@]}"; do
        if ls \$item 1> /dev/null 2>&1; then
            log "Backing up: \$item"
            cp -r \$item "\$BACKUP_DIR/application/" 2>/dev/null || true
        fi
    done
    
    echo "Flask Application Backup - \$TIMESTAMP" > "\$BACKUP_DIR/application/BACKUP_INFO.txt"
    echo "Application Directory: \$APP_DIR" >> "\$BACKUP_DIR/application/BACKUP_INFO.txt"
    echo "Backup Date: \$(date)" >> "\$BACKUP_DIR/application/BACKUP_INFO.txt"
    
    # List backed up files
    find "\$BACKUP_DIR/application" -type f | sed "s|\$BACKUP_DIR/application/||" >> "\$BACKUP_DIR/application/BACKUP_INFO.txt"
    
    log "Application backup completed"
}

# Backup database
backup_database() {
    log "Starting database backup..."
    
    mkdir -p "\$BACKUP_DIR/database"
    
    DB_BACKUP_FILE="\$BACKUP_DIR/database/\${DB_NAME}_backup_\$TIMESTAMP.sql"
    
    # Use appropriate connection method
    if [ -n "\$DB_HOST" ]; then
        mysqldump \\
            --user="\$DB_USER" \\
            --password="\$DB_PASSWORD" \\
            --single-transaction \\
            --routines \\
            --triggers \\
            --events \\
            --hex-blob \\
            --opt \\
            --add-drop-database \\
            --databases "\$DB_NAME" \\
            > "\$DB_BACKUP_FILE" 2>/dev/null
    else
        mysqldump \\
            --user="\$DB_USER" \\
            --password="\$DB_PASSWORD" \\
            --single-transaction \\
            --routines \\
            --triggers \\
            --events \\
            --hex-blob \\
            --opt \\
            --add-drop-database \\
            --databases "\$DB_NAME" \\
            > "\$DB_BACKUP_FILE" 2>/dev/null
    fi
    
    if [ ! -s "\$DB_BACKUP_FILE" ]; then
        log "ERROR: Database backup failed or is empty"
        exit 1
    fi
    
    BACKUP_SIZE=\$(du -h "\$DB_BACKUP_FILE" | cut -f1)
    log "Database backup completed. Size: \$BACKUP_SIZE"
    
    echo "Database Backup - \$TIMESTAMP" > "\$BACKUP_DIR/database/DATABASE_INFO.txt"
    echo "Database Name: \$DB_NAME" >> "\$BACKUP_DIR/database/DATABASE_INFO.txt"
    echo "Database Host: \${DB_HOST:-socket}" >> "\$BACKUP_DIR/database/DATABASE_INFO.txt"
    echo "Backup Date: \$(date)" >> "\$BACKUP_DIR/database/DATABASE_INFO.txt"
    echo "Backup Size: \$BACKUP_SIZE" >> "\$BACKUP_DIR/database/DATABASE_INFO.txt"
    echo "Backup File: \$(basename "\$DB_BACKUP_FILE")" >> "\$BACKUP_DIR/database/DATABASE_INFO.txt"
}

# Create archive
create_archive() {
    log "Creating compressed archive..."
    
    cd "\$BACKUP_BASE_DIR"
    ZIP_FILE="flask_backup_\$TIMESTAMP.zip"
    zip -r "\$ZIP_FILE" "backup_\$TIMESTAMP" > /dev/null 2>&1
    
    if [ ! -f "\$ZIP_FILE" ]; then
        log "ERROR: Failed to create archive"
        exit 1
    fi
    
    ARCHIVE_SIZE=\$(du -h "\$ZIP_FILE" | cut -f1)
    log "Archive created: \$ZIP_FILE (Size: \$ARCHIVE_SIZE)"
    
    # Test archive integrity
    if zip -T "\$ZIP_FILE" > /dev/null 2>&1; then
        log "Archive integrity verified"
    else
        log "WARNING: Archive integrity check failed"
    fi
    
    rm -rf "backup_\$TIMESTAMP"
    log "Temporary directory cleaned up"
}

# Cleanup old backups
cleanup_old_backups() {
    log "Cleaning up backups older than \$RETENTION_DAYS days..."
    
    DELETED_COUNT=0
    find "\$BACKUP_BASE_DIR" -name "flask_backup_*.zip" -mtime +\$RETENTION_DAYS -type f | while read backup_file; do
        log "Deleting old backup: \$(basename "\$backup_file")"
        rm -f "\$backup_file"
        DELETED_COUNT=\$((DELETED_COUNT + 1))
    done
    
    # Clean up log file
    if [ -f "\$LOG_FILE" ]; then
        tail -n 1000 "\$LOG_FILE" > "\$LOG_FILE.tmp" && mv "\$LOG_FILE.tmp" "\$LOG_FILE"
    fi
    
    log "Cleanup completed"
}

# Main function
main() {
    log "=========================================="
    log "Starting Flask Application Backup"
    log "Timestamp: \$TIMESTAMP"
    log "=========================================="
    
    load_db_credentials
    pre_backup_checks
    backup_application
    backup_database
    create_archive
    cleanup_old_backups
    
    log "=========================================="
    log "Backup completed successfully!"
    log "Archive: \$BACKUP_BASE_DIR/flask_backup_\$TIMESTAMP.zip"
    log "=========================================="
}

# Run main function
main "\$@"
EOF

    chmod +x "$BACKUP_SCRIPT"
    log "Backup script updated with working database connection"
}

# =============================================================================
# Step 5: Test the Fixed Backup
# =============================================================================
test_fixed_backup() {
    log "Testing the fixed backup script..."
    
    BACKUP_SCRIPT="$HOME/scripts/flask-backup.sh"
    
    if [ ! -f "$BACKUP_SCRIPT" ]; then
        error "Backup script not found"
        return 1
    fi
    
    log "Running test backup with fixed database connection..."
    if "$BACKUP_SCRIPT"; then
        log "✓ Test backup completed successfully!"
        
        # Show created backup
        LATEST_BACKUP=$(ls -t "$HOME/flask-backups/flask_backup_"*.zip 2>/dev/null | head -1)
        if [ -n "$LATEST_BACKUP" ]; then
            BACKUP_SIZE=$(du -h "$LATEST_BACKUP" | cut -f1)
            log "Latest backup: $(basename "$LATEST_BACKUP") (Size: $BACKUP_SIZE)"
            
            # Test archive contents
            log "Archive contents:"
            unzip -l "$LATEST_BACKUP" | grep -E "(application|database)" | head -10
        fi
    else
        error "✗ Test backup still failed"
        return 1
    fi
}

# =============================================================================
# Main Execution
# =============================================================================
main() {
    echo "=========================================="
    echo "Database Connection Fix for Flask Backup"
    echo "=========================================="
    
    cleanup_directories
    
    if ! diagnose_mysql; then
        error "MySQL/MariaDB service issues detected"
        info "Please install and start MySQL/MariaDB service"
        exit 1
    fi
    
    if ! test_database_connection; then
        error "Could not establish database connection"
        exit 1
    fi
    
    update_backup_script
    test_fixed_backup
    
    echo "=========================================="
    echo "Database Fix Completed!"
    echo "=========================================="
    echo ""
    echo "Your backup system is now ready:"
    echo "• Backup script: $HOME/scripts/flask-backup.sh"
    echo "• Backup location: $HOME/flask-backups/"
    echo "• Database connection: ${WORKING_HOST:-socket} ✓"
    echo ""
    echo "Next steps:"
    echo "1. Test manual backup: $HOME/scripts/flask-backup.sh"
    echo "2. Set up daily automation with systemd service"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Don't run this script as root"
    exit 1
fi

# Initialize working host variable
WORKING_HOST=""

# Run main function
main "$@"