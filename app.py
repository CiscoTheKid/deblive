from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory, flash
from config import Config
from db_handler import DatabaseHandler
from qr_email_sender import QREmailSender
import os
import logging
from datetime import datetime
from ssl_config import SSLConfig
import pandas as pd
import threading
from functools import wraps
import hmac
import hashlib
from typing import Dict, Tuple
import json
import requests
from werkzeug.datastructures import ImmutableMultiDict


qr_sender = QREmailSender()

app = Flask(__name__, static_folder='static')
app.secret_key = Config.FLASK_SECRET_KEY
db = DatabaseHandler()
qr_sender = QREmailSender()
email_progress = {
    'status': 'idle',
    'current': 0,
    'total': 0,
    'current_email': ''
}

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
app.logger.setLevel(logging.DEBUG)
app.debug = True
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)



@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*'
    return response



def process_jotform_submission(form_data):
    """Process JotForm submission and store in database"""
    try:
        app.logger.debug(f"Processing submission data: {form_data}")
        
        # Extract submission ID
        submission_id = form_data.get('submissionID')
        if not submission_id:
            raise ValueError("Missing submission ID")

        # Extract user information from rawRequest
        raw_request = form_data.get('rawRequest', {})
        app.logger.debug(f"Raw request data: {raw_request}")

        # Extract fields using the correct form field names
        first_name = raw_request.get('q3_first_name', '').strip()
        last_name = raw_request.get('q4_last_name', '').strip()
        email = raw_request.get('q5_email', '').strip()
        city = raw_request.get('q7_City', '').strip()
        
        # Extract package type from the array if present
        package_type = None
        package_array = raw_request.get('q8_package_type', [])
        if isinstance(package_array, list) and len(package_array) > 0:
            package_type = package_array[0]
        elif isinstance(package_array, str):
            package_type = package_array

        # Log extracted data
        app.logger.debug(f"""
            Extracted Data:
            Name: {first_name} {last_name}
            Email: {email}
            City: {city}
            Package: {package_type}
            Submission ID: {submission_id}
        """)

        # Validate required fields
        if not all([first_name, last_name, email]):
            raise ValueError(f"Missing required fields. First Name: {first_name}, Last Name: {last_name}, Email: {email}")

        # Check if submission already exists
        db.cursor.execute("""
            SELECT id FROM users 
            WHERE jotform_submission_id = %s
        """, (submission_id,))
        existing_user = db.cursor.fetchone()

        if existing_user:
            # Update existing record
            db.cursor.execute("""
                UPDATE users 
                SET first_name = %s,
                    last_name = %s,
                    email = %s,
                    city = %s,
                    package_type = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE jotform_submission_id = %s
            """, (first_name, last_name, email, city, package_type, submission_id))
            user_id = existing_user['id']
            app.logger.info(f"Updated existing user record: {user_id}")
        else:
            # Insert new record
            db.cursor.execute("""
                INSERT INTO users 
                (first_name, last_name, email, city, package_type, jotform_submission_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (first_name, last_name, email, city, package_type, submission_id))
            user_id = db.cursor.lastrowid
            app.logger.info(f"Created new user record: {user_id}")

        db.connection.commit()

        # Generate and send QR code
        success, result = qr_sender.send_email(
            email,
            first_name,
            last_name,
            city,
            package_type
        )

        if not success:
            raise Exception(f"Failed to send QR code email: {result}")

        return True, "Submission processed successfully", user_id

    except Exception as e:
        if hasattr(db, 'connection'):
            db.connection.rollback()
        error_msg = f"Error processing submission: {str(e)}"
        app.logger.error(error_msg)
        return False, error_msg, None

def parse_payment_fields(form_data):
    """Parse and debug JotForm payment fields"""
    try:
        raw_request = form_data.get('rawRequest', {})
        
        # Get the package_type data which contains the products
        package_data = raw_request.get('q11_package_type', {})
        
        # Extract package information from the products array
        selected_package = None
        if package_data and 'products' in package_data:
            for product in package_data['products']:
                selected_package = {
                    'name': product.get('productName', ''),
                    'quantity': product.get('quantity', 0),
                    'price': product.get('unitPrice', 0),
                    'currency': product.get('currency', 'USD'),
                    'subtotal': product.get('subTotal', 0)
                }
                break  # Get the first product

        # Extract billing information
        billing_info = {
            'address1': package_data.get('addr_line1', ''),
            'address2': package_data.get('addr_line2', ''),
            'city': package_data.get('city', ''),
            'state': package_data.get('state', ''),
            'postal': package_data.get('postal', ''),
            'country': package_data.get('country', '')
        }

        return {
            'package_info': selected_package,
            'billing_info': billing_info,
            'total_info': package_data.get('totalInfo', {}),
            'raw_package_data': package_data  # Include raw data for debugging
        }
        
    except Exception as e:
        return {
            'error': str(e),
            'raw_data': raw_request
        }
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'role' not in session or session['role'] != 'admin':
            flash('Admin access required', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function



def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def staff_or_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please log in first', 'error')
            return redirect(url_for('login'))
            
        if 'role' not in session:
            flash('Session error: no role assigned', 'error')
            return redirect(url_for('login'))
            
        if session['role'] not in ['admin', 'user']:
            flash('Access denied: insufficient privileges', 'error')
            return redirect(url_for('login'))
            
        return f(*args, **kwargs)
    return decorated_function


@app.route('/api/jotform-webhook', methods=['POST'])
def jotform_webhook():
    """Handle incoming JotForm webhook"""
    try:
        # Get the form data and handle different content types
        if request.content_type.startswith('multipart/form-data'):
            form_data = request.form.to_dict()
            if 'rawRequest' in form_data and isinstance(form_data['rawRequest'], str):
                try:
                    form_data['rawRequest'] = json.loads(form_data['rawRequest'])
                except json.JSONDecodeError:
                    app.logger.error("Failed to parse rawRequest JSON")
                    return jsonify({"error": "Invalid rawRequest format"}), 400
        else:
            form_data = request.json

        if not form_data:
            app.logger.error("No form data received")
            return jsonify({"error": "No form data received"}), 400

        # Extract submission ID
        submission_id = form_data.get('submissionID')
        if not submission_id:
            raise ValueError("Missing submission ID")

        # Get raw request data
        raw_request = form_data.get('rawRequest', {})
        
        # Extract user information
        first_name = raw_request.get('q3_first_name', '').strip()
        last_name = raw_request.get('q4_last_name', '').strip()
        email = raw_request.get('q5_email', '').strip()
        city = raw_request.get('q7_City', '').strip()

        # Parse package information
        package_data = raw_request.get('q11_package_type', {})
        products = package_data.get('products', [])
        
        package_type = None
        if products and len(products) > 0:
            product = products[0]
            package_type = f"{product.get('productName', 'Unknown')} (Qty: {product.get('quantity', 0)})"

        # Log the data before database operation
        app.logger.debug(f"""
        ====== PROCESSING SUBMISSION ======
        Submission ID: {submission_id}
        First Name: {first_name}
        Last Name: {last_name}
        Email: {email}
        City: {city}
        Package Type: {package_type}
        ================================
        """)

        # Validate required fields
        if not all([first_name, last_name, email]):
            error_details = {
                'first_name': bool(first_name),
                'last_name': bool(last_name),
                'email': bool(email)
            }
            raise ValueError(f"Missing required fields: {error_details}")

        try:
            # Check if submission already exists
            db.cursor.execute("""
                SELECT id FROM users 
                WHERE jotform_submission_id = %s
            """, (submission_id,))
            existing_user = db.cursor.fetchone()

            if existing_user:
                # Log update attempt
                app.logger.debug(f"Updating existing user with ID: {existing_user['id']}")
                
                # Update existing record
                db.cursor.execute("""
                    UPDATE users 
                    SET first_name = %s,
                        last_name = %s,
                        email = %s,
                        city = %s,
                        package_type = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE jotform_submission_id = %s
                """, (first_name, last_name, email, city, package_type, submission_id))
                user_id = existing_user['id']
                app.logger.info(f"Updated existing user record: {user_id}")
            else:
                # Log insert attempt
                app.logger.debug("Creating new user record")
                
                # Insert new record
                db.cursor.execute("""
                    INSERT INTO users 
                    (first_name, last_name, email, city, package_type, jotform_submission_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (first_name, last_name, email, city, package_type, submission_id))
                user_id = db.cursor.lastrowid
                app.logger.info(f"Created new user record: {user_id}")

            db.connection.commit()
            app.logger.debug("Database operation successful")

        except mysql.connector.Error as db_error:
            app.logger.error(f"Database error: {str(db_error)}")
            raise

        # Generate and send QR code
        app.logger.debug("Initializing QR email sender")
        qr_sender = QREmailSender()
        success, result = qr_sender.send_email(
            email,
            first_name,
            last_name,
            city,
            package_type
        )

        if not success:
            raise Exception(f"Failed to send QR code email: {result}")

        app.logger.debug("Webhook processing completed successfully")
        response = {
            "status": "success",
            "message": "Submission processed successfully",
            "user_id": user_id,
            "package_info": package_type
        }
        return jsonify(response), 200

    except Exception as e:
        error_msg = f"Error in webhook: {str(e)}"
        app.logger.error(error_msg)
        if hasattr(db, 'connection'):
            db.connection.rollback()
        return jsonify({"status": "error", "message": error_msg}), 500
    
# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Log login attempt (but never log passwords!)
        app.logger.debug(f"Login attempt for username: {username}")
        
        # Admin login check
        if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
            session['logged_in'] = True
            session['role'] = 'admin'
            session['username'] = username
            app.logger.info(f"Successful admin login: {username}")
            return redirect(url_for('home'))
            
        # Staff login check
        elif username == Config.USER_CREDENTIALS and password == Config.USER_PASSWORD:
            session['logged_in'] = True
            session['role'] = 'user'
            session['username'] = username
            app.logger.info(f"Successful staff login: {username}")
            # Now redirecting to home instead of scan
            return redirect(url_for('home'))
            
        # Failed login
        error = 'Invalid credentials'
        app.logger.warning(f"Failed login attempt for username: {username}")
        
    # GET request or failed login
    return render_template('login.html', error=error)

# Logout route
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# Serve static files
@app.route('/static/<path:filename>')
def serve_static(filename):
    response = send_from_directory('static', filename)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# Home route
@app.route('/')
@login_required
def home():
    return render_template('home.html', title='QR System')

# Scanner page route
@app.route('/scan')
@login_required  # This route is accessible to all logged-in users
def scan():
    return render_template('scan.html')


@app.route('/email-client', methods=['GET', 'POST'])
@admin_required
def email_client():
    if request.method == 'POST':
        app.logger.info("Processing email client POST request")
        
        if 'csv_file' not in request.files:
            app.logger.error("No file part in request")
            flash('No file uploaded', 'error')
            return redirect(url_for('email_client'))
        
        file = request.files['csv_file']
        if file.filename == '':
            app.logger.error("No selected file")
            flash('No file selected', 'error')
            return redirect(url_for('email_client'))
        
        if not file.filename.endswith('.csv'):
            app.logger.error(f"Invalid file type: {file.filename}")
            flash('Please upload a CSV file', 'error')
            return redirect(url_for('email_client'))
        
        try:
            temp_path = os.path.join(app.static_folder, 'temp', file.filename)
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            file.save(temp_path)
            
            app.logger.info(f"Processing CSV file: {temp_path}")
            results = qr_sender.process_csv(temp_path)
            os.remove(temp_path)
            
            if not results:
                app.logger.error("No results returned from CSV processing")
                flash('No results were generated from the CSV file', 'error')
                return redirect(url_for('email_client'))
            
            total = len(results)
            successful = sum(1 for r in results if r['success'])
            failed = total - successful
            
            app.logger.info(f"CSV Processing Summary - Total: {total}, Successful: {successful}, Failed: {failed}")
            
            for result in results:
                if not result['success']:
                    app.logger.error(f"Email failed for {result['email']}: {result['result']}")
            
            return render_template(
                'email_client.html',
                results=results,
                summary={
                    'total': total,
                    'successful': successful,
                    'failed': failed
                }
            )
            
        except Exception as e:
            app.logger.exception(f"Error processing file: {str(e)}")
            flash(f'Error processing file: {str(e)}', 'error')
            return redirect(url_for('email_client'))
            
    return render_template('email_client.html')


# QR code verification API endpoint
@app.route('/api/lookup', methods=['POST'])
@login_required
def api_lookup():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        qr_code = data.get('qr_code')
        if not qr_code:
            return jsonify({"error": "No QR code provided"}), 400

        user_data = db.verify_qr_code(qr_code)
        if user_data:
            return jsonify({"success": True, "user": user_data})
        return jsonify({"error": "Invalid QR code"}), 404

    except Exception as e:
        app.logger.error(f"Error in API lookup: {str(e)}")
        return jsonify({"error": "Server error", "details": str(e)}), 500
    
#Toggle Switch using js
@app.route('/api/toggle-rental/<int:user_id>', methods=['POST'])
@login_required
def toggle_rental_status(user_id):
    try:
        data = request.get_json()
        new_status = int(data.get('status', 0))  # Convert to integer
        
        if new_status not in [0, 1, 2]:
            return jsonify({"error": "Invalid status value"}), 400
            
        db.update_rental_status(user_id, new_status)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    
# Manual lookup route
@app.route('/lookup', methods=['GET', 'POST'])
@login_required
def lookup():
    if request.method == 'GET':
        qr_code = request.args.get('qr_code')
        if qr_code:
            try:
                user_data = db.verify_qr_code(qr_code)
                if user_data:
                    # Debug logging
                    app.logger.debug(f"Found user data:")
                    app.logger.debug(f"Name: {user_data.get('first_name')} {user_data.get('last_name')}")
                    app.logger.debug(f"City: {user_data.get('city')}")
                    app.logger.debug(f"Package Type: {user_data.get('package_type')}")
                    
                    # Ensure data is properly formatted for template
                    user_data['city'] = user_data.get('city', '') or 'Not specified'
                    user_data['package_type'] = user_data.get('package_type', '') or 'Not specified'
                    
                    return render_template('user_details.html', user=user_data)
                    
                app.logger.warning(f"No user found for QR code: {qr_code}")
                return render_template('lookup.html', error="Invalid QR code")
                
            except Exception as e:
                app.logger.error(f"Error in lookup: {str(e)}")
                return render_template('lookup.html', error=str(e))
                
        return render_template('lookup.html')
    
    elif request.method == 'POST':
        search_type = request.form.get('search_type')
        search_term = request.form.get('search_term')

        if not search_term:
            return render_template('lookup.html', error="Please enter a search term")

        try:
            user_data = None
            
            if search_type == 'qr_code':
                user_data = db.verify_qr_code(search_term)
                if user_data:
                    # Debug logging
                    app.logger.debug(f"Found user data:")
                    app.logger.debug(f"City: {user_data.get('city')}")
                    app.logger.debug(f"Package Type: {user_data.get('package_type')}")
                    return render_template('user_details.html', user=user_data)
                    
            elif search_type == 'first_name':
                users = db.search_by_first_name(search_term)
                if users:
                    return render_template('search_results.html', users=users)
                    
            elif search_type == 'last_name':
                users = db.search_by_last_name(search_term)
                if users:
                    return render_template('search_results.html', users=users)
            
            return render_template('lookup.html', 
                                error=f"No user found for this {search_type.replace('_', ' ')}")
            
        except Exception as e:
            app.logger.error(f"Error in lookup POST: {str(e)}")
            return render_template('lookup.html', error=str(e))
        

@app.route('/api/reset-rental/<int:user_id>', methods=['POST'])
@login_required
def reset_rental_status(user_id):
    try:
        db.update_rental_status(user_id, 0)  # Set to Not Active
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500  
    
@app.route('/api/save-notes', methods=['POST'])
@login_required
def save_notes():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        notes = data.get('notes')
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID is required'}), 400
            
        db.cursor.execute("""
            UPDATE users 
            SET notes = %s,
                notes_updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (notes, user_id))
        
        db.connection.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        app.logger.error(f"Error saving notes: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
            
# Email logs route
@app.route('/email-logs')
@login_required
def email_logs():
    try:
        query = """
        SELECT 
            u.id,
            u.first_name,
            u.last_name,
            u.email,
            u.rental_status,
            qr.qr_code_number,
            COALESCE(
                (SELECT created_at 
                 FROM email_logs 
                 WHERE user_id = u.id 
                 ORDER BY created_at DESC 
                 LIMIT 1),
                NULL
            ) as last_action
        FROM users u
        LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
        ORDER BY u.last_name, u.first_name
        """
        db.cursor.execute(query)
        users = db.cursor.fetchall()
        
        return render_template('email_logs.html', users=users)
    except Exception as e:
        return render_template('email_logs.html', error=str(e), users=[])

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# Health check endpoint
@app.route('/health')
def health_check():
    try:
        db.cursor.execute("SELECT 1")
        db.cursor.fetchone()
        return jsonify({
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/admin')
@admin_required
def admin():
    return render_template('admin.html')

@app.route('/api/stats')
def get_stats():
    try:
        db.connect()  
        stats = db.get_database_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/reset-database', methods=['POST'])
@admin_required
def reset_database():
    try:
        db.reset_database()
        db.connect()
        return jsonify({"message": "Database reset successful"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

@app.route('/api/filter-users/<status>')
@login_required
def filter_users(status):
    """
    Filter users by rental status
    
    Args:
        status: 'all', 'not_active' (0), 'active' (1), or 'returned' (2)
    """
    try:
        if status not in ['all', 'not_active', 'active', 'returned']:
            return jsonify({'error': 'Invalid status parameter'}), 400
            
        # Base query with common joins
        base_query = """
            SELECT 
                u.id,
                u.first_name,
                u.last_name,
                u.email,
                u.rental_status,
                qr.qr_code_number,
                COALESCE(
                    (SELECT created_at 
                     FROM email_logs 
                     WHERE user_id = u.id 
                     ORDER BY created_at DESC 
                     LIMIT 1),
                    NULL
                ) as last_action
            FROM users u
            LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
        """
        
        # Add WHERE clause based on status
        if status == 'active':
            base_query += " WHERE u.rental_status = 1"  # Active Rental
        elif status == 'returned':
            base_query += " WHERE u.rental_status = 2"  # Returned
        elif status == 'not_active':
            base_query += " WHERE u.rental_status = 0"  # Not Active
            
        base_query += " ORDER BY u.last_name, u.first_name"
        
        # Execute query
        db.cursor.execute(base_query)
        users = db.cursor.fetchall()
        
        # Convert datetime objects to strings for JSON serialization
        for user in users:
            if user['last_action']:
                user['last_action'] = user['last_action'].strftime('%Y-%m-%d %H:%M:%S')
                
        return jsonify({'users': users})
        
    except Exception as e:
        app.logger.error(f"Error filtering users: {str(e)}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    ssl_context = (SSLConfig.SSL_CERTIFICATE, SSLConfig.SSL_KEY)
    print(f"Starting server on https://{SSLConfig.HOST}:{SSLConfig.PORT}")
    
    app.run(
        host=SSLConfig.HOST,
        port=SSLConfig.PORT,
        ssl_context=ssl_context,
        debug=Config.FLASK_DEBUG
    )