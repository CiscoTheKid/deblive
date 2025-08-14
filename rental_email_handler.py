import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from typing import Tuple

class RentalEmailHandler:
    """Handles thank you emails when rentals are returned"""
    
    def __init__(self, gmail_address, app_password):
        """Initialize email handler with Gmail credentials"""
        self.gmail_address = gmail_address
        self.app_password = app_password
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.logger = logging.getLogger(__name__)

    def create_thank_you_email(self, first_name: str, last_name: str, 
                              city: str = None, package_type: str = None) -> str:
        """Create HTML content for thank you email"""
        
        # Handle default values
        city_display = city or "your local"
        package_display = package_type or "rental"
        
        return f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Thank You from Rentals to Remember!</h2>
                    
                    <p>Dear {first_name} {last_name},</p>
                    
                    <p>Thank you for returning your {package_display} package from the {city_display} Dîner en Blanc event!</p>
                    
                    <p>We hope you had a magical evening and that our rental items helped make your experience special.</p>
                    
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="color: #2c3e50;">See You Next Year!</h3>
                        <p>We'd love to be part of your Dîner en Blanc experience again next year.</p>
                        <p>Visit us at <a href="https://www.rentalstoremember.com">www.rentalstoremember.com</a> 
                           for all your event rental needs!</p>
                    </div>
                    
                    <p>Best regards,<br>
                    The Rentals To Remember Team</p>
                </div>
            </body>
        </html>
        """

    def send_thank_you_email(self, recipient_email: str, first_name: str, last_name: str, 
                            city: str = None, package_type: str = None) -> Tuple[bool, str]:
        """Send thank you email after rental return"""
        try:
            self.logger.info(f"Sending thank you email to {recipient_email}")
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = 'Thank You for Returning Your Rental!'
            msg['From'] = self.gmail_address
            msg['To'] = recipient_email
            
            # Add HTML content
            html_content = self.create_thank_you_email(
                first_name, last_name, city, package_type
            )
            msg.attach(MIMEText(html_content, 'html'))
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)
            
            self.logger.info(f"Thank you email sent successfully to {recipient_email}")
            return True, "Email sent successfully"
            
        except Exception as e:
            error_msg = f"Failed to send thank you email: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg