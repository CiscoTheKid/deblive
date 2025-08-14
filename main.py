"""
Command-line interface for QR email sending
Note: This is a standalone CLI tool - the web application uses app.py
"""

from qr_email_sender import QREmailSender
from dotenv import load_dotenv
import sys
import os
import logging

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_db_connection():
    """Test database connection"""
    try:
        from db_handler import DatabaseHandler
        db = DatabaseHandler()
        db.cursor.execute("SELECT 1")
        db.cursor.fetchone()
        db.close()
        return True
    except Exception as err:
        logger.error(f"Database connection failed: {err}")
        return False

def main():
    """Main CLI function for sending QR codes via CSV"""
    print("QR Code Email System - CLI Tool")
    print("================================\n")
    
    try:
        # Verify environment variables
        required_vars = ['DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
                        'GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD']
        
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            sys.exit(f"Missing environment variables: {', '.join(missing_vars)}")
        
        # Test database connection
        print("Testing database connection...")
        if not test_db_connection():
            sys.exit("Database connection failed")
        print("Database connected successfully!\n")
        
        # Get CSV file path
        csv_path = input("Enter CSV file path: ").strip()
        if not os.path.exists(csv_path) or not csv_path.endswith('.csv'):
            sys.exit(f"Invalid CSV file: {csv_path}")
        
        # Process CSV
        print(f"\nProcessing: {csv_path}")
        print("Press Ctrl+C to stop\n")
        
        sender = QREmailSender()
        results = sender.process_csv(csv_path)
        
        # Print summary
        successful = sum(1 for r in results if r['success'])
        failed = len(results) - successful
        
        print("\n" + "="*50)
        print(f"Processing Complete!")
        print(f"Total: {len(results)} | Success: {successful} | Failed: {failed}")
        print("="*50)
        
        # Show failed emails if any
        if failed > 0:
            print("\nFailed emails:")
            for r in results:
                if not r['success']:
                    print(f"  - {r['email']}: {r['result']}")
        
    except KeyboardInterrupt:
        print("\n\nStopped by user")
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()