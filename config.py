import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Configuration class for application settings"""
    
    # Database configuration
    DB_HOST = os.getenv('DB_HOST')
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    
    # Email configuration
    GMAIL_ADDRESS = os.getenv('GMAIL_ADDRESS')
    GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')
    
    # Admin credentials
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
    
    # User credentials
    USER_CREDENTIALS = os.getenv('USER_CREDENTIALS')
    USER_PASSWORD = os.getenv('USER_PASSWORD')
    
    # Flask configuration
    FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
    FLASK_PORT = int(os.getenv('FLASK_PORT', 5000))
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'default-secret-key')
    
    # JotForm configuration
    JOTFORM_SECRET = os.getenv('JOTFORM_SECRET')
    JOTFORM_API_KEY = os.getenv('JOTFORM_API_KEY')
    
    @classmethod
    def get_db_config(cls):
        """Get database configuration dictionary"""
        return {
            'host': cls.DB_HOST,
            'database': cls.DB_NAME,
            'user': cls.DB_USER,
            'password': cls.DB_PASSWORD,
            'auth_plugin': 'mysql_native_password',
            'buffered': True
        }