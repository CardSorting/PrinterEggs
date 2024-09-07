import os
from flask import Flask, flash, redirect, url_for, render_template, Response, jsonify, request
from flask_login import login_user, logout_user, LoginManager, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Priority
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional, Tuple, Dict
from uuid import uuid4
from datetime import datetime, timedelta
from authlib.jose import jwt, JoseError
from functools import wraps
import requests


class AuthService:
    def __init__(self, app: Flask, login_manager: LoginManager, limiter):
        self.app = app
        self.limiter = limiter
        self.login_manager = login_manager
        self.email_service = EmailService()  # Initialize EmailService
        self.jwt_secret = os.getenv('JWT_SECRET_KEY', 'default_secret_key')
        self.jwt_expiration = int(os.getenv('JWT_EXPIRATION_SECONDS', 3600))

        self.login_manager.user_loader(self.load_user)

    # --- User Registration ---
    def register_user(self, form: Dict[str, str]) -> Response:
        username, email, password = self._extract_form_data(form)
        if not self._validate_registration_input(username, email, password):
            return self._render_error_response("auth.register", "Invalid registration data.")

        hashed_password = self._hash_password(password)
        verification_token = self._generate_verification_token()

        if self._create_user(username, email, hashed_password, verification_token):
            self.email_service.send_verification_email(email, verification_token)
            return self._render_success_response("auth.login", "Account created successfully. Please check your email to verify your account.")

        return self._render_error_response("auth.register", "Error creating your account. Please try again.")

    # --- User Login ---
    def login_user(self, form: Dict[str, str]) -> Response:
        username, password = form.get("username", "").strip(), form.get("password", "").strip()
        if not username or not password:
            return self._render_error_response("auth.login", "Both username and password are required.")

        user = self._get_user_by_username(username)
        if user and self._check_password(user, password):
            if not user.is_verified:
                return self._render_error_response("auth.login", "Please verify your account before logging in.")
            self._update_last_login(user)
            login_user(user)
            return redirect(url_for("main.index"))

        return self._render_error_response("auth.login", "Invalid username or password")

    # --- User Logout ---
    def logout_user(self) -> Response:
        logout_user()
        return redirect(url_for("main.index"))

    # --- Email Verification ---
    def verify_email(self, token: str) -> bool:
        user = self._get_user_by_verification_token(token)
        if user:
            user.is_verified = True
            user.verification_token = None
            db.session.commit()
            return True
        return False

    # --- Password Reset ---
    def reset_password(self, email: str) -> Response:
        user = self._get_user_by_email(email)
        if not user:
            return self._render_error_response("auth.reset_password", "Email not found.")

        reset_token = self._generate_reset_token(user)
        self.email_service.send_reset_email(email, reset_token)
        return self._render_success_response("auth.login", "Password reset instructions have been sent to your email.")

    # --- Change Password ---
    def change_password(self, form: Dict[str, str]) -> Response:
        if not current_user.is_authenticated:
            return self._render_error_response("auth.login", "Please log in to change your password.")

        current_password, new_password, confirm_password = form.get("current_password", "").strip(), form.get("new_password", "").strip(), form.get("confirm_password", "").strip()

        if not self._validate_password_change(current_password, new_password, confirm_password):
            return self._render_error_response("auth.change_password", "Invalid password change data.")

        current_user.password = self._hash_password(new_password)
        db.session.commit()
        return self._render_success_response("main.index", "Password changed successfully.")

    # --- JWT Authorization ---
    def require_auth(self, f):
        @wraps(f)
        def decorated(*args, **kwargs):
            token = request.headers.get('Authorization')
            if not token:
                return jsonify({'message': 'Authentication token is missing'}), 401
            try:
                data = jwt.decode(token, self.jwt_secret)
                user = User.query.get(data['user_id'])
                if not user:
                    return jsonify({'message': 'User not found'}), 401
            except JoseError as e:
                return jsonify({'message': str(e)}), 401
            return f(user, *args, **kwargs)
        return decorated

    # --- Utility Methods ---
    def _extract_form_data(self, form: Dict[str, str]) -> Tuple[str, str, str]:
        return (form.get("username", "").strip(), form.get("email", "").strip(), form.get("password", "").strip())

    def _validate_registration_input(self, username: str, email: str, password: str) -> bool:
        if not all([username, email, password]) or len(password) < 8:
            flash("Invalid input. Make sure all fields are filled and the password is at least 8 characters long.")
            return False
        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Username or Email already registered.")
            return False
        return True

    def _create_user(self, username: str, email: str, hashed_password: str, verification_token: str) -> bool:
        try:
            new_user = User(username=username, email=email, password=hashed_password, verification_token=verification_token, is_verified=False, credits=100, priority=Priority.LOW)
            db.session.add(new_user)
            db.session.commit()
            return True
        except SQLAlchemyError as e:
            self._handle_database_error(e)
            return False

    def _get_user_by_username(self, username: str) -> Optional[User]:
        try:
            return User.query.filter_by(username=username).first()
        except SQLAlchemyError as e:
            self._handle_database_error(e)
            return None

    def _get_user_by_email(self, email: str) -> Optional[User]:
        try:
            return User.query.filter_by(email=email).first()
        except SQLAlchemyError as e:
            self._handle_database_error(e)
            return None

    def _get_user_by_verification_token(self, token: str) -> Optional[User]:
        try:
            return User.query.filter_by(verification_token=token).first()
        except SQLAlchemyError as e:
            self._handle_database_error(e)
            return None

    def _update_last_login(self, user: User) -> None:
        user.last_login = datetime.utcnow()
        db.session.commit()

    def _hash_password(self, password: str) -> str:
        return generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

    def _check_password(self, user: User, password: str) -> bool:
        return check_password_hash(user.password, password)

    def _generate_verification_token(self) -> str:
        return str(uuid4())

    def _generate_reset_token(self, user: User) -> str:
        payload = {'user_id': user.id, 'exp': datetime.utcnow() + timedelta(hours=24)}
        header = {'alg': 'HS256'}
        return jwt.encode(header, payload, self.jwt_secret).decode('utf-8')

    def _validate_password_change(self, current_password: str, new_password: str, confirm_password: str) -> bool:
        if not self._check_password(current_user, current_password) or new_password != confirm_password or len(new_password) < 8:
            flash("Invalid password change data.")
            return False
        return True

    def _render_error_response(self, template: str, message: str) -> Response:
        flash(message)
        return render_template(f"{template}.html")

    def _render_success_response(self, redirect_url: str, message: str) -> Response:
        flash(message)
        return redirect(url_for(redirect_url))

    def _handle_database_error(self, error: Exception) -> None:
        self.app.logger.error(f"Database error: {str(error)}")
        db.session.rollback()
        flash("An unexpected error occurred. Please try again later.")

    def load_user(self, user_id: int) -> Optional[User]:
        return User.query.get(int(user_id))


