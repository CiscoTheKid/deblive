The Dinner En Blanc QR Rental System is a specialized web application designed to streamline the rental process for Dinner En Blanc events. 
The system handles the entire rental lifecycle, from QR code generation and email distribution to package checkout and return processing. 
Built with Flask, MySQL, and Tailwind CSS, this application offers a responsive and intuitive interface for both administrators and staff.
This application will work with JotForms submissions. This means you can create a form, take payment and collect information all in a streamlined way, allowing you to track
all customers who attended the event, and track rental items. 

Key Features:

QR Code Generation & Distribution: Automatically generates unique QR codes for each customer and emails them directly
Multiple User Roles: Separate admin and staff access with appropriate permissions
Real-time Rental Status Tracking: Track rental status (Not Active, Active Rental, Returned) with a simple interface
Bulk Email Processing: Process CSV lists of customers to send QR codes in batch
Manual Lookup Options: Search by QR code, first name, or last name
Mobile-Friendly Scanner: Web-based QR scanner works on various devices
Customer Notes: Add and track notes for each customer
Thank You Emails: Automated emails when items are returned
Database Management: Admin tools for database viewing and reset functions

Installation Prerequisites



Python 3.8+ & pip installs found in requirements.txt
MySQL Server
SMTP email account (Gmail used to develop this app, but could use something else)
                    If using Gmail make sure you set up App Passwords
                    You will need a domain name to point to eventually for testing the app.
                    Ngrok can be used to substitute as a domain name for localhost machines. This was handy in testing and is recommended for trailing the program

Environment Setup

Clone the repository:
git clone https://github.com/yourusername/dinner-en-blanc-qr-system.git
cd dinner-en-blanc-qr-system

Create a virtual environment:
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies:

pip install -r requirements.txt

Create a .env file in the project root directory with the following variables on each line:

# Database Configuration
DB_HOST=localhost 
DB_NAME=dinnerenblanc
DB_USER=your_db_user
DB_PASSWORD=your_db_password

# Email Configuration
GMAIL_ADDRESS=your_email@gmail.com
GMAIL_APP_PASSWORD=your_app_password

# Admin Credentials
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin_password

# Staff Credentials
USER_CREDENTIALS=staff
USER_PASSWORD=staff_password

# Flask Configuration
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=True
FLASK_SECRET_KEY=your_secret_key

#  JotForm Configuration (Optional)
JOTFORM_SECRET=your_jotform_secret
JOTFORM_API_KEY=your_jotform_api_key

# Core set up for the MySQL database:


sqlCREATE DATABASE dinnerenblanc;
USE dinnerenblanc;

CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  first_name VARCHAR(100) NOT NULL,
  last_name VARCHAR(100) NOT NULL,
  email VARCHAR(255) NOT NULL,
  city VARCHAR(100),
  package_type VARCHAR(255),
  rental_status TINYINT DEFAULT 0 COMMENT '0=Not Active, 1=Active Rental, 2=Returned',
  notes TEXT,
  notes_updated_at TIMESTAMP NULL,
  jotform_submission_id VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE qr_codes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  qr_data VARCHAR(255) NOT NULL,
  qr_code_number VARCHAR(20) NOT NULL,
  qr_image LONGBLOB NOT NULL,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE rentals (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  qr_code_id INT NOT NULL,
  rental_item_id INT DEFAULT 1,
  checkout_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  return_time TIMESTAMP NULL,
  status ENUM('checked_out', 'returned') DEFAULT 'checked_out',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (qr_code_id) REFERENCES qr_codes(id)
);

CREATE TABLE email_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  qr_code_id INT,
  status VARCHAR(20) NOT NULL,
  error_message TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (qr_code_id) REFERENCES qr_codes(id)
);


Running the Application

Start the Flask application:
python3 app.py

Access the application in your web browser:

https://IP.ADDR.WHATEVER.NUMBER:5000

Log in with the admin or staff credentials set in your .env file.

Usage Guide

Setup System: Log in as admin, access the admin panel to verify database connection
Generate QR Codes: Upload a CSV file via the Email Client to send QR codes to customers
Monitor Rentals: Track active rentals and return status through the customer database

Staff Workflow

Package Checkout:

Scan customer QR code using the scanner interface
Verify customer information
Click "Check Out Package" to mark as an active rental


Package Return:

Scan the same QR code when items are returned
Click "Check In Package" to mark as returned
System will automatically send a thank you email



Manual Lookup
If a QR code is unavailable or unreadable:

Go to "Manual Lookup"
Search by QR code number, first name, or last name
Select the correct customer from search results
Process checkout or return as normal

System Architecture
The application follows a standard web architecture:

Flask Backend: Handles routing, database interactions, and business logic
MySQL Database: Stores user, QR code, rental, and email data
Jinja2 Templates: Renders dynamic HTML with Tailwind CSS for styling
JavaScript: Provides interactive elements like the QR scanner and dynamic UI updates
SMTP Email Integration: Handles automatic email sending for QR codes and thank you messages

*Customization of Email Templates
Email templates can be customized by modifying the HTML content in:

qr_email_sender.py - For QR code emails, this is what client will present at "check in" 
rental_email_handler.py - For thank you emails, follow up email etc

*Appearance

The UI is built with Tailwind CSS. Customize the appearance by modifying the HTML templates in the templates directory.


*Troubleshooting Common Issues

Email Sending Failures: Verify your Gmail app password and ensure "Less secure app access" is enabled
Database Connection Errors: Check your MySQL credentials and ensure the server is running
QR Scanner Not Working: Ensure camera permissions are granted in your browser and make sure that there is some kind of SSL cert, even if it is a snakeoil cert. 
                        I had issues with SSL and the JS QR code library when developing this. 
