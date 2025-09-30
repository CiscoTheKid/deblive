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
import csv
import io
from flask import Response
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
def extract_user_data_from_new_form(raw_request, form_data):
    """
    Extract user data from the new JotForm structure with payment validation
    Now properly extracts real package names and quantities from q17_package_type
    """
    user_data = {
        'first_name': '',
        'last_name': '',
        'email': '',
        'city': 'Washington DC',  # Default city from form title
        'package_type': 'Standard Package',  # Default package type
        'quantity': 1,  # Default quantity
        'phone': '',
        'pickup_person': '',
        'group_leader': '',
        'transaction_id': '',
        'paid_status': '0'
    }
    
    # Extract basic user data using the field mappings from captured webhook
    if 'q5_email' in raw_request:
        user_data['email'] = str(raw_request['q5_email']).strip()
    
    # Phone number (handle object format)
    if 'q6_phoneNumber' in raw_request:
        phone_data = raw_request['q6_phoneNumber']
        if isinstance(phone_data, dict):
            user_data['phone'] = phone_data.get('full', '').strip()
        else:
            user_data['phone'] = str(phone_data).strip()
    
    # Group leader name from q8_city field (this seems to contain group leader info)
    if 'q8_city' in raw_request:
        user_data['group_leader'] = str(raw_request['q8_city']).strip()
    
    # Pickup person name
    if 'q30_nameOf' in raw_request:
        user_data['pickup_person'] = str(raw_request['q30_nameOf']).strip()
    
    # First and last names
    if 'q34_first_name' in raw_request:
        user_data['first_name'] = str(raw_request['q34_first_name']).strip()
    if 'q35_last_name' in raw_request:
        user_data['last_name'] = str(raw_request['q35_last_name']).strip()
    
    # Questions/concerns
    if 'q25_questionsConcerns' in raw_request:
        user_data['questions'] = str(raw_request['q25_questionsConcerns']).strip()
    
    # Payment status - this is our validation field
    if 'q43_paid' in raw_request:
        user_data['paid_status'] = str(raw_request['q43_paid']).strip()
    
    # Extract city from form title if available
    if form_data and 'formTitle' in form_data:
        form_title = form_data['formTitle']
        if 'Washington DC' in form_title:
            user_data['city'] = 'Washington DC'
        elif 'Boston' in form_title:
            user_data['city'] = 'Boston'
        elif 'New York' in form_title:
            user_data['city'] = 'New York'
        elif 'Philadelphia' in form_title:
            user_data['city'] = 'Philadelphia'
        elif 'Baltimore' in form_title:
            user_data['city'] = 'Baltimore'
    
    # EXTRACT REAL PACKAGE DATA from q17_package_type field
    if 'q17_package_type' in raw_request:
        package_data = raw_request['q17_package_type']
        
        try:
            # Method 1: Try to get from products array (most reliable)
            if isinstance(package_data, dict) and 'products' in package_data:
                products = package_data['products']
                if products and len(products) > 0:
                    first_product = products[0]
                    if 'productName' in first_product:
                        user_data['package_type'] = first_product['productName']
                    if 'quantity' in first_product:
                        user_data['quantity'] = int(first_product['quantity'])
                    
                    logger.info(f"Extracted package from products array: {user_data['package_type']} x{user_data['quantity']}")
            
            # Method 2: Fallback to numbered object structure
            elif isinstance(package_data, dict) and '1' in package_data:
                package_info = package_data['1']
                if 'name' in package_info:
                    user_data['package_type'] = package_info['name']
                if 'quantity' in package_info:
                    user_data['quantity'] = int(package_info['quantity'])
                    
                logger.info(f"Extracted package from numbered structure: {user_data['package_type']} x{user_data['quantity']}")
            
            # Method 3: Check if it's a direct products list
            elif isinstance(package_data, list) and len(package_data) > 0:
                first_item = package_data[0]
                if isinstance(first_item, dict):
                    if 'productName' in first_item:
                        user_data['package_type'] = first_item['productName']
                    if 'quantity' in first_item:
                        user_data['quantity'] = int(first_item['quantity'])
                        
                logger.info(f"Extracted package from direct list: {user_data['package_type']} x{user_data['quantity']}")
            
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Error extracting package data: {e}, using defaults")
            # Keep default values if extraction fails
    
    # If no package extracted, set city-based default (fallback)
    if user_data['package_type'] == 'Standard Package':
        if user_data['city'] == 'Washington DC':
            user_data['package_type'] = 'DC Package'
        elif user_data['city'] == 'Boston':
            user_data['package_type'] = 'Boston Package'
        else:
            user_data['package_type'] = 'Standard Package'
    
    logger.info(f"Final extracted user data: {user_data['first_name']} {user_data['last_name']} ({user_data['email']}) - "
                f"City: {user_data['city']}, Package: {user_data['package_type']}, Quantity: {user_data['quantity']}, "
                f"Paid: {user_data['paid_status']}")
    
    return user_data

