from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory, flash
from config import Config
from db_handler import DatabaseHandler
from qr_email_sender import QREmailSender
import os
import logging
from datetime import datetime
from ssl_config import SSLConfig
import pandas as pd
from functools import wraps
import json
from rental_email_handler import RentalEmailHandler

# Initialize Flask app and components
app = Flask(__name__, static_folder='static')
app.secret_key = Config.FLASK_SECRET_KEY
db = DatabaseHandler()
qr_sender = QREmailSender()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Form field mapping for JotForm webhook
FORM_FIELD_MAPPINGS = {
    'default': {
        'first_name': ['q3_first_name', 'q34_first_name'],
        'last_name': ['q4_last_name', 'q35_last_name'],
        'email': ['q5_email'],
        'city': ['q7_City', 'q8_city'],
        'package_type': ['q8_package_type', 'q17_package_type'],
        'package_products': ['q11_package_type', 'q17_package_type'],
        'phone': ['q6_phoneNumber']
    }
}

# Headers configuration
@app.after_request
def after_request(response):
    """Add CORS headers to all responses"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*'
    return response

# Authentication decorators
def admin_required(f):
    """Require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or session.get('role') != 'admin':
            flash('Admin access required', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    """Require any user authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Helper function to extract user data from webhook
def extract_user_data_from_webhook(raw_request):
    """Extract user data from JotForm webhook with flexible field mapping"""
    user_data = {
        'first_name': '',
        'last_name': '',
        'email': '',
        'city': '',
        'package_type': 'Not specified',
        'quantity': 1,
        'phone': ''
    }
    
    # Try to find each field using the mapping
    for field_name, field_keys in FORM_FIELD_MAPPINGS['default'].items():
        for key in field_keys:
            if key in raw_request:
                value = raw_request[key]
                
                # Handle name fields that might be objects
                if field_name in ['first_name', 'last_name'] and isinstance(value, dict):
                    if field_name == 'first_name':
                        user_data[field_name] = value.get('first', '').strip()
                    else:
                        user_data[field_name] = value.get('last', '').strip()
                # Handle package/product fields
                elif field_name in ['package_type', 'package_products']:
                    if isinstance(value, dict) and 'products' in value:
                        products = value['products']
                        if products:
                            user_data['package_type'] = products[0].get('productName', 'Unknown')
                            user_data['quantity'] = int(products[0].get('quantity', 1))
                    elif isinstance(value, list) and value:
                        user_data['package_type'] = value[0]
                    elif isinstance(value, str):
                        user_data['package_type'] = value
                # Handle regular string fields
                else:
                    user_data[field_name] = str(value).strip()
                break  # Found the field, move to next
    
    return user_data

# Main webhook handler
@app.route('/api/jotform-webhook', methods=['POST'])
def jotform_webhook():
    """Process JotForm webhook submissions"""
    try:
        logger.info("Processing JotForm webhook")
        
        # Parse incoming data
        if request.content_type and 'multipart/form-data' in request.content_type:
            form_data = request.form.to_dict()
            if 'rawRequest' in form_data and isinstance(form_data['rawRequest'], str):
                form_data['rawRequest'] = json.loads(form_data['rawRequest'])
        else:
            form_data = request.json
        
        if not form_data or 'rawRequest' not in form_data:
            return jsonify({"error": "Invalid webhook data"}), 400
        
        # Extract user data
        raw_request = form_data['rawRequest']
        submission_id = form_data.get('submissionID', 'unknown')
        user_data = extract_user_data_from_webhook(raw_request)
        
        # Validate required fields
        if not all([user_data['first_name'], user_data['last_name'], user_data['email']]):
            return jsonify({"error": "Missing required fields"}), 400
        
        logger.info(f"Processing webhook for {user_data['email']} - {user_data['quantity']} {user_data['package_type']} packages")
        
        # Create/update user and add packages
        db.connect()  # Ensure connection
        user_id = db.create_user(
            user_data['first_name'],
            user_data['last_name'],
            user_data['email'],
            user_data['city'],
            user_data['package_type']
        )
        
        # Add packages to inventory
        db.add_user_packages(user_id, user_data['package_type'], user_data['quantity'])
        
        # Send QR code email
        try:
            qr_sender.send_email(
                user_data['email'],
                user_data['first_name'],
                user_data['last_name'],
                user_data['city'],
                user_data['package_type'],
                user_data['quantity']
            )
        except Exception as email_error:
            logger.error(f"Email failed but data saved: {email_error}")
        
        return jsonify({
            "status": "success",
            "message": f"Added {user_data['quantity']} packages for {user_data['email']}",
            "user_id": user_id
        })
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
            session['logged_in'] = True
            session['role'] = 'admin'
            session['username'] = username
            return redirect(url_for('home'))
        elif username == Config.USER_CREDENTIALS and password == Config.USER_PASSWORD:
            session['logged_in'] = True
            session['role'] = 'user'
            session['username'] = username
            return redirect(url_for('home'))
        
        return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/api/export-data', methods=['GET'])
@admin_required
def export_data():
    """Export SQL database dump in a ZIP archive"""
    import subprocess
    import zipfile
    import io
    import tempfile
    import os
    from datetime import datetime
    
    try:
        # Generate timestamp for filenames
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Create temporary file for SQL dump
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.sql', delete=False) as temp_sql:
            temp_sql_path = temp_sql.name
        
        try:
            # Build mysqldump command
            dump_cmd = [
                'mysqldump',
                f'--host={Config.DB_HOST}',
                f'--user={Config.DB_USER}',
                f'--password={Config.DB_PASSWORD}',
                '--single-transaction',
                '--routines',
                '--triggers',
                '--complete-insert',
                Config.DB_NAME
            ]
            
            # Execute mysqldump
            logger.info(f"Starting database export for {Config.DB_NAME}")
            with open(temp_sql_path, 'w') as f:
                result = subprocess.run(dump_cmd, stdout=f, stderr=subprocess.PIPE, text=True)
            
            if result.returncode != 0:
                raise Exception(f"mysqldump failed: {result.stderr}")
            
            # Create ZIP file in memory
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Add SQL dump to ZIP
                sql_filename = f'{Config.DB_NAME}_backup_{timestamp}.sql'
                zip_file.write(temp_sql_path, sql_filename)
                
                # Add export info file
                export_info = f"""Database Export Information
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Database: {Config.DB_NAME}
Host: {Config.DB_HOST}
Exported by: {session.get('username', 'Unknown')}
Export Type: Full SQL Database Dump

Files in this archive:
- {sql_filename} - Complete SQL database dump

Restore Instructions:
1. Create new database: CREATE DATABASE {Config.DB_NAME};
2. Import dump: mysql -u username -p {Config.DB_NAME} < {sql_filename}

Note: This is a complete database backup including structure and data.
"""
                zip_file.writestr('README.txt', export_info)
            
            zip_buffer.seek(0)
            
            # Generate ZIP filename
            zip_filename = f'{Config.DB_NAME}_export_{timestamp}.zip'
            
            logger.info(f"Database export completed: {zip_filename}")
            
            # Return ZIP file as download
            from flask import Response
            return Response(
                zip_buffer.getvalue(),
                mimetype='application/zip',
                headers={'Content-Disposition': f'attachment; filename={zip_filename}'}
            )
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_sql_path):
                os.unlink(temp_sql_path)
        
    except Exception as e:
        logger.error(f"Database export error: {str(e)}")
        return jsonify({"error": f"Database export failed: {str(e)}"}), 500

@app.route('/api/edit-package-quantity/<int:user_id>', methods=['POST'])
@login_required
def edit_package_quantity(user_id):
    """Manually adjust package quantity for a user"""
    try:
        data = request.get_json()
        action = data.get('action')  # 'add' or 'remove'
        quantity = int(data.get('quantity', 1))
        package_type = data.get('package_type', 'Standard Package')
        
        # Validate inputs
        if action not in ['add', 'remove']:
            return jsonify({"error": "Invalid action. Use 'add' or 'remove'"}), 400
        
        if quantity <= 0:
            return jsonify({"error": "Quantity must be greater than 0"}), 400
        
        # Get current package summary
        current_summary = db.get_user_package_summary(user_id)
        
        if action == 'add':
            # Add new packages
            success = db.add_user_packages(user_id, package_type, quantity)
            if success:
                message = f"Added {quantity} packages successfully"
            else:
                return jsonify({"error": "Failed to add packages"}), 500
                
        elif action == 'remove':
            # Remove packages (only available ones)
            success, message = db.remove_user_packages(user_id, quantity)
            if not success:
                return jsonify({"error": message}), 400
        
        # Get updated summary
        updated_summary = db.get_user_package_summary(user_id)
        
        logger.info(f"Package quantity edited for user {user_id}: {action} {quantity} packages")
        
        return jsonify({
            "success": True,
            "message": message,
            "package_summary": updated_summary,
            "previous_total": current_summary['total_packages'],
            "new_total": updated_summary['total_packages']
        })
        
    except ValueError as e:
        return jsonify({"error": "Invalid quantity value"}), 400
    except Exception as e:
        logger.error(f"Error editing package quantity: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/import-data', methods=['POST'])
@admin_required
def import_data():
    """Import SQL database from uploaded file"""
    import subprocess
    import tempfile
    import os
    from datetime import datetime
    
    try:
        # Check if file was uploaded
        if 'sql_file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['sql_file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Validate file extension
        if not file.filename.lower().endswith('.sql'):
            return jsonify({"error": "Only .sql files are allowed"}), 400
        
        # Check file size (limit to 50MB)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)  # Reset file pointer
        
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            return jsonify({"error": "File too large. Maximum size is 50MB"}), 400
        
        if file_size == 0:
            return jsonify({"error": "File is empty"}), 400
        
        # Create temporary file for SQL import
        with tempfile.NamedTemporaryFile(mode='w+b', suffix='.sql', delete=False) as temp_sql:
            temp_sql_path = temp_sql.name
            file.save(temp_sql_path)
        
        try:
            logger.info(f"Starting database import for {Config.DB_NAME}")
            
            # Build mysql import command
            import_cmd = [
                'mysql',
                f'--host={Config.DB_HOST}',
                f'--user={Config.DB_USER}',
                f'--password={Config.DB_PASSWORD}',
                Config.DB_NAME
            ]
            
            # Execute mysql import
            with open(temp_sql_path, 'r') as f:
                result = subprocess.run(import_cmd, stdin=f, stderr=subprocess.PIPE, text=True)
            
            if result.returncode != 0:
                error_msg = result.stderr or "Unknown import error"
                raise Exception(f"MySQL import failed: {error_msg}")
            
            logger.info(f"Database import completed successfully for {Config.DB_NAME}")
            
            return jsonify({
                "success": True,
                "message": f"Database imported successfully from {file.filename}",
                "imported_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_sql_path):
                os.unlink(temp_sql_path)
        
    except Exception as e:
        logger.error(f"Database import error: {str(e)}")
        return jsonify({"error": f"Database import failed: {str(e)}"}), 500

@app.route('/logout')
def logout():
    """Handle user logout"""
    session.clear()
    return redirect(url_for('login'))

# Main application routes
@app.route('/')
@login_required
def home():
    """Dashboard page"""
    return render_template('home.html')

@app.route('/scan')
@login_required
def scan():
    """QR scanner page"""
    return render_template('scan.html')

@app.route('/lookup', methods=['GET', 'POST'])
@login_required
def lookup():
    """Customer lookup page"""
    if request.method == 'GET':
        qr_code = request.args.get('qr_code')
        if qr_code:
            user_data = db.verify_qr_code(qr_code)
            if user_data:
                packages = db.get_user_packages(user_data['user_id'])
                # Ensure package_summary always exists
                package_summary = db.get_user_package_summary(user_data['user_id'])
                if not package_summary:
                    package_summary = {
                        'total_packages': 0,
                        'available_packages': 0,
                        'rented_packages': 0,
                        'has_packages': False,
                        'all_returned': True
                    }
                return render_template('user_details.html', 
                                     user=user_data, 
                                     packages=packages,
                                     package_summary=package_summary)
            return render_template('lookup.html', error="Invalid QR code")
        return render_template('lookup.html')
    
    # POST - search by name or QR
    search_type = request.form.get('search_type')
    search_term = request.form.get('search_term')
    
    if not search_term:
        return render_template('lookup.html', error="Please enter a search term")
    
    if search_type == 'qr_code':
        user_data = db.verify_qr_code(search_term)
        if user_data:
            packages = db.get_user_packages(user_data['user_id'])
            # Ensure package_summary always exists
            package_summary = db.get_user_package_summary(user_data['user_id'])
            if not package_summary:
                package_summary = {
                    'total_packages': 0,
                    'available_packages': 0,
                    'rented_packages': 0,
                    'has_packages': False,
                    'all_returned': True
                }
            return render_template('user_details.html', 
                                 user=user_data, 
                                 packages=packages,
                                 package_summary=package_summary)
    elif search_type == 'first_name':
        users = db.search_by_first_name(search_term)
        if users:
            for user in users:
                summary = db.get_user_package_summary(user['user_id'])
                user['package_summary'] = summary if summary else {
                    'total_packages': 0, 'available_packages': 0, 'rented_packages': 0,
                    'has_packages': False, 'all_returned': True
                }
            return render_template('search_results.html', users=users)
    elif search_type == 'last_name':
        users = db.search_by_last_name(search_term)
        if users:
            for user in users:
                summary = db.get_user_package_summary(user['user_id'])
                user['package_summary'] = summary if summary else {
                    'total_packages': 0, 'available_packages': 0, 'rented_packages': 0,
                    'has_packages': False, 'all_returned': True
                }
            return render_template('search_results.html', users=users)
    
    return render_template('lookup.html', error="No results found")

@app.route('/email-client', methods=['GET', 'POST'])
@admin_required
def email_client():
    """Bulk email sender for QR codes"""
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file uploaded', 'error')
            return redirect(url_for('email_client'))
        
        file = request.files['csv_file']
        if not file.filename.endswith('.csv'):
            flash('Please upload a CSV file', 'error')
            return redirect(url_for('email_client'))
        
        # Process CSV
        temp_path = os.path.join(app.static_folder, 'temp', file.filename)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        file.save(temp_path)
        
        results = qr_sender.process_csv(temp_path)
        os.remove(temp_path)
        
        total = len(results)
        successful = sum(1 for r in results if r['success'])
        
        return render_template('email_client.html',
                             results=results,
                             summary={'total': total, 'successful': successful, 'failed': total - successful})
    
    return render_template('email_client.html')

@app.route('/email-logs')
@login_required
def email_logs():
    """View customer database"""
    db.connect()
    query = """
    SELECT u.id, u.first_name, u.last_name, u.email, u.rental_status,
           qr.qr_code_number,
           (SELECT created_at FROM email_logs WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1) as last_action
    FROM users u
    LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
    ORDER BY u.last_name, u.first_name
    """
    db.cursor.execute(query)
    users = db.cursor.fetchall()
    return render_template('email_logs.html', users=users)

@app.route('/admin')
@admin_required
def admin():
    """Admin dashboard"""
    return render_template('admin.html')

# API endpoints
@app.route('/api/lookup', methods=['POST'])
@login_required
def api_lookup():
    """API endpoint for QR verification"""
    data = request.get_json()
    qr_code = data.get('qr_code')
    if not qr_code:
        return jsonify({"error": "No QR code provided"}), 400
    
    user_data = db.verify_qr_code(qr_code)
    if user_data:
        return jsonify({"success": True, "user": user_data})
    return jsonify({"error": "Invalid QR code"}), 404

@app.route('/api/package-action/<int:user_id>', methods=['POST'])
@login_required
def package_action(user_id):
    """Handle package checkout/checkin actions"""
    data = request.get_json()
    action = data.get('action')
    
    valid_actions = ['checkout_one', 'checkin_one', 'checkout_all', 'checkin_all']
    if action not in valid_actions:
        return jsonify({"error": "Invalid action"}), 400
    
    success, message = db.update_rental_status_new(user_id, action)
    
    if success:
        summary = db.get_user_package_summary(user_id)
        return jsonify({
            "success": True,
            "message": message,
            "package_summary": summary,
            "email_sent": summary.get('all_returned', False) and summary.get('has_packages', False)
        })
    
    return jsonify({"error": message}), 400

@app.route('/api/update-package-status/<int:package_id>', methods=['POST'])
@login_required
def update_package_status_api(package_id):
    """Update individual package status"""
    data = request.get_json()
    new_status = data.get('status')
    
    if new_status not in ['available', 'rented_out']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    
    # Get user_id for this package
    db.cursor.execute("SELECT user_id FROM user_packages WHERE id = %s", (package_id,))
    result = db.cursor.fetchone()
    if not result:
        return jsonify({'success': False, 'error': 'Package not found'}), 404
    
    user_id = result['user_id']
    
    # Update package
    if db.update_package_status(package_id, new_status):
        # Check if all packages returned
        summary = db.get_user_package_summary(user_id)
        
        if new_status == 'available' and summary['all_returned'] and summary['has_packages']:
            # Send thank you email
            db.cursor.execute("""
                SELECT first_name, last_name, email, city, package_type
                FROM users WHERE id = %s
            """, (user_id,))
            user = db.cursor.fetchone()
            
            if user:
                email_handler = RentalEmailHandler(
                    os.getenv('GMAIL_ADDRESS'),
                    os.getenv('GMAIL_APP_PASSWORD')
                )
                email_handler.send_thank_you_email(
                    user['email'],
                    user['first_name'],
                    user['last_name'],
                    user.get('city'),
                    user.get('package_type')
                )
            
            return jsonify({
                'success': True,
                'email_sent': True,
                'message': f'Package checked in - all packages returned!'
            })
        
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Update failed'}), 500

@app.route('/api/reset-rental/<int:user_id>', methods=['POST'])
@login_required
def reset_rental_status(user_id):
    """Reset all packages to available"""
    try:
        # Set all packages to available
        db.cursor.execute("""
            UPDATE user_packages 
            SET status = 'available', last_activity_time = CURRENT_TIMESTAMP
            WHERE user_id = %s
        """, (user_id,))
        
        # Set user to inactive
        db.cursor.execute("""
            UPDATE users 
            SET rental_status = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (user_id,))
        
        db.connection.commit()
        
        summary = db.get_user_package_summary(user_id)
        return jsonify({
            "success": True,
            "message": f"Reset complete - all {summary['total_packages']} packages available",
            "package_summary": summary
        })
    except Exception as e:
        db.connection.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/save-notes', methods=['POST'])
