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

# Initialize Flask app and components
app = Flask(__name__, static_folder='static')
app.secret_key = Config.FLASK_SECRET_KEY
db = DatabaseHandler()
qr_sender = QREmailSender()

# Email progress tracking
email_progress = {
    'status': 'idle',
    'current': 0,
    'total': 0,
    'current_email': ''
}

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
app.logger.setLevel(logging.DEBUG)
app.debug = True
logger = logging.getLogger(__name__)

# Form field mapping configuration for multiple JotForm forms
# Each form can have different field names - add new forms here
FORM_FIELD_MAPPINGS = {
    # Original form mapping
    'original_form': {
        'first_name': 'q3_first_name',
        'last_name': 'q4_last_name', 
        'email': 'q5_email',
        'city': 'q7_City',
        'package_type': 'q8_package_type',
        'package_products': 'q11_package_type',
        'phone': None,  # Not available in original form
        'paid_status': None  # Not available in original form
    },
    
    # Second form mapping (based on your webhook logs)
    'second_form': {
        'first_name': 'q34_first_name',
        'last_name': 'q35_last_name',
        'email': 'q5_email',
        'city': 'q8_city',
        'package_type': 'q17_package_type',
        'package_products': 'q17_package_type',  # Same field likely contains product data
        'phone': 'q6_phoneNumber',  # Phone number field available
        'paid_status': 'q27_paidIn'  # Backend field for payment status
    }
}

# Headers configuration
@app.after_request
def after_request(response):
    """Add CORS headers and permissions policy to all responses"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*'
    return response

# Authentication decorators
def admin_required(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or 'role' not in session or session['role'] != 'admin':
            flash('Admin access required', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    """Decorator to require any user authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def staff_or_admin_required(f):
    """Decorator to require staff or admin authentication"""
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

# Form processing helper functions
def detect_form_type(raw_request):
    """
    Detect which form this submission came from based on available fields
    
    Args:
        raw_request (dict): The rawRequest data from JotForm
        
    Returns:
        str: The form type key, or 'unknown' if no match found
    """
    try:
        available_fields = set(raw_request.keys())
        app.logger.debug(f"Available fields in submission: {available_fields}")
        
        # Check each form mapping to see which one matches
        best_match = 'unknown'
        best_score = 0
        
        for form_type, field_mapping in FORM_FIELD_MAPPINGS.items():
            # Count how many expected fields are present
            matching_fields = 0
            total_fields = sum(1 for field in field_mapping.values() if field is not None)
            
            for logical_field, actual_field in field_mapping.items():
                if actual_field and actual_field in available_fields:
                    matching_fields += 1
            
            # Calculate match percentage
            match_percentage = matching_fields / total_fields if total_fields > 0 else 0
            app.logger.debug(f"Form type '{form_type}': {matching_fields}/{total_fields} fields match ({match_percentage:.2%})")
            
            # Keep track of best match
            if match_percentage > best_score:
                best_score = match_percentage
                best_match = form_type
        
        # Only accept matches with at least 60% field overlap
        if best_score > 0.6:
            app.logger.info(f"Detected form type: {best_match} (confidence: {best_score:.2%})")
            return best_match
        else:
            app.logger.warning(f"Could not detect form type - best match was {best_match} with {best_score:.2%} confidence")
            return 'unknown'
        
    except Exception as e:
        app.logger.error(f"Error detecting form type: {str(e)}")
        return 'unknown'

def extract_user_data(raw_request, form_type):
    """
    Extract user data from rawRequest based on detected form type
    Fixed to handle both dict and string field values properly
    
    Args:
        raw_request (dict): The rawRequest data from JotForm
        form_type (str): The detected form type
        
    Returns:
        dict: Extracted user data with standardized field names
    """
    try:
        # Get the field mapping for this form type
        if form_type not in FORM_FIELD_MAPPINGS:
            raise ValueError(f"Unknown form type: {form_type}")
        
        field_mapping = FORM_FIELD_MAPPINGS[form_type]
        user_data = {}
        
        # Extract first name - handle both dict and string formats
        first_name_field = field_mapping.get('first_name')
        if first_name_field and first_name_field in raw_request:
            field_value = raw_request[first_name_field]
            # Handle name fields that might be objects with 'first' and 'last' properties
            if isinstance(field_value, dict):
                user_data['first_name'] = field_value.get('first', '').strip()
            else:
                user_data['first_name'] = str(field_value).strip()
        
        # Extract last name - handle both dict and string formats
        last_name_field = field_mapping.get('last_name')
        if last_name_field and last_name_field in raw_request:
            field_value = raw_request[last_name_field]
            if isinstance(field_value, dict):
                user_data['last_name'] = field_value.get('last', '').strip()
            else:
                user_data['last_name'] = str(field_value).strip()
        
        # Extract email
        email_field = field_mapping.get('email')
        if email_field and email_field in raw_request:
            user_data['email'] = str(raw_request[email_field]).strip()
        
        # Extract city
        city_field = field_mapping.get('city')
        if city_field and city_field in raw_request:
            user_data['city'] = str(raw_request[city_field]).strip()
        
        # Extract phone number
        phone_field = field_mapping.get('phone')
        if phone_field and phone_field in raw_request:
            phone_value = raw_request[phone_field]
            if isinstance(phone_value, dict):
                # JotForm phone fields often have 'full' property
                user_data['phone'] = phone_value.get('full', '').strip()
            else:
                user_data['phone'] = str(phone_value).strip()
        
        # Extract package type and quantity - try both simple and product-based selection
        package_type = None
        quantity = 1  # Default quantity
        
        # First try simple package selection
        package_field = field_mapping.get('package_type')
        if package_field and package_field in raw_request:
            package_value = raw_request[package_field]
            
            # Handle different package field formats
            if isinstance(package_value, list) and len(package_value) > 0:
                package_type = package_value[0]
            elif isinstance(package_value, str):
                package_type = package_value
            elif isinstance(package_value, dict):
                # Handle product-based package selection
                if 'products' in package_value or '0' in package_value:
                    # This looks like a product selection field
                    if 'products' in package_value:
                        products = package_value['products']
                        if products and len(products) > 0:
                            product = products[0]
                            package_type = product.get('productName', 'Unknown')
                            # Extract quantity from the product
                            quantity = int(product.get('quantity', 1))
                    elif '0' in package_value:
                        # Handle numbered product format
                        first_product = package_value['0']
                        if 'id' in first_product:
                            package_type = f"Product ID: {first_product['id']}"
                            quantity = int(first_product.get('quantity', 1))
        
        # If still no package type, try the products field
        if not package_type:
            products_field = field_mapping.get('package_products')
            if products_field and products_field in raw_request:
                package_data = raw_request[products_field]
                if isinstance(package_data, dict) and 'products' in package_data:
                    products = package_data['products']
                    if products and len(products) > 0:
                        product = products[0]
                        package_type = product.get('productName', 'Unknown')
                        quantity = int(product.get('quantity', 1))
        
        user_data['package_type'] = package_type or 'Not specified'
        user_data['quantity'] = quantity
        
        # Extract payment/backend status if available
        paid_field = field_mapping.get('paid_status')
        if paid_field and paid_field in raw_request:
            user_data['paid_status'] = raw_request[paid_field]
        
        # Set default values for any missing required fields
        user_data.setdefault('first_name', '')
        user_data.setdefault('last_name', '')
        user_data.setdefault('email', '')
        user_data.setdefault('city', '')
        user_data.setdefault('phone', '')
        user_data.setdefault('paid_status', 0)
        
        app.logger.debug(f"Extracted user data: {user_data}")
        return user_data
        
    except Exception as e:
        app.logger.error(f"Error extracting user data: {str(e)}")
        raise


