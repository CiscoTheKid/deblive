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
        """
        Get current database statistics including package inventory count
        
        Returns:
            dict: Database statistics including user count, QR codes, active rentals, and total packages
        """
        try:
            stats = {
                'total_users': 0,
                'total_qr_codes': 0,
                'active_rentals': 0,
                'total_packages': 0,          # NEW - total packages in inventory
                'available_packages': 0,      # NEW - available packages
                'rented_packages': 0          # NEW - rented out packages
            }
            
            # Get total users
            self.cursor.execute("SELECT COUNT(*) as count FROM users")
            stats['total_users'] = self.cursor.fetchone()['count']
            
            # Get total QR codes
            self.cursor.execute("SELECT COUNT(*) as count FROM qr_codes")
            stats['total_qr_codes'] = self.cursor.fetchone()['count']
            
            # Get active rentals (users with status = 1)
            self.cursor.execute("SELECT COUNT(*) as count FROM users WHERE rental_status = 1")
            stats['active_rentals'] = self.cursor.fetchone()['count']
            
            # Get package inventory statistics (NEW)
            try:
                # Total packages in inventory
                self.cursor.execute("SELECT COUNT(*) as count FROM user_packages")
                stats['total_packages'] = self.cursor.fetchone()['count']
                
                # Available packages
                self.cursor.execute("SELECT COUNT(*) as count FROM user_packages WHERE status = 'available'")
                stats['available_packages'] = self.cursor.fetchone()['count']
                
                # Rented out packages
                self.cursor.execute("SELECT COUNT(*) as count FROM user_packages WHERE status = 'rented_out'")
                stats['rented_packages'] = self.cursor.fetchone()['count']
                
            except mysql.connector.Error as pkg_error:
                # If user_packages table doesn't exist (older schema), set package stats to 0
                logger.warning(f"Could not get package statistics: {pkg_error}")
                stats['total_packages'] = 0
                stats['available_packages'] = 0
                stats['rented_packages'] = 0
            
            return stats
            
        except mysql.connector.Error as err:
            logger.error(f"Error getting database stats: {err}")
            raise Exception(f"Database error: {err}")

    def reset_database(self):
        """
        Reset the database by dropping all client data including package inventory
        
        This method truncates all tables that contain user and rental data,
        including the new user_packages table for inventory management.
        """
        try:
            # Disable foreign key checks temporarily to avoid constraint errors
            self.cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            
            # List of tables to truncate in order
            # Note: user_packages is included to clear package inventory
            # Order matters due to foreign key relationships, even with checks disabled
            tables = [
                'email_logs',      # References users and qr_codes
                'rentals',         # References users and qr_codes  
                'user_packages',   # References users (NEW - for package inventory)
                'qr_codes',        # References users
                'users'            # Base user table
            ]
            
            # Truncate all tables
            for table in tables:
                try:
                    self.cursor.execute(f"TRUNCATE TABLE {table}")
                    logger.info(f"Successfully truncated table: {table}")
                except mysql.connector.Error as table_error:
                    # Log warning but continue - table might not exist in older schemas
                    logger.warning(f"Could not truncate table {table}: {table_error}")
            
            # Re-enable foreign key checks
            self.cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            
            # Commit the changes
            self.connection.commit()
            logger.info("Database reset completed successfully - all user data and package inventory cleared")
            
        except mysql.connector.Error as err:
            # Rollback on error
            self.connection.rollback()
            logger.error(f"Error resetting database: {err}")
            raise Exception(f"Database reset failed: {err}")
        finally:
            # Ensure foreign key checks are always re-enabled
            try:
                self.cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            except:
                pass  # If connection is lost, this will fail but that's okay

    def update_rental_status(self, user_id: int, status: int) -> bool:
        """
        DEPRECATED: Use update_rental_status_new() instead
        Legacy method for backward compatibility
        """
        logger.warning("update_rental_status() is deprecated. Use update_rental_status_new() instead.")
        
        try:
            if status == 1:  # Checkout
                success, _ = self.update_rental_status_new(user_id, 'checkout_all')
                return success
            elif status == 2:  # Check in
                success, _ = self.update_rental_status_new(user_id, 'checkin_all')
                return success
            elif status == 0:  # Reset - set all packages to available
                self.cursor.execute("""
                    UPDATE user_packages 
                    SET status = 'available',
                        last_activity_time = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                """, (user_id,))
                
                self.cursor.execute("""
                    UPDATE users 
                    SET rental_status = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (user_id,))
                
                self.connection.commit()
                return True
            else:
                return False
                
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Error in legacy update_rental_status: {err}")
            return False

    
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

    def add_user_packages(self, user_id: int, package_type: str, quantity: int) -> bool:
        """
        Adds multiple package records for a given user.

        Args:
            user_id (int): The ID of the user purchasing the packages.
            package_type (str): The type of package being purchased.
            quantity (int): The number of packages to add.
            
        Returns:
            bool: True if packages were added successfully.
        """
        try:
            # We loop 'quantity' times to insert a separate row for each package
            for _ in range(quantity):
                self.cursor.execute("""
                    INSERT INTO user_packages (user_id, package_type, status)
                    VALUES (%s, %s, 'available')
                """, (user_id, package_type))
            
            self.connection.commit()
            logger.info(f"Added {quantity} of '{package_type}' packages for user_id {user_id}.")
            return True

        except mysql.connector.Error as err:
            self.connection.rollback()
            logger.error(f"Failed to add packages for user {user_id}: {err}")
            raise Exception(f"Database error: {err}")
    
    def get_user_packages(self, user_id: int) -> list:
        """
        Retrieves all packages owned by a specific user.

        Args:
            user_id (int): The user's ID.
            
        Returns:
            list: A list of dictionaries, where each dictionary is a package.
        """
        try:
            self.cursor.execute("""
                SELECT id, package_type, status, last_activity_time
                FROM user_packages
                WHERE user_id = %s
                ORDER BY package_type, status
            """, (user_id,))
            
            packages = self.cursor.fetchall()
            return packages if packages else []

        except mysql.connector.Error as err:
            logger.error(f"Failed to get packages for user {user_id}: {err}")
            raise Exception(f"Database error: {err}")
    
    def get_user_package_summary(self, user_id: int) -> Dict:
        """
        Get a summary of user's package inventory and rental status
        FIXED VERSION - More accurate state tracking
        
        Args:
            user_id (int): User ID to check
            
        Returns:
            dict: Summary containing package counts and status information
        """
        try:
            # Get package counts by status with more detailed information
            self.cursor.execute("""
                SELECT 
                    status,
                    COUNT(*) as count,
                    package_type
                FROM user_packages 
                WHERE user_id = %s 
                GROUP BY status, package_type
            """, (user_id,))
            
            package_counts = self.cursor.fetchall()
            
            # Also get total count for verification
            self.cursor.execute("""
                SELECT COUNT(*) as total_count
                FROM user_packages 
                WHERE user_id = %s
            """, (user_id,))
            
            total_count_result = self.cursor.fetchone()
            total_from_query = total_count_result['total_count'] if total_count_result else 0
            
            # Initialize summary
            summary = {
                'total_packages': total_from_query,
                'available_packages': 0,
                'rented_packages': 0,
                'package_types': {},
                'all_returned': True,
                'has_packages': total_from_query > 0
            }
            
            # Process the counts
            running_total = 0
            for row in package_counts:
                status = row['status']
                count = row['count']
                package_type = row['package_type']
                
                running_total += count
                
                if status == 'available':
                    summary['available_packages'] += count
                elif status == 'rented_out':
                    summary['rented_packages'] += count
                    summary['all_returned'] = False
                
                # Track by package type
                if package_type not in summary['package_types']:
                    summary['package_types'][package_type] = {'available': 0, 'rented_out': 0}
                summary['package_types'][package_type][status] = count
            
            # Verify totals match
            calculated_total = summary['available_packages'] + summary['rented_packages']
            if calculated_total != total_from_query:
                logger.warning(f"Package count mismatch for user {user_id}: calculated={calculated_total}, actual={total_from_query}")
            
            # Final determination of all_returned status
            if summary['has_packages']:
                summary['all_returned'] = (summary['rented_packages'] == 0)
            else:
                summary['all_returned'] = True
            
            logger.debug(f"Package summary for user {user_id}: {summary}")
            return summary
            
        except mysql.connector.Error as err:
            logger.error(f"Error getting package summary for user {user_id}: {err}")
            raise Exception(f"Database error: {err}")

    def checkout_user_packages(self, user_id: int, package_count: int = None) -> Tuple[bool, str, int]:
        """
        Check out packages for a user (change from 'available' to 'rented_out')
        FIXED VERSION - Proper database commits and verification
        
        Args:
            user_id (int): User ID
            package_count (int): Number of packages to check out (None = all available)
            
        Returns:
            tuple: (success, message, packages_checked_out)
        """
        try:
            logger.info(f"Starting check-out process for user {user_id}, package_count: {package_count}")
            
            # Get available packages for this user
            self.cursor.execute("""
                SELECT id, package_type 
                FROM user_packages 
                WHERE user_id = %s AND status = 'available'
                ORDER BY package_type, id
            """, (user_id,))
            
            available_packages = self.cursor.fetchall()
            logger.debug(f"Found {len(available_packages)} available packages for user {user_id}")
            
            if not available_packages:
                return False, "No available packages to check out", 0
            
            # Determine how many to check out
            if package_count is None:
                package_count = len(available_packages)
            else:
                package_count = min(package_count, len(available_packages))
            
            if package_count <= 0:
                return False, "Invalid package count", 0
            
            # Check out the specified number of packages
            packages_to_checkout = available_packages[:package_count]
            package_ids = [pkg['id'] for pkg in packages_to_checkout]
            
            logger.info(f"Checking out {package_count} packages for user {user_id}: {package_ids}")
            
            # Update packages to rented_out status
            format_strings = ','.join(['%s'] * len(package_ids))
            self.cursor.execute(f"""
                UPDATE user_packages 
                SET status = 'rented_out', 
                    last_activity_time = CURRENT_TIMESTAMP 
                WHERE id IN ({format_strings})
            """, package_ids)
            
            # Update user's overall rental status to active (1)
            self.cursor.execute("""
                UPDATE users 
                SET rental_status = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (user_id,))
            
            # Log the rental activity
            self.cursor.execute("""
                SELECT id FROM qr_codes 
                WHERE user_id = %s AND is_active = TRUE
                LIMIT 1
            """, (user_id,))
            qr_code = self.cursor.fetchone()
            
            if qr_code:
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
            
            # Commit all changes
            self.connection.commit()
            logger.debug(f"Committed all checkout updates for user {user_id}")
            
            success_msg = f"Successfully checked out {package_count} packages"
            logger.info(f"Checkout completed for user {user_id}: {success_msg}")
            return True, success_msg, package_count
            
        except mysql.connector.Error as err:
            self.connection.rollback()
            logger.error(f"Database error checking out packages for user {user_id}: {err}")
            raise Exception(f"Database error: {err}")
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Unexpected error checking out packages for user {user_id}: {err}")
            raise Exception(f"Unexpected error: {err}")

    def checkin_user_packages(self, user_id: int, package_count: int = None) -> Tuple[bool, str, int, bool]:
        """
        Check in packages for a user (change from 'rented_out' to 'available')
        FIXED VERSION - Sends email when ALL packages are available, regardless of previous state
        
        Args:
            user_id (int): User ID  
            package_count (int): Number of packages to check in (None = all rented)
            
        Returns:
            tuple: (success, message, packages_checked_in, all_returned)
        """
        try:
            logger.info(f"Starting check-in process for user {user_id}, package_count: {package_count}")
            
            # FIRST: Check if user has any packages at all
            self.cursor.execute("""
                SELECT COUNT(*) as total_packages
                FROM user_packages 
                WHERE user_id = %s
            """, (user_id,))
            
            total_packages_result = self.cursor.fetchone()
            total_packages = total_packages_result['total_packages'] if total_packages_result else 0
            
            if total_packages == 0:
                return False, "User has no packages in inventory", 0, True
            
            # Get currently rented packages for this user
            self.cursor.execute("""
                SELECT id, package_type 
                FROM user_packages 
                WHERE user_id = %s AND status = 'rented_out'
                ORDER BY package_type, id
            """, (user_id,))
            
            rented_packages = self.cursor.fetchall()
            logger.debug(f"Found {len(rented_packages)} rented packages for user {user_id}")
            
            if not rented_packages:
                return False, "No rented packages to check in", 0, True
            
            # Determine how many to check in
            if package_count is None:
                package_count = len(rented_packages)
            else:
                package_count = min(package_count, len(rented_packages))
            
            if package_count <= 0:
                return False, "Invalid package count", 0, False
            
            # Check in the specified number of packages
            packages_to_checkin = rented_packages[:package_count]
            package_ids = [pkg['id'] for pkg in packages_to_checkin]
            
            logger.info(f"Checking in {package_count} packages for user {user_id}: {package_ids}")
            
            # Update packages to available status
            format_strings = ','.join(['%s'] * len(package_ids))
            self.cursor.execute(f"""
                UPDATE user_packages 
                SET status = 'available', 
                    last_activity_time = CURRENT_TIMESTAMP 
                WHERE id IN ({format_strings})
            """, package_ids)
            
            # CRITICAL: Commit the package updates first
            self.connection.commit()
            logger.debug(f"Committed package status updates for user {user_id}")
            
            # NOW verify the actual state after the update
            self.cursor.execute("""
                SELECT COUNT(*) as rented_count
                FROM user_packages 
                WHERE user_id = %s AND status = 'rented_out'
            """, (user_id,))
            
            actual_rented_remaining = self.cursor.fetchone()['rented_count']
            all_returned = (actual_rented_remaining == 0)
            
            logger.info(f"After check-in for user {user_id}: {actual_rented_remaining} packages still rented out, all_returned: {all_returned}")
            
            # Update user's overall rental status
            if all_returned:
                # ALL packages are now available - set status to 2 (returned)
                logger.info(f"All packages available for user {user_id} - updating status to returned (2)")
                
                self.cursor.execute("""
                    UPDATE users 
                    SET rental_status = 2,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (user_id,))
                
                # Update rental record to returned
                self.cursor.execute("""
                    UPDATE rentals
                    SET status = 'returned',
                        return_time = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                    AND status = 'checked_out'
                """, (user_id,))
                
                # Commit user status updates
                self.connection.commit()
                logger.debug(f"Committed user status updates for user {user_id}")
                
            else:
                # Still have packages rented out - keep status as active (1)
                logger.info(f"User {user_id} still has {actual_rented_remaining} packages rented out - keeping status active")
                
                self.cursor.execute("""
                    UPDATE users 
                    SET rental_status = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (user_id,))
                
                # Commit the user status update
                self.connection.commit()
            
            # 🔥 FIXED: Send email when ALL packages are available, regardless of previous rental state
            if all_returned and total_packages > 0:
                try:
                    logger.info(f"ALL {total_packages} packages are now available for user {user_id} - sending thank you email")
                    
                    # Get user details for email
                    self.cursor.execute("""
                        SELECT first_name, last_name, email, city, package_type
                        FROM users
                        WHERE id = %s
                    """, (user_id,))
                    user = self.cursor.fetchone()
                    
                    if user:
                        # Import here to avoid circular imports
                        from rental_email_handler import RentalEmailHandler
                        
                        email_handler = RentalEmailHandler(
                            os.getenv('GMAIL_ADDRESS'),
                            os.getenv('GMAIL_APP_PASSWORD')
                        )
                        
                        # Send thank you email with proper user details
                        success, message = email_handler.send_thank_you_email(
                            user['email'],
                            user['first_name'],
                            user['last_name'],
                            user.get('city'),
                            user.get('package_type')
                        )
                        
                        if success:
                            logger.info(f"✅ Successfully sent thank you email to user {user_id} ({user['email']}) - ALL {total_packages} packages returned")
                        else:
                            logger.error(f"❌ Failed to send thank you email to user {user_id}: {message}")
                        
                        # Log the email attempt - get QR code ID
                        self.cursor.execute("""
                            SELECT id FROM qr_codes 
                            WHERE user_id = %s AND is_active = TRUE
                            LIMIT 1
                        """, (user_id,))
                        qr_code = self.cursor.fetchone()
                        
                        qr_code_id = qr_code['id'] if qr_code else None
                        self.log_email(
                            user_id,
                            qr_code_id,
                            'success_thank_you' if success else 'failed_thank_you',
                            None if success else message
                        )
                        
                        # Final commit for email log
                        self.connection.commit()
                        
                    else:
                        logger.error(f"Could not find user details for user {user_id} to send thank you email")
                        
                except Exception as email_error:
                    logger.error(f"Exception occurred while sending thank you email for user {user_id}: {email_error}")
                    # Don't fail the check-in process due to email errors
            
            success_msg = f"Successfully checked in {package_count} packages"
            if all_returned:
                success_msg += f" - All {total_packages} packages are now available! Thank you email sent."
            else:
                success_msg += f" - {actual_rented_remaining} packages still checked out"
                
            logger.info(f"Check-in completed for user {user_id}: {success_msg}")
            return True, success_msg, package_count, all_returned
            
        except mysql.connector.Error as err:
            self.connection.rollback()
            logger.error(f"Database error checking in packages for user {user_id}: {err}")
            raise Exception(f"Database error: {err}")
        except Exception as err:
            self.connection.rollback()
            logger.error(f"Unexpected error checking in packages for user {user_id}: {err}")
            raise Exception(f"Unexpected error: {err}")

    def update_rental_status_new(self, user_id: int, action: str) -> Tuple[bool, str]:
        """
        New rental status update method that works with individual packages
        FIXED VERSION - Proper error handling and logging
        
        Args:
            user_id (int): User ID
            action (str): 'checkout_all', 'checkin_all', 'checkout_one', 'checkin_one'
            
        Returns:
            tuple: (success, message)
        """
        try:
            logger.info(f"Performing action '{action}' for user {user_id}")
            
            # Get current package summary
            summary = self.get_user_package_summary(user_id)
            
            if not summary['has_packages']:
                return False, "User has no packages in inventory"
            
            if action == 'checkout_all':
                if summary['available_packages'] == 0:
                    return False, "No packages available to check out"
                success, message, count = self.checkout_user_packages(user_id)
                return success, message
                
            elif action == 'checkin_all':
                if summary['rented_packages'] == 0:
                    return False, "No packages currently rented"
                success, message, count, all_returned = self.checkin_user_packages(user_id)
                return success, message
                
            elif action == 'checkout_one':
                if summary['available_packages'] == 0:
                    return False, "No packages available to check out"
                success, message, count = self.checkout_user_packages(user_id, 1)
                return success, message
                
            elif action == 'checkin_one':
                if summary['rented_packages'] == 0:
                    return False, "No packages currently rented"
                success, message, count, all_returned = self.checkin_user_packages(user_id, 1)
                return success, message
                
            else:
                return False, f"Invalid action: {action}"
                
        except Exception as err:
            logger.error(f"Error in update_rental_status_new for user {user_id}, action {action}: {err}")
            return False, f"Error: {str(err)}"
        
    def update_package_status(self, package_id: int, new_status: str) -> bool:
        """
        Updates the status of a single package item and its activity time.

        Args:
            package_id (int): The unique ID of the package from the user_packages table.
            new_status (str): The new status ('available' or 'rented_out').
            
        Returns:
            bool: True if the update was successful.
        """
        if new_status not in ['available', 'rented_out']:
            raise ValueError("Invalid status. Must be 'available' or 'rented_out'.")

        try:
            self.cursor.execute("""
                UPDATE user_packages
                SET status = %s,
                    last_activity_time = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (new_status, package_id))
            
            self.connection.commit()
            logger.info(f"Updated package {package_id} to status '{new_status}'.")
            return True

        except mysql.connector.Error as err:
            self.connection.rollback()
            logger.error(f"Failed to update status for package {package_id}: {err}")
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