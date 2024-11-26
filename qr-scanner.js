let html5QrcodeScanner = null;

// Helper functions
function showError(message) {
    const errorMessage = document.getElementById('error-message');
    if (errorMessage) {
        errorMessage.textContent = message;
        errorMessage.classList.toggle('hidden', !message);
    }
}

function showStatus(message) {
    const statusMessage = document.getElementById('status-message');
    if (statusMessage) {
        statusMessage.textContent = message;
        statusMessage.classList.toggle('hidden', !message);
    }
}

function showLoading(show) {
    const loadingIndicator = document.getElementById('loadingIndicator');
    if (loadingIndicator) {
        loadingIndicator.classList.toggle('hidden', !show);
    }
}
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
async function handleQRCode(decodedText) {
    showStatus('QR Code found! Processing...');
    
    try {
        const response = await fetch('/api/lookup', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ qr_code: decodedText })
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        if (data.success) {
            // Show quick status before redirecting
            const status = getStatusDisplay(data.user.rental_status);
            showStatus(`Found: ${data.user.first_name} ${data.user.last_name} (${status})`);
            
            // Redirect to user details page
            window.location.href = `/lookup?qr_code=${encodeURIComponent(decodedText)}`;
        } else {
            showError(data.error || 'Invalid QR code');
            if (html5QrcodeScanner) {
                html5QrcodeScanner.resume();
            }
        }
    } catch (error) {
        console.error('API error:', error);
        showError('Error processing QR code');
        if (html5QrcodeScanner) {
            html5QrcodeScanner.resume();
        }
    }
}
function createManualEntryForm() {
    const manualEntry = document.getElementById('manualEntry');
    if (!manualEntry) return;

    const form = document.createElement('form');
    form.innerHTML = `
        <div class="flex gap-1">
            <input type="text" 
                   name="qr_code" 
                   placeholder="Enter QR code manually" 
                   class="flex-1 px-3 py-2 border rounded">
            <button type="submit" 
                    class="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 whitespace-nowrap">
                Submit
            </button>
        </div>
    `;

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const input = this.querySelector('input[name="qr_code"]');
        const qrCode = input.value.trim();
        if (qrCode) {
            handleQRCode(qrCode);
            input.value = '';
        }
    });

    manualEntry.appendChild(form);
}

async function initializeScanner() {
    try {
        showLoading(true);
        showStatus('Initializing camera...');

        if (!Html5QrcodeScanner) {
            throw new Error('QR Scanner library not loaded');
        }

        // Create scanner instance
        html5QrcodeScanner = new Html5QrcodeScanner(
            "reader",
            {
                fps: 10,
                qrbox: { width: 250, height: 250 },
                aspectRatio: 1.0,
                rememberLastUsedCamera: true,
            }
        );

        // Define success callback
        const onScanSuccess = (decodedText, decodedResult) => {
            console.log('Code scanned:', decodedText);
            html5QrcodeScanner.pause(true);
            handleQRCode(decodedText);
        };

        // Start scanning
        await html5QrcodeScanner.render(onScanSuccess, (error) => {
            // Ignore scan failures as they're common
            console.debug('QR scan error:', error);
        });

        showStatus('Scanner active - point at a QR code');
        showLoading(false);
        createManualEntryForm();

    } catch (error) {
        console.error('Scanner initialization error:', error);
        showError(`Failed to start scanner: ${error.message}`);
        showLoading(false);
    }
}

// Start everything when the page loads
document.addEventListener('DOMContentLoaded', () => {
    console.log('Starting scanner initialization...');
    setTimeout(initializeScanner, 1000); // Give a slight delay for everything to load
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (html5QrcodeScanner) {
        html5QrcodeScanner.clear();
    }
});