def analyze_form_fields(raw_request):
    """
    Analyze form fields and try to identify their purpose
    
    Args:
        raw_request (dict): The rawRequest data from JotForm
        
    Returns:
        dict: Analysis of each field with suggested purpose
    """
    field_analysis = {}
    
    for field_name, field_value in raw_request.items():
        analysis = {
            'value': field_value,
            'type': type(field_value).__name__,
            'is_empty': not bool(field_value),
            'suggested_purpose': 'unknown',
            'mapping_confidence': 0
        }
        
        # Analyze field name to guess purpose
        field_name_lower = field_name.lower()
        
        # Check for name fields
        if 'name' in field_name_lower:
            if 'first' in field_name_lower:
                analysis['suggested_purpose'] = 'first_name'
                analysis['mapping_confidence'] = 95
            elif 'last' in field_name_lower:
                analysis['suggested_purpose'] = 'last_name' 
                analysis['mapping_confidence'] = 95
            elif isinstance(field_value, dict):
                # Full name component with first/last
                if 'first' in field_value or 'last' in field_value:
                    analysis['suggested_purpose'] = 'full_name_component'
                    analysis['mapping_confidence'] = 90
                else:
                    analysis['suggested_purpose'] = 'name_field'
                    analysis['mapping_confidence'] = 75
            else:
                analysis['suggested_purpose'] = 'name_field'
                analysis['mapping_confidence'] = 70
        
        # Check for email fields
        elif 'email' in field_name_lower:
            analysis['suggested_purpose'] = 'email'
            analysis['mapping_confidence'] = 95
            
        # Check for location fields
        elif any(keyword in field_name_lower for keyword in ['city', 'location', 'address']):
            analysis['suggested_purpose'] = 'city'
            analysis['mapping_confidence'] = 85
            
        # Check for phone fields
        elif 'phone' in field_name_lower:
            analysis['suggested_purpose'] = 'phone'
            analysis['mapping_confidence'] = 90
            
        # Check for package/product fields
        elif any(keyword in field_name_lower for keyword in ['package', 'product', 'service', 'plan']):
            if isinstance(field_value, dict) and ('products' in field_value or '0' in field_value):
                analysis['suggested_purpose'] = 'package_products'
                analysis['mapping_confidence'] = 85
            else:
                analysis['suggested_purpose'] = 'package_type'
                analysis['mapping_confidence'] = 80
                
        # Check for payment/paid fields
        elif any(keyword in field_name_lower for keyword in ['paid', 'payment', 'price']):
            analysis['suggested_purpose'] = 'paid_status'
            analysis['mapping_confidence'] = 80
            
        field_analysis[field_name] = analysis
    
    return field_analysis

