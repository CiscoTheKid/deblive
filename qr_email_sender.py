import qrcode
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import random
import io
import pandas as pd
from typing import Tuple, Dict, List
import time
import ssl
import os
from dotenv import load_dotenv
from db_handler import DatabaseHandler
from config import Config
import mysql.connector

# Load environment variables
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Add file handler
file_handler = logging.FileHandler('qr_email_sender.log')
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

class QREmailSender:
    def __init__(self, gmail_address=None, app_password=None, db_config=None, max_retries=3):
        """Initialize the QR Email Sender with email and database configurations"""
        self.max_retries = max_retries
        
        # Get email credentials from environment variables if not provided
        self.gmail_address = gmail_address or os.getenv('GMAIL_ADDRESS')
        self.app_password = app_password or os.getenv('GMAIL_APP_PASSWORD')
        
        if not self.gmail_address or not self.app_password:
            error_msg = "Missing email credentials in environment variables"
            logger.error(error_msg)
            raise ValueError(error_msg)
            
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        
        logger.info("Initializing QR Email Sender")
        
        try:
            # Set up database configuration from environment if not provided
            if db_config is None:
                db_config = {
                    'host': os.getenv('DB_HOST'),
                    'database': os.getenv('DB_NAME'),
                    'user': os.getenv('DB_USER'),
                    'password': os.getenv('DB_PASSWORD')
                }
                
                # Verify all database credentials are present
                missing_db_vars = [k for k, v in db_config.items() if not v]
                if missing_db_vars:
                    error_msg = f"Missing database credentials in environment variables: {', '.join(missing_db_vars)}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
            
            self.db_config = db_config
            self.db = None
            self.ensure_db_connection()
            logger.info("Database connection established")
            
            # Test SMTP connection
            self.test_smtp_connection()
            
        except Exception as e:
            logger.error(f"Initialization error: {str(e)}")
            raise

    def ensure_db_connection(self):
        """Ensures a fresh database connection"""
        try:
            if self.db:
                try:
                    self.db.close()
                except:
                    pass
            self.db = DatabaseHandler(config=self.db_config)
        except Exception as e:
            logger.error(f"Failed to ensure database connection: {str(e)}")
            raise

    def _execute_with_retry(self, operation, *args, **kwargs):
        """Execute database operations with retry logic"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return operation(*args, **kwargs)
            except mysql.connector.Error as err:
                last_error = err
                if err.errno == 1412:  # Table definition changed error
                    logger.warning(f"Retry attempt {attempt + 1}/{self.max_retries} due to table definition change")
                    time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                    self.ensure_db_connection()  # Get fresh connection
                    continue
                raise
            except Exception as e:
                raise e
        raise last_error or Exception("Max retries exceeded")

    def test_smtp_connection(self):
        """Test SMTP connection with credentials"""
        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
            logger.info("SMTP connection test successful")
        except Exception as e:
            error_msg = f"SMTP connection test failed: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def generate_qr_code(self, user_id: int, first_name: str, last_name: str) -> Tuple[bytes, str, str]:
        """Generate unique QR code with database validation and retry logic"""
        def _generate():
            logger.debug(f"Generating QR code for user {user_id} ({first_name} {last_name})")
            
            # Function to check if QR code number exists
            def is_code_unique(code_number: str) -> bool:
                self.db.cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM qr_codes
                    WHERE qr_code_number = %s
                """, (code_number,))
                result = self.db.cursor.fetchone()
                return result['count'] == 0

            # Generate unique QR code number with retry logic
            max_attempts = 100  # Prevent infinite loop
            attempt = 0
            qr_code_number = None
            
            while attempt < max_attempts:
                # Generate a 4-digit number with leading zeros if necessary
                candidate_number = f"{random.randint(1, 9999):04d}"
                if is_code_unique(candidate_number):
                    qr_code_number = candidate_number
                    break
                attempt += 1

            if not qr_code_number:
                raise ValueError("Failed to generate unique QR code after maximum attempts")

            # Generate QR code with the unique number
            qr_data = qr_code_number
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG')
            img_bytes = img_buffer.getvalue()
            
            # Store QR code in database with default rental status 0 (Not Active)
            def _store_qr():
                # Deactivate previous QR codes
                self.db.cursor.execute("""
                    UPDATE qr_codes 
                    SET is_active = FALSE 
                    WHERE user_id = %s
                """, (user_id,))
                
                # Insert new QR code with verified unique number
                self.db.cursor.execute("""
                    INSERT INTO qr_codes (user_id, qr_data, qr_code_number, qr_image, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                """, (user_id, qr_data, qr_code_number, img_bytes))
                
                self.db.connection.commit()
                
            self._execute_with_retry(_store_qr)
            
            logger.debug(f"Successfully generated unique QR code {qr_code_number}")
            return img_bytes, qr_code_number, qr_data
            
        return self._execute_with_retry(_generate)

    def create_email_content(self, first_name: str, last_name: str, qr_code_number: str, 
                            city: str = None, package_type: str = None) -> str:
        """Create HTML email content with the QR code information including city and package type"""
        try:
            logger.debug(f"Creating email content for {first_name} {last_name}")
            
            # Set default values for optional fields
            city_display = city if city else "your local"
            package_display = package_type if package_type else "selected"
            
            html_content = f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #2c3e50;">Rentals To Remember QR Code</h2>
                        <p>Dear {first_name} {last_name},</p>
                        <p>Thank you for your rental package purchase! Attached is your QR code ticket.</p>
                        <p>Your confirmation number: <strong style="color: #2c3e50;">{qr_code_number}</strong> for the {city_display} DEB event.</p>
                        <p>Please present this QR code or confirmation number when picking up your rental items.</p>
                        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                            <h3 style="color: #e74c3c;">Important Notes:</h3>
                            <ul>
                                <li>You have purchased the <strong>{package_display}</strong> package.</li>
                                <li>Save this QR code to your phone or print it</li>
                                <li><strong>Without your code or QR we can not verify you!</strong></li>
                                <li>This code is unique to your rental package pickup. Without it you will not be able to pick up your {package_display}</li>
                            </ul>
                        </div>
                        <p>Best regards,<br>The Rentals To Remember Team</p>
                    </div>
                </body>
            </html>
            """
            return html_content
        except Exception as e:
            error_msg = f"Error creating email content: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def process_csv(self, csv_file_path: str) -> List[Dict]:
        """Process CSV file with proper column handling"""
        try:
            logger.info(f"Starting CSV processing: {csv_file_path}")
            
            # Read CSV file with standard comma delimiter
            df = pd.read_csv(csv_file_path)
            
            # Define expected column mappings (both original and alternative names)
            column_mappings = {
                'First Name': ['First Name', 'FirstName', 'First'],
                'Last Name': ['Last Name', 'LastName', 'Last'],
                'Email': ['Email', 'EmailAddress', 'email'],
                'City': ['City', 'Location', 'city'],
                'Package Type': ['Package Type', 'PackageType', 'Package']
            }
            
            # Standardize column names
            for standard_name, possible_names in column_mappings.items():
                for name in possible_names:
                    if name in df.columns:
                        df.rename(columns={name: standard_name}, inplace=True)
                        break
            
            # Verify required columns
            required_columns = ['First Name', 'Last Name', 'Email']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                error_msg = f"Missing required columns: {', '.join(missing_columns)}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Process each row
            results = []
            for _, row in df.iterrows():
                try:
                    logger.info(f"Processing email for: {row['First Name']} {row['Last Name']}")
                    
                    # Ensure fresh connection before each email
                    self.ensure_db_connection()
                    
                    # Clean and validate email
                    email = row['Email'].strip()
                    if not email:
                        raise ValueError("Empty email address")
                    
                    # Get optional fields with default None if not present
                    city = row.get('City', '').strip() if 'City' in df.columns else None
                    package_type = row.get('Package Type', '').strip() if 'Package Type' in df.columns else None
                    
                    success, result, user_id = self.send_email(
                        email,
                        row['First Name'].strip(),
                        row['Last Name'].strip(),
                        city,
                        package_type
                    )
                    
                    result_dict = {
                        'email': email,
                        'success': success,
                        'result': result,
                        'user_id': user_id,
                        'city': city,
                        'package_type': package_type
                    }
                    results.append(result_dict)
                    logger.debug(f"Result for {email}: {result_dict}")
                    
                    # Rate limiting to avoid email server restrictions
                    time.sleep(1)
                    
                except Exception as row_error:
                    error_msg = str(row_error)
                    logger.error(f"Error processing row: {error_msg}")
                    results.append({
                        'email': row['Email'],
                        'success': False,
                        'result': error_msg,
                        'user_id': None,
                        'city': row.get('City', None),
                        'package_type': row.get('Package Type', None)
                    })
            
            if not results:
                logger.warning("No results were generated from CSV processing")
                return []
                
            logger.info(f"Completed processing CSV with {len(results)} entries")
            return results
            
        except Exception as e:
            error_msg = f"Error processing CSV: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def send_email(self, recipient_email: str, first_name: str, last_name: str,
                city: str = None, package_type: str = None) -> Tuple[bool, str, int]:
        """Send email with QR code and store in database"""
        user_id = None
        qr_code_id = None
        
        try:
            logger.info(f"Starting email send process for {recipient_email}")
            
            # Create or update user with new fields
            user_id = self.db.create_user(first_name, last_name, recipient_email, city, package_type)
            logger.debug(f"Created/Updated user ID: {user_id}")
            
            # Generate and store QR code
            qr_img_bytes, qr_code_number, qr_data = self.generate_qr_code(user_id, first_name, last_name)
            
            # Create email with new fields
            msg = MIMEMultipart('related')
            msg['Subject'] = 'Your Rental Package QR Code'
            msg['From'] = self.gmail_address
            msg['To'] = recipient_email

            # Add HTML content with new fields
            html_content = self.create_email_content(
                first_name, 
                last_name, 
                qr_code_number,
                city,
                package_type
            )
            msg.attach(MIMEText(html_content, 'html'))

            # Attach QR code
            qr_image = MIMEImage(qr_img_bytes)
            qr_image.add_header('Content-ID', '<qr_code>')
            msg.attach(qr_image)

            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)
            
            logger.info(f"Successfully sent email to {recipient_email}")
            
            # Log successful email
            self.db.log_email(user_id, qr_code_id, 'success')
            return True, qr_code_number, user_id
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send email to {recipient_email}: {error_msg}")
            
            if user_id is not None and qr_code_id is not None:
                try:
                    self.db.log_email(user_id, qr_code_id, 'failed', error_msg)
                except Exception as log_error:
                    logger.error(f"Failed to log email error: {str(log_error)}")
                    
            return False, error_msg, user_id if user_id else 0
        

    def __del__(self):
        """Cleanup database connection safely"""
        try:
            if hasattr(self, 'db'):
                self.db.close()
                logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")