/**
 * QR Code Scanner JavaScript - Compatible with multiple html5-qrcode versions
 * Handles QR code scanning, manual entry, and API communication
 */

// Global variable to store the scanner instance
let html5QrcodeScanner = null;

/**
 * Display error messages to the user
 * @param {string} message - Error message to display
 */
function showError(message) {
    const errorMessage = document.getElementById('error-message');
    const errorText = document.getElementById('error-text');
    
    if (errorMessage && errorText) {
        errorText.textContent = message;
        errorMessage.classList.remove('hidden');
    } else if (errorMessage) {
        errorMessage.textContent = message;
        errorMessage.classList.remove('hidden');
    }
    
    console.error('QR Scanner Error:', message);
}

/**
 * Display status messages to the user
 * @param {string} message - Status message to display
 */
function showStatus(message) {
    const statusMessage = document.getElementById('status-message');
    const statusText = document.getElementById('status-text');
    
    if (statusMessage && statusText) {
        statusText.textContent = message;
        statusMessage.classList.remove('hidden');
        // Hide error message when showing status
        const errorMessage = document.getElementById('error-message');
        if (errorMessage) {
            errorMessage.classList.add('hidden');
        }
    } else if (statusMessage) {
        statusMessage.textContent = message;
        statusMessage.classList.remove('hidden');
    }
    
    console.info('QR Scanner Status:', message);
}

/**
 * Show or hide loading indicator
 * @param {boolean} show - Whether to show loading indicator
 */
function showLoading(show) {
    const loadingIndicator = document.getElementById('loadingIndicator');
    if (loadingIndicator) {
        loadingIndicator.classList.toggle('hidden', !show);
    }
}

/**
 * Convert rental status number to human-readable text
 * @param {number} status - Rental status code
 * @returns {string} Human-readable status
 */
function getStatusDisplay(status) {
    switch (parseInt(status)) {
        case 0:
            return 'Not Active';
        case 1:
            return 'Active Rental';
        case 2:
            return 'Returned';
        default:
            return 'Unknown Status';
    }
}

/**
 * Handle QR code scanning result
 * Makes API call to verify QR code and redirects to user details
 * @param {string} decodedText - The scanned QR code text
 */
