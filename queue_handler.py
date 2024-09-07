import time
import threading
import logging
from collections import deque
from typing import Tuple, Optional, Any
from flask_limiter import Limiter
from dataclasses import dataclass
from enum import Enum
import uuid
from notification_service import NotificationService  # Import NotificationService

class Priority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

@dataclass
class Request:
    prompt: str
    user_id: int
    request_id: str
    priority: Priority
    retries: int = 0

class QueueHandler:
    """
    Advanced QueueHandler that manages and processes image generation requests
    with integrated rate-limiting, credit checking/deduction, and credit-based priority queuing.
    """
    CREDITS_PER_REQUEST = 12
    MAX_RETRIES = 3

    # Credit thresholds for priority levels
    MEDIUM_PRIORITY_THRESHOLD = 100
    HIGH_PRIORITY_THRESHOLD = 500

    def __init__(self, image_service: Any, limiter: Limiter, credit_service: Any, cache_manager: Any, notification_service: NotificationService, max_calls_per_minute: int = 10, num_workers: int = 3):
        self.image_service = image_service
        self.limiter = limiter
        self.credit_service = credit_service
        self.cache_manager = cache_manager
        self.notification_service = notification_service  # Initialize NotificationService
        self.queues = {
            Priority.LOW: deque(),
            Priority.MEDIUM: deque(),
            Priority.HIGH: deque()
        }
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.num_workers = num_workers
        self.max_calls_per_minute = max_calls_per_minute
        self.logger = self._initialize_logger()
        self._initialize_rate_limiter()
        self._start_worker_threads()

    def _initialize_logger(self) -> logging.Logger:
        logger = logging.getLogger("QueueHandler")
        logger.setLevel(logging.DEBUG)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        file_handler = logging.FileHandler("queue_handler.log")
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        return logger

    def _initialize_rate_limiter(self):
        self.enqueue_rate_limit = f"{self.max_calls_per_minute} per minute"

    def enqueue(self, prompt: str, user_id: int) -> str:
        with self.limiter.limit(self.enqueue_rate_limit, key_func=lambda: f"user:{user_id}"):
            if not self._check_and_deduct_credits(user_id):
                raise ValueError("Insufficient credits to process the request.")

            with self.lock:
                request_id = str(uuid.uuid4())
                priority = self._determine_priority(user_id)
                request = Request(prompt, user_id, request_id, priority)
                self.queues[priority].append(request)
                self.logger.info(f"Request {request_id} added to {priority.name} queue for user {user_id}.")

                # Notify user that their request has started processing
                self._notify_user_start(request.user_id, request.request_id)

                return request_id

    def _check_and_deduct_credits(self, user_id: int) -> bool:
        try:
            user_credits = self.credit_service.get_user_credits(user_id)
            if user_credits < self.CREDITS_PER_REQUEST:
                return False
            self.credit_service.deduct_credits(user_id, self.CREDITS_PER_REQUEST)
            return True
        except ValueError:
            self.logger.warning(f"User {user_id} has insufficient credits.")
            return False

    def _determine_priority(self, user_id: int) -> Priority:
        user_credits = self.credit_service.get_user_credits(user_id)
        if user_credits >= self.HIGH_PRIORITY_THRESHOLD:
            return Priority.HIGH
        elif user_credits >= self.MEDIUM_PRIORITY_THRESHOLD:
            return Priority.MEDIUM
        else:
            return Priority.LOW

    def _worker(self):
        while not self.stop_event.is_set():
            request = self._get_next_request()
            if request:
                self._process_request(request)
            else:
                time.sleep(0.1)

    def _get_next_request(self) -> Optional[Request]:
        with self.lock:
            for priority in reversed(Priority):
                if self.queues[priority]:
                    return self.queues[priority].popleft()
        return None

    def _process_request(self, request: Request):
        try:
            self.logger.info(f"Processing request {request.request_id} for user {request.user_id}")
            image_data, _ = self.image_service.generate_image(request.prompt)
            image_url = self._upload_image(image_data, request.request_id)
            self.logger.info(f"Generated image URL for user {request.user_id}: {image_url}")
            self._notify_user_complete(request.user_id, request.request_id, image_url)
        except Exception as e:
            self.logger.error(f"Error processing request {request.request_id}: {e}")
            if request.retries < self.MAX_RETRIES:
                self._requeue_request(request)
            else:
                self._handle_failed_request(request)

    def _upload_image(self, image_data: Any, request_id: str) -> str:
        filename = f"{request_id}.png"
        return self.image_service.upload_image_to_backblaze(image_data, filename)

    def _notify_user_start(self, user_id: int, request_id: str):
        """Notify the user that their request has started processing."""
        notification_data = {
            'user_id': user_id,
            'request_id': request_id,
            'message': f'Your image request {request_id} has started processing.'
        }
        if not self.notification_service.send_in_app_notification(user_id, notification_data):
            self.logger.warning(f"Failed to send start notification to user {user_id} for request {request_id}.")

    def _notify_user_complete(self, user_id: int, request_id: str, image_url: str):
        """Notify the user that their request has been processed and is complete."""
        notification_data = {
            'user_id': user_id,
            'request_id': request_id,
            'image_url': image_url,
            'message': f'Your image request {request_id} has been processed. View it at {image_url}.'
        }
        if not self.notification_service.send_in_app_notification(user_id, notification_data):
            self.logger.warning(f"Failed to send completion notification to user {user_id} for request {request_id}.")

    def _requeue_request(self, request: Request):
        request.retries += 1
        with self.lock:
            self.queues[request.priority].append(request)
        self.logger.info(f"Requeued request {request.request_id} for retry (attempt {request.retries})")

    def _handle_failed_request(self, request: Request):
        self.logger.error(f"Request {request.request_id} failed after {self.MAX_RETRIES} attempts")
        self._refund_credits(request.user_id)

    def _refund_credits(self, user_id: int):
        try:
            self.credit_service.add_credits(user_id, self.CREDITS_PER_REQUEST)
            self.logger.info(f"Refunded {self.CREDITS_PER_REQUEST} credits to user {user_id}")
        except Exception as e:
            self.logger.error(f"Failed to refund credits to user {user_id}: {e}")

    def _start_worker_threads(self):
        for _ in range(self.num_workers):
            thread = threading.Thread(target=self._worker, daemon=True)
            thread.start()

    def stop(self):
        self.stop_event.set()
        self.logger.info("Stopping QueueHandler")

    def get_queue_status(self):
        with self.lock:
            return {priority.name: len(queue) for priority, queue in self.queues.items()}