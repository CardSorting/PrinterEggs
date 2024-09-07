from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from typing import Dict, Any, Tuple, Union
from functools import wraps
from utils import rate_limit, track_performance, credit_service, auth_service, RateLimitExceeded, logger
from models import User, Image, Collection
from cache_manager import cache, CachePriority

main_bp = Blueprint('main', __name__)

# Type Aliases
JsonResponse = Tuple[Dict[str, Any], int]
RouteResponse = Union[str, JsonResponse]

def json_response(data: Dict[str, Any] = None, message: str = "", status: int = 200) -> JsonResponse:
    """Create a standardized JSON response."""
    response = {"status": "success" if status < 400 else "error", "message": message}
    if data:
        response["data"] = data
    return jsonify(response), status

def handle_exceptions(func):
    """Decorator for consistent exception handling across routes."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except HTTPException as e:
            return json_response(message=str(e), status=e.code)
        except RateLimitExceeded:
            return json_response(message="Rate limit exceeded", status=429)
        except SQLAlchemyError as e:
            logger.error(f"Database error in {func.__name__}: {str(e)}")
            return json_response(message="A database error occurred", status=500)
        except Exception as e:
            logger.error(f"Unhandled error in {func.__name__}: {str(e)}")
            return json_response(message="An unexpected error occurred", status=500)
    return wrapper

# Main Routes

@main_bp.route("/")
@track_performance
@handle_exceptions
@cache(ttl=300, priority=CachePriority.MEDIUM)
def index() -> str:
    """Render the main index page."""
    user_data = current_user.to_dict() if current_user.is_authenticated else None
    return render_template("index.html", data={"message": "Welcome to the Main Index Page", "user": user_data})

@main_bp.route("/queue-status")
@rate_limit("30/minute")
@track_performance
@handle_exceptions
@cache(ttl=10, priority=CachePriority.LOW)
def queue_status() -> JsonResponse:
    """Get the current queue status and user priority info."""
    status = current_app.queue_handler.get_queue_status()
    if current_user.is_authenticated:
        status.update(credit_service.get_user_priority_info(current_user.id))
    return json_response(data=status)

@main_bp.route("/user-info")
@login_required
@rate_limit("10/minute")
@track_performance
@handle_exceptions
@cache(ttl=60, priority=CachePriority.MEDIUM)
def user_info() -> JsonResponse:
    """Get authenticated user's information and credit status."""
    info = credit_service.get_user_priority_info(current_user.id)
    info["can_make_request"] = credit_service.can_make_request(current_user.id)
    return json_response(data=info)

@main_bp.route("/enqueue_request", methods=["POST"])
@login_required
@rate_limit("5/minute")
@track_performance
@handle_exceptions
def enqueue_request() -> JsonResponse:
    """Enqueue a new image generation request."""
    prompt = request.json.get('prompt', '').strip()
    if not prompt:
        return json_response(message="Prompt cannot be empty", status=400)
    request_id = current_app.queue_handler.enqueue(prompt, current_user.id)
    return json_response(data={"request_id": request_id}, message="Request queued")

@main_bp.route("/image/generate", methods=["POST"])
@login_required
@rate_limit("5/minute")
@track_performance
@handle_exceptions
def generate_image() -> JsonResponse:
    """Generate a new image based on the provided prompt."""
    prompt = request.json.get("prompt")
    if not prompt:
        return json_response(message="No prompt provided", status=400)
    request_id = current_app.queue_handler.enqueue(prompt, current_user.id)
    return json_response(data={"request_id": request_id}, message="Image generation request queued")

@main_bp.route('/api/images')
@login_required
@rate_limit("30/minute")
@track_performance
@handle_exceptions
@cache(ttl=60, priority=CachePriority.LOW)
def get_images() -> JsonResponse:
    """Retrieve all images associated with the current user."""
    images = [image.to_dict() for image in Image.query.filter_by(user_id=current_user.id).all()]
    return json_response(data=images)

@main_bp.route('/api/collections')
@login_required
@rate_limit("30/minute")
@track_performance
@handle_exceptions
@cache(ttl=60, priority=CachePriority.LOW)
def get_collections() -> JsonResponse:
    """Retrieve all collections associated with the current user."""
    collections = [collection.to_dict() for collection in Collection.query.filter_by(user_id=current_user.id).all()]
    return json_response(data=collections)

@main_bp.route("/public_gallery")
@track_performance
@handle_exceptions
@cache(ttl=60, priority=CachePriority.MEDIUM)
def public_gallery() -> str:
    """Render the public gallery page."""
    public_images = [image.to_dict() for image in Image.query.filter_by(public=True).all()]
    return render_template("public_gallery.html", images=public_images)

# Authentication Routes

@main_bp.route("/auth/register", methods=["GET", "POST"])
@rate_limit("5/hour")
@track_performance
@handle_exceptions
def register() -> RouteResponse:
    """Handle user registration."""
    if request.method == "POST":
        result = auth_service.register_user(request.form)
        if isinstance(result, tuple):
            return json_response(message=result[0], status=result[1])
        flash("Account created. Please check your email to verify.")
        return redirect(url_for('main.login'))
    return render_template("register.html")