@login_required
def save_notes():
    """Save notes for a user"""
    data = request.get_json()
    user_id = data.get('user_id')
    notes = data.get('notes')
    
    if not user_id:
        return jsonify({'success': False, 'error': 'User ID required'}), 400
    
    db.cursor.execute("""
        UPDATE users 
        SET notes = %s, notes_updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (notes, user_id))
    
    db.connection.commit()
    return jsonify({'success': True})

@app.route('/api/toggle-rental/<int:user_id>', methods=['POST'])
@login_required
def toggle_rental_status_new(user_id):
    """Toggle rental status with package support"""
    data = request.get_json()
    status = data.get('status', 0)
    
    # Map old status codes to new actions
    action_map = {0: 'checkin_all', 1: 'checkout_all', 2: 'checkin_all'}
    action = action_map.get(status, 'checkin_all')
    
    success, message = db.update_rental_status_new(user_id, action)
    return jsonify({"success": success, "message": message}) if success else jsonify({"error": message}), 400

@app.route('/api/filter-users/<status>')
@login_required
def filter_users(status):
    """Filter users by rental status with package summary data"""
    if status not in ['all', 'not_active', 'active', 'returned']:
        return jsonify({'error': 'Invalid status'}), 400
    
    try:
        # Base query to get users with QR codes
        query = """
        SELECT u.id, u.first_name, u.last_name, u.email, u.rental_status,
               qr.qr_code_number,
               (SELECT created_at FROM email_logs WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1) as last_action
        FROM users u
        LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
        """
        
        # Add status filter
        if status == 'active':
            query += " WHERE u.rental_status = 1"
        elif status == 'returned':
            query += " WHERE u.rental_status = 2"
        elif status == 'not_active':
            query += " WHERE u.rental_status = 0"
        
        query += " ORDER BY u.last_name, u.first_name"
        
        # Ensure database connection
        db.ensure_connection()
        db.cursor.execute(query)
        users = db.cursor.fetchall()
        
        # Add package summary for each user
        for user in users:
            # Format last_action timestamp
            if user['last_action']:
                user['last_action'] = user['last_action'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Get package summary for this user
            package_summary = db.get_user_package_summary(user['id'])
            user['package_summary'] = package_summary
            
            # Add convenience fields for frontend
            user['has_packages'] = package_summary['has_packages']
            user['total_packages'] = package_summary['total_packages']
            user['available_packages'] = package_summary['available_packages']
            user['rented_packages'] = package_summary['rented_packages']
            user['all_returned'] = package_summary['all_returned']
        
        logger.info(f"Filtered users by status '{status}': {len(users)} users found")
        return jsonify({'users': users})
        
    except Exception as e:
        logger.error(f"Error filtering users by status '{status}': {str(e)}")
        return jsonify({'error': 'Database error occurred'}), 500
    
@app.route('/api/stats')
def get_stats():
    """Get database statistics"""
    db.connect()
    stats = db.get_database_stats()
    return jsonify(stats)

@app.route('/api/reset-database', methods=['POST'])
@admin_required
def reset_database():
    """Reset entire database"""
    try:
        db.reset_database()
        db.connect()
        return jsonify({"message": "Database reset successful"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        db.cursor.execute("SELECT 1")
        db.cursor.fetchone()
        return jsonify({"status": "healthy", "database": "connected"})
    except:
        return jsonify({"status": "unhealthy"}), 500

# Static file serving
@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files"""
    response = send_from_directory('static', filename)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# Run application
if __name__ == '__main__':
    ssl_context = (SSLConfig.SSL_CERTIFICATE, SSLConfig.SSL_KEY)
    print(f"Starting server on https://{SSLConfig.HOST}:{SSLConfig.PORT}")
    app.run(
        host=SSLConfig.HOST,
        port=SSLConfig.PORT,
        ssl_context=ssl_context,
        debug=Config.FLASK_DEBUG
    )