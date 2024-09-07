document.addEventListener('DOMContentLoaded', () => {
    const creditOptions = document.querySelectorAll('.credit-option');
    const errorMessageElement = document.getElementById('error-message');

    // Initialize the application
    initializeCreditOptions();

    /**
     * Initializes event listeners for all credit options buttons.
     * Enhances UX by setting up listeners and managing error states.
     */
    function initializeCreditOptions() {
        if (!creditOptions.length) {
            logError('No credit options available to initialize.');
            return;
        }

        creditOptions.forEach(option => option.addEventListener('click', handleCreditOptionClick));
    }

    /**
     * Handles the click event on a credit option button.
     * Extracts the number of credits from the clicked button and initiates the purchase process.
     * 
     * @param {Event} event - The click event object.
     */
    async function handleCreditOptionClick(event) {
        clearError();  // Clear any existing errors before proceeding
        const credits = event.currentTarget.getAttribute('data-credits');

        if (!credits) {
            displayError('Invalid credit option selected.');
            return;
        }

        try {
            await initiateCreditPurchase(credits);
        } catch (error) {
            displayError(error.message);
        }
    }

    /**
     * Initiates the purchase process for the specified number of credits.
     * Sends a request to the server to create a checkout session and redirects the user to Stripe if successful.
     * 
     * @param {string} credits - The number of credits to purchase.
     */
    async function initiateCreditPurchase(credits) {
        try {
            const response = await fetch('/buy-credits', createRequestOptions(credits));

            if (!response.ok) {
                await handleFailedResponse(response);
                return;
            }

            const data = await response.json();
            redirectToCheckout(data.checkout_url);
        } catch (error) {
            logError(`Failed to initiate credit purchase: ${error.message}`);
            throw new Error('Failed to initiate purchase. Please try again later.');
        }
    }

    /**
     * Handles failed HTTP responses and provides user feedback.
     * 
     * @param {Response} response - The HTTP response object from the fetch request.
     */
    async function handleFailedResponse(response) {
        const errorData = await response.json();
        const errorMessage = errorData.error || 'An unexpected error occurred. Please try again later.';
        logError(`Error from server: ${errorMessage}`);
        displayError(errorMessage);
    }

    /**
     * Creates the request options for the fetch API to initiate a credit purchase.
     * 
     * @param {string} credits - The number of credits to purchase.
     * @returns {object} The options object for the fetch request.
     */
    function createRequestOptions(credits) {
        return {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ credits: parseInt(credits, 10) })
        };
    }

    /**
     * Redirects the user to the Stripe checkout page.
     * 
     * @param {string} checkoutUrl - The URL to redirect to.
     */
    function redirectToCheckout(checkoutUrl) {
        if (!checkoutUrl) {
            throw new Error('Checkout URL not provided.');
        }
        window.location.href = checkoutUrl;
    }

    /**
     * Displays an error message to the user.
     * 
     * @param {string} message - The error message to display.
     */
    function displayError(message) {
        if (errorMessageElement) {
            errorMessageElement.textContent = message;
            errorMessageElement.style.display = 'block';
        }
    }

    /**
     * Clears any displayed error message.
     */
    function clearError() {
        if (errorMessageElement) {
            errorMessageElement.textContent = '';
            errorMessageElement.style.display = 'none';
        }
    }

    /**
     * Logs error messages for debugging and monitoring purposes.
     * 
     * @param {string} message - The error message to log.
     */
    function logError(message) {
        console.error(`[ERROR] ${new Date().toISOString()} - ${message}`);
    }
});