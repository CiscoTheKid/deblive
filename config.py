import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # Database configuration
    DB_HOST = os.getenv('DB_HOST')
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    
    # Email configuration
    GMAIL_ADDRESS = os.getenv('GMAIL_ADDRESS')
    GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')
    

    #Admin Account Credentials
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME')  # Default to 'admin' if not set
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')  # Default to 'rentals2024' if not set

    #Default User Credentials
    USER_CREDENTIALS= os.getenv('USER_CREDENTIALS')
    USER_PASSWORD= os.getenv('USER_PASSWORD')


    # Flask configuration
    FLASK_HOST = os.getenv('FLASK_HOST')
    FLASK_PORT = int(os.getenv('FLASK_PORT'))
    FLASK_DEBUG = os.getenv('FLASK_DEBUG').lower() == 'true'
    FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY') 

    #Jotform API config details
    JOTFORM_SECRET = os.getenv('JOTFORM_SECRET')  # JotForm webhook secret
    JOTFORM_API_KEY = os.getenv('JOTFORM_API_KEY')  # JotForm API key

    

    @classmethod
    def get_db_config(cls):
        return {
            'host': cls.DB_HOST,
            'database': cls.DB_NAME,
            'user': cls.DB_USER,
            'password': cls.DB_PASSWORD,
            'auth_plugin': 'mysql_native_password',
            'use_pure': True,
            'ssl_disabled': True,
            'ssl_verify_cert': False,
            'ssl_verify_identity': False,
            'get_warnings': True,
            'raise_on_warnings': False,
            'connection_timeout': 10,
            'buffered': True
        }