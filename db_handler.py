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
        """Establish database connection with proper cleanup of old connections"""
        try:
            # Clean up any existing connections first
            self._cleanup_connections()
            
            # Create new connection
            self.connection = mysql.connector.connect(**self.config)
            self.cursor = self.connection.cursor(dictionary=True)
            
            # Set connection parameters for longer timeout and better stability
            self.cursor.execute("SET SESSION wait_timeout=28800")  # 8 hours
            self.cursor.execute("SET SESSION interactive_timeout=28800")  # 8 hours
            self.cursor.execute("SET SESSION net_read_timeout=600")  # 10 minutes
            self.cursor.execute("SET SESSION net_write_timeout=600")  # 10 minutes
            
            logger.info("Database connection established successfully")
            
        except mysql.connector.Error as err:
            logger.error(f"Database connection failed: {err}")
            self._cleanup_connections()
            raise

    def _cleanup_connections(self):
        """Safely close existing connections and cursors"""
        try:
            if hasattr(self, 'cursor') and self.cursor:
                self.cursor.close()
        except:
            pass
        
        try:
            if hasattr(self, 'connection') and self.connection:
                self.connection.close()
        except:
            pass
        
        self.cursor = None
        self.connection = None

    def ensure_connection(self):
        """Ensure database connection is active with comprehensive checks"""
        try:
            # Check if connection objects exist and are connected
            if (not self.connection or 
                not self.cursor or 
                not self.connection.is_connected()):
                logger.info("Database connection lost, reconnecting...")
                self.connect()
                return
            
            # Test the connection with a simple query
            self.cursor.execute("SELECT 1")
            self.cursor.fetchone()
            
        except (mysql.connector.Error, AttributeError) as e:
            logger.warning(f"Connection test failed: {e}, reconnecting...")
            self.connect()

    def _execute_query(self, query, params=None, fetch_type='none'):
        """Execute query with automatic connection management"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                self.ensure_connection()
                
                if params:
                    self.cursor.execute(query, params)
                else:
                    self.cursor.execute(query)
                
                if fetch_type == 'one':
                    return self.cursor.fetchone()
                elif fetch_type == 'all':
                    return self.cursor.fetchall()
                elif fetch_type == 'lastrowid':
                    return self.cursor.lastrowid
                elif fetch_type == 'rowcount':
                    return self.cursor.rowcount
                    
                return True
                
            except mysql.connector.Error as e:
                logger.error(f"Query execution failed (attempt {attempt + 1}/{max_retries}): {e}")
                
                if attempt < max_retries - 1:
                    # Force reconnection on next attempt
                    self._cleanup_connections()
                    continue
                else:
                    raise

    def get_database_stats(self) -> Dict:
        """Get database statistics"""
        stats = {
            'total_users': 0,
            'total_qr_codes': 0,
            'active_rentals': 0,
            'total_packages': 0,
            'available_packages': 0,
            'rented_packages': 0
        }
        
        # Get counts with error handling
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
                result = self._execute_query(query, fetch_type='one')
                stats[key] = result['count'] if result else 0
            except Exception as e:
                logger.error(f"Error getting stat {key}: {e}")
                stats[key] = 0
                
        return stats

    def reset_database(self):
        """Reset all database tables"""
        try:
            self.ensure_connection()
            
            # Disable foreign key checks
            self._execute_query("SET FOREIGN_KEY_CHECKS = 0")
            
            tables = ['email_logs', 'rentals', 'user_packages', 'qr_codes', 'users']
            for table in tables:
                try:
                    self._execute_query(f"TRUNCATE TABLE {table}")
                    logger.info(f"Truncated table: {table}")
                except Exception as e:
                    logger.warning(f"Could not truncate {table}: {e}")
            
            # Re-enable foreign key checks
            self._execute_query("SET FOREIGN_KEY_CHECKS = 1")
            self.connection.commit()
            logger.info("Database reset completed")
            
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            raise Exception(f"Database reset failed: {err}")

    def create_user(self, first_name: str, last_name: str, email: str, 
                   city: str = None, package_type: str = None) -> int:
        """Create or update user by email"""
        try:
            # Check if user exists
            existing_user = self._execute_query(
                "SELECT id FROM users WHERE email = %s", 
                (email,), 
                fetch_type='one'
            )
            
            if existing_user:
                # Update existing user
                self._execute_query("""
                    UPDATE users 
                    SET first_name = %s, last_name = %s, city = %s, 
                        package_type = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (first_name, last_name, city, package_type, existing_user['id']))
                
                self.connection.commit()
                return existing_user['id']
            else:
                # Create new user
                self._execute_query("""
                    INSERT INTO users (first_name, last_name, email, city, package_type, rental_status)
                    VALUES (%s, %s, %s, %s, %s, 0)
                """, (first_name, last_name, email, city, package_type))
                
                user_id = self.cursor.lastrowid
                self.connection.commit()
                return user_id
                
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            raise

    def store_qr_code(self, user_id: int, qr_data: str, qr_code_number: str, qr_image: bytes) -> int:
        """Store QR code for user"""
        try:
            # Deactivate old codes
            self._execute_query(
                "UPDATE qr_codes SET is_active = FALSE WHERE user_id = %s", 
                (user_id,)
            )
            
            # Insert new code
            self._execute_query("""
                INSERT INTO qr_codes (user_id, qr_data, qr_code_number, qr_image, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (user_id, qr_data, qr_code_number, qr_image))
            
            qr_code_id = self.cursor.lastrowid
            self.connection.commit()
            return qr_code_id
            
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            raise

    def log_email(self, user_id: int, qr_code_id: int, status: str, error_message: str = None):
        """Log email sending attempt"""
        try:
            self._execute_query("""
                INSERT INTO email_logs (user_id, qr_code_id, status, error_message)
                VALUES (%s, %s, %s, %s)
            """, (user_id, qr_code_id, status, error_message))
            
            self.connection.commit()
            
        except Exception as err:
            logger.error(f"Failed to log email: {err}")

    def verify_qr_code(self, qr_code_number: str) -> Optional[Dict]:
        """Verify QR code and return user data"""
        try:
            return self._execute_query("""
                SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                       u.city, u.package_type, u.rental_status, u.notes,
                       u.notes_updated_at, qr.id as qr_code_id, qr.qr_code_number
                FROM users u
                JOIN qr_codes qr ON u.id = qr.user_id
                WHERE qr.qr_code_number = %s AND qr.is_active = TRUE
            """, (qr_code_number,), fetch_type='one')
            
        except Exception as err:
            logger.error(f"Error verifying QR code: {err}")
            return None

    def search_by_first_name(self, first_name: str) -> List[Dict]:
        """Search users by first name"""
        try:
            return self._execute_query("""
                SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                       u.rental_status, u.updated_at, qr.qr_code_number
                FROM users u
                LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
                WHERE LOWER(u.first_name) LIKE LOWER(%s)
            """, (f"%{first_name}%",), fetch_type='all') or []
            
        except Exception as err:
            logger.error(f"Error searching by first name: {err}")
            return []

    def search_by_last_name(self, last_name: str) -> List[Dict]:
        """Search users by last name"""
        try:
            return self._execute_query("""
                SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                       u.rental_status, u.updated_at, qr.qr_code_number
                FROM users u
                LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
                WHERE LOWER(u.last_name) LIKE LOWER(%s)
            """, (f"%{last_name}%",), fetch_type='all') or []
            
        except Exception as err:
            logger.error(f"Error searching by last name: {err}")
            return []

    def search_all_users(self, search_term: str) -> List[Dict]:
        """Search all users by name or email for admin management"""
        try:
            return self._execute_query("""
                SELECT u.id as user_id, u.first_name, u.last_name, u.email,
                    u.rental_status, u.created_at, u.package_type,
                    qr.qr_code_number,
                    (SELECT COUNT(*) FROM user_packages WHERE user_id = u.id) as package_count
                FROM users u
                LEFT JOIN qr_codes qr ON u.id = qr.user_id AND qr.is_active = TRUE
                WHERE LOWER(u.first_name) LIKE LOWER(%s) 
                OR LOWER(u.last_name) LIKE LOWER(%s)
                OR LOWER(u.email) LIKE LOWER(%s)
                ORDER BY u.last_name, u.first_name
                LIMIT 50
            """, (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"), fetch_type='all') or []
            
        except Exception as err:
            logger.error(f"Error searching all users: {err}")
            return []
        
    def delete_user_completely(self, user_id: int) -> Tuple[bool, str]:
        """Delete user and all associated data"""
        try:
            # Start transaction
            self.ensure_connection()
            self.connection.start_transaction()
            
            # Get user info for confirmation
            user = self._execute_query(
                "SELECT first_name, last_name, email FROM users WHERE id = %s", 
                (user_id,), 
                fetch_type='one'
            )
            
            if not user:
                self.connection.rollback()
                return False, "User not found"
            
            # Delete in correct order to handle foreign key constraints
            # 1. Delete email logs
            self._execute_query("DELETE FROM email_logs WHERE user_id = %s", (user_id,))
            logs_deleted = self.cursor.rowcount
            
            # 2. Delete rentals (if table exists)
            try:
                self._execute_query("DELETE FROM rentals WHERE user_id = %s", (user_id,))
                rentals_deleted = self.cursor.rowcount
            except:
                rentals_deleted = 0  # Table might not exist
            
            # 3. Delete user packages
            self._execute_query("DELETE FROM user_packages WHERE user_id = %s", (user_id,))
            packages_deleted = self.cursor.rowcount
            
            # 4. Delete QR codes
            self._execute_query("DELETE FROM qr_codes WHERE user_id = %s", (user_id,))
            qr_deleted = self.cursor.rowcount
            
            # 5. Finally delete the user
            self._execute_query("DELETE FROM users WHERE id = %s", (user_id,))
            user_deleted = self.cursor.rowcount
            
            if user_deleted == 0:
                self.connection.rollback()
                return False, "Failed to delete user"
            
            # Commit transaction
            self.connection.commit()
            
            logger.info(f"User {user_id} completely deleted: {packages_deleted} packages, {qr_deleted} QR codes, {logs_deleted} logs")
            
            return True, f"User '{user['first_name']} {user['last_name']}' and all associated data deleted successfully"
            
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            logger.error(f"Error deleting user {user_id}: {err}")
            return False, f"Database error: {str(err)}"

    def add_user_packages(self, user_id: int, package_type: str, quantity: int) -> bool:
        """Add packages to user inventory"""
        try:
            for _ in range(quantity):
                self._execute_query("""
                    INSERT INTO user_packages (user_id, package_type, status)
                    VALUES (%s, %s, 'available')
                """, (user_id, package_type))
            
            self.connection.commit()
            logger.info(f"Added {quantity} {package_type} packages for user {user_id}")
            return True
            
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            logger.error(f"Failed to add packages: {err}")
            raise

    def remove_user_packages(self, user_id: int, quantity: int) -> Tuple[bool, str]:
        """Remove available packages from user inventory"""
        try:
            # Get available packages count
            result = self._execute_query("""
                SELECT COUNT(*) as available_count
                FROM user_packages 
                WHERE user_id = %s AND status = 'available'
            """, (user_id,), fetch_type='one')
            
            available_count = result['available_count'] if result else 0
            
            # Check if we have enough available packages to remove
            if available_count < quantity:
                return False, f"Cannot remove {quantity} packages. Only {available_count} available packages found."
            
            # Get the package IDs to remove (only available ones)
            packages_to_remove = self._execute_query("""
                SELECT id FROM user_packages 
                WHERE user_id = %s AND status = 'available'
                ORDER BY id DESC
                LIMIT %s
            """, (user_id, quantity), fetch_type='all')
            
            if len(packages_to_remove) != quantity:
                return False, f"Could not find {quantity} available packages to remove."
            
            # Delete the packages
            package_ids = [pkg['id'] for pkg in packages_to_remove]
            format_strings = ','.join(['%s'] * len(package_ids))
            
            self._execute_query(f"""
                DELETE FROM user_packages 
                WHERE id IN ({format_strings})
            """, package_ids)
            
            self.connection.commit()
            
            logger.info(f"Removed {quantity} packages for user {user_id}")
            return True, f"Successfully removed {quantity} packages"
            
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            logger.error(f"Failed to remove packages: {err}")
            return False, f"Database error: {str(err)}"

    def get_user_packages(self, user_id: int) -> List[Dict]:
        """Get all packages for a user"""
        try:
            return self._execute_query("""
                SELECT id, package_type, status, last_activity_time
                FROM user_packages
                WHERE user_id = %s
                ORDER BY package_type, status
            """, (user_id,), fetch_type='all') or []
            
        except Exception as err:
            logger.error(f"Error getting packages: {err}")
            return []

    def get_user_package_summary(self, user_id: int) -> Dict:
        """Get package summary for user - always returns valid structure"""
        try:
            # Get total packages
            result = self._execute_query("""
                SELECT COUNT(*) as total,
                    SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) as available,
                    SUM(CASE WHEN status = 'rented_out' THEN 1 ELSE 0 END) as rented
                FROM user_packages WHERE user_id = %s
            """, (user_id,), fetch_type='one')
            
            # Ensure we have valid numbers
            total = int(result['total'] or 0) if result else 0
            available = int(result['available'] or 0) if result else 0
            rented = int(result['rented'] or 0) if result else 0
            
            return {
                'total_packages': total,
                'available_packages': available,
                'rented_packages': rented,
                'has_packages': total > 0,
                'all_returned': rented == 0
            }
            
        except Exception as err:
            logger.error(f"Error getting package summary for user {user_id}: {err}")
            # Return safe defaults on error
            return {
                'total_packages': 0,
                'available_packages': 0,
                'rented_packages': 0,
                'has_packages': False,
                'all_returned': True
            }

    def update_package_status(self, package_id: int, new_status: str) -> bool:
        """Update single package status"""
        try:
            self._execute_query("""
                UPDATE user_packages
                SET status = %s, last_activity_time = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (new_status, package_id))
            
            self.connection.commit()
            return True
            
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            logger.error(f"Failed to update package {package_id}: {err}")
            return False

    def update_rental_status_new(self, user_id: int, action: str) -> Tuple[bool, str]:
        """Handle package checkout/checkin actions"""
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
            packages = self._execute_query("""
                SELECT id FROM user_packages 
                WHERE user_id = %s AND status = 'available'
                LIMIT %s
            """, (user_id, count), fetch_type='all')
            
            if not packages:
                return False, "No available packages"
            
            # Update packages to rented
            package_ids = [p['id'] for p in packages]
            format_strings = ','.join(['%s'] * len(package_ids))
            
            self._execute_query(f"""
                UPDATE user_packages 
                SET status = 'rented_out', last_activity_time = CURRENT_TIMESTAMP
                WHERE id IN ({format_strings})
            """, package_ids)
            
            # Update user status
            self._execute_query("""
                UPDATE users SET rental_status = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (user_id,))
            
            self.connection.commit()
            return True, f"Checked out {len(packages)} packages"
            
        except Exception as err:
            if self.connection:
                self.connection.rollback()
            logger.error(f"Checkout error: {err}")
            return False, str(err)

    def _checkin_packages(self, user_id: int, count: int) -> Tuple[bool, str]:
        """Check in packages for user"""
        if count <= 0:
            return False, "Invalid package count"
            
        try:
            # Get rented packages
            packages = self._execute_query("""
                SELECT id FROM user_packages 
                WHERE user_id = %s AND status = 'rented_out'
                LIMIT %s
            """, (user_id, count), fetch_type='all')
            
            if not packages:
                return False, "No rented packages"
            
            # Update packages to available
            package_ids = [p['id'] for p in packages]
            format_strings = ','.join(['%s'] * len(package_ids))
            
            self._execute_query(f"""
                UPDATE user_packages 
                SET status = 'available', last_activity_time = CURRENT_TIMESTAMP
                WHERE id IN ({format_strings})
            """, package_ids)
            
            self.connection.commit()
            
            # Check if all returned
            summary = self.get_user_package_summary(user_id)
            
            if summary['all_returned']:
                # Update user status to returned
                self._execute_query("""
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
            if self.connection:
                self.connection.rollback()
            logger.error(f"Checkin error: {err}")
            return False, str(err)

    def _send_thank_you_email(self, user_id: int):
        """Send thank you email when all packages returned"""
        try:
            user = self._execute_query("""
                SELECT first_name, last_name, email, city, package_type
                FROM users WHERE id = %s
            """, (user_id,), fetch_type='one')
            
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
                qr = self._execute_query("""
                    SELECT id FROM qr_codes WHERE user_id = %s AND is_active = TRUE LIMIT 1
                """, (user_id,), fetch_type='one')
                
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