@main_bp.route("/auth/login", methods=["GET", "POST"])
@rate_limit("10/hour")
@track_performance
@handle_exceptions
def login() -> RouteResponse:
    """Handle user login."""
    if request.method == "POST":
        result = auth_service.login_user(request.form)
        if isinstance(result, tuple):
            return json_response(message=result[0], status=result[1])
        return redirect(url_for('main.index'))
    return render_template("login.html")

@main_bp.route("/auth/logout")
@login_required
@track_performance
@handle_exceptions
def logout() -> str:
    """Handle user logout."""
    auth_service.logout_user()
    flash("You have been logged out.")
    return redirect(url_for('main.index'))

@main_bp.route("/auth/verify_email/<token>")
@track_performance
@handle_exceptions
def verify_email(token: str) -> str:
    """Verify user's email address."""
    if auth_service.verify_email(token):
        flash("Email verified. You can now log in.")
    else:
        flash("Invalid or expired verification token.")
    return redirect(url_for('main.login'))

@main_bp.route("/auth/resend_verification", methods=["POST"])
@rate_limit("3/hour")
@track_performance
@handle_exceptions
def resend_verification() -> RouteResponse:
    """Resend email verification link."""
    email = request.form.get('email')
    if not email:
        return json_response(message="Email is required", status=400)
    result = auth_service.send_verification_email(email)
    if isinstance(result, tuple):
        return json_response(message=result[0], status=result[1])
    flash("Verification email sent. Please check your inbox.")
    return redirect(url_for('main.login'))

@main_bp.route("/auth/request_password_reset", methods=["GET", "POST"])
@rate_limit("3/hour")
@track_performance
@handle_exceptions
def request_password_reset() -> RouteResponse:
    """Handle password reset request."""
    if request.method == "POST":
        email = request.form.get('email')
        if not email:
            return json_response(message="Email is required", status=400)
        if auth_service.send_password_reset_email(email):
            flash("Password reset link sent to your email.")
        else:
            flash("Unable to send reset email. Please try again later.")
        return redirect(url_for('main.login'))
    return render_template("request_password_reset.html")

@main_bp.route("/auth/reset_password/<token>", methods=["GET", "POST"])
@track_performance
@handle_exceptions
def reset_password(token: str) -> RouteResponse:
    """Handle password reset."""
    if request.method == "POST":
        new_password = request.form.get('new_password')
        if not new_password:
            return json_response(message="New password is required", status=400)
        if auth_service.reset_password(token, new_password):
            flash("Password reset successful. You can now log in.")
            return redirect(url_for('main.login'))
        flash("Invalid or expired reset token.")
    return render_template("reset_password.html", token=token)

# User Profile Routes

@main_bp.route("/auth/profile")
@login_required
@track_performance
@handle_exceptions
def profile() -> str:
    """Render user profile page."""
    return render_template("profile.html", user=current_user)

@main_bp.route("/auth/update_profile", methods=["POST"])
@login_required
@rate_limit("10/day")
@track_performance
@handle_exceptions
def update_profile() -> str:
    """Update user profile information."""
    if auth_service.update_user_profile(current_user.id, request.form):
        flash("Profile updated successfully.")
    else:
        flash("Failed to update profile. Please try again.")
    return redirect(url_for('main.profile'))

@main_bp.route("/auth/change_password", methods=["POST"])
@login_required
@rate_limit("3/day")
@track_performance
@handle_exceptions
def change_password() -> RouteResponse:
    """Handle password change request."""
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    if not current_password or not new_password:
        return json_response(message="Both current and new passwords are required", status=400)
    if auth_service.change_user_password(current_user.id, current_password, new_password):
        flash("Password changed successfully.")
        return redirect(url_for('main.logout'))
    flash("Failed to change password. Please check your current password.")
    return redirect(url_for('main.profile'))

@main_bp.route("/auth/delete_account", methods=["POST"])
@login_required
@rate_limit("1/day")
@track_performance
@handle_exceptions
def delete_account() -> RouteResponse:
    """Handle account deletion request."""
    password = request.form.get('password')
    if not password:
        return json_response(message="Password is required to delete account", status=400)
    if auth_service.delete_user_account(current_user.id, password):
        flash("Your account has been deleted.")
        return redirect(url_for('main.index'))
    flash("Failed to delete account. Please check your password.")
    return redirect(url_for('main.profile'))

# Health Check Route

@main_bp.route("/health")
@track_performance
@handle_exceptions
def health_check() -> JsonResponse:
    """Perform a health check of the application."""
    db_status = "OK" if current_app.db.engine.execute("SELECT 1").scalar() else "ERROR"
    cache_status = "OK" if current_app.cache_manager.get("health_check") is not None else "ERROR"
    return json_response(data={
        "database": db_status,
        "cache": cache_status,
        "queue_size": current_app.queue_handler.get_queue_size()
    })