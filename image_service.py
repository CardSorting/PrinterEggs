import os
import requests
import base64
from uuid import uuid4
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from cache_manager import MultiLevelCache, CachePriority  # Updated import
import logging
import time
from typing import Optional, Dict  # Added Dict import

class ImageService:
    def __init__(self, logger: logging.Logger, cache_manager: MultiLevelCache):
        self.B2_APPLICATION_KEY_ID = os.environ['B2_APPLICATION_KEY_ID']
        self.B2_APPLICATION_KEY = os.environ['B2_APPLICATION_KEY']
        self.B2_BUCKET_NAME = os.environ['B2_BUCKET_NAME']
        self.B2_ENDPOINT = os.environ['B2_ENDPOINT']
        self.stable_diffusion_api_key = os.environ.get('STABLE_DIFFUSION_API_KEY')

        self.cache_manager = cache_manager
        self.logger = logger

        # Initialize S3 client for Backblaze B2
        self.s3 = boto3.client(
            's3',
            endpoint_url=self.B2_ENDPOINT,
            aws_access_key_id=self.B2_APPLICATION_KEY_ID,
            aws_secret_access_key=self.B2_APPLICATION_KEY,
            config=Config(signature_version='s3v4')
        )

    def generate_image(self, prompt: str, user_id: int) -> tuple:
        """
        Generates an image using Stable Diffusion API based on a text prompt.
        """
        if not prompt:
            raise ValueError("No prompt provided")

        request_id = str(uuid4())
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.stable_diffusion_api_key}"
        }
        payload = {
            "text_prompts": [{"text": prompt}],
            "cfg_scale": 7,
            "height": 1024,
            "width": 1024,
            "samples": 1,
            "steps": 30,
        }

        try:
            response = requests.post(
                "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            image_data = response.json()["artifacts"][0]["base64"]

            # Log the generation for analytics purposes
            self._log_image_generation(prompt, user_id, request_id)

            return image_data, request_id
        except requests.RequestException as e:
            self.logger.error(f"Error generating image: {e}")
            raise

    def upload_image_to_backblaze(self, image_data: str, filename: str) -> str:
        """
        Uploads an image to Backblaze B2 cloud storage and caches the result.
        """
        # Check cache for existing upload
        cache_key = f"image_upload:{filename}"
        cached_url = self.cache_manager.get(cache_key)
        if cached_url:
            self.logger.info(f"Cache hit for uploaded image: {filename}")
            return cached_url

        try:
            image_bytes = base64.b64decode(image_data)
            self.s3.put_object(Bucket=self.B2_BUCKET_NAME, Key=filename, Body=image_bytes, ContentType='image/png')
            file_url = f"{self.B2_ENDPOINT}/{self.B2_BUCKET_NAME}/{filename}"

            # Cache the upload result
            self.cache_manager.put(cache_key, file_url, CachePriority.MEDIUM)
            self.logger.info(f"Cached uploaded image URL: {filename}")

            return file_url
        except ClientError as e:
            self.logger.error(f"Error uploading image to Backblaze B2: {e}")
            raise

    def get_image_url(self, filename: str) -> str:
        """
        Retrieves the URL of an image stored in Backblaze B2, checking the cache first.
        """
        cache_key = f"image_url:{filename}"
        cached_url = self.cache_manager.get(cache_key)
        if cached_url:
            self.logger.info(f"Cache hit for image URL: {filename}")
            return cached_url

        file_url = f"{self.B2_ENDPOINT}/{self.B2_BUCKET_NAME}/{filename}"
        self.cache_manager.put(cache_key, file_url, CachePriority.LOW)
        self.logger.info(f"Cached image URL: {filename}")
        return file_url

    def _log_image_generation(self, prompt: str, user_id: int, request_id: str):
        """
        Logs an image generation event for analytics.
        """
        log_key = f"image_generation_log:{int(time.time())}"
        log_data = {
            "prompt": prompt,
            "user_id": user_id,
            "request_id": request_id,
            "timestamp": time.time()
        }
        self.cache_manager.put(log_key, log_data, CachePriority.LOW)

    def get_recent_generations(self, limit: int = 10) -> list:
        """
        Retrieves a list of recent image generation logs.
        """
        recent_logs = []
        current_time = int(time.time())
        for i in range(limit):
            log_key = f"image_generation_log:{current_time - i}"
            log_data = self.cache_manager.get(log_key)
            if log_data:
                recent_logs.append(log_data)
        return recent_logs

    def clear_upload_cache(self, filename: Optional[str] = None):
        """
        Clears cached entries related to image uploads.
        """
        if filename:
            self.cache_manager.invalidate(f"image_upload:{filename}")
            self.cache_manager.invalidate(f"image_url:{filename}")
            self.logger.info(f"Cleared cache for filename: {filename}")
        else:
            self.logger.info("Clearing all upload caches not supported. Please provide a filename.")

    def get_cache_metrics(self) -> Dict:
        """
        Returns cache metrics for monitoring and analysis.
        """
        return self.cache_manager.get_metrics()