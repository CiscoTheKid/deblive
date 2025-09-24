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
import os
from dotenv import load_dotenv
from db_handler import DatabaseHandler

# Load environment variables
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)

class QREmailSender:
    def __init__(self, gmail_address=None, app_password=None, from_address=None):
        """
        Initialize QR Email Sender with Gmail credentials and optional from address
        
        Args:
            gmail_address: Gmail account for SMTP authentication (akhanetskyy@rentalstoremember.com)
            app_password: Gmail app password for authentication
            from_address: Email address to show as sender (DinerEnBlanc@rentalstoremember.com)
        """
        # Get credentials from environment variables
        self.gmail_address = gmail_address or os.getenv('GMAIL_ADDRESS')
        self.app_password = app_password or os.getenv('GMAIL_APP_PASSWORD')
        
        # Set the "From" address - use DinerEnBlanc alias if not specified
        self.from_address = from_address or os.getenv('FROM_EMAIL_ADDRESS', 'DinerEnBlanc@rentalstoremember.com')
        
        # Validate credentials are available
        if not self.gmail_address or not self.app_password:
            raise ValueError("Missing email credentials")
        
        # SMTP server configuration for Gmail
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        
        # Initialize database handler for storing QR codes and logging
        self.db = DatabaseHandler()
        logger.info(f"QR Email Sender initialized - Auth: {self.gmail_address}, From: {self.from_address}")

    def generate_qr_code(self, user_id: int) -> Tuple[bytes, str, str]:
        """
        Generate unique 4-digit QR code for user
        Returns: (image_bytes, qr_code_number, qr_data)
        """
        # Try to generate unique 4-digit code (up to 100 attempts)
        max_attempts = 100
        qr_code_number = None
        
        for _ in range(max_attempts):
            # Generate random 4-digit number (0001-9999)
            candidate = f"{random.randint(1, 9999):04d}"
            
            # Check if this code already exists in database
            self.db.cursor.execute(
                "SELECT COUNT(*) as count FROM qr_codes WHERE qr_code_number = %s", 
                (candidate,)
            )
            
            # If code is unique, use it
            if self.db.cursor.fetchone()['count'] == 0:
                qr_code_number = candidate
                break
        
        # Fail if no unique code found
        if not qr_code_number:
            raise ValueError("Failed to generate unique QR code")
        
        # Create QR code image using qrcode library
        qr = qrcode.QRCode(
            version=1,  # Controls size (1 = 21x21 grid)
            error_correction=qrcode.constants.ERROR_CORRECT_L,  # Low error correction
            box_size=10,  # Pixels per box
            border=4  # Box border size
        )
        
        # Add the QR code number as data
        qr.add_data(qr_code_number)
        qr.make(fit=True)
        
        # Generate black and white QR code image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert PIL image to bytes for email attachment
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_bytes = img_buffer.getvalue()
        
        return img_bytes, qr_code_number, qr_code_number

    def format_package_display(self, package_type: str, quantity: int) -> str:
        """
        Format package display text to avoid redundancy
        Handles cases where package_type already contains "Package" or "Packages"
        """
        # Default fallback if package_type is None or empty
        if not package_type or package_type.strip() == '':
            package_type = "rental item"
        
        package_type = package_type.strip()
        
        # Check if package_type already ends with "Package" or "Packages"
        lower_package_type = package_type.lower()
        
        if lower_package_type.endswith('package'):
            # Package type ends with "package" - handle pluralization properly
            if quantity == 1:
                return package_type  # "Full Rental Package"
            else:
                # Replace "Package" with "Packages" for plural
                return package_type[:-7] + "Packages"  # "Full Rental Packages"
        elif lower_package_type.endswith('packages'):
            # Already plural, just return as is
            return package_type  # "Full Rental Packages"
        else:
            # Package type doesn't include "package", add it
            if quantity == 1:
                return f"{package_type} package"  # "Standard package"
            else:
                return f"{package_type} packages"  # "Standard packages"

    def create_email_content(self, first_name: str, last_name: str, qr_code_number: str,
                           city: str = None, package_type: str = None, quantity: int = 1) -> str:
        """
        Create HTML email content for QR code delivery
        Includes order details, QR code information, and pickup instructions
        Updated to reflect DinerEnBlanc branding
        """
        # Set default values for display
        city_display = city or "your local"
        
        # Format package display to avoid redundancy (e.g., "DC Package package")
        package_display = self.format_package_display(package_type, quantity)
        
        # Create professional HTML email template with DinerEnBlanc branding
        return f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Your QR Code for Dîner en Blanc</h2>
                    
                    <p>Dear {first_name} {last_name},</p>
                    
                    <p>Thank you for your order! Your QR code is attached to this email. We look forward to seeing you for our and DEB's 10th year Anniversary!</p>
                    
                    <p><strong>Confirmation Number:</strong> {qr_code_number}</p>
                    
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="color: #2c3e50;">Order Details:</h3>
                        <ul style="margin: 10px 0; padding-left: 20px;">
                            <li><strong>Event:</strong> {city_display} Dîner en Blanc</li>
                            <li><strong>Package:</strong> {quantity} x {package_display}</li>
                            <li><strong>Pickup Code:</strong> {qr_code_number}</li>
                        </ul>
                    </div>
                    
                    <div style="background-color: #ffe9e9; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <p style="margin: 0;"><strong>Important:</strong> Save this QR code to your phone. You'll need it to pick up and to return your items.</p>
                    </div>
                    
                    <div style="background-color: #e8f5e8; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h4 style="color: #2c3e50; margin-top: 0;">Pickup Instructions:</h4>
                        <ul style="margin: 10px 0; padding-left: 20px;">
                            <li>Present this QR code at the pickup location or have your ID and confirmation number ready: <strong>{qr_code_number}</strong></li>
                            <li>You can find us at the designated pickup location along with your group leader</li>
                            <li>You will need the QR code / confirmation number for both the <strong>pickup</strong> and the <strong>drop off</strong> of your package.</li>
                        </ul>
                    </div>
                    
                    <p>Best regards,<br>
                    <strong>Dîner en Blanc Team</strong><br>
                    <em>Powered by Rentals To Remember</em></p>
                    
                    <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
                    
                    <p style="font-size: 12px; color: #666;">
                        Questions? Contact us at DinerEnBlanc@rentalstoremember.com<br>
                        Visit us: <a href="https://www.rentalstoremember.com">www.rentalstoremember.com</a>
                    </p>
                </div>
            </body>
        </html>
        """

    def send_email(self, recipient_email: str, first_name: str, last_name: str,
                  city: str = None, package_type: str = None, quantity: int = 1) -> Tuple[bool, str, int]:
        """
        Send QR code email to recipient using DinerEnBlanc alias
        Creates user in database, generates QR code, and sends formatted email
        Returns: (success_status, result_message, user_id)
        """
        user_id = None
        qr_code_id = None
        
        try:
            # Create or update user in database
            user_id = self.db.create_user(first_name, last_name, recipient_email, city, package_type)
            
            # Generate unique QR code for this user
            qr_img_bytes, qr_code_number, qr_data = self.generate_qr_code(user_id)
            
            # Store QR code in database with user association
            qr_code_id = self.db.store_qr_code(user_id, qr_data, qr_code_number, qr_img_bytes)
            
            # Create email message with proper MIME structure
            msg = MIMEMultipart('related')
            msg['Subject'] = f'Your Dîner en Blanc QR Code - {qr_code_number}'
            
            # IMPORTANT: Use the DinerEnBlanc alias as the From address
            # but authenticate with the main Gmail account
            msg['From'] = self.from_address  # This will be DinerEnBlanc@rentalstoremember.com
            msg['To'] = recipient_email
            
            # Optional: Add Reply-To if you want replies to go to a specific address
            msg['Reply-To'] = 'dinerenblanc@rentalstoremember.com'
            
            # Create and attach HTML content
            html_content = self.create_email_content(
                first_name, last_name, qr_code_number, city, package_type, quantity
            )
            msg.attach(MIMEText(html_content, 'html'))
            
            # Attach QR code image
            qr_image = MIMEImage(qr_img_bytes)
            qr_image.add_header('Content-ID', '<qr_code>')
            qr_image.add_header('Content-Disposition', 'attachment', 
                              filename=f'qr_code_{qr_code_number}.png')
            msg.attach(qr_image)
            
            # Send email via Gmail SMTP
            # Note: We authenticate with the main Gmail account but send as the alias
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()  # Enable TLS encryption
                # Authentication uses the main Gmail account credentials
                server.login(self.gmail_address, self.app_password)
                # Send the message (which has the alias in the From field)
                server.send_message(msg)
            
            # Log successful email delivery
            self.db.log_email(user_id, qr_code_id, 'success')
            logger.info(f"Email sent successfully to {recipient_email} from {self.from_address} - QR Code: {qr_code_number}")
            
            return True, qr_code_number, user_id
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send email to {recipient_email}: {error_msg}")
            
            # Log failure in database
            if user_id and qr_code_id:
                self.db.log_email(user_id, qr_code_id, 'failed', error_msg)
            
            return False, error_msg, user_id or 0

    def process_csv(self, csv_file_path: str) -> List[Dict]:
        """
        Process CSV file and send QR code emails to all recipients
        Handles various CSV column name formats and validates data
        Returns: List of results for each processed row
        """
        try:
            logger.info(f"Processing CSV file: {csv_file_path}")
            
            # Read CSV file into pandas DataFrame
            df = pd.read_csv(csv_file_path)
            
            # Normalize column names to handle different formats
            column_map = {
                'First Name': ['First Name', 'FirstName', 'first_name', 'fname'],
                'Last Name': ['Last Name', 'LastName', 'last_name', 'lname'],
                'Email': ['Email', 'email', 'EmailAddress', 'email_address'],
                'City': ['City', 'city', 'Location', 'location'],
                'Package Type': ['Package Type', 'PackageType', 'package_type', 'Package'],
                'Quantity': ['Quantity', 'quantity', 'Qty', 'qty', 'Count']
            }
            
            # Map column names to standardized names
            for standard_name, variations in column_map.items():
                for variation in variations:
                    if variation in df.columns:
                        df.rename(columns={variation: standard_name}, inplace=True)
                        break
            
            # Validate that required columns exist
            required_columns = ['First Name', 'Last Name', 'Email']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")
            
            logger.info(f"Processing {len(df)} rows from CSV")
            
            # Process each row in the CSV
            results = []
            for index, row in df.iterrows():
                try:
                    # Extract and clean data from each row
                    email = str(row['Email']).strip()
                    first_name = str(row['First Name']).strip()
                    last_name = str(row['Last Name']).strip()
                    
                    # Optional fields with defaults
                    city = str(row.get('City', '')).strip() if 'City' in df.columns else None
                    package_type = str(row.get('Package Type', '')).strip() if 'Package Type' in df.columns else None
                    quantity = int(row.get('Quantity', 1)) if 'Quantity' in df.columns else 1
                    
                    # Validate email is not empty
                    if not email or email.lower() == 'nan':
                        raise ValueError("Empty or invalid email address")
                    
                    # Send QR code email
                    success, result, user_id = self.send_email(
                        email, first_name, last_name, city, package_type, quantity
                    )
                    
                    # Store result for this row
                    results.append({
                        'row': index + 1,
                        'email': email,
                        'name': f"{first_name} {last_name}",
                        'success': success,
                        'result': result,
                        'user_id': user_id
                    })
                    
                    # Log processing result
                    status = "Success" if success else "Failed"
                    logger.info(f"Row {index + 1} - {email}: {status}")
                    
                except Exception as row_error:
                    error_msg = str(row_error)
                    logger.error(f"Error processing row {index + 1}: {error_msg}")
                    
                    # Store error result
                    results.append({
                        'row': index + 1,
                        'email': row.get('Email', 'Unknown'),
                        'name': f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip(),
                        'success': False,
                        'result': error_msg,
                        'user_id': None
                    })
            
            # Log completion summary
            successful = sum(1 for r in results if r['success'])
            failed = len(results) - successful
            logger.info(f"CSV processing complete: {successful} successful, {failed} failed out of {len(results)} total")
            
            return results
            
        except Exception as e:
            error_msg = f"CSV processing error: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def __del__(self):
        """Cleanup database connection when object is destroyed"""
        try:
            if hasattr(self, 'db') and self.db:
                self.db.close()
        except Exception as e:
            logger.warning(f"Error closing database connection: {e}")
            pass