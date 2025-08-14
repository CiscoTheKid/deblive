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
        """Generate unique QR code with database validation and retry logic."""
        logger.debug(f"Generating QR code for user {user_id} ({first_name} {last_name})")

        def is_code_unique(code_number: str) -> bool:
            self.db.cursor.execute("SELECT COUNT(*) as count FROM qr_codes WHERE qr_code_number = %s", (code_number,))
            result = self.db.cursor.fetchone()
            return result['count'] == 0

        max_attempts = 100
        qr_code_number = None
        for _ in range(max_attempts):
            candidate_number = f"{random.randint(1, 9999):04d}"
            if self._execute_with_retry(is_code_unique, candidate_number):
                qr_code_number = candidate_number
                break

        if not qr_code_number:
            raise ValueError("Failed to generate unique QR code after maximum attempts")

        qr_data = qr_code_number
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_bytes = img_buffer.getvalue()

        return img_bytes, qr_code_number, qr_data

    def _store_qr_in_database(self, user_id: int, qr_data: str, qr_code_number: str, qr_image: bytes) -> int:
        """Store QR code in the database using an 'upsert' (update or insert) logic."""
        try:
            self.db.cursor.execute("SELECT id FROM qr_codes WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))
            existing_qr = self.db.cursor.fetchone()

            if existing_qr:
                qr_code_id = existing_qr['id']
                self.db.cursor.execute("""
                    UPDATE qr_codes
                    SET qr_data = %s, qr_code_number = %s, qr_image = %s, is_active = TRUE, created_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (qr_data, qr_code_number, qr_image, qr_code_id))
                logger.info(f"Successfully updated QR code {qr_code_id} for user {user_id}.")
            else:
                self.db.cursor.execute("""
                    INSERT INTO qr_codes (user_id, qr_data, qr_code_number, qr_image, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                """, (user_id, qr_data, qr_code_number, qr_image))
                qr_code_id = self.db.cursor.lastrowid
                logger.info(f"Successfully created new QR code {qr_code_id} for user {user_id}.")

            self.db.connection.commit()
            return qr_code_id

        except mysql.connector.Error as err:
            self.db.connection.rollback()
            logger.error(f"Database error in _store_qr_in_database: {err}")
            raise Exception(f"Database error: {err}")

    def create_email_content(self, first_name: str, last_name: str, qr_code_number: str,
                            city: str = None, package_type: str = None, quantity: int = 1) -> str:
        """Create HTML email content with QR code info, including city, package type, and quantity."""
        try:
            logger.debug(f"Creating email content for {first_name} {last_name}")

            city_display = city if city else "your local"
            package_display = package_type if package_type else "selected"
            package_str = "package" if quantity == 1 else "packages"

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
                                <li>You have purchased <strong>{quantity} {package_display} {package_str}</strong>.</li>
                                <li>Save this QR code to your phone or print it.</li>
                                <li><strong>Without your code or QR we can not verify you!</strong></li>
                                <li>This code is unique to your rental package pickup. Without it you will not be able to pick up your {package_display} {package_str}.</li>
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
            
    def create_thank_you_email_content(self, first_name: str, last_name: str) -> str:
        """Create HTML for the 'all items returned' thank you email."""
        logger.debug(f"Creating thank you email content for {first_name} {last_name}")
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Thank You from Rentals To Remember!</h2>
                    <p>Dear {first_name} {last_name},</p>
                    <p>This email is to confirm that we have received all of your rented items. We hope you enjoyed the event!</p>
                    <p>Thank you for choosing us, and we hope to see you again soon.</p>
                    <p>Best regards,<br>The Rentals To Remember Team</p>
                </div>
            </body>
        </html>
        """
        return html_content

    def process_csv(self, csv_file_path: str) -> List[Dict]:
        """Process CSV file with proper column handling, including quantity."""
        try:
            logger.info(f"Starting CSV processing: {csv_file_path}")
            df = pd.read_csv(csv_file_path)

            column_mappings = {
                'First Name': ['First Name', 'FirstName', 'First'],
                'Last Name': ['Last Name', 'LastName', 'Last'],
                'Email': ['Email', 'EmailAddress', 'email'],
                'City': ['City', 'Location', 'city'],
                'Package Type': ['Package Type', 'PackageType', 'Package'],
                'Quantity': ['Quantity', 'quantity', 'Qty']
            }

            for standard_name, possible_names in column_mappings.items():
                for name in possible_names:
                    if name in df.columns:
                        df.rename(columns={name: standard_name}, inplace=True)
                        break

            required_columns = ['First Name', 'Last Name', 'Email']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

            results = []
            for _, row in df.iterrows():
                try:
                    logger.info(f"Processing email for: {row['First Name']} {row['Last Name']}")
                    self.ensure_db_connection()

                    email = row['Email'].strip()
                    if not email:
                        raise ValueError("Empty email address")

                    city = row.get('City', '').strip() if 'City' in df.columns else None
                    package_type = row.get('Package Type', '').strip() if 'Package Type' in df.columns else None
                    quantity = int(row.get('Quantity', 1)) if 'Quantity' in df.columns else 1

                    success, result, user_id = self.send_email(
                        email,
                        row['First Name'].strip(),
                        row['Last Name'].strip(),
                        city,
                        package_type,
                        quantity
                    )

                    results.append({
                        'email': email, 'success': success, 'result': result, 'user_id': user_id,
                        'city': city, 'package_type': package_type, 'quantity': quantity
                    })
                    time.sleep(1)

                except Exception as row_error:
                    error_msg = str(row_error)
                    logger.error(f"Error processing row: {error_msg}")
                    results.append({
                        'email': row['Email'], 'success': False, 'result': error_msg, 'user_id': None,
                        'city': row.get('City', None), 'package_type': row.get('Package Type', None),
                        'quantity': row.get('Quantity', 1)
                    })

            logger.info(f"Completed processing CSV with {len(results)} entries")
            return results

        except Exception as e:
            error_msg = f"Error processing CSV: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def send_email(self, recipient_email: str, first_name: str, last_name: str,
                city: str = None, package_type: str = None, quantity: int = 1) -> Tuple[bool, str, int]:
        """Send email with QR code and store in database."""
        user_id = None
        qr_code_id = None

        try:
            logger.info(f"Starting email send process for {recipient_email}")
            user_id = self._execute_with_retry(self.db.create_user, first_name, last_name, recipient_email, city, package_type)
            qr_img_bytes, qr_code_number, qr_data = self.generate_qr_code(user_id, first_name, last_name)
            qr_code_id = self._execute_with_retry(self._store_qr_in_database, user_id, qr_data, qr_code_number, qr_img_bytes)

            msg = MIMEMultipart('related')
            msg['Subject'] = 'Your Rental Package QR Code'
            msg['From'] = self.gmail_address
            msg['To'] = recipient_email

            html_content = self.create_email_content(first_name, last_name, qr_code_number, city, package_type, quantity)
            msg.attach(MIMEText(html_content, 'html'))

            qr_image = MIMEImage(qr_img_bytes)
            qr_image.add_header('Content-ID', '<qr_code>')
            msg.attach(qr_image)

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)

            logger.info(f"Successfully sent email to {recipient_email}")
            self._execute_with_retry(self.db.log_email, user_id, qr_code_id, 'success')
            return True, qr_code_number, user_id

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send email to {recipient_email}: {error_msg}")
            if user_id is not None and qr_code_id is not None:
                try:
                    self._execute_with_retry(self.db.log_email, user_id, qr_code_id, 'failed', error_msg)
                except Exception as log_error:
                    logger.error(f"Failed to log email error: {str(log_error)}")
            return False, error_msg, user_id if user_id else 0
            
    def send_thank_you_email(self, recipient_email: str, first_name: str, last_name: str) -> Tuple[bool, str]:
        """Send a thank you email after all packages have been returned."""
        user_id = None
        try:
            logger.info(f"Starting thank you email process for {recipient_email}")

            self.db.cursor.execute("SELECT id FROM users WHERE email = %s", (recipient_email,))
            user = self.db.cursor.fetchone()
            if not user:
                raise ValueError(f"Cannot send thank you email. User not found with email: {recipient_email}")
            user_id = user['id']

            msg = MIMEMultipart('alternative')
            msg['Subject'] = 'Thank You for Your Return!'
            msg['From'] = self.gmail_address
            msg['To'] = recipient_email

            html_content = self.create_thank_you_email_content(first_name, last_name)
            msg.attach(MIMEText(html_content, 'html'))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)

            logger.info(f"Successfully sent thank you email to {recipient_email}")
            # Log successful email; qr_code_id is None as it's not applicable
            self._execute_with_retry(self.db.log_email, user_id, None, 'success_thank_you')
            return True, "Thank you email sent successfully."

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send thank you email to {recipient_email}: {error_msg}")
            if user_id:
                try:
                    # Use a different status message for failed thank you emails for clarity in logs
                    self._execute_with_retry(self.db.log_email, user_id, None, 'failed_thank_you', error_msg)
                except Exception as log_error:
                    logger.error(f"Failed to log thank you email error: {str(log_error)}")
            return False, error_msg

    def __del__(self):
        """Cleanup database connection safely"""
        try:
            if hasattr(self, 'db'):
                self.db.close()
                logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")