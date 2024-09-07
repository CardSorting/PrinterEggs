import os
import logging
import atexit
import threading
from flask import Flask, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_session import Session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from authlib.integrations.flask_client import OAuth
from models import db, User
from image_service import ImageService
from queue_handler import QueueHandler
from credit_service import CreditService
from auth_service import AuthService
from cache_manager import MultiLevelCache, CachePriority
from notification_service import NotificationService

class AppConfig:
    SECRET_KEY = os.getenv('SECRET_KEY', os.urandom(24))
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_TYPE = 'filesystem'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your_jwt_secret_key')
    LOG_LEVEL = logging.DEBUG
    CACHE_SIZE = 10000
    CACHE_TTL = 300
    QUEUE_MAX_CALLS_PER_MINUTE = 10
    QUEUE_NUM_WORKERS = 5
    RATELIMIT_STORAGE_URI = "memory://"

class FlaskApp:
    def __init__(self):
        self.app = Flask(__name__)
        self.logger = self._setup_logger()
        self.configure_app()
        self.init_extensions()
        self.init_services()
        self.register_blueprints()
        self.register_error_handlers()

    def configure_app(self):
        self.app.config.from_object(AppConfig)
        self.logger.info("Application configured with AppConfig settings.")

    def init_extensions(self):
        db.init_app(self.app)
        self.migrate = Migrate(self.app, db)
        Session(self.app)
        self._init_limiter()
        self._init_login_manager()
        OAuth(self.app)
        self.logger.info("Flask extensions initialized.")

    def _init_limiter(self):
        self.limiter = Limiter(key_func=get_remote_address, storage_uri=self.app.config["RATELIMIT_STORAGE_URI"])
        self.limiter.init_app(self.app)

    def _init_login_manager(self):
        self.login_manager = LoginManager(self.app)
        self.login_manager.login_view = 'auth.login'

        @self.login_manager.user_loader
        def load_user(user_id):
            return User.query.get(int(user_id))

    def init_services(self):
        self.app.cache_manager = self._create_cache_manager()
        self.app.notification_service = NotificationService(self.app.cache_manager)
        self.app.credit_service = CreditService(db, self.app.cache_manager)
        self.app.image_service = ImageService(self.logger, self.app.cache_manager)
        self.app.auth_service = AuthService(self.app, self.login_manager, self.limiter)
        self.app.queue_handler = self._create_queue_handler()
        self.logger.info("Application services initialized.")

    def _create_cache_manager(self):
        return MultiLevelCache(
            "app_cache",
            memory_size=self.app.config['CACHE_SIZE'],
            shared_size=self.app.config['CACHE_SIZE'] * 2,
            db_path="disk_cache.db"
        )

    def _create_queue_handler(self):
        return QueueHandler(
            self.app.image_service,
            self.limiter,
            self.app.credit_service,
            self.app.cache_manager,
            self.app.notification_service,
            max_calls_per_minute=AppConfig.QUEUE_MAX_CALLS_PER_MINUTE,
            num_workers=AppConfig.QUEUE_NUM_WORKERS
        )

    def register_blueprints(self):
        from main_routes import main_bp
        self.app.register_blueprint(main_bp)
        self.logger.info("Blueprints registered.")

    def register_error_handlers(self):
        @self.app.errorhandler(404)
        def not_found_error(error):
            return render_template('404.html'), 404

        @self.app.errorhandler(500)
        def internal_error(error):
            db.session.rollback()
            return render_template('500.html'), 500

        @self.app.errorhandler(429)
        def ratelimit_handler(e):
            return jsonify(error="Rate limit exceeded. Please try again later."), 429

        self.logger.info("Error handlers registered.")

    def _setup_logger(self):
        logger = logging.getLogger('AppLogger')
        if not logger.handlers:
            logger.setLevel(AppConfig.LOG_LEVEL)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

            file_handler = logging.FileHandler('app.log')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        return logger

    def warm_up_cache(self):
        frequently_accessed_data = {
            "key1": ("value1", CachePriority.HIGH),
            "key2": ("value2", CachePriority.MEDIUM),
        }
        self.app.cache_manager.warm_up_cache(frequently_accessed_data)
        self.logger.info("Cache warmed up with frequently accessed data.")

    def log_cache_performance(self):
        self.app.cache_manager.log_cache_metrics()
        threading.Timer(3600, self.log_cache_performance).start()

    def run(self):
        with self.app.app_context():
            db.create_all()
        self.warm_up_cache()
        self.log_cache_performance()
        self.logger.info("Starting the application...")
        self.app.run(host="0.0.0.0", port=int(os.getenv('PORT', 5000)), debug=False)

    def shutdown(self):
        self.logger.info("Shutting down the application...")
        self.app.cache_manager.close()
        self.app.queue_handler.stop()

def main():
    flask_app = FlaskApp()
    atexit.register(flask_app.shutdown)
    flask_app.run()

if __name__ == "__main__":
    main()