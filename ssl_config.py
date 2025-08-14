"""
SSL Configuration for QR Rental System

This module contains SSL certificate paths and server configuration
for HTTPS connections required by the application.
"""
import os

class SSLConfig:
    # SSL Certificate Configuration
    # These paths point to your SSL certificate files
    SSL_CERTIFICATE = '/var/www/qr-rental-system/ssl/cert.pem'
    SSL_KEY = '/var/www/qr-rental-system/ssl/key.pem'
    
    # Server Configuration
    # HOST: IP address to bind to (0.0.0.0 means all interfaces)
    # PORT: Port number for HTTPS connections
    HOST = '0.0.0.0'
    PORT = 5000