# Main webhook handler (original)
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

# Payment-validated webhook handler
@app.route('/api/jotform-webhook-paid', methods=['POST'])
def jotform_webhook_paid():
    """JotForm webhook that only processes submissions when payment is confirmed (q43_paid = "1")"""
    try:
        logger.info("Processing JotForm webhook with payment validation")
        
        # Parse incoming data - handle multipart form data from JotForm
        if request.content_type and 'multipart/form-data' in request.content_type:
            form_data = request.form.to_dict()
            
            if 'rawRequest' not in form_data:
                logger.error("No rawRequest found in webhook data")
                return jsonify({"error": "Invalid webhook data - missing rawRequest"}), 400
            
            try:
                raw_request = json.loads(form_data['rawRequest'])
            except json.JSONDecodeError as e:
                logger.error(f"Could not parse rawRequest JSON: {e}")
                return jsonify({"error": "Invalid rawRequest JSON format"}), 400
        else:
            form_data = request.json
            if not form_data or 'rawRequest' not in form_data:
                return jsonify({"error": "Invalid webhook data"}), 400
            raw_request = form_data['rawRequest']
        
        # PAYMENT VALIDATION - Only process if paid status is confirmed
        paid_status = raw_request.get('q43_paid', '0')
        is_paid = str(paid_status).lower() in ['1', 'true', 'yes']
        
        if not is_paid:
            logger.info(f"Submission ignored - payment not confirmed. q43_paid = {paid_status}")
            return jsonify({
                "status": "ignored", 
                "message": "Submission not processed - payment not confirmed",
                "paid_status": paid_status
            }), 200
        
        # Extract user data from the new form structure
        user_data = extract_user_data_from_new_form(raw_request, form_data)
        
        # Validate required fields
        if not all([user_data['first_name'], user_data['last_name'], user_data['email']]):
            missing_fields = []
            if not user_data['first_name']: missing_fields.append('first_name')
            if not user_data['last_name']: missing_fields.append('last_name') 
            if not user_data['email']: missing_fields.append('email')
            
            logger.error(f"Missing required fields: {missing_fields}")
            return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400
        
        logger.info(f"Processing PAID submission for {user_data['email']} - {user_data['quantity']} {user_data['package_type']} packages")
        
        # Create/update user in database
        db.ensure_connection()
        user_id = db.create_user(
            user_data['first_name'],
            user_data['last_name'], 
            user_data['email'],
            user_data['city'],
            user_data['package_type']
        )
        
        # Add packages to user's inventory
        db.add_user_packages(user_id, user_data['package_type'], user_data['quantity'])
        
        # Send QR code email to customer
        email_sent = False
        email_error = None
        
        try:
            qr_sender.send_email(
                user_data['email'],
                user_data['first_name'], 
                user_data['last_name'],
                user_data['city'],
                user_data['package_type'],
                user_data['quantity']
            )
            email_sent = True
            logger.info(f"QR code email sent successfully to {user_data['email']}")
            
        except Exception as email_error:
            email_error_msg = str(email_error)
            logger.error(f"Email failed but data saved: {email_error_msg}")
            email_error = email_error_msg
        
        return jsonify({
            "status": "success",
            "message": f"Processed paid submission: Added {user_data['quantity']} packages for {user_data['email']}",
            "user_id": user_id,
            "email_sent": email_sent,
            "email_error": email_error,
            "transaction_id": user_data.get('transaction_id'),
            "paid_status_confirmed": True
        }), 200
        
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return jsonify({
            "status": "error", 
            "message": f"Webhook processing failed: {str(e)}"
        }), 500

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

