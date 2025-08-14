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
        """Initialize database handler with connection pooling"""
        self.config = config or Config.get_db_config()
        self.connection = None
        self.cursor = None
        self.connect()

    def connect(self):
        """Establish database connection"""
        try:
            if self.connection and self.connection.is_connected():
                return  # Already connected
                
            self.connection = mysql.connector.connect(**self.config)
            self.cursor = self.connection.cursor(dictionary=True)
            self.cursor.execute("SET SESSION wait_timeout=28800")
            logger.info("Database connection successful")
        except mysql.connector.Error as err:
            logger.error(f"Database connection failed: {err}")
            raise

    def ensure_connection(self):
        """Ensure database connection is active"""
        if not self.connection or not self.connection.is_connected():
            self.connect()

    def get_database_stats(self) -> Dict:
        """Get database statistics"""
        self.ensure_connection()
        stats = {
            'total_users': 0,
            'total_qr_codes': 0,
            'active_rentals': 0,
            'total_packages': 0,
            'available_packages': 0,
            'rented_packages': 0
        }
        
        # Get counts
        queries = [
            ("SELECT COUNT(*) as count FROM users", 'total_users'),
            ("SELECT COUNT(*) as count FROM qr_codes", 'total_qr_codes'),
            ("SELECT COUNT(*) as count FROM users WHERE rental_status = 1", 'active_rentals'),
            ("SELECT COUNT(*) as count FROM user_packages", 'total_packages'),
            ("SELECT COUNT(*) as count FROM user_packages WHERE status = 'available'", 'available_packages'),
            ("SELECT COUNT(*) as count FROM user_packages WHERE status = 'rented_out'", 'rented_packages')
        ]
        
        for query, key in queries:
            try:
                self.cursor.execute(query)
                stats[key] = self.cursor.fetchone()['count']
            except:
                stats[key] = 0
                
        return stats

    def reset_database(self):
        """Reset all database tables"""
        self.ensure_connection()
        try:
            self.cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            
            tables = ['email_logs', 'rentals', 'user_packages', 'qr_codes', 'users']
            for table in tables:
                try:
                    self.cursor.execute(f"TRUNCATE TABLE {table}")
                    logger.info(f"Truncated table: {table}")
                except Exception as e:
                    logger.warning(f"Could not truncate {table}: {e}")
            
            self.cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            self.connection.commit()
            logger.info("Database reset completed")
        except Exception as err:
            self.connection.rollback()
            raise Exception(f"Database reset failed: {err}")

    def create_user(self, first_name: str, last_name: str, email: str, 
                   city: str = None, package_type: str = None) -> int:
        """Create or update user by email"""
        self.ensure_connection()
        try:
            # Check if user exists
            self.cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            existing_user = self.cursor.fetchone()
            
            if existing_user:
                # Update existing user
                self.cursor.execute("""
                    UPDATE users 
                    SET first_name = %s, last_name = %s, city = %s, 
                        package_type = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (first_name, last_name, city, package_type, existing_user['id']))
                self.connection.commit()
                return existing_user['id']
            else:
                # Create new user
                self.cursor.execute("""
                    INSERT INTO users (first_name, last_name, email, city, package_type, rental_status)
                    VALUES (%s, %s, %s, %s, %s, 0)
                """, (first_name, last_name, email, city, package_type))
                self.connection.commit()
                return self.cursor.lastrowid
        except Exception as err:
            self.connection.rollback()
            raise

    def store_qr_code(self, user_id: int, qr_data: str, qr_code_number: str, qr_image: bytes) -> int:
        """Store QR code for user"""
        self.ensure_connection()
        try:
            # Deactivate old codes
            self.cursor.execute("UPDATE qr_codes SET is_active = FALSE WHERE user_id = %s", (user_id,))
            
            # Insert new code
            self.cursor.execute("""
                INSERT INTO qr_codes (user_id, qr_data, qr_code_number, qr_image, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (user_id, qr_data, qr_code_number, qr_image))
            
            self.connection.commit()
            return self.cursor.lastrowid
        except Exception as err:
            self.connection.rollback()
            raise

    def log_email(self, user_id: int, qr_code_id: int, status: str, error_message: str = None):
        """Log email sending attempt"""
        self.ensure_connection()
        try:
            self.cursor.execute("""
                INSERT INTO email_logs (user_id, qr_code_id, status, error_message)
                VALUES (%s, %s, %s, %s)
            """, (user_id, qr_code_id, status, error_message))
            self.connection.commit()
        except Exception as err:
            logger.error(f"Failed to log email: {err}")

    def verify_qr_code(self, qr_code_number: str) -> Optional[Dict]:
        """Verify QR code and return user data"""
        self.ensure_connection()
        try:
            self.cursor.execute("""
                SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                       u.city, u.package_type, u.rental_status, u.notes,
                       u.notes_updated_at, qr.id as qr_code_id, qr.qr_code_number
                FROM users u
                JOIN qr_codes qr ON u.id = qr.user_id
                WHERE qr.qr_code_number = %s AND qr.is_active = TRUE
            """, (qr_code_number,))
            return self.cursor.fetchone()
        except Exception as err:
            logger.error(f"Error verifying QR code: {err}")
            return None

    def search_by_first_name(self, first_name: str) -> List[Dict]:
        """Search users by first name"""
        self.ensure_connection()
        try:
            self.cursor.execute("""
                SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                       u.rental_status, u.updated_at, qr.qr_code_number
                FROM users u
                LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
                WHERE LOWER(u.first_name) LIKE LOWER(%s)
            """, (f"%{first_name}%",))
            return self.cursor.fetchall()
        except Exception as err:
            logger.error(f"Error searching by first name: {err}")
            return []

    def search_by_last_name(self, last_name: str) -> List[Dict]:
        """Search users by last name"""
        self.ensure_connection()
        try:
            self.cursor.execute("""
                SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                       u.rental_status, u.updated_at, qr.qr_code_number
                FROM users u
                LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
                WHERE LOWER(u.last_name) LIKE LOWER(%s)
            """, (f"%{last_name}%",))
            return self.cursor.fetchall()
        except Exception as err:
            logger.error(f"Error searching by last name: {err}")
            return []

    def add_user_packages(self, user_id: int, package_type: str, quantity: int) -> bool:
        """Add packages to user inventory"""
        self.ensure_connection()
        try:
            for _ in range(quantity):
                self.cursor.execute("""
                    INSERT INTO user_packages (user_id, package_type, status)
                    VALUES (%s, %s, 'available')
                """, (user_id, package_type))
            
            self.connection.commit()
            logger.info(f"Added {quantity} {package_type} packages for user {user_id}")
            return True
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Failed to add packages: {err}")
            raise

    def get_user_packages(self, user_id: int) -> List[Dict]:
        """Get all packages for a user"""
        self.ensure_connection()
        try:
            self.cursor.execute("""
                SELECT id, package_type, status, last_activity_time
                FROM user_packages
                WHERE user_id = %s
                ORDER BY package_type, status
            """, (user_id,))
            return self.cursor.fetchall() or []
        except Exception as err:
            logger.error(f"Error getting packages: {err}")
            return []

    def get_user_package_summary(self, user_id: int) -> Dict:
        """Get package summary for user"""
        self.ensure_connection()
        try:
            # Get total packages
            self.cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available,
                       SUM(CASE WHEN status = 'rented_out' THEN 1 ELSE 0 END) as rented
                FROM user_packages WHERE user_id = %s
            """, (user_id,))
            
            result = self.cursor.fetchone()
            
            return {
                'total_packages': result['total'] or 0,
                'available_packages': result['available'] or 0,
                'rented_packages': result['rented'] or 0,
                'has_packages': (result['total'] or 0) > 0,
                'all_returned': (result['rented'] or 0) == 0
            }
        except Exception as err:
            logger.error(f"Error getting package summary: {err}")
            return {
                'total_packages': 0,
                'available_packages': 0,
                'rented_packages': 0,
                'has_packages': False,
                'all_returned': True
            }

    def update_package_status(self, package_id: int, new_status: str) -> bool:
        """Update single package status"""
        self.ensure_connection()
        try:
            self.cursor.execute("""
                UPDATE user_packages
                SET status = %s, last_activity_time = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (new_status, package_id))
            self.connection.commit()
            return True
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Failed to update package {package_id}: {err}")
            return False

    def update_rental_status_new(self, user_id: int, action: str) -> Tuple[bool, str]:
        """Handle package checkout/checkin actions"""
        self.ensure_connection()
        try:
            summary = self.get_user_package_summary(user_id)
            
            if not summary['has_packages']:
                return False, "User has no packages"
            
            if action == 'checkout_all':
                return self._checkout_packages(user_id, summary['available_packages'])
            elif action == 'checkin_all':
                return self._checkin_packages(user_id, summary['rented_packages'])
            elif action == 'checkout_one':
                return self._checkout_packages(user_id, 1)
            elif action == 'checkin_one':
                return self._checkin_packages(user_id, 1)
            else:
                return False, f"Invalid action: {action}"
                
        except Exception as err:
            logger.error(f"Error in update_rental_status_new: {err}")
            return False, str(err)

    def _checkout_packages(self, user_id: int, count: int) -> Tuple[bool, str]:
        """Check out packages for user"""
        if count <= 0:
            return False, "Invalid package count"
            
        try:
            # Get available packages
            self.cursor.execute("""
                SELECT id FROM user_packages 
                WHERE user_id = %s AND status = 'available'
                LIMIT %s
            """, (user_id, count))
            
            packages = self.cursor.fetchall()
            if not packages:
                return False, "No available packages"
            
            # Update packages to rented
            package_ids = [p['id'] for p in packages]
            format_strings = ','.join(['%s'] * len(package_ids))
            self.cursor.execute(f"""
                UPDATE user_packages 
                SET status = 'rented_out', last_activity_time = CURRENT_TIMESTAMP
                WHERE id IN ({format_strings})
            """, package_ids)
            
            # Update user status
            self.cursor.execute("""
                UPDATE users SET rental_status = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (user_id,))
            
            self.connection.commit()
            return True, f"Checked out {len(packages)} packages"
            
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Checkout error: {err}")
            return False, str(err)

    def _checkin_packages(self, user_id: int, count: int) -> Tuple[bool, str]:
        """Check in packages for user"""
        if count <= 0:
            return False, "Invalid package count"
            
        try:
            # Get rented packages
            self.cursor.execute("""
                SELECT id FROM user_packages 
                WHERE user_id = %s AND status = 'rented_out'
                LIMIT %s
            """, (user_id, count))
            
            packages = self.cursor.fetchall()
            if not packages:
                return False, "No rented packages"
            
            # Update packages to available
            package_ids = [p['id'] for p in packages]
            format_strings = ','.join(['%s'] * len(package_ids))
            self.cursor.execute(f"""
                UPDATE user_packages 
                SET status = 'available', last_activity_time = CURRENT_TIMESTAMP
                WHERE id IN ({format_strings})
            """, package_ids)
            
            self.connection.commit()
            
            # Check if all returned
            summary = self.get_user_package_summary(user_id)
            
            if summary['all_returned']:
                # Update user status to returned
                self.cursor.execute("""
                    UPDATE users SET rental_status = 2, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (user_id,))
                self.connection.commit()
                
                # Send thank you email
                self._send_thank_you_email(user_id)
                
                return True, f"All {summary['total_packages']} packages returned - Thank you email sent"
            else:
                return True, f"Checked in {len(packages)} packages"
                
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Checkin error: {err}")
            return False, str(err)

    def _send_thank_you_email(self, user_id: int):
        """Send thank you email when all packages returned"""
        try:
            self.cursor.execute("""
                SELECT first_name, last_name, email, city, package_type
                FROM users WHERE id = %s
            """, (user_id,))
            user = self.cursor.fetchone()
            
            if user:
                email_handler = RentalEmailHandler(
                    os.getenv('GMAIL_ADDRESS'),
                    os.getenv('GMAIL_APP_PASSWORD')
                )
                success, message = email_handler.send_thank_you_email(
                    user['email'],
                    user['first_name'],
                    user['last_name'],
                    user.get('city'),
                    user.get('package_type')
                )
                
                # Log email
                qr_code_id = None
                self.cursor.execute("""
                    SELECT id FROM qr_codes WHERE user_id = %s AND is_active = TRUE LIMIT 1
                """, (user_id,))
                qr = self.cursor.fetchone()
                if qr:
                    qr_code_id = qr['id']
                    
                self.log_email(
                    user_id, 
                    qr_code_id,
                    'success_thank_you' if success else 'failed_thank_you',
                    None if success else message
                )
                
                logger.info(f"Thank you email {'sent' if success else 'failed'} for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error sending thank you email: {e}")

    def close(self):
        """Close database connections"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.connection:
                self.connection.close()
            logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error closing connection: {e}")