class EmailService:
    def __init__(self):
        self.sendlayer_api_key = os.getenv('SENDLAYER_API_KEY')
        self.sender_email = 'noreply@yourdomain.com'
        self.sender_name = 'YourApp Security Team'
        self.sendlayer_api_url = "https://console.sendlayer.com/api/v1/email"

    def send_verification_email(self, email: str, token: str) -> None:
        """Send a verification email to the user with a unique verification link."""
        verification_url = url_for('auth.verify_email', token=token, _external=True)
        self._send_email(
            email=email,
            subject=f"Verify Your Email Address for {self._app_name()}",
            html_content=self._generate_email_content(verification_url, action='Verify Your Email Address')
        )

    def send_reset_email(self, email: str, token: str) -> None:
        """Send a password reset email to the user with a unique reset link."""
        reset_url = url_for('auth.reset_password', token=token, _external=True)
        self._send_email(
            email=email,
            subject=f"Reset Your Password for {self._app_name()}",
            html_content=self._generate_email_content(reset_url, action='Reset Your Password')
        )

    def _send_email(self, email: str, subject: str, html_content: str) -> None:
        """Internal method to handle sending an email via the SendLayer API."""
        headers = self._prepare_email_headers()
        data = self._prepare_email_data(email, subject, html_content)
        try:
            response = requests.post(self.sendlayer_api_url, headers=headers, json=data)
            response.raise_for_status()
            print(f"Email sent to {email}.")
        except requests.exceptions.RequestException as e:
            print(f"Failed to send email to {email}: {e}")

    def _prepare_email_headers(self) -> Dict[str, str]:
        """Prepare headers required for the SendLayer API request."""
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.sendlayer_api_key}'
        }

    def _prepare_email_data(self, email: str, subject: str, html_content: str) -> Dict[str, any]:
        """Prepare the email payload for the SendLayer API."""
        return {
            "From": {"name": self.sender_name, "email": self.sender_email},
            "To": [{"name": email.split('@')[0], "email": email}],
            "Subject": subject,
            "ContentType": "HTML",
            "HTMLContent": html_content
        }

    def _generate_email_content(self, action_url: str, action: str) -> str:
        """Generate the HTML content for the email based on the action type."""
        app_name = self._app_name()
        year = datetime.now().year
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{action}</title>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #4CAF50; color: white; text-align: center; padding: 10px; }}
                .content {{ background-color: #f9f9f9; border: 1px solid #ddd; padding: 20px; }}
                .button {{ display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 5px; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 0.8em; color: #777; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.sender_name}</h1>
                </div>
                <div class="content">
                    <h2>{action}</h2>
                    <p>To ensure the security of your account, please {action.lower()} by clicking the button below:</p>
                    <p style="text-align: center;">
                        <a href="{action_url}" class="button">{action.split()[0]}</a>
                    </p>
                    <p>If you didn't request this, please ignore this email or contact our support team if you have concerns.</p>
                    <p>For your security:</p>
                    <ul>
                        <li>We will never ask for your password via email.</li>
                        <li>Always check that emails from us use our official domain: {self.sender_email.split('@')[1]}</li>
                        <li>If you're unsure about an email's authenticity, please contact our support team.</li>
                    </ul>
                </div>
                <div class="footer">
                    <p>This is an automated message, please do not reply to this email. If you need assistance, please contact our support team.</p>
                    <p>&copy; {year} {app_name}. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """

    def _app_name(self) -> str:
        """Helper method to extract the app name from the sender's name."""
        return self.sender_name.split()[0]