@app.route('/api/admin/search-users', methods=['POST'])
@admin_required
def admin_search_users():
    """Search users for admin management"""
    try:
        data = request.get_json()
        search_term = data.get('search_term', '').strip()
        
        if not search_term:
            return jsonify({"success": False, "error": "Search term required"}), 400
        
        db.ensure_connection()
        
        # Search users in database using LIKE queries
        db.cursor.execute("""
            SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                   u.rental_status, u.created_at, u.package_type,
                   qr.qr_code_number,
                   (SELECT COUNT(*) FROM user_packages WHERE user_id = u.id) as package_count
            FROM users u
            LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
            WHERE LOWER(u.first_name) LIKE LOWER(%s) 
               OR LOWER(u.last_name) LIKE LOWER(%s)
               OR LOWER(u.email) LIKE LOWER(%s)
            ORDER BY u.last_name, u.first_name
            LIMIT 50
        """, (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"))
        
        users = db.cursor.fetchall() or []
        
        return jsonify({
            "success": True,
            "users": users,
            "count": len(users)
        })
        
    except Exception as e:
        logger.error(f"Admin user search error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/export-csv')
@login_required
def export_csv_page():
    """
    Render the CSV export page
    This page allows users to export data to CSV for comparison with JotForm
    """
    return render_template('export_csv.html')


@app.route('/api/export-csv-data', methods=['POST'])
@login_required
def export_csv_data():
    """
    API endpoint to export user and package data to CSV
    Supports preview, export, and statistics actions
    """
    try:
        # Get request data
        data = request.get_json()
        action = data.get('action', 'preview')  # preview, export, or stats
        filters = data.get('filters', {})
        
        # Ensure database connection
        db.ensure_connection()
        
        # Handle statistics request
        if action == 'stats':
            # Get database statistics
            stats = {
                'total_users': 0,
                'total_packages': 0,
                'active_rentals': 0,
                'returned_rentals': 0
            }
            
            # Count total users
            db.cursor.execute("SELECT COUNT(*) as count FROM users")
            result = db.cursor.fetchone()
            stats['total_users'] = result['count'] if result else 0
            
            # Count total packages
            db.cursor.execute("SELECT COUNT(*) as count FROM user_packages")
            result = db.cursor.fetchone()
            stats['total_packages'] = result['count'] if result else 0
            
            # Count active rentals (status = 1)
            db.cursor.execute("SELECT COUNT(*) as count FROM users WHERE rental_status = 1")
            result = db.cursor.fetchone()
            stats['active_rentals'] = result['count'] if result else 0
            
            # Count returned rentals (status = 2)
            db.cursor.execute("SELECT COUNT(*) as count FROM users WHERE rental_status = 2")
            result = db.cursor.fetchone()
            stats['returned_rentals'] = result['count'] if result else 0
            
            return jsonify({'success': True, 'stats': stats})
        
        # Build the base SQL query with all possible fields
        base_query = """
            SELECT 
                u.id,
                u.first_name,
                u.last_name,
                u.email,
                u.city,
                u.package_type,
                u.rental_status,
                u.notes,
                u.created_at,
                u.updated_at,
                u.notes_updated_at,
                qr.qr_code_number,
                COALESCE(pkg_counts.total_packages, 0) as package_quantity,
                COALESCE(pkg_counts.available_packages, 0) as available_packages,
                COALESCE(pkg_counts.rented_packages, 0) as rented_packages
            FROM users u
            LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
            LEFT JOIN (
                SELECT 
                    user_id,
                    COUNT(*) as total_packages,
                    SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available_packages,
                    SUM(CASE WHEN status = 'rented_out' THEN 1 ELSE 0 END) as rented_packages
                FROM user_packages
                GROUP BY user_id
            ) pkg_counts ON u.id = pkg_counts.user_id
            WHERE 1=1
        """
        
        # Apply filters
        query_params = []
        
        # Status filter
        if filters.get('status') and filters['status'] != 'all':
            base_query += " AND u.rental_status = %s"
            query_params.append(int(filters['status']))
        
        # City filter
        if filters.get('city'):
            cities = [city.strip() for city in filters['city'].split(',')]
            placeholders = ','.join(['%s'] * len(cities))
            base_query += f" AND u.city IN ({placeholders})"
            query_params.extend(cities)
        
        # Date range filter
        if filters.get('dateFrom'):
            base_query += " AND u.created_at >= %s"
            query_params.append(filters['dateFrom'] + ' 00:00:00')
        
        if filters.get('dateTo'):
            base_query += " AND u.created_at <= %s"
            query_params.append(filters['dateTo'] + ' 23:59:59')
        
        # Add ordering
        base_query += " ORDER BY u.last_name, u.first_name"
        
        # Execute query
        if query_params:
            db.cursor.execute(base_query, query_params)
        else:
            db.cursor.execute(base_query)
        
        # Fetch all results
        results = db.cursor.fetchall()
        
        # Get selected fields (or use defaults)
        selected_fields = filters.get('fields', [
            'id', 'first_name', 'last_name', 'email', 'city',
            'package_type', 'package_quantity', 'qr_code_number', 'rental_status'
        ])
        
        # Filter results to only include selected fields
        filtered_results = []
        for row in results:
            filtered_row = {}
            for field in selected_fields:
                if field in row:
                    # Format datetime fields
                    if field in ['created_at', 'updated_at', 'notes_updated_at'] and row[field]:
                        filtered_row[field] = row[field].strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        filtered_row[field] = row[field]
            filtered_results.append(filtered_row)
        
        # Handle preview action
        if action == 'preview':
            return jsonify({
                'success': True,
                'headers': selected_fields,
                'data': filtered_results
            })
        
        # Handle export action
        elif action == 'export':
            # Determine export format
            export_format = filters.get('format', 'csv')
            
            # Create CSV/TSV output
            output = io.StringIO()
            
            # Set delimiter based on format
            if export_format == 'tsv':
                delimiter = '\t'
                mimetype = 'text/tab-separated-values'
                extension = 'tsv'
            else:
                delimiter = ','
                mimetype = 'text/csv'
                extension = 'csv'
            
            # Handle Excel compatibility
            if export_format == 'csv-excel':
                # Add UTF-8 BOM for Excel
                output.write('\ufeff')
            
            # Create CSV writer
            writer = csv.DictWriter(
                output,
                fieldnames=selected_fields,
                delimiter=delimiter,
                quoting=csv.QUOTE_MINIMAL
            )
            
            # Write headers with proper formatting
            header_names = {}
            for field in selected_fields:
                # Convert field names to readable format
                readable_name = field.replace('_', ' ').title()
                header_names[field] = readable_name
            
            writer.writerow(header_names)
            
            # Write data rows
            for row in filtered_results:
                # Convert None values to empty strings
                clean_row = {k: (v if v is not None else '') for k, v in row.items()}
                writer.writerow(clean_row)
            
            # Get the CSV string
            output.seek(0)
            csv_data = output.getvalue()
            
            # Create response
            response = Response(
                csv_data,
                mimetype=mimetype,
                headers={
                    'Content-Disposition': f'attachment; filename=dinerenblanc_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.{extension}',
                    'Content-Type': f'{mimetype}; charset=utf-8'
                }
            )
            
            return response
        
        else:
            return jsonify({'error': 'Invalid action specified'}), 400
            
    except Exception as e:
        logger.error(f"CSV export error: {str(e)}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500


@app.route('/api/export-comparison-csv', methods=['GET'])
@login_required
def export_comparison_csv():
    """
    Export a comprehensive CSV specifically formatted for comparison with JotForm data
    This includes all relevant fields in a format matching JotForm's export structure
    """
    try:
        # Ensure database connection
        db.ensure_connection()
        
        # Query to get all user data with package information
        # This matches the structure that would come from JotForm
        query = """
            SELECT 
                u.id as 'User ID',
                u.first_name as 'First Name',
                u.last_name as 'Last Name',
                u.email as 'Email',
                u.city as 'City',
                u.package_type as 'Package Type',
                COALESCE(pkg_counts.total_packages, 0) as 'Quantity',
                qr.qr_code_number as 'QR Code',
                CASE 
                    WHEN u.rental_status = 0 THEN 'Not Active'
                    WHEN u.rental_status = 1 THEN 'Active'
                    WHEN u.rental_status = 2 THEN 'Returned'
                    ELSE 'Unknown'
                END as 'Rental Status',
                COALESCE(pkg_counts.available_packages, 0) as 'Available Packages',
                COALESCE(pkg_counts.rented_packages, 0) as 'Rented Packages',
                DATE_FORMAT(u.created_at, '%Y-%m-%d %H:%i:%s') as 'Submission Date',
                DATE_FORMAT(u.updated_at, '%Y-%m-%d %H:%i:%s') as 'Last Updated',
                u.notes as 'Notes'
            FROM users u
            LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
            LEFT JOIN (
                SELECT 
                    user_id,
                    COUNT(*) as total_packages,
                    SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available_packages,
                    SUM(CASE WHEN status = 'rented_out' THEN 1 ELSE 0 END) as rented_packages
                FROM user_packages
                GROUP BY user_id
            ) pkg_counts ON u.id = pkg_counts.user_id
            ORDER BY u.created_at DESC, u.last_name, u.first_name
        """
        
        # Execute query
        db.cursor.execute(query)
        results = db.cursor.fetchall()
        
        # Create CSV output with UTF-8 BOM for Excel compatibility
        output = io.StringIO()
        output.write('\ufeff')  # UTF-8 BOM
        
        if results:
            # Get field names from the first result
            fieldnames = list(results[0].keys())
            
            # Create CSV writer
            writer = csv.DictWriter(
                output,
                fieldnames=fieldnames,
                delimiter=',',
                quoting=csv.QUOTE_MINIMAL
            )
            
            # Write headers
            writer.writeheader()
            
            # Write data rows
            for row in results:
                # Convert None values to empty strings
                clean_row = {k: (v if v is not None else '') for k, v in row.items()}
                writer.writerow(clean_row)
        else:
            # If no results, still create headers
            fieldnames = [
                'User ID', 'First Name', 'Last Name', 'Email', 'City',
                'Package Type', 'Quantity', 'QR Code', 'Rental Status',
                'Available Packages', 'Rented Packages', 'Submission Date',
                'Last Updated', 'Notes'
            ]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
        
        # Get the CSV string
        output.seek(0)
        csv_data = output.getvalue()
        
        # Create response
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        response = Response(
            csv_data,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=dinerenblanc_comparison_{timestamp}.csv',
                'Content-Type': 'text/csv; charset=utf-8'
            }
        )
        
        logger.info(f"Exported comparison CSV with {len(results)} records")
        return response
        
    except Exception as e:
        logger.error(f"Comparison CSV export error: {str(e)}")
        return jsonify({'error': f'Export failed: {str(e)}'}), 500


@app.route('/api/admin/delete-user/<int:user_id>', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    """Delete user and all associated data"""
    try:
        db.ensure_connection()
        
        # Get user info before deletion for logging
        db.cursor.execute("SELECT first_name, last_name, email FROM users WHERE id = %s", (user_id,))
        user_info = db.cursor.fetchone()
        
        if not user_info:
            return jsonify({"success": False, "error": "User not found"}), 404
        
        # Disable foreign key checks for easier deletion
        db.cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        
        try:
            # Delete in any order since foreign keys are disabled
            # 1. Delete email logs
            db.cursor.execute("DELETE FROM email_logs WHERE user_id = %s", (user_id,))
            logs_deleted = db.cursor.rowcount
            
            # 2. Delete user packages
            db.cursor.execute("DELETE FROM user_packages WHERE user_id = %s", (user_id,))
            packages_deleted = db.cursor.rowcount
            
            # 3. Delete QR codes
            db.cursor.execute("DELETE FROM qr_codes WHERE user_id = %s", (user_id,))
            qr_deleted = db.cursor.rowcount
            
            # 4. Finally delete the user
            db.cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            user_deleted = db.cursor.rowcount
            
            # Re-enable foreign key checks
            db.cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            
            # Commit all changes
            db.connection.commit()
            
            if user_deleted == 0:
                return jsonify({"success": False, "error": "Failed to delete user"}), 500
            
            logger.info(f"Admin deleted user {user_id}: {user_info['first_name']} {user_info['last_name']} ({user_info['email']}) - {packages_deleted} packages, {qr_deleted} QR codes, {logs_deleted} logs deleted")
            
            return jsonify({
                "success": True,
                "message": f"User '{user_info['first_name']} {user_info['last_name']}' and all associated data deleted successfully"
            })
            
        except Exception as delete_error:
            # Re-enable foreign keys and rollback on error
            db.cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            db.connection.rollback()
            logger.error(f"Error during deletion: {str(delete_error)}")
            raise delete_error
            
    except Exception as e:
        logger.error(f"Admin user deletion error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/verify-qr', methods=['POST'])
@login_required
def verify_qr_api():
    """API endpoint to verify QR codes from the scanner"""
    try:
        # Get JSON data from request
        data = request.get_json()
        
        # Validate request data
        if not data or 'qr_code' not in data:
            return jsonify({
                "success": False,
                "error": "Missing QR code in request"
            }), 400
        
        # Extract QR code from request
        qr_code = str(data['qr_code']).strip()
        
        # Validate QR code format (should be 4-digit number)
        if not qr_code:
            return jsonify({
                "success": False,
                "error": "QR code cannot be empty"
            }), 400
        
        # Log the QR verification attempt
        logger.info(f"API QR verification attempt: {qr_code}")
        
        # Ensure database connection is active
        db.ensure_connection()
        
        # Verify QR code in database
        user_data = db.verify_qr_code(qr_code)
        
        if not user_data:
            logger.warning(f"Invalid QR code attempted: {qr_code}")
            return jsonify({
                "success": False,
                "error": "Invalid QR code. Please check the code and try again."
            }), 404
        
        # Get additional user information
        packages = db.get_user_packages(user_data['user_id'])
        package_summary = db.get_user_package_summary(user_data['user_id'])
        
        # Ensure package_summary has default values if None
        if not package_summary:
            package_summary = {
                'total_packages': 0,
                'available_packages': 0,
                'rented_packages': 0,
                'has_packages': False,
                'all_returned': True
            }
        
        # Format response data
        response_data = {
            "success": True,
            "user": {
                "user_id": user_data['user_id'],
                "first_name": user_data['first_name'],
                "last_name": user_data['last_name'],
                "email": user_data['email'],
                "city": user_data.get('city', ''),
                "package_type": user_data.get('package_type', ''),
                "rental_status": user_data['rental_status'],
                "qr_code_number": user_data['qr_code_number'],
                "notes": user_data.get('notes', ''),
                "notes_updated_at": user_data.get('notes_updated_at')
            },
            "packages": packages,
            "package_summary": package_summary,
            "message": f"Found user: {user_data['first_name']} {user_data['last_name']}"
        }
        
        # Log successful verification
        logger.info(f"QR code verified successfully: {qr_code} -> User {user_data['user_id']} ({user_data['first_name']} {user_data['last_name']})")
        
        return jsonify(response_data)
        
    except Exception as e:
        # Log the error for debugging
        logger.error(f"QR verification API error: {str(e)}")
        
        # Return generic error message to client
        return jsonify({
            "success": False,
            "error": "An error occurred while verifying the QR code. Please try again."
        }), 500

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

@app.route('/webhook-capture', methods=['POST'])
def webhook_capture():
    """
    Simple webhook endpoint to capture all incoming JotForm data to a text file
    This endpoint logs all form data and JSON data to help identify field names
    """
    import json
    from datetime import datetime
    import os
    
    try:
        # Get current timestamp for logging
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(app.static_folder, 'webhook_logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Create log file path with date
        log_filename = f"webhook_capture_{datetime.now().strftime('%Y%m%d')}.txt"
        log_filepath = os.path.join(logs_dir, log_filename)
        
        # Initialize data storage for logging
        captured_data = {
            'timestamp': timestamp,
            'content_type': request.content_type,
            'method': request.method,
            'form_data': {},
            'json_data': {},
            'headers': {},
            'args': {},
            'raw_request_extracted': {}
        }
        
        # Capture request headers (useful for debugging)
        captured_data['headers'] = dict(request.headers)
        
        # Capture URL arguments (GET parameters)
        captured_data['args'] = request.args.to_dict()
        
        # Handle different content types
        logger.info(f"Webhook capture - Content Type: {request.content_type}")
        
        # Case 1: Handle multipart form data (typical JotForm webhook format)
        if request.content_type and 'multipart/form-data' in request.content_type:
            # Convert form data to dictionary
            captured_data['form_data'] = request.form.to_dict()
            
            # JotForm often sends rawRequest as a JSON string in form data
            if 'rawRequest' in captured_data['form_data']:
                try:
                    # Try to parse rawRequest as JSON
                    raw_request_str = captured_data['form_data']['rawRequest']
                    captured_data['raw_request_extracted'] = json.loads(raw_request_str)
                    logger.info("Successfully parsed rawRequest from form data")
                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse rawRequest as JSON: {e}")
                    captured_data['raw_request_extracted'] = {"error": f"JSON parse failed: {str(e)}"}
        
        # Case 2: Handle JSON content type
        elif request.content_type and 'application/json' in request.content_type:
            try:
                captured_data['json_data'] = request.get_json()
                logger.info("Successfully captured JSON data")
            except Exception as e:
                logger.warning(f"Could not parse JSON data: {e}")
                captured_data['json_data'] = {"error": f"JSON parse failed: {str(e)}"}
        
        # Case 3: Handle URL encoded form data
        elif request.content_type and 'application/x-www-form-urlencoded' in request.content_type:
            captured_data['form_data'] = request.form.to_dict()
            logger.info("Successfully captured form data")
        
        # Case 4: Unknown content type - try to capture what we can
        else:
            logger.warning(f"Unknown content type: {request.content_type}")
            # Try to get form data anyway
            try:
                captured_data['form_data'] = request.form.to_dict()
            except:
                pass
            # Try to get JSON data anyway
            try:
                captured_data['json_data'] = request.get_json()
            except:
                pass
        
        # Write captured data to log file
        with open(log_filepath, 'a', encoding='utf-8') as log_file:
            log_file.write("=" * 80 + "\n")
            log_file.write(f"WEBHOOK CAPTURE - {timestamp}\n")
            log_file.write("=" * 80 + "\n")
            
            # Write all captured data in a readable format
            for key, value in captured_data.items():
                log_file.write(f"\n--- {key.upper().replace('_', ' ')} ---\n")
                if isinstance(value, dict):
                    # Pretty print dictionaries
                    log_file.write(json.dumps(value, indent=2, ensure_ascii=False))
                else:
                    log_file.write(str(value))
                log_file.write("\n")
            
            log_file.write("\n" + "=" * 80 + "\n\n")
        
        # Count total fields captured for logging
        total_fields = 0
        if captured_data['form_data']:
            total_fields += len(captured_data['form_data'])
        if captured_data['json_data']:
            total_fields += len(captured_data['json_data']) if isinstance(captured_data['json_data'], dict) else 0
        if captured_data['raw_request_extracted']:
            total_fields += len(captured_data['raw_request_extracted']) if isinstance(captured_data['raw_request_extracted'], dict) else 0
        
        # Log success message
        logger.info(f"Webhook data captured successfully. Total fields: {total_fields}. Logged to: {log_filename}")
        
        # Return success response (JotForm expects this)
        return jsonify({
            "status": "success",
            "message": "Webhook data captured successfully",
            "timestamp": timestamp,
            "log_file": log_filename,
            "fields_captured": total_fields
        }), 200
        
    except Exception as e:
        # Log any errors that occur during capture
        error_msg = f"Webhook capture error: {str(e)}"
        logger.error(error_msg)
        
        try:
            # Try to log the error to file as well
            error_log_path = os.path.join(app.static_folder, 'webhook_logs', 'capture_errors.txt')
            with open(error_log_path, 'a', encoding='utf-8') as error_file:
                error_file.write(f"{timestamp} - ERROR: {error_msg}\n")
        except:
            pass  # Don't let error logging break the response
        
        # Return error response
        return jsonify({
            "status": "error",
            "message": "Failed to capture webhook data",
            "error": str(e),
            "timestamp": timestamp
        }), 500


# Optional: Add a route to view captured webhook data
@app.route('/api/view-webhook-logs')
@admin_required  # Only allow admin access
def view_webhook_logs():
    """
    Admin endpoint to view captured webhook logs
    Returns a list of available log files and their contents
    """
    try:
        logs_dir = os.path.join(app.static_folder, 'webhook_logs')
        
        if not os.path.exists(logs_dir):
            return jsonify({"error": "No webhook logs directory found"}), 404
        
        # Get list of log files
        log_files = []
        for filename in os.listdir(logs_dir):
            if filename.startswith('webhook_capture_') and filename.endswith('.txt'):
                filepath = os.path.join(logs_dir, filename)
                file_size = os.path.getsize(filepath)
                modified_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                
                log_files.append({
                    'filename': filename,
                    'size_bytes': file_size,
                    'modified': modified_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'download_url': f'/api/download-webhook-log/{filename}'
                })
        
        # Sort by modification time (newest first)
        log_files.sort(key=lambda x: x['modified'], reverse=True)
        
        return jsonify({
            "success": True,
            "log_files": log_files,
            "total_files": len(log_files)
        })
        
    except Exception as e:
        logger.error(f"Error viewing webhook logs: {str(e)}")
        return jsonify({"error": str(e)}), 500



@app.route('/lookup-search-results', methods=['POST'])
def lookup_search_results():
    """Handle search requests and display results"""
    search_type = request.form.get('search_type')
    search_term = request.form.get('search_term', '').strip()
    
    if not search_term:
        return render_template('lookup-search-results.html', 
                             error="Please enter a search term", 
                             users=[], 
                             search_term=search_term, 
                             search_type=search_type)
    
    try:
        db = DatabaseHandler()
        users = []
        
        # Execute appropriate search based on type
        if search_type == 'qr_code':
            # For QR code, verify format and search
            if len(search_term) == 4 and search_term.isdigit():
                user = db.verify_qr_code(search_term)
                if user:
                    users = [user]
            else:
                return render_template('lookup-search-results.html',
                                     error="QR code must be exactly 4 digits",
                                     users=[],
                                     search_term=search_term,
                                     search_type=search_type)
                                     
        elif search_type == 'first_name':
            users = db.search_by_first_name(search_term)
            
        elif search_type == 'last_name':
            users = db.search_by_last_name(search_term)
            
        else:
            return render_template('lookup-search-results.html',
                                 error="Invalid search type",
                                 users=[],
                                 search_term=search_term,
                                 search_type=search_type)
        
        # Enhance users with package summaries
        for user in users:
            user['package_summary'] = db.get_user_package_summary(user['user_id'])
            
        db.close()
        
        return render_template('lookup-search-results.html',
                             users=users,
                             search_term=search_term,
                             search_type=search_type)
                             
    except Exception as e:
        logger.error(f"Search error: {e}")
        return render_template('lookup-search-results.html',
                             error=f"Search failed: {str(e)}",
                             users=[],
                             search_term=search_term,
                             search_type=search_type)

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