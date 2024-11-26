import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from typing import Tuple

class RentalEmailHandler:
    def __init__(self, gmail_address, app_password):
        self.gmail_address = gmail_address
        self.app_password = app_password
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.logger = logging.getLogger(__name__)

    def create_thank_you_email(self, first_name: str, last_name: str, city: str = None, package_type: str = None) -> str:
        """Create HTML content for thank you email with city and package information"""
        
        # Handle None values with defaults
        city_display = city if city and city.strip() else "your local"
        package_display = package_type if package_type and package_type.strip() else "rental"
        
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2c3e50;">Thank You for Returning Your {package_display.title()} - We Hope Your Dîner en Blanc Was Magical!</h2>
                    <p>Dear {first_name} {last_name},</p>
                    <p>Thank you for choosing Rentals to Remember as your table setting partner for {city_display} Dîner en Blanc. Your satisfaction with the {package_display} means everything to us.</p>
                    <p>We'd be honored to be part of your celebration again next year!</p>
                    <p>Did you know? Beyond Dîner en Blanc, we're your local event specialists offering a vast collection of premium rental items for all occasions. Whether it's an intimate gathering or a grand celebration, we're here to make every event special.</p>
                    <p>Discover our full collection and services:</p>
                    <a href="https://www.rentalstoremember.com" target="_blank">Explore Rentals to Remember - Transform Your Next Event</a>

                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="color: #2c3e50;">Keep the Magic Going:</h3>
                                <ul>
                                    <li>Join us next year for another enchanting Dîner en Blanc {city_display}</li>
                                    <li>Secure your {package_display} early to guarantee your spot at next year's celebration</li>
                                    <li>Spread the joy - share your magical {city_display} DEB moments with loved ones</li>
                                    <li>Share your experience with us! <a href="mailto:info@rentalstoremember.com">Drop us an email</a> or provide anonymous feedback through our <a href="https://www.rentalstoremember.com/linktree/">website</a></li>
                                </ul>                        
                    </div>
                    <div style="background-color: #e8f4fd; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <h3 style="color: #2c3e50;">Your Rental Details:</h3>
                        <ul>
                            <li><strong>Location:</strong> {city_display} DEB Event</li>
                            <li><strong>Package:</strong> {package_display.title()} Package</li>
                            </ul>
                    </div>
                    <p>Our Best regards,<br>The Rentals To Remember Team</p>
                </div>
            </body>
        </html>
        """
        return html_content

    def send_thank_you_email(self, recipient_email: str, first_name: str, last_name: str, 
                            city: str = None, package_type: str = None) -> Tuple[bool, str]:
        """Send thank you email after rental return with city and package information"""
        try:
            self.logger.info(f"Sending thank you email to {recipient_email}")
            
            # Log the received values for debugging
            self.logger.debug(f"Email parameters - City: {city}, Package Type: {package_type}")
            
            msg = MIMEMultipart('related')
            msg['Subject'] = f'Thank You for Your {package_type.title() if package_type else "Rental"} Rental!'
            msg['From'] = self.gmail_address
            msg['To'] = recipient_email

            # Add HTML content with city and package information
            html_content = self.create_thank_you_email(
                first_name,
                last_name,
                city,
                package_type
            )
            msg.attach(MIMEText(html_content, 'html'))

            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)
            
            self.logger.info(f"Successfully sent thank you email to {recipient_email} for {city} event")
            return True, "Email sent successfully"
            
        except Exception as e:
            error_msg = f"Failed to send thank you email: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg