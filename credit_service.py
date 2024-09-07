import os
import stripe
import logging
from flask import request, jsonify, current_app, url_for
from flask_sqlalchemy import SQLAlchemy
from stripe.error import StripeError
from stripe.error import SignatureVerificationError
from sqlalchemy.orm.exc import NoResultFound
from flask_login import login_required, current_user
from datetime import datetime
from enum import Enum
from models import User, Priority
from cache_manager import MultiLevelCache, CachePriority  # Updated import
from typing import Dict, Any, Optional

# Ensure that Stripe API key is set once for the entire application
stripe.api_key = os.environ['STRIPE_SECRET_KEY']

class CreditServiceError(Exception):
    """Base exception class for CreditService"""

class InsufficientCreditsError(CreditServiceError):
    """Raised when a user doesn't have enough credits"""

class UserNotFoundError(CreditServiceError):
    """Raised when a user is not found"""

class CreditService:
    """
    CreditService manages user credits, handles credit purchases using Stripe integration,
    and supports credit-based priority system with advanced caching.
    """

    DAILY_FREE_CREDITS = 50  # Constant for daily free credits
    CREDITS_PER_REQUEST = 12  # Credits required for each image generation request
    MEDIUM_PRIORITY_THRESHOLD = 100
    HIGH_PRIORITY_THRESHOLD = 500

    def __init__(self, db: SQLAlchemy, cache_manager: MultiLevelCache):
        self.db = db
        self.logger = self._initialize_logger()
        self.cache_manager = cache_manager  # Use MultiLevelCache

    def _initialize_logger(self) -> logging.Logger:
        logger = logging.getLogger("CreditService")
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def get_user_credits(self, user_id: int) -> int:
        cache_key = f"user_credits:{user_id}"
        cached_credits = self.cache_manager.get(cache_key)

        if cached_credits is not None:
            self.logger.debug(f"Cache hit for user credits: {user_id}")
            return cached_credits

        self.logger.debug(f"Cache miss for user credits: {user_id}")
        user = self._get_user(user_id)
        self._add_daily_credits(user)

        self.cache_manager.put(cache_key, user.credits, CachePriority.HIGH)
        return user.credits

    def deduct_credits(self, user_id: int, amount: int = CREDITS_PER_REQUEST) -> bool:
        cache_key = f"user_credits:{user_id}"
        user = self._get_user(user_id)

        if user.credits < amount:
            self.logger.error(f"User {user_id} has insufficient credits for deduction.")
            raise InsufficientCreditsError("Insufficient credits.")

        user.credits -= amount
        self._update_user_priority(user)
        self.db.session.commit()

        self.cache_manager.put(cache_key, user.credits, CachePriority.HIGH)
        self.logger.info(f"Deducted {amount} credits from user {user_id}. New balance: {user.credits}.")
        return True

    def add_credits(self, user_id: int, amount: int) -> bool:
        cache_key = f"user_credits:{user_id}"
        user = self._get_user(user_id)

        user.credits += amount
        self._update_user_priority(user)
        self.db.session.commit()

        self.cache_manager.put(cache_key, user.credits, CachePriority.HIGH)
        self.logger.info(f"Added {amount} credits to user {user_id}. New balance: {user.credits}.")
        return True

    def determine_priority(self, user_id: int) -> Priority:
        credits = self.get_user_credits(user_id)
        return self._calculate_priority(credits)

    def _calculate_priority(self, credits: int) -> Priority:
        if credits >= self.HIGH_PRIORITY_THRESHOLD:
            return Priority.HIGH
        elif credits >= self.MEDIUM_PRIORITY_THRESHOLD:
            return Priority.MEDIUM
        else:
            return Priority.LOW

    def _update_user_priority(self, user: User):
        new_priority = self._calculate_priority(user.credits)
        if user.priority != new_priority:
            user.priority = new_priority
            self.logger.info(f"Updated priority for user {user.id} to {new_priority.name}")

    def get_user_priority_info(self, user_id: int) -> Dict[str, Any]:
        cache_key = f"user_priority_info:{user_id}"
        cached_info = self.cache_manager.get(cache_key)

        if cached_info is not None:
            self.logger.debug(f"Cache hit for user priority info: {user_id}")
            return cached_info

        self.logger.debug(f"Cache miss for user priority info: {user_id}")
        credits = self.get_user_credits(user_id)
        priority = self._calculate_priority(credits)

        info = {
            "user_credits": credits,
            "user_priority": priority.name,
            "medium_priority_threshold": self.MEDIUM_PRIORITY_THRESHOLD,
            "high_priority_threshold": self.HIGH_PRIORITY_THRESHOLD
        }

        self.cache_manager.put(cache_key, info, CachePriority.MEDIUM)
        return info

    def create_checkout_session(self, user_id: int, credits: int, success_url: str, cancel_url: str) -> Dict[str, str]:
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {'name': f'Purchase {credits} Credits'},
                        'unit_amount': credits * 1,  # 1 credit = $0.01, hence multiply credits by 1 cent
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={'user_id': user_id, 'credits': credits},
            )
            self.logger.info(f"Created Stripe checkout session for user {user_id} for {credits} credits.")
            return {'checkout_url': session.url}
        except StripeError as e:
            self.logger.error(f"Stripe error during checkout session creation for user {user_id}: {str(e)}")
            raise CreditServiceError("Failed to create Stripe checkout session. Please try again later.")

    def handle_payment_success(self, payload: str, sig_header: str) -> Dict[str, str]:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, os.environ['STRIPE_WEBHOOK_SECRET']
            )
        except (ValueError, SignatureVerificationError) as e:
            self.logger.error(f"Stripe webhook verification failed: {str(e)}")
            return {"status": "failure", "error": "Invalid webhook signature"}

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            user_id = int(session['metadata']['user_id'])
            credits = int(session['metadata']['credits'])
            self.add_credits(user_id, credits)
            self.logger.info(f"Successfully added {credits} credits to user {user_id} after payment.")
            return {"status": "success"}

        return {"status": "ignored"}

    def _get_user(self, user_id: int) -> User:
        cache_key = f"user:{user_id}"
        cached_user = self.cache_manager.get(cache_key)

        if cached_user is not None:
            self.logger.debug(f"Cache hit for user: {user_id}")
            return cached_user

        self.logger.debug(f"Cache miss for user: {user_id}")
        try:
            user = User.query.filter_by(id=user_id).one()
            self.cache_manager.put(cache_key, user, CachePriority.MEDIUM)
            return user
        except NoResultFound:
            self.logger.error(f"User {user_id} not found.")
            raise UserNotFoundError(f"User {user_id} not found.")

    def _add_daily_credits(self, user: User):
        if user.last_credits_update is None or user.last_credits_update.date() < datetime.utcnow().date():
            user.credits += self.DAILY_FREE_CREDITS
            user.last_credits_update = datetime.utcnow()
            self._update_user_priority(user)
            self.db.session.commit()

            cache_key = f"user_credits:{user.id}"
            self.cache_manager.put(cache_key, user.credits, CachePriority.HIGH)

            self.logger.info(f"Added daily free credits to user {user.id}. New balance: {user.credits}.")

    def can_make_request(self, user_id: int) -> bool:
        return self.get_user_credits(user_id) >= self.CREDITS_PER_REQUEST

    def get_cache_metrics(self) -> Dict[str, Any]:
        return self.cache_manager.get_metrics()

    def __del__(self):
        self.cache_manager.close()