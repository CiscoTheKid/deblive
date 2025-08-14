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
    def __init__(self, gmail_address=None, app_password=None):
        """Initialize QR Email Sender"""
        # Get credentials from environment
        self.gmail_address = gmail_address or os.getenv('GMAIL_ADDRESS')
        self.app_password = app_password or os.getenv('GMAIL_APP_PASSWORD')
        
        if not self.gmail_address or not self.app_password:
            raise ValueError("Missing email credentials")
        
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        
        # Initialize database handler
        self.db = DatabaseHandler()
        logger.info("QR Email Sender initialized")

    def generate_qr_code(self, user_id: int) -> Tuple[bytes, str, str]:
        """Generate unique QR code for user"""
        # Generate unique 4-digit code
        max_attempts = 100
        qr_code_number = None
        
        for _ in range(max_attempts):
            candidate = f"{random.randint(1, 9999):04d}"
            self.db.cursor.execute(
                "SELECT COUNT(*) as count FROM qr_codes WHERE qr_code_number = %s", 
                (candidate,)
            )
            if self.db.cursor.fetchone()['count'] == 0:
                qr_code_number = candidate
                break
        
        if not qr_code_number:
            raise ValueError("Failed to generate unique QR code")
        
        # Generate QR code image
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4
        )
        qr.add_data(qr_code_number)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_bytes = img_buffer.getvalue()
        
        return img_bytes, qr_code_number, qr_code_number

    def create_email_content(self, first_name: str, last_name: str, qr_code_number: str,
                           city: str = None, package_type: str = None, quantity: int = 1) -> str:
        """Create HTML email content"""
        city_display = city or "your local"
        package_display = package_type or "rental"
        package_str = "package" if quantity == 1 else "packages"
        
        return f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Your QR Code for DÃ®ner en Blanc</h2>
                    <p>Dear {first_name} {last_name},</p>
                    <p>Thank you for your order! Your QR code is attached.</p>
                    <p>Confirmation number: <strong>{qr_code_number}</strong></p>
                    
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="color: #2c3e50;">Order Details:</h3>
                        <ul>
                            <li>Event: {city_display} DÃ®ner en Blanc</li>
                            <li>Package: {quantity} x {package_display} {package_str}</li>
                            <li>Pickup Code: {qr_code_number}</li>
                        </ul>
                    </div>
                    
                    <div style="background-color: #ffe9e9; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <strong>Important:</strong> Save this QR code to your phone. You'll need it to pick up your items.
                    </div>
                    
                    <p>Best regards,<br>Rentals To Remember Team</p>
                </div>
            </body>
        </html>
        """

    def send_email(self, recipient_email: str, first_name: str, last_name: str,
                  city: str = None, package_type: str = None, quantity: int = 1) -> Tuple[bool, str, int]:
        """Send QR code email to user"""
        user_id = None
        qr_code_id = None
        
        try:
            # Create/update user
            user_id = self.db.create_user(first_name, last_name, recipient_email, city, package_type)
            
            # Generate QR code
            qr_img_bytes, qr_code_number, qr_data = self.generate_qr_code(user_id)
            
            # Store QR code in database
            qr_code_id = self.db.store_qr_code(user_id, qr_data, qr_code_number, qr_img_bytes)
            
            # Create email
            msg = MIMEMultipart('related')
            msg['Subject'] = f'Your Dîner en Blanc QR Code - {qr_code_number}'
            msg['From'] = self.gmail_address
            msg['To'] = recipient_email
            
            # Add HTML content
            html_content = self.create_email_content(
                first_name, last_name, qr_code_number, city, package_type, quantity
            )
            msg.attach(MIMEText(html_content, 'html'))
            
            # Add QR code image
            qr_image = MIMEImage(qr_img_bytes)
            qr_image.add_header('Content-ID', '<qr_code>')
            qr_image.add_header('Content-Disposition', 'attachment', 
                              filename=f'qr_code_{qr_code_number}.png')
            msg.attach(qr_image)
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)
            
            # Log success
            self.db.log_email(user_id, qr_code_id, 'success')
            logger.info(f"Email sent successfully to {recipient_email}")
            return True, qr_code_number, user_id
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send email to {recipient_email}: {error_msg}")
            
            # Log failure
            if user_id and qr_code_id:
                self.db.log_email(user_id, qr_code_id, 'failed', error_msg)
            
            return False, error_msg, user_id or 0

    def process_csv(self, csv_file_path: str) -> List[Dict]:
        """Process CSV file and send emails"""
        try:
            logger.info(f"Processing CSV: {csv_file_path}")
            
            # Read CSV
            df = pd.read_csv(csv_file_path)
            
            # Normalize column names
            column_map = {
                'First Name': ['First Name', 'FirstName', 'first_name'],
                'Last Name': ['Last Name', 'LastName', 'last_name'],
                'Email': ['Email', 'email', 'EmailAddress'],
                'City': ['City', 'city', 'Location'],
                'Package Type': ['Package Type', 'PackageType', 'package_type'],
                'Quantity': ['Quantity', 'quantity', 'Qty', 'qty']
            }
            
            for standard, variations in column_map.items():
                for var in variations:
                    if var in df.columns:
                        df.rename(columns={var: standard}, inplace=True)
                        break
            
            # Validate required columns
            required = ['First Name', 'Last Name', 'Email']
            missing = [col for col in required if col not in df.columns]
            if missing:
                raise ValueError(f"Missing required columns: {', '.join(missing)}")
            
            # Process each row
            results = []
            for _, row in df.iterrows():
                try:
                    # Extract data
                    email = str(row['Email']).strip()
                    first_name = str(row['First Name']).strip()
                    last_name = str(row['Last Name']).strip()
                    city = str(row.get('City', '')).strip() if 'City' in df.columns else None
                    package_type = str(row.get('Package Type', '')).strip() if 'Package Type' in df.columns else None
                    quantity = int(row.get('Quantity', 1)) if 'Quantity' in df.columns else 1
                    
                    if not email:
                        raise ValueError("Empty email address")
                    
                    # Send email
                    success, result, user_id = self.send_email(
                        email, first_name, last_name, city, package_type, quantity
                    )
                    
                    results.append({
                        'email': email,
                        'success': success,
                        'result': result,
                        'user_id': user_id
                    })
                    
                    logger.info(f"Processed {email}: {'Success' if success else 'Failed'}")
                    
                except Exception as row_error:
                    error_msg = str(row_error)
                    logger.error(f"Error processing row: {error_msg}")
                    results.append({
                        'email': row.get('Email', 'Unknown'),
                        'success': False,
                        'result': error_msg,
                        'user_id': None
                    })
            
            logger.info(f"CSV processing complete: {len(results)} entries")
            return results
            
        except Exception as e:
            error_msg = f"CSV processing error: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def __del__(self):
        """Cleanup database connection"""
        try:
            if hasattr(self, 'db'):
                self.db.close()
        except:
            pass