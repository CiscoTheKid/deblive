import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from typing import Tuple
import os

class RentalEmailHandler:
    """
    Handles thank you emails when rentals are returned
    Updated to support sending from DinerEnBlanc alias while authenticating with main account
    """
    
    def __init__(self, gmail_address, app_password, from_address=None):
        """
        Initialize email handler with Gmail credentials and optional from address
        
        Args:
            gmail_address: Gmail account for SMTP authentication (akhanetskyy@rentalstoremember.com)
            app_password: Gmail app password for authentication
            from_address: Email address to show as sender (DinerEnBlanc@rentalstoremember.com)
        """
        self.gmail_address = gmail_address
        self.app_password = app_password
        
        # Set the "From" address - use DinerEnBlanc alias if not specified
        self.from_address = from_address or os.getenv('FROM_EMAIL_ADDRESS', 'DinerEnBlanc@rentalstoremember.com')
        
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.logger = logging.getLogger(__name__)
        
        # Log initialization with both auth and from addresses for clarity
        self.logger.info(f"RentalEmailHandler initialized - Auth: {self.gmail_address}, From: {self.from_address}")

    def format_package_display(self, package_type: str) -> str:
        """
        Format package display text for thank you emails
        Handles cases where package_type already contains "Package" or "Packages"
        """
        # Default fallback if package_type is None or empty
        if not package_type or package_type.strip() == '':
            return "rental items"
        
        package_type = package_type.strip()
        
        # Check if package_type already ends with "Package" or "Packages"
        lower_package_type = package_type.lower()
        
        if lower_package_type.endswith('package') or lower_package_type.endswith('packages'):
            # Package type already includes "package", return as is
            return package_type
        else:
            # Package type doesn't include "package", add appropriate suffix
            return f"{package_type} package"

    def create_thank_you_email(self, first_name: str, last_name: str, 
                              city: str = None, package_type: str = None) -> str:
        """
        Create HTML content for thank you email with improved formatting
        Updated to reflect DinerEnBlanc branding and contact information
        """
        
        # Handle default values and format package display properly
        city_display = city or "your local"
        package_display = self.format_package_display(package_type) if package_type else "rental items"
        
        return f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Thank You from Dîner en Blanc!</h2>
                    
                    <p>Dear {first_name} {last_name},</p>
                    
                    <p>Thank you for returning your {package_display} from the {city_display} Dîner en Blanc event!</p>
                    
                    <p>We hope you had a magical evening and that our rental items helped make your experience special.</p>
                    
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="color: #2c3e50; margin-top: 0;">Confirmation of check in & return</h3>
                        <p style="margin: 12px 0;">This email serves as a confirmation that your package has been checked back in and returned.</p>
                    </div>
                    
                    <div style="background-color: #e8f5e8; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="color: #2c3e50; margin-top: 0;">See You Next Year!</h3>
                        <p style="margin: 10px 0;">We'd love to be part of your Dîner en Blanc experience again next year.</p>
                        <p style="margin: 10px 0;">If you have any questions or have any suggestions on how we can make your experience better, feel free to email us at<br>
                        DinerEnBlanc@rentalstoremember.com</p>
                    </div>
                    
                    <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h4 style="color: #2c3e50; margin-top: 0;">Rental Return Confirmed</h4>
                        <p style="margin: 10px 0;">✓ All items have been successfully checked back in</p>
                    </div>
                    
                    <p>We truly appreciate your business and look forward to serving you again!</p>
                    
                    <p>Best regards,<br>
                    <strong>The Dîner en Blanc Team</strong><br>
                    <em>Powered by Rentals To Remember</em></p>
                    
                    <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
                    
                    <p style="font-size: 12px; color: #666;">
                        Questions about your rental experience? Contact us at DinerEnBlanc@rentalstoremember.com<br>
                        Visit us: <a href="https://www.rentalstoremember.com" style="color: #666;">www.rentalstoremember.com</a><br>
                        Follow us on social media for event inspiration and updates!
                    </p>
                </div>
            </body>
        </html>
        """

    def send_thank_you_email(self, recipient_email: str, first_name: str, last_name: str, 
                            city: str = None, package_type: str = None) -> Tuple[bool, str]:
        """
        Send thank you email after rental return with enhanced error handling
        Uses DinerEnBlanc alias as sender while authenticating with main Gmail account
        """
        try:
            self.logger.info(f"Sending thank you email to {recipient_email} for {package_type} return from {self.from_address}")
            
            # Validate email parameters
            if not recipient_email or not first_name or not last_name:
                raise ValueError("Missing required email parameters")
            
            # Create email message with proper MIME structure
            msg = MIMEMultipart('alternative')
            
            # Format subject line with proper package display
            package_display = self.format_package_display(package_type)
            msg['Subject'] = f'Thank You for Returning Your {package_display}!'
            
            # IMPORTANT: Use the DinerEnBlanc alias as the From address
            # but authenticate with the main Gmail account
            msg['From'] = self.from_address  # This will be DinerEnBlanc@rentalstoremember.com
            msg['To'] = recipient_email
            
            # Optional: Add Reply-To if you want replies to go to a specific address
            msg['Reply-To'] = 'dinerenblanc@rentalstoremember.com'
            
            # Add HTML content
            html_content = self.create_thank_you_email(
                first_name, last_name, city, package_type
            )
            msg.attach(MIMEText(html_content, 'html'))
            
            # Send email via Gmail SMTP
            # Note: We authenticate with the main Gmail account but send as the alias
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()  # Enable TLS encryption
                # Authentication uses the main Gmail account credentials
                server.login(self.gmail_address, self.app_password)
                # Send the message (which has the alias in the From field)
                server.send_message(msg)
            
            self.logger.info(f"Thank you email sent successfully to {recipient_email} from {self.from_address}")
            return True, "Thank you email sent successfully"
            
        except ValueError as ve:
            error_msg = f"Validation error: {str(ve)}"
            self.logger.error(error_msg)
            return False, error_msg
            
        except smtplib.SMTPException as smtp_e:
            error_msg = f"SMTP error sending thank you email: {str(smtp_e)}"
            self.logger.error(error_msg)
            return False, error_msg
            
        except Exception as e:
            error_msg = f"Failed to send thank you email: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg