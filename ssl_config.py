"""SSL Configuration for QR Rental System"""

class SSLConfig:
    """SSL and server configuration settings"""
    
    # SSL Certificate paths
    SSL_CERTIFICATE = '/var/www/qr-rental-system/ssl/cert.pem'
    SSL_KEY = '/var/www/qr-rental-system/ssl/key.pem'
    
    # Server configuration
    HOST = '0.0.0.0'  # Listen on all interfaces
    PORT = 5000       # HTTPS port