async function handleQRCode(decodedText) {
    console.log('Processing QR Code:', decodedText);
    showStatus('QR Code found! Processing...');
    
    try {
        // Make API call to verify QR code using the correct endpoint and format
        const response = await fetch('/api/verify-qr', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({ qr_code: decodedText })
        });

        // Check if response is ok
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP ${response.status}: ${errorText}`);
        }

        // Parse JSON response
        const data = await response.json();
        
        if (data.success) {
            // Show quick status before redirecting
            const status = getStatusDisplay(data.user.rental_status);
            showStatus(`Found: ${data.user.first_name} ${data.user.last_name} (${status})`);
            
            // Small delay to show status, then redirect
            setTimeout(() => {
                window.location.href = `/lookup?qr_code=${encodeURIComponent(decodedText)}`;
            }, 1000);
        } else {
            // Handle API error response
            showError(data.error || 'Invalid QR code');
            // Resume scanning after error
            resumeScanner();
        }
    } catch (error) {
        console.error('API error:', error);
        
        // Provide more specific error messages
        let errorMessage = 'Error processing QR code';
        if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
            errorMessage = 'Network error - check your connection';
        } else if (error.message.includes('HTTP 404')) {
            errorMessage = 'Invalid QR code - not found in system';
        } else if (error.message.includes('HTTP 500')) {
            errorMessage = 'Server error - please try again';
        } else if (error.message.includes('HTTP 403') || error.message.includes('HTTP 401')) {
            errorMessage = 'Access denied - please log in again';
        }
        
        showError(errorMessage);
        
        // Resume scanning after error
        resumeScanner();
    }
}

/**
 * Resume scanning after processing or error
 */
function resumeScanner() {
    if (html5QrcodeScanner) {
        setTimeout(() => {
            try {
                html5QrcodeScanner.resume();
                showStatus('Scanner ready - point at QR code');
            } catch (error) {
                console.warn('Could not resume scanner:', error);
            }
        }, 2000);
    }
}

/**
 * Create manual QR code entry form
 * Allows users to manually enter QR codes if camera scanning fails
 */
function createManualEntryForm() {
    const manualEntry = document.getElementById('manualEntry');
    if (!manualEntry) {
        console.warn('Manual entry container not found');
        return;
    }

    // Check if form already exists
    const existingForm = manualEntry.querySelector('form');
    if (existingForm) {
        return; // Form already created
    }

    // Create form element with input and submit button
    const form = document.createElement('form');
    form.innerHTML = `
        <div class="flex gap-2">
            <input type="text" 
                   name="qr_code" 
                   placeholder="Enter 4-digit QR code" 
                   class="flex-1 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                   maxlength="10"
                   pattern="[0-9]*"
                   inputmode="numeric"
                   title="Enter 4-digit QR code number">
            <button type="submit" 
                    class="px-6 py-2 bg-blue-500 text-white rounded-md hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500 whitespace-nowrap">
                Look Up
            </button>
        </div>
        <p class="text-sm text-gray-600 mt-2">Enter the 4-digit code if camera scanning isn't working</p>
    `;

    // Handle form submission
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const input = this.querySelector('input[name="qr_code"]');
        const qrCode = input.value.trim();
        
        // Validate input
        if (!qrCode) {
            showError('Please enter a QR code');
            input.focus();
            return;
        }
        
        if (!/^\d{1,10}$/.test(qrCode)) {
            showError('QR code should contain only numbers');
            input.focus();
            return;
        }
        
        // Process the manually entered code
        handleQRCode(qrCode);
        input.value = ''; // Clear the input
    });

    // Add form to the container
    manualEntry.appendChild(form);
}

/**
 * Get scanner configuration based on library version and capabilities
 * @returns {Object} Configuration object for the scanner
 */
function getScannerConfig() {
    // Basic configuration that works with most versions
    const config = {
        fps: 10,                        // Frames per second for scanning
        qrbox: { width: 250, height: 250 }, // Size of scanning box
        aspectRatio: 1.0,               // Square aspect ratio
        disableFlip: false,             // Allow flipping camera if needed
    };
    
    // Try to detect library version and add compatible features
    try {
        // Check if rememberLastUsedCamera is supported (usually in newer versions)
        config.rememberLastUsedCamera = true;
        
        // Check if verbose logging is supported
        config.verbose = false;
        
    } catch (error) {
        console.warn('Some scanner config options not supported:', error);
    }
    
    return config;
}

/**
 * Initialize the QR code scanner
 * Sets up camera permissions and starts scanning
 */
async function initializeScanner() {
    try {
        showLoading(true);
        showStatus('Initializing camera...');

        // Check if HTML5 QR Scanner library is loaded
        if (typeof Html5QrcodeScanner === 'undefined') {
            throw new Error('QR Scanner library not loaded. Please refresh the page.');
        }

        console.log('Html5QrcodeScanner available, initializing...');

        // Get scanner configuration
        const config = getScannerConfig();
        console.log('Scanner config:', config);

        // Create scanner instance with error handling
        try {
            html5QrcodeScanner = new Html5QrcodeScanner("reader", config, false);
        } catch (constructorError) {
            console.error('Scanner constructor error:', constructorError);
            throw new Error(`Scanner initialization failed: ${constructorError.message}`);
        }

        // Define success callback - called when QR code is successfully scanned
        const onScanSuccess = (decodedText, decodedResult) => {
            console.log('QR Code scanned successfully:', decodedText);
            
            // Pause scanning to prevent multiple scans
            try {
                html5QrcodeScanner.pause(true);
            } catch (pauseError) {
                console.warn('Could not pause scanner:', pauseError);
            }
            
            // Process the scanned code
            handleQRCode(decodedText);
        };

        // Define error callback - called on scan failures (usually ignorable)
        const onScanFailure = (error) => {
            // Most scan failures are normal (no QR code in view, bad lighting, etc.)
            // Only log them in debug mode to avoid console spam
            console.debug('QR scan attempt failed (normal):', error);
        };

        // Start the scanner
        console.log('Starting scanner render...');
        await html5QrcodeScanner.render(onScanSuccess, onScanFailure);

        // Update UI to show scanner is ready
        showStatus('Scanner ready - point camera at QR code');
        showLoading(false);
        
        // Create manual entry form as fallback option
        createManualEntryForm();

        console.log('QR Scanner initialized successfully');

    } catch (error) {
        console.error('Scanner initialization error:', error);
        
        // Provide helpful error messages based on error type
        let errorMessage = `Failed to start scanner: ${error.message}`;
        
        if (error.name === 'NotAllowedError' || error.message.includes('Permission denied')) {
            errorMessage = 'Camera access denied. Please allow camera permissions and refresh the page.';
        } else if (error.name === 'NotFoundError' || error.message.includes('No camera found')) {
            errorMessage = 'No camera found. Please connect a camera and refresh the page.';
        } else if (error.name === 'NotSupportedError' || error.message.includes('not supported')) {
            errorMessage = 'Camera not supported by your browser. Try using Chrome, Firefox, or Safari.';
        } else if (error.message.includes('library not loaded')) {
            errorMessage = 'Scanner library failed to load. Please check your internet connection and refresh the page.';
        }
        
        showError(errorMessage);
        showLoading(false);
        
        // Still create manual entry form as fallback
        createManualEntryForm();
    }
}

/**
 * Cleanup function to properly stop scanner
 */
function cleanupScanner() {
    if (html5QrcodeScanner) {
        try {
            html5QrcodeScanner.clear().then(() => {
                console.log('QR Scanner cleaned up successfully');
            }).catch((error) => {
                console.warn('Error cleaning up scanner:', error);
            });
        } catch (error) {
            console.warn('Error during scanner cleanup:', error);
        }
        html5QrcodeScanner = null;
    }
}

// Event Listeners

/**
 * Initialize scanner when DOM is fully loaded
 */
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded - starting scanner initialization...');
    
    // Small delay to ensure all resources are loaded
    setTimeout(() => {
        initializeScanner();
    }, 500);
});

/**
 * Cleanup scanner when page is being unloaded
 */
window.addEventListener('beforeunload', () => {
    console.log('Page unloading - cleaning up scanner...');
    cleanupScanner();
});

/**
 * Handle visibility changes (tab switching, minimizing)
 * Pause/resume scanner to save battery and resources
 */
document.addEventListener('visibilitychange', () => {
    if (html5QrcodeScanner) {
        if (document.hidden) {
            // Page is hidden, pause scanner
            console.log('Page hidden - pausing scanner');
            try {
                html5QrcodeScanner.pause(true);
            } catch (error) {
                console.warn('Error pausing scanner:', error);
            }
        } else {
            // Page is visible, resume scanner
            console.log('Page visible - resuming scanner');
            setTimeout(() => {
                try {
                    html5QrcodeScanner.resume();
                    showStatus('Scanner resumed - point at QR code');
                } catch (error) {
                    console.warn('Error resuming scanner:', error);
                }
            }, 500);
        }
    }
});

// Export functions for debugging (only in development)
if (typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname.includes('127.0.0.1'))) {
    window.qrScannerDebug = {
        handleQRCode,
        initializeScanner,
        cleanupScanner,
        resumeScanner,
        scanner: () => html5QrcodeScanner
    };
}