def generate_mapping_code(field_analysis, submission_id):
    """
    Generate Python code for the field mapping based on analysis
    
    Args:
        field_analysis (dict): Analysis of form fields
        submission_id (str): The submission ID for reference
        
    Returns:
        str: Python code for the mapping
    """
    # Find the best field for each purpose
    mappings = {
        'first_name': None,
        'last_name': None,
        'email': None,
        'city': None,
        'package_type': None,
        'package_products': None,
        'phone': None,
        'paid_status': None
    }
    
    confidence_scores = {key: 0 for key in mappings.keys()}
    
    # Find best matches for each purpose
    for field_name, analysis in field_analysis.items():
        purpose = analysis['suggested_purpose']
        confidence = analysis['mapping_confidence']
        
        # Handle full name component specially
        if purpose == 'full_name_component':
            if confidence > confidence_scores['first_name']:
                mappings['first_name'] = field_name
                mappings['last_name'] = field_name  # Same field for both
                confidence_scores['first_name'] = confidence
                confidence_scores['last_name'] = confidence
        
        # Handle other purposes
        elif purpose in mappings:
            if confidence > confidence_scores[purpose]:
                mappings[purpose] = field_name
                confidence_scores[purpose] = confidence
    
    # Generate the mapping code
    form_name = f"form_{submission_id[:8]}"  # Use first 8 chars of submission ID
    
    code_lines = [
        f"# Suggested mapping for form with submission ID: {submission_id}",
        f"# Add this to your FORM_FIELD_MAPPINGS dictionary:",
        f"",
        f"'{form_name}': {{",
    ]
    
    for logical_field, actual_field in mappings.items():
        if actual_field:
            confidence = confidence_scores[logical_field]
            code_lines.append(f"    '{logical_field}': '{actual_field}',  # Confidence: {confidence}%")
        else:
            code_lines.append(f"    '{logical_field}': None,  # Field not found")
    
    code_lines.extend([
        "},",
        "",
        "# Detailed Field Analysis:",
    ])
    
    # Add detailed analysis as comments
    for field_name, analysis in field_analysis.items():
        purpose = analysis['suggested_purpose']
        confidence = analysis['mapping_confidence']
        value_preview = str(analysis['value'])[:50] + "..." if len(str(analysis['value'])) > 50 else str(analysis['value'])
        
        code_lines.append(f"# {field_name}: {purpose} ({confidence}% confidence) = {value_preview}")
    
    return "\n".join(code_lines)

# Main webhook handler
@app.route('/api/jotform-webhook', methods=['POST'])
def jotform_webhook():
    """
    Enhanced webhook handler that processes JotForm submissions and adds packages
    to user inventory using the 1-to-many relationship model.
    
    This handler:
    1. Detects which form the submission came from
    2. Extracts data including package type and quantity
    3. Finds or creates the user by email
    4. Adds the purchased packages to their inventory
    5. Sends a QR code email to the user
    """
    try:
        app.logger.info("=== PROCESSING JOTFORM WEBHOOK ===")
        
        # Parse the incoming form data based on content type
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            # Handle form-encoded data (common for JotForm webhooks)
            form_data = request.form.to_dict()
            
            # Parse the rawRequest JSON string if present
            if 'rawRequest' in form_data and isinstance(form_data['rawRequest'], str):
                try:
                    form_data['rawRequest'] = json.loads(form_data['rawRequest'])
                    app.logger.debug("Successfully parsed rawRequest JSON")
                except json.JSONDecodeError as e:
                    app.logger.error(f"Failed to parse rawRequest JSON: {str(e)}")
                    return jsonify({"error": "Invalid rawRequest format"}), 400
        else:
            # Handle JSON data
            form_data = request.json
            if not form_data:
                app.logger.error("No JSON data received")
                return jsonify({"error": "No JSON data received"}), 400

        # Validate that we have the required data structure
        if not form_data:
            app.logger.error("No form data received")
            return jsonify({"error": "No form data received"}), 400

        # Extract submission ID (this should be consistent across all forms)
        submission_id = form_data.get('submissionID')
        if not submission_id:
            app.logger.error("Missing submission ID")
            return jsonify({"error": "Missing submission ID"}), 400

        # Get the raw request data
        raw_request = form_data.get('rawRequest', {})
        if not raw_request:
            app.logger.error("Missing rawRequest data")
            return jsonify({"error": "Missing rawRequest data"}), 400

        app.logger.info(f"Processing submission ID: {submission_id}")
        
        # Detect which form this submission came from
        form_type = detect_form_type(raw_request)
        if form_type == 'unknown':
            # Log available fields to help with debugging
            available_fields = list(raw_request.keys())
            app.logger.error(f"Unknown form type. Available fields: {available_fields}")
            
            # Try to process with fallback logic (assume it's like original form)
            app.logger.warning("Attempting to process with original form mapping as fallback")
            form_type = 'original_form'

        # Extract user data using the detected form type (now includes quantity)
        user_data = extract_user_data(raw_request, form_type)
        
        # Validate that we have the required fields
        required_fields = ['first_name', 'last_name', 'email']
        missing_fields = [field for field in required_fields if not user_data.get(field)]
        
        if missing_fields:
            error_msg = f"Missing required fields: {missing_fields}"
            app.logger.error(error_msg)
            return jsonify({"error": error_msg}), 400

        # Log the extracted data for debugging
        app.logger.info(f"""
        ====== EXTRACTED USER DATA ======
        Form Type: {form_type}
        Submission ID: {submission_id}
        First Name: {user_data['first_name']}
        Last Name: {user_data['last_name']}
        Email: {user_data['email']}
        City: {user_data['city']}
        Phone: {user_data.get('phone', 'N/A')}
        Package Type: {user_data['package_type']}
        Quantity: {user_data['quantity']}
        Paid Status: {user_data.get('paid_status', 'N/A')}
        ================================
        """)

        # Database operations with proper error handling
        try:
            # Ensure database connection is active
            if not hasattr(db, 'connection') or not db.connection.is_connected():
                db.connect()
            
            # Step 1: Find or create the user by their unique EMAIL
            user_id = db.create_user(
                user_data['first_name'],
                user_data['last_name'],
                user_data['email'],
                user_data.get('city'),
                user_data['package_type']  # Keep for compatibility, but packages will be in separate table
            )
            app.logger.info(f"User ID: {user_id}")

            # Step 2: Add the new packages to the user's inventory
            # This is the key feature that prevents overwriting existing packages
            db.add_user_packages(
                user_id,
                user_data['package_type'],
                user_data['quantity']
            )
            app.logger.info(f"Successfully added {user_data['quantity']} packages of type '{user_data['package_type']}' to user {user_id}")

        except Exception as db_error:
            # Rollback on database error
            app.logger.error(f"Database error: {str(db_error)}")
            if hasattr(db, 'connection') and db.connection.is_connected():
                db.connection.rollback()
            raise

        # Send QR code email with updated logic
        try:
            app.logger.info("Sending QR code email")
            qr_sender_instance = QREmailSender()
            
            # Send email with quantity information
            success, result, _ = qr_sender_instance.send_email(
                user_data['email'],
                user_data['first_name'],
                user_data['last_name'],
                user_data.get('city'),
                user_data['package_type'],
                user_data['quantity']  # Pass quantity to email
            )

            if not success:
                app.logger.error(f"QR code email failed: {result}")
                # Don't fail the webhook for email errors - data is already saved
                
            app.logger.info("QR code email processing completed")
            
        except Exception as email_error:
            # Log email error but don't fail the webhook
            # The user data is already saved, so this is not critical
            app.logger.error(f"Email sending failed: {str(email_error)}")

        # Prepare success response
        response_data = {
            "status": "success",
            "message": f"Successfully added {user_data['quantity']} {user_data['package_type']} package(s)",
            "user_id": user_id,
            "form_type": form_type,
            "packages_added": user_data['quantity'],
            "package_type": user_data['package_type']
        }
        
        app.logger.info("Webhook processing completed successfully")
        return jsonify(response_data), 200

    except Exception as e:
        # Handle any unexpected errors
        error_msg = f"Error in webhook processing: {str(e)}"
        app.logger.error(error_msg, exc_info=True)
        
        # Rollback database changes if needed
        if hasattr(db, 'connection') and db.connection.is_connected():
            try:
                db.connection.rollback()
            except:
                pass
        
        # Return error response
        return jsonify({
            "status": "error", 
            "message": error_msg
        }), 500

