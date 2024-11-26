from qr_email_sender import QREmailSender
from config import Config
from dotenv import load_dotenv
import sys
import os
import mysql.connector
import logging

# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_db_connection():
    """Test database connection with configured credentials"""
    try:
        conn = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD')
        )
        conn.close()
        return True
    except mysql.connector.Error as err:
        logger.error(f"Database connection failed: {err}")
        return False

def main():
    print("Welcome to the QR Code Email System")
    print("===================================")

    try:
        # Verify environment variables
        required_vars = [
            'DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
            'GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD'
        ]
        
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            sys.exit(f"Missing required environment variables: {', '.join(missing_vars)}")

        # Test database connection
        print("\nTesting database connection...")
        if not test_db_connection():
            sys.exit("Database connection failed. Please check your configuration.")
        
        # Initialize sender using environment variables
        sender = QREmailSender()  # Will automatically use env variables
        
        # Get CSV path
        while True:
            csv_path = input("\nEnter the path to your CSV file: ").strip()
            if os.path.exists(csv_path) and csv_path.endswith('.csv'):
                break
            print(f"Error: File not found at {csv_path}. Please enter a valid CSV file path.")
        
        # Process CSV
        print(f"\nProcessing CSV file: {csv_path}")
        print("Press Ctrl+C to stop at any time...\n")
        
        results = sender.process_csv(csv_path)
        
        # Print results and summary
        print("\nDetailed Results:")
        for result in results:
            status = "✓ Success" if result['success'] else "✗ Failed"
            if result['success']:
                print(f"{status} - {result['email']} (QR Code: {result['result']})")
            else:
                print(f"{status} - {result['email']} (Error: {result['result']})")

        successful = sum(1 for r in results if r['success'])
        print(f"\nSummary:")
        print(f"Total processed: {len(results)}")
        print(f"Successful: {successful}")
        print(f"Failed: {len(results) - successful}")

    except KeyboardInterrupt:
        print("\n\nProcessing stopped by user.")
    except Exception as e:
        logger.exception("An unexpected error occurred")
        print(f"\nAn error occurred: {str(e)}")
    finally:
        print("\nProcessing complete!")

if __name__ == "__main__":
    main()