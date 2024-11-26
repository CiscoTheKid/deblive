import mysql.connector
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from config import Config
import logging
import os
from rental_email_handler import RentalEmailHandler

# Set up logging
logger = logging.getLogger(__name__)

class DatabaseHandler:
    def __init__(self, config=None):
        self.config = config or Config.get_db_config()
        self.max_retries = 3
        self.connect()

    def connect(self):
        try:
            self.connection = mysql.connector.connect(**self.config)
            self.cursor = self.connection.cursor(dictionary=True)
            self.cursor.execute("SET SESSION wait_timeout=28800")
            
            # Check and update rental_status column if needed
            self.cursor.execute("""
                SELECT DATA_TYPE 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'users' 
                AND COLUMN_NAME = 'rental_status'
                AND TABLE_SCHEMA = DATABASE()
            """)
            column_type = self.cursor.fetchone()
            
            # If column doesn't exist or isn't TINYINT, modify it
            if not column_type or column_type['DATA_TYPE'] != 'tinyint':
                self.cursor.execute("""
                    ALTER TABLE users 
                    MODIFY COLUMN rental_status TINYINT DEFAULT 0 
                    COMMENT '0=Not Active, 1=Active Rental, 2=Returned'
                """)
                self.connection.commit()
                
            logger.info("Database connection successful!")
        except mysql.connector.Error as err:
            logger.error(f"Failed to connect to database: {err}")
            raise Exception(f"Database error: {err}")

    def get_database_stats(self) -> Dict:
        """Get current database statistics"""
        try:
            stats = {
                'total_users': 0,
                'total_qr_codes': 0,
                'active_rentals': 0
            }
            
            # Get total users
            self.cursor.execute("SELECT COUNT(*) as count FROM users")
            stats['total_users'] = self.cursor.fetchone()['count']
            
            # Get total QR codes
            self.cursor.execute("SELECT COUNT(*) as count FROM qr_codes")
            stats['total_qr_codes'] = self.cursor.fetchone()['count']
            
            # Get active rentals (status = 1)
            self.cursor.execute("SELECT COUNT(*) as count FROM users WHERE rental_status = 1")
            stats['active_rentals'] = self.cursor.fetchone()['count']
            
            return stats
            
        except mysql.connector.Error as err:
            logger.error(f"Error getting database stats: {err}")
            raise Exception(f"Database error: {err}")

    def reset_database(self):
        """Reset the database by dropping all client data"""
        try:
            # Disable foreign key checks temporarily
            self.cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            
            # List of tables to truncate
            tables = ['email_logs', 'rentals', 'qr_codes', 'users']
            
            # Truncate all tables
            for table in tables:
                self.cursor.execute(f"TRUNCATE TABLE {table}")
                logger.info(f"Truncated table: {table}")
            
            # Re-enable foreign key checks
            self.cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            
            self.connection.commit()
            logger.info("Database reset completed successfully")
            
        except mysql.connector.Error as err:
            self.connection.rollback()
            logger.error(f"Error resetting database: {err}")
            raise Exception(f"Database error: {err}")
        finally:
            self.cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    def update_rental_status(self, user_id: int, status: int) -> bool:
        """
        Update the rental status for a user
        
        Args:
            user_id (int): User ID to update
            status (int): New status (0=Not Active, 1=Active Rental, 2=Returned)
            
        Returns:
            bool: True if update was successful
        """
        try:
            # Validate status value
            if status not in [0, 1, 2]:
                raise ValueError("Invalid rental status. Must be 0, 1, or 2")
                
            # Get user details first
            self.cursor.execute("""
                SELECT first_name, last_name, email, city, package_type
                FROM users
                WHERE id = %s
            """, (user_id,))
            user = self.cursor.fetchone()
            
            if not user:
                raise ValueError(f"User not found with ID: {user_id}")
                
            # Update user's rental status
            self.cursor.execute("""
                UPDATE users 
                SET rental_status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (status, user_id))
            
            # Get active QR code for the user
            self.cursor.execute("""
                SELECT id 
                FROM qr_codes 
                WHERE user_id = %s AND is_active = TRUE
                LIMIT 1
            """, (user_id,))
            qr_code = self.cursor.fetchone()
            
            if qr_code:
                if status == 1:  # Active Rental
                    self.cursor.execute("""
                        INSERT INTO rentals (
                            user_id, 
                            qr_code_id,
                            rental_item_id,
                            checkout_time,
                            status
                        ) VALUES (
                            %s, %s, 1, CURRENT_TIMESTAMP, 'checked_out'
                        )
                    """, (user_id, qr_code['id']))
                elif status == 2:  # Returned
                    self.cursor.execute("""
                        UPDATE rentals
                        SET status = 'returned',
                            return_time = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                        AND status = 'checked_out'
                    """, (user_id,))
                    
                    if user:
                        email_handler = RentalEmailHandler(
                            os.getenv('GMAIL_ADDRESS'),
                            os.getenv('GMAIL_APP_PASSWORD')
                        )
                        
                        # Send thank you email with proper user details
                        success, message = email_handler.send_thank_you_email(
                            user['email'],
                            user['first_name'],
                            user['last_name'],
                            user.get('city'),  # Use get() to handle possible None values
                            user.get('package_type')
                        )
                        
                        # Log the email attempt
                        self.log_email(
                            user_id,
                            qr_code['id'],
                            'success' if success else 'failed',
                            None if success else message
                        )
            
            self.connection.commit()
            logger.info(f"Updated rental status for user {user_id} to {status}")
            return True
            
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Failed to update rental status: {err}")
            raise Exception(f"Database error: {err}")

    
    def create_user(self, first_name: str, last_name: str, email: str, city: str = None, package_type: str = None) -> int:
        """Create a new user or update existing user"""
        try:
            # Check if user exists
            self.cursor.execute("""
                SELECT id FROM users 
                WHERE email = %s
            """, (email,))
            
            existing_user = self.cursor.fetchone()
            
            if existing_user:
                # Update existing user
                self.cursor.execute("""
                    UPDATE users 
                    SET first_name = %s,
                        last_name = %s,
                        city = %s,
                        package_type = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (first_name, last_name, city, package_type, existing_user['id']))
                self.connection.commit()
                return existing_user['id']
            else:
                # Create new user with default status 0 (Not Active)
                self.cursor.execute("""
                    INSERT INTO users (first_name, last_name, email, city, package_type, rental_status)
                    VALUES (%s, %s, %s, %s, %s, 0)
                """, (first_name, last_name, email, city, package_type))
                self.connection.commit()
                return self.cursor.lastrowid
                
        except mysql.connector.Error as err:
            self.connection.rollback()
            raise Exception(f"Database error: {err}")


    def store_qr_code(self, user_id: int, qr_data: str, qr_code_number: str, qr_image: bytes) -> int:
        """Store QR code in database"""
        try:
            # Deactivate previous QR codes for this user
            self.cursor.execute("""
                UPDATE qr_codes 
                SET is_active = FALSE 
                WHERE user_id = %s
            """, (user_id,))
            
            # Insert new QR code
            self.cursor.execute("""
                INSERT INTO qr_codes (user_id, qr_data, qr_code_number, qr_image, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (user_id, qr_data, qr_code_number, qr_image))
            
            self.connection.commit()
            return self.cursor.lastrowid
            
        except mysql.connector.Error as err:
            self.connection.rollback()
            raise Exception(f"Database error: {err}")

    def log_email(self, user_id: int, qr_code_id: int, status: str, error_message: str = None):
        """Log email sending attempt"""
        try:
            self.cursor.execute("""
                INSERT INTO email_logs (user_id, qr_code_id, status, error_message)
                VALUES (%s, %s, %s, %s)
            """, (user_id, qr_code_id, status, error_message))
            self.connection.commit()
            
        except mysql.connector.Error as err:
            self.connection.rollback()
            logger.error(f"Failed to log email: {err}")

    def verify_qr_code(self, qr_code_number: str) -> Optional[Dict]:
        try:
            query = """
            SELECT 
                u.id as user_id,
                u.first_name,
                u.last_name,
                u.email,
                u.city,
                u.package_type,
                u.rental_status,
                u.notes,
                u.notes_updated_at,
                qr.id as qr_code_id,
                qr.qr_code_number,
                qr.created_at as qr_created_at
            FROM users u
            JOIN qr_codes qr ON u.id = qr.user_id
            WHERE qr.qr_code_number = %s
            AND qr.is_active = TRUE
            """
            self.cursor.execute(query, (qr_code_number,))
            return self.cursor.fetchone()
        except mysql.connector.Error as err:
            logger.error(f"Database error in verify_qr_code: {err}")
            raise Exception(f"Database error: {err}")
    

    def search_by_first_name(self, first_name: str) -> List[Dict]:
        """Search users by first name"""
        try:
            query = """
            SELECT 
                u.id as user_id,
                u.first_name,
                u.last_name,
                u.email,
                COALESCE(u.rental_status, FALSE) as rental_status,
                u.updated_at,
                qr.id as qr_code_id,
                qr.qr_code_number,
                qr.created_at as qr_created_at
            FROM users u
            LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
            WHERE LOWER(u.first_name) LIKE LOWER(%s)
            """
            self.cursor.execute(query, (f"%{first_name}%",))
            return self.cursor.fetchall()
        except mysql.connector.Error as err:
            logger.error(f"Error searching by first name: {err}")
            raise Exception(f"Database error: {err}")

    def search_by_last_name(self, last_name: str) -> List[Dict]:
        """Search users by last name"""
        try:
            query = """
            SELECT 
                u.id as user_id,
                u.first_name,
                u.last_name,
                u.email,
                COALESCE(u.rental_status, FALSE) as rental_status,
                u.updated_at,
                qr.id as qr_code_id,
                qr.qr_code_number,
                qr.created_at as qr_created_at
            FROM users u
            LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
            WHERE LOWER(u.last_name) LIKE LOWER(%s)
            """
            self.cursor.execute(query, (f"%{last_name}%",))
            return self.cursor.fetchall()
        except mysql.connector.Error as err:
            logger.error(f"Error searching by last name: {err}")
            raise Exception(f"Database error: {err}")


    def get_email_logs(self) -> List[Dict]:
        """Get all email logs"""
        try:
            query = """
            SELECT 
                el.id,
                u.first_name,
                u.last_name,
                u.email,
                el.status,
                el.error_message,
                el.created_at
            FROM email_logs el
            JOIN users u ON el.user_id = u.id
            ORDER BY el.created_at DESC
            """
            self.cursor.execute(query)
            return self.cursor.fetchall()
            
        except mysql.connector.Error as err:
            raise Exception(f"Database error: {err}")

    def get_rental_history(self, user_id: int) -> List[Dict]:
        """Get rental history for a specific user"""
        try:
            query = """
            SELECT 
                r.id as rental_id,
                r.checkout_time,
                r.return_time,
                r.status,
                qr.qr_code_number
            FROM rentals r
            JOIN qr_codes qr ON r.qr_code_id = qr.id
            WHERE r.user_id = %s
            ORDER BY r.checkout_time DESC
            """
            self.cursor.execute(query, (user_id,))
            return self.cursor.fetchall()
            
        except mysql.connector.Error as err:
            raise Exception(f"Database error: {err}")

    def get_active_rentals(self) -> List[Dict]:
        """Get all active rentals"""
        try:
            query = """
            SELECT 
                r.id as rental_id,
                u.first_name,
                u.last_name,
                u.email,
                qr.qr_code_number,
                r.checkout_time,
                r.status
            FROM rentals r
            JOIN users u ON r.user_id = u.id
            JOIN qr_codes qr ON r.qr_code_id = qr.id
            WHERE r.status = 'checked_out'
            ORDER BY r.checkout_time DESC
            """
            self.cursor.execute(query)
            return self.cursor.fetchall()
            
        except mysql.connector.Error as err:
            raise Exception(f"Database error: {err}")

    def close(self):
        """Safely close database connections"""
        try:
            if hasattr(self, 'cursor') and self.cursor:
                self.cursor.close()
            if hasattr(self, 'connection') and self.connection:
                self.connection.close()
                logger.info("Database connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing database connection: {e}")