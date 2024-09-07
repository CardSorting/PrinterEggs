document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('image-form');
    const promptInput = document.getElementById('prompt');
    const generateBtn = document.getElementById('generate-btn');
    const resultContainer = document.getElementById('result-container');
    const loadingIndicator = document.getElementById('loading-indicator');
    const errorMessage = document.getElementById('error-message');

    initializeForm();

    function initializeForm() {
        if (form) {
            form.addEventListener('submit', handleFormSubmit);
        }
    }

    async function handleFormSubmit(event) {
        event.preventDefault();
        const prompt = promptInput.value.trim();

        if (!prompt) {
            showError('Please enter a prompt.');
            return;
        }

        showLoading(true);
        hideError();

        try {
            const requestId = generateRequestId();
            const response = await fetchImage(prompt, requestId);
            const data = await response.json();
            if (data.error) {
                throw new Error(data.error);
            }
            displayImage(data.image_url, requestId);  // Pass requestId to displayImage
        } catch (error) {
            showError(error.message);
        } finally {
            showLoading(false);
        }
    }

    async function fetchImage(prompt, requestId) {
        try {
            const response = await fetch('/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ prompt, request_id: requestId }),  // Include request_id in the body
            });

            if (!response.ok) {
                const errorMessage = await response.json();
                if (response.status === 401) {
                    throw new Error('Please login to generate images.');
                }
                throw new Error(errorMessage.error || 'Failed to generate image');
            }
            return response;
        } catch (error) {
            showError('Error fetching image: ' + error.message);
            throw error;
        }
    }

    function displayImage(imageUrl, requestId) {
        clearResultContainer();

        const img = createImageElement(imageUrl);
        const downloadBtn = createDownloadButton(imageUrl);
        const requestIdElement = createRequestIdElement(requestId);

        resultContainer.appendChild(img);
        resultContainer.appendChild(downloadBtn);
        resultContainer.appendChild(requestIdElement);
    }

    function createImageElement(imageUrl) {
        const img = document.createElement('img');
        img.src = imageUrl;
        img.alt = 'Generated Image';
        img.className = 'w-full h-auto rounded-lg shadow-lg';
        return img;
    }

    function createDownloadButton(imageUrl) {
        const downloadBtn = document.createElement('button');
        downloadBtn.className = 'mt-4 bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded';
        downloadBtn.textContent = 'Download Image';
        downloadBtn.addEventListener('click', async () => {
            try {
                const response = await fetch(imageUrl);
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'generated-image.png';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
            } catch (error) {
                showError('Error downloading image: ' + error.message);
            }
        });
        return downloadBtn;
    }

    function createRequestIdElement(requestId) {
        const requestIdDiv = document.createElement('div');
        requestIdDiv.className = 'mt-4 text-gray-600';
        requestIdDiv.textContent = `Request ID: ${requestId}`;
        return requestIdDiv;
    }

    function clearResultContainer() {
        resultContainer.innerHTML = '';
    }

    function showLoading(isLoading) {
        generateBtn.disabled = isLoading;
        loadingIndicator.style.display = isLoading ? 'block' : 'none';
    }

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.style.display = 'block';
    }

    function hideError() {
        errorMessage.style.display = 'none';
    }

    function generateRequestId() {
        return 'xxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }
});