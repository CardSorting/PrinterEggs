from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from enum import Enum
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, validates
from sqlalchemy import event, text
from sqlalchemy.sql import func

db = SQLAlchemy()

class Priority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    _password = db.Column('password', db.String(255), nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    verification_token = db.Column(db.String(100), nullable=True)
    credits = db.Column(db.Integer, default=100, nullable=False)
    last_credits_update = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    priority = db.Column(db.Enum(Priority), default=Priority.LOW, nullable=False)

    images = relationship('Image', back_populates='user', lazy='dynamic', cascade="all, delete-orphan")
    collections = relationship('Collection', back_populates='user', lazy='dynamic', cascade="all, delete-orphan")

    @hybrid_property
    def password(self) -> str:
        return self._password

    @password.setter
    def password(self, plain_password: str) -> None:
        self._password = generate_password_hash(plain_password, method='pbkdf2:sha256', salt_length=16)

    @validates('username', 'email')
    def validate_fields(self, key: str, value: str) -> str:
        if key == 'username' and len(value) < 3:
            raise ValueError('Username must be at least 3 characters long')
        elif key == 'email' and '@' not in value:
            raise ValueError('Invalid email address')
        return value.lower()

    def check_password(self, password: str) -> bool:
        return check_password_hash(self._password, password)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'is_verified': self.is_verified,
            'credits': self.credits,
            'priority': self.priority.name,
            'created_at': self.created_at.isoformat(),
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'images_count': self.images.count(),
            'collections_count': self.collections.count()
        }

class Image(TimestampMixin, db.Model):
    __tablename__ = 'images'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    file_path = db.Column(db.String(255), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    user = relationship('User', back_populates='images')
    tags = relationship('Tag', secondary='image_tags', back_populates='images', lazy='joined')
    collections = relationship('Collection', secondary='collection_images', back_populates='images', lazy='dynamic')

    @validates('file_path')
    def validate_file_path(self, key: str, file_path: str) -> str:
        if not file_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            raise ValueError('Invalid file format. Only PNG, JPG, JPEG, and GIF are allowed.')
        return file_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'file_path': self.file_path,
            'user_id': self.user_id,
            'created_at': self.created_at.isoformat(),
            'tags': [tag.name for tag in self.tags],
            'collections_count': self.collections.count()
        }

class Tag(TimestampMixin, db.Model):
    __tablename__ = 'tags'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)

    images = relationship('Image', secondary='image_tags', back_populates='tags', lazy='dynamic')

    @validates('name')
    def validate_name(self, key: str, name: str) -> str:
        return name.lower().strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'images_count': self.images.count()
        }

class Collection(TimestampMixin, db.Model):
    __tablename__ = 'collections'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    user = relationship('User', back_populates='collections')
    images = relationship('Image', secondary='collection_images', back_populates='collections', lazy='dynamic')

    @validates('name')
    def validate_name(self, key: str, name: str) -> str:
        if len(name) < 3:
            raise ValueError('Collection name must be at least 3 characters long')
        return name

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat(),
            'user_id': self.user_id,
            'images_count': self.images.count()
        }

image_tags = db.Table('image_tags',
    db.Column('image_id', db.Integer, db.ForeignKey('images.id', ondelete='CASCADE'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)
)

collection_images = db.Table('collection_images',
    db.Column('collection_id', db.Integer, db.ForeignKey('collections.id', ondelete='CASCADE'), primary_key=True),
    db.Column('image_id', db.Integer, db.ForeignKey('images.id', ondelete='CASCADE'), primary_key=True)
)

@event.listens_for(User.credits, 'set')
def update_last_credits_update(target, value, oldvalue, initiator):
    target.last_credits_update = datetime.utcnow()

def init_db(app):
    with app.app_context():
        db.create_all()
        db.session.execute(text('PRAGMA foreign_keys = ON'))
        db.session.commit()

# Index creation for performance optimization
def create_indexes():
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_images_user_id ON images (user_id)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_collections_user_id ON collections (user_id)'))
    db.session.commit()

# Example usage
if __name__ == '__main__':
    from flask import Flask
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///example.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    with app.app_context():
        init_db(app)
        create_indexes()

    print("Database initialized with optimized models and indexes.")