# Field discovery tool (no authentication required for webhooks)
@app.route('/api/field-mapper', methods=['GET', 'POST'])
def field_mapper():
    """
    Tool to help discover and map field names from JotForm submissions
    No authentication required so JotForm webhooks can access it
    """
    if request.method == 'GET':
        # Show simple instructions page
        return """
        <html>
        <head>
            <title>Field Mapping Discovery Tool</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }
                .container { max-width: 800px; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-family: 'Courier New', monospace; }
                .warning { background: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0; }
                .step { background: #e3f2fd; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #2196f3; }
                .url { font-weight: bold; color: #1976d2; font-size: 16px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔍 JotForm Field Mapping Discovery Tool</h1>
                
                <div class="warning">
                    <strong>⚠️ Important:</strong> This tool is for temporary use only. 
                    Remember to change your webhook URL back after testing!
                </div>
                
                <h2>📝 Setup Instructions:</h2>
                
                <div class="step">
                    <strong>Step 1:</strong> Go to your JotForm → Settings → Integrations → Webhooks
                </div>
                
                <div class="step">
                    <strong>Step 2:</strong> Set webhook URL to:<br>
                    <span class="url">{}/api/field-mapper</span>
                </div>
                
                <div class="step">
                    <strong>Step 3:</strong> Make a test submission in your form
                </div>
                
                <div class="step">
                    <strong>Step 4:</strong> Check your Flask console logs for the mapping
                </div>
                
                <div class="step">
                    <strong>Step 5:</strong> Change webhook URL back to:<br>
                    <span class="url">{}/api/jotform-webhook</span>
                </div>
            </div>
        </body>
        </html>
        """.format(request.url_root.rstrip('/'), request.url_root.rstrip('/'))
    
    elif request.method == 'POST':
        try:
            app.logger.info("=== FIELD MAPPING DISCOVERY STARTED ===")
            
            # Parse the incoming webhook data
            if request.content_type and request.content_type.startswith('multipart/form-data'):
                form_data = request.form.to_dict()
                if 'rawRequest' in form_data and isinstance(form_data['rawRequest'], str):
                    try:
                        form_data['rawRequest'] = json.loads(form_data['rawRequest'])
                        app.logger.info("Successfully parsed rawRequest JSON")
                    except json.JSONDecodeError as e:
                        app.logger.error(f"Failed to parse rawRequest JSON: {str(e)}")
                        return jsonify({"error": "Invalid rawRequest format"}), 400
            else:
                form_data = request.json
                app.logger.info("Processing JSON webhook data")

            raw_request = form_data.get('rawRequest', {})
            submission_id = form_data.get('submissionID', 'unknown')
            
            app.logger.info(f"Analyzing submission ID: {submission_id}")
            app.logger.info(f"Number of fields received: {len(raw_request)}")
            
            # Show all raw field data in logs
            app.logger.info("=== RAW FIELD DATA ===")
            for field_name, field_value in raw_request.items():
                value_str = str(field_value)
                if len(value_str) > 100:
                    value_str = value_str[:100] + "..."
                app.logger.info(f"  {field_name}: {value_str}")
            
            # Analyze the fields and suggest mappings
            field_analysis = analyze_form_fields(raw_request)
            
            # Generate suggested mapping code
            suggested_mapping = generate_mapping_code(field_analysis, submission_id)
            
            # Log the suggested mapping prominently
            app.logger.info("=== SUGGESTED MAPPING CODE ===")
            for line in suggested_mapping.split('\n'):
                app.logger.info(line)
            app.logger.info("=== END MAPPING CODE ===")
            
            app.logger.info("=== FIELD MAPPING DISCOVERY COMPLETED ===")
            
            # Return success response to JotForm
            return jsonify({
                "status": "success",
                "message": "Field mapping analysis complete - check Flask logs",
                "submission_id": submission_id,
                "fields_analyzed": len(raw_request)
            })
            
        except Exception as e:
            app.logger.error(f"Error in field mapping discovery: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

# Legacy webhook processing function (keep for compatibility)
def process_jotform_submission(form_data):
    """Legacy function for processing JotForm submissions (original form only)"""
    try:
        app.logger.debug(f"Processing submission data: {form_data}")
        
        # Extract submission ID
        submission_id = form_data.get('submissionID')
        if not submission_id:
            raise ValueError("Missing submission ID")

        # Extract user information from rawRequest
        raw_request = form_data.get('rawRequest', {})
        app.logger.debug(f"Raw request data: {raw_request}")

        # Extract fields using the original form field names
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

# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login for both admin and staff users"""
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
            return redirect(url_for('home'))
            
        # Failed login
        error = 'Invalid credentials'
        app.logger.warning(f"Failed login attempt for username: {username}")
        
    # GET request or failed login
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    """Handle user logout"""
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# Static file serving
@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files with no-cache headers"""
    response = send_from_directory('static', filename)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# Main application routes
@app.route('/')
@login_required
def home():
    """Main dashboard/home page"""
    return render_template('home.html', title='QR System')

@app.route('/scan')
@login_required
def scan():
    """QR code scanner page"""
    return render_template('scan.html')


@app.route('/api/update-package-status/<int:package_id>', methods=['POST'])
@login_required
def update_package_status_api(package_id):
    """
    API endpoint to update the status of an individual package.
    FIXED VERSION - Includes email logic when all packages become available
    """
    try:
        data = request.get_json()
        new_status = data.get('status')

        if not new_status or new_status not in ['available', 'rented_out']:
            return jsonify({'success': False, 'error': 'Invalid status provided'}), 400

        app.logger.info(f"Updating package {package_id} to status: {new_status}")

        # First, get the user_id for this package
        db.cursor.execute("""
            SELECT user_id FROM user_packages WHERE id = %s
        """, (package_id,))
        
        package_result = db.cursor.fetchone()
        if not package_result:
            return jsonify({'success': False, 'error': 'Package not found'}), 404
        
        user_id = package_result['user_id']
        app.logger.debug(f"Package {package_id} belongs to user {user_id}")

        # Update the individual package status
        if db.update_package_status(package_id, new_status):
            app.logger.info(f"Successfully updated package {package_id} to {new_status}")
            
            # 🔥 NEW: Check if we need to send thank you email after individual package update
            if new_status == 'available':  # Only check when checking IN a package
                try:
                    # Get current package summary for this user
                    summary = db.get_user_package_summary(user_id)
                    
                    app.logger.debug(f"Package summary after individual update for user {user_id}: {summary}")
                    
                    # If ALL packages are now available, send thank you email
                    if summary['has_packages'] and summary['all_returned'] and summary['total_packages'] > 0:
                        app.logger.info(f"🎉 ALL {summary['total_packages']} packages are now available for user {user_id} - sending thank you email")
                        
                        # Update user rental status to "returned" (2)
                        db.cursor.execute("""
                            UPDATE users 
                            SET rental_status = 2,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (user_id,))
                        
                        # Update any active rental records
                        db.cursor.execute("""
                            UPDATE rentals
                            SET status = 'returned',
                                return_time = CURRENT_TIMESTAMP
                            WHERE user_id = %s
                            AND status = 'checked_out'
                        """, (user_id,))
                        
                        db.connection.commit()
                        
                        # Get user details for email
                        db.cursor.execute("""
                            SELECT first_name, last_name, email, city, package_type
                            FROM users
                            WHERE id = %s
                        """, (user_id,))
                        user = db.cursor.fetchone()
                        
                        if user:
                            from rental_email_handler import RentalEmailHandler
                            
                            email_handler = RentalEmailHandler(
                                os.getenv('GMAIL_ADDRESS'),
                                os.getenv('GMAIL_APP_PASSWORD')
                            )
                            
                            # Send thank you email
                            success, message = email_handler.send_thank_you_email(
                                user['email'],
                                user['first_name'],
                                user['last_name'],
                                user.get('city'),
                                user.get('package_type')
                            )
                            
                            # Log the email attempt
                            db.cursor.execute("""
                                SELECT id FROM qr_codes 
                                WHERE user_id = %s AND is_active = TRUE
                                LIMIT 1
                            """, (user_id,))
                            qr_code = db.cursor.fetchone()
                            
                            qr_code_id = qr_code['id'] if qr_code else None
                            db.log_email(
                                user_id,
                                qr_code_id,
                                'success_thank_you' if success else 'failed_thank_you',
                                None if success else message
                            )
                            
                            db.connection.commit()
                            
                            if success:
                                app.logger.info(f"✅ Thank you email sent to user {user_id} after individual package check-in")
                                return jsonify({
                                    'success': True, 
                                    'message': f'Package checked in successfully. All {summary["total_packages"]} packages returned - thank you email sent!',
                                    'email_sent': True,
                                    'package_summary': summary
                                })
                            else:
                                app.logger.error(f"❌ Failed to send thank you email: {message}")
                        
                except Exception as email_error:
                    app.logger.error(f"Error in email logic for individual package update: {email_error}")
                    # Don't fail the package update due to email errors
                    
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Database update failed'}), 500

    except Exception as e:
        app.logger.error(f"Error updating package status for package_id {package_id}: {str(e)}")
        return jsonify({'success': False, 'error': 'An internal error occurred'}), 500


@app.route('/email-client', methods=['GET', 'POST'])
@admin_required
def email_client():
    """Email client for bulk sending QR codes via CSV upload"""
    if request.method == 'POST':
        app.logger.info("Processing email client POST request")
        
        # Validate file upload
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
            # Save uploaded file temporarily
            temp_path = os.path.join(app.static_folder, 'temp', file.filename)
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            file.save(temp_path)
            
            # Process the CSV file
            app.logger.info(f"Processing CSV file: {temp_path}")
            results = qr_sender.process_csv(temp_path)
            os.remove(temp_path)  # Clean up temp file
            
            if not results:
                app.logger.error("No results returned from CSV processing")
                flash('No results were generated from the CSV file', 'error')
                return redirect(url_for('email_client'))
            
            # Calculate summary statistics
            total = len(results)
            successful = sum(1 for r in results if r['success'])
            failed = total - successful
            
            app.logger.info(f"CSV Processing Summary - Total: {total}, Successful: {successful}, Failed: {failed}")
            
            # Log failed emails for debugging
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

# API endpoints
@app.route('/api/lookup', methods=['POST'])
@login_required
def api_lookup():
    """API endpoint for QR code verification"""
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

@app.route('/api/toggle-rental/<int:user_id>', methods=['POST'])
@login_required
def toggle_rental_status_new(user_id):
    """
    Updated rental status toggle that works with individual packages
    FIXED VERSION - Proper handling of return values and database state
    """
    try:
        data = request.get_json()
        action = data.get('action', 'auto')  # 'auto', 'checkout_all', 'checkin_all', etc.
        
        app.logger.info(f"Toggle rental request for user {user_id}, action: {action}")
        
        # Get current package summary to determine action
        summary = db.get_user_package_summary(user_id)
        
        if not summary['has_packages']:
            return jsonify({"error": "User has no packages in inventory"}), 400
        
        # Auto-determine action if not specified
        if action == 'auto':
            if summary['rented_packages'] > 0:
                # Has rented packages - check them in
                action = 'checkin_all'
            elif summary['available_packages'] > 0:
                # Has available packages - check them out  
                action = 'checkout_all'
            else:
                return jsonify({"error": "No packages to process"}), 400
        
        app.logger.info(f"Executing action '{action}' for user {user_id}")
        
        # Execute the action using new package-based method
        success, message = db.update_rental_status_new(user_id, action)
        
        if success:
            # Get updated summary for response - IMPORTANT: Get fresh data after operation
            updated_summary = db.get_user_package_summary(user_id)
            
            app.logger.info(f"Action '{action}' completed successfully for user {user_id}: {message}")
            
            return jsonify({
                "success": True,
                "message": message,
                "package_summary": updated_summary,
                "action_performed": action
            })
        else:
            app.logger.error(f"Action '{action}' failed for user {user_id}: {message}")
            return jsonify({"error": message}), 400
            
    except Exception as e:
        app.logger.error(f"Error in toggle_rental_status_new: {str(e)}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route('/api/user-summary/<int:user_id>', methods=['GET'])
@login_required
def get_user_summary(user_id):
    """
    Get detailed package summary for a user
    SIMPLIFIED VERSION - No dependency on verify_qr_code_by_user_id
    """
    try:
        # Get both summary and individual packages
        summary = db.get_user_package_summary(user_id)
        packages = db.get_user_packages(user_id)
        
        # Simple user info from the users table
        db.cursor.execute("""
            SELECT id, first_name, last_name, email, city, package_type, rental_status
            FROM users WHERE id = %s
        """, (user_id,))
        user_info = db.cursor.fetchone()
        
        response = {
            "success": True,
            "summary": summary,
            "packages": packages,
            "user_info": user_info,
            "timestamp": datetime.now().isoformat()
        }
        
        app.logger.debug(f"User summary requested for user {user_id}: {summary}")
        
        return jsonify(response)
        
    except Exception as e:
        app.logger.error(f"Error getting user summary for user {user_id}: {str(e)}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500
    
@app.route('/api/package-action/<int:user_id>', methods=['POST'])
@login_required
def package_action(user_id):
    """
    New endpoint for specific package actions
    FIXED VERSION - Better error handling and response formatting
    """
    try:
        data = request.get_json()
        action = data.get('action')
        
        if not action:
            return jsonify({"error": "Action is required"}), 400
        
        valid_actions = ['checkout_one', 'checkin_one', 'checkout_all', 'checkin_all']
        if action not in valid_actions:
            return jsonify({"error": f"Invalid action. Must be one of: {valid_actions}"}), 400
        
        app.logger.info(f"Package action request: {action} for user {user_id}")
        
        # Get initial state for logging
        initial_summary = db.get_user_package_summary(user_id)
        app.logger.debug(f"Initial state for user {user_id}: {initial_summary}")
        
        # Execute the package action
        success, message = db.update_rental_status_new(user_id, action)
        
        if success:
            # Get updated package summary - CRITICAL: Fresh data after operation
            final_summary = db.get_user_package_summary(user_id)
            
            app.logger.info(f"Package action '{action}' completed for user {user_id}: {message}")
            app.logger.debug(f"Final state for user {user_id}: {final_summary}")
            
            # Prepare detailed response
            response = {
                "success": True,
                "message": message,
                "package_summary": final_summary,
                "action_performed": action,
                "changes": {
                    "available_before": initial_summary['available_packages'],
                    "available_after": final_summary['available_packages'],
                    "rented_before": initial_summary['rented_packages'],
                    "rented_after": final_summary['rented_packages']
                }
            }
            
            # Add special flag if email was sent (all packages returned)
            if final_summary['all_returned'] and final_summary['has_packages']:
                response['email_sent'] = True
                response['message'] += " Thank you email sent to customer."
            
            return jsonify(response)
        else:
            app.logger.error(f"Package action '{action}' failed for user {user_id}: {message}")
            return jsonify({"error": message}), 400
            
    except Exception as e:
        app.logger.error(f"Error in package_action: {str(e)}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500
    
@app.route('/api/reset-rental/<int:user_id>', methods=['POST'])
@login_required
def reset_rental_status(user_id):
    """
    Reset rental status to 'Not Active' - makes everything inactive/available
    SIMPLIFIED VERSION - Just focuses on making everything inactive
    """
    try:
        app.logger.info(f"🔄 Resetting user {user_id} to inactive state")
        
        # Get current state for logging
        current_summary = db.get_user_package_summary(user_id)
        app.logger.info(f"📊 BEFORE reset: {current_summary}")
        
        if not current_summary['has_packages']:
            return jsonify({"error": "User has no packages to reset"}), 400
        
        # Step 1: Set ALL packages to available (inactive state)
        db.cursor.execute("""
            UPDATE user_packages 
            SET status = 'available',
                last_activity_time = CURRENT_TIMESTAMP
            WHERE user_id = %s
        """, (user_id,))
        
        packages_updated = db.cursor.rowcount
        app.logger.info(f"📦 Set {packages_updated} packages to available")
        
        # Step 2: Set user to inactive state (rental_status = 0)
        db.cursor.execute("""
            UPDATE users 
            SET rental_status = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (user_id,))
        
        app.logger.info(f"👤 Set user {user_id} to inactive state (rental_status = 0)")
        
        # Step 3: Commit the changes
        db.connection.commit()
        app.logger.info(f"✅ Reset committed to database")
        
        # Step 4: Verify everything is now inactive/available
        final_summary = db.get_user_package_summary(user_id)
        app.logger.info(f"📊 AFTER reset: {final_summary}")
        
        # Validation
        if final_summary['rented_packages'] != 0:
            app.logger.error(f"❌ Reset failed - {final_summary['rented_packages']} packages still rented")
            return jsonify({
                "error": f"Reset failed - {final_summary['rented_packages']} packages still active",
                "summary": final_summary
            }), 500
        
        # Success!
        success_message = f"✅ Reset complete! All {final_summary['total_packages']} packages are now inactive/available."
        app.logger.info(f"🎉 Reset successful for user {user_id}")
        
        return jsonify({
            "success": True, 
            "message": success_message,
            "package_summary": final_summary,
            "packages_reset": packages_updated
        })
            
    except mysql.connector.Error as db_error:
        # Rollback on database error
        try:
            db.connection.rollback()
            app.logger.error(f"🔄 Rolled back due to database error")
        except:
            pass
            
        app.logger.error(f"❌ Database error during reset for user {user_id}: {str(db_error)}")
        return jsonify({
            "error": f"Database error: {str(db_error)}",
            "user_id": user_id
        }), 500
        
    except Exception as e:
        # Rollback on any error
        try:
            db.connection.rollback()
            app.logger.error(f"🔄 Rolled back due to error")
        except:
            pass
            
        app.logger.error(f"❌ Error during reset for user {user_id}: {str(e)}")
        return jsonify({
            "error": f"Reset failed: {str(e)}",
            "user_id": user_id
        }), 500

# ALSO: Add this improved reset method to your DatabaseHandler class
def reset_user_packages_to_available_improved(self, user_id: int) -> Tuple[bool, str, Dict]:
    """
    Improved helper method to reset all user packages to available status with verification
    
    Args:
        user_id (int): User ID to reset packages for
        
    Returns:
        tuple: (success, message, summary_dict)
    """
    try:
        logger.info(f"Resetting all packages to available for user {user_id}")
        
        # Get before state
        before_summary = self.get_user_package_summary(user_id)
        
        # Update all packages to available
        self.cursor.execute("""
            UPDATE user_packages 
            SET status = 'available',
                last_activity_time = CURRENT_TIMESTAMP
            WHERE user_id = %s
        """, (user_id,))
        packages_updated = self.cursor.rowcount
        
        # Update user status to not active
        self.cursor.execute("""
            UPDATE users 
            SET rental_status = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (user_id,))
        
        # Cancel any active rentals
        self.cursor.execute("""
            UPDATE rentals
            SET status = 'cancelled',
                return_time = CURRENT_TIMESTAMP
            WHERE user_id = %s
            AND status = 'checked_out'
        """, (user_id,))
        
        # Commit changes
        self.connection.commit()
        
        # Verify the reset
        after_summary = self.get_user_package_summary(user_id)
        
        if after_summary['rented_packages'] == 0 and after_summary['total_packages'] > 0:
            logger.info(f"Successfully reset {packages_updated} packages for user {user_id}")
            return True, f"Reset successful - all {after_summary['total_packages']} packages available", after_summary
        else:
            logger.error(f"Reset verification failed for user {user_id}: {after_summary}")
            return False, f"Reset failed verification - {after_summary['rented_packages']} packages still rented", after_summary
        
    except mysql.connector.Error as err:
        self.connection.rollback()
        logger.error(f"Database error resetting packages for user {user_id}: {err}")
        return False, f"Database error: {err}", {}
    except Exception as err:
        self.connection.rollback()
        logger.error(f"Unexpected error resetting packages for user {user_id}: {err}")
        return False, f"Unexpected error: {err}", {}

@app.route('/api/save-notes', methods=['POST'])
@login_required
def save_notes():
    """Save notes for a specific user"""
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

@app.route('/api/filter-users/<status>')
@login_required
def filter_users_with_packages(status):
    """
    Filter users by rental status with package information included
    """
    try:
        if status not in ['all', 'not_active', 'active', 'returned']:
            return jsonify({'error': 'Invalid status parameter'}), 400
            
        # Base query with package information
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
                ) as last_action,
                -- Package counts
                COALESCE(
                    (SELECT COUNT(*) 
                     FROM user_packages 
                     WHERE user_id = u.id),
                    0
                ) as total_packages,
                COALESCE(
                    (SELECT COUNT(*) 
                     FROM user_packages 
                     WHERE user_id = u.id AND status = 'available'),
                    0
                ) as available_packages,
                COALESCE(
                    (SELECT COUNT(*) 
                     FROM user_packages 
                     WHERE user_id = u.id AND status = 'rented_out'),
                    0
                ) as rented_packages
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
        
        # Convert datetime objects to strings and add package summary flags
        for user in users:
            if user['last_action']:
                user['last_action'] = user['last_action'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Add helpful flags
            user['has_packages'] = user['total_packages'] > 0
            user['all_returned'] = user['rented_packages'] == 0 if user['has_packages'] else True
                
        return jsonify({'users': users})
        
    except Exception as e:
        app.logger.error(f"Error filtering users with packages: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Get database statistics"""
    try:
        # Add this line to ensure the database is connected
        db.connect()  
        stats = db.get_database_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/reset-database', methods=['POST'])
@admin_required
def reset_database():
    """Reset the entire database (admin only)"""
    try:
        db.reset_database()
        # Add this line to reconnect after resetting
        db.connect()
        return jsonify({"message": "Database reset successful"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Web interface routes
@app.route('/lookup', methods=['GET', 'POST'])
@login_required
def lookup():
    if request.method == 'GET':
        qr_code = request.args.get('qr_code')
        if qr_code:
            try:
                user_data = db.verify_qr_code(qr_code)
                if user_data:
                    # Get user's package inventory and summary
                    packages = db.get_user_packages(user_data['user_id'])
                    package_summary = db.get_user_package_summary(user_data['user_id'])
                    
                    app.logger.info(f"Lookup successful for QR {qr_code}: user {user_data['user_id']} with {len(packages)} packages")
                    app.logger.debug(f"Package summary: {package_summary}")
                    
                    return render_template('user_details.html', 
                                         user=user_data, 
                                         packages=packages,
                                         package_summary=package_summary)
                else:
                    app.logger.warning(f"Invalid QR code lookup attempt: {qr_code}")
                
                return render_template('lookup.html', error="Invalid QR code")
            except Exception as e:
                app.logger.error(f"Error in lookup GET: {str(e)}")
                return render_template('lookup.html', error=f"System error: {str(e)}")
        return render_template('lookup.html')
    
    elif request.method == 'POST':
        search_type = request.form.get('search_type')
        search_term = request.form.get('search_term')

        if not search_term:
            return render_template('lookup.html', error="Please enter a search term")

        try:
            users_to_display = []
            if search_type == 'qr_code':
                user_data = db.verify_qr_code(search_term)
                if user_data:
                    # Get user's package inventory and summary
                    packages = db.get_user_packages(user_data['user_id'])
                    package_summary = db.get_user_package_summary(user_data['user_id'])
                    
                    app.logger.info(f"QR code search successful for {search_term}: user {user_data['user_id']}")
                    
                    return render_template('user_details.html', 
                                         user=user_data, 
                                         packages=packages,
                                         package_summary=package_summary)
            else:
                # For name searches, get multiple users with package summaries
                if search_type == 'first_name':
                    users_to_display = db.search_by_first_name(search_term)
                elif search_type == 'last_name':
                    users_to_display = db.search_by_last_name(search_term)
                
                app.logger.info(f"Name search for '{search_term}' returned {len(users_to_display)} results")
                
                # Add package summaries for each user
                for user in users_to_display:
                    try:
                        user['package_summary'] = db.get_user_package_summary(user['user_id'])
                    except Exception as summary_error:
                        app.logger.warning(f"Could not get package summary for user {user['user_id']}: {summary_error}")
                        user['package_summary'] = {'has_packages': False, 'total_packages': 0}
                
                if users_to_display:
                    return render_template('search_results.html', users=users_to_display)
            
            return render_template('lookup.html', 
                                error=f"No user found for this {search_type.replace('_', ' ')}")
            
        except Exception as e:
            app.logger.error(f"Error in lookup POST: {str(e)}")
            return render_template('lookup.html', error=f"System error: {str(e)}")
        
@app.route('/email-logs')
@login_required
def email_logs():
    """Display email logs and user activity"""
    try:
        # It's good practice to ensure connection here as well
        db.connect()
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

@app.route('/admin')
@admin_required
def admin():
    """Admin dashboard page"""
    return render_template('admin.html')

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors"""
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 errors"""
    return render_template('500.html'), 500

# Health check endpoint
@app.route('/health')
def health_check():
    """Health check endpoint to verify server and database status"""
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

# Development and debugging routes
@app.route('/api/debug-webhook', methods=['POST'])
def debug_webhook():
    """Simple webhook for debugging - logs all received data"""
    try:
        app.logger.info("=== DEBUG WEBHOOK CALLED ===")
        
        # Log request headers
        app.logger.info("Headers:")
        for header, value in request.headers:
            app.logger.info(f"  {header}: {value}")
        
        # Log form data
        if request.form:
            app.logger.info("Form Data:")
            for key, value in request.form.items():
                app.logger.info(f"  {key}: {str(value)[:200]}...")
        
        # Log JSON data
        if request.json:
            app.logger.info("JSON Data:")
            app.logger.info(json.dumps(request.json, indent=2)[:1000] + "...")
        
        app.logger.info("=== END DEBUG WEBHOOK ===")
        
        return jsonify({"status": "logged", "message": "Check Flask logs for details"})
        
    except Exception as e:
        app.logger.error(f"Error in debug webhook: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Run the application
if __name__ == '__main__':
    """
    Start the Flask application with SSL configuration
    
    The server will start using the HOST and PORT defined in ssl_config.py
    SSL certificates are also configured in ssl_config.py
    """
    ssl_context = (SSLConfig.SSL_CERTIFICATE, SSLConfig.SSL_KEY)
    print(f"Starting server on https://{SSLConfig.HOST}:{SSLConfig.PORT}")
    
    app.run(
        host=SSLConfig.HOST,
        port=SSLConfig.PORT,
        ssl_context=ssl_context,
        debug=Config.FLASK_DEBUG
    )