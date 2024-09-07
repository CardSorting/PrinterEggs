import logging
from datetime import datetime, timedelta
from math import log, exp
from models import db, User

class Image(db.Model):
    __tablename__ = 'images'
    id = db.Column(db.Integer, primary_key=True)
    prompt = db.Column(db.String(255), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    public = db.Column(db.Boolean, default=False)
    request_id = db.Column(db.String(36), unique=True, nullable=False)

    views = db.Column(db.Integer, default=0, nullable=False)
    upvotes = db.Column(db.Integer, default=0, nullable=False)
    shares = db.Column(db.Integer, default=0, nullable=False)
    saves = db.Column(db.Integer, default=0, nullable=False)
    engagement_score = db.Column(db.Float, default=0.0, nullable=False)
    quality_score = db.Column(db.Float, default=0.0, nullable=False)
    trending_score = db.Column(db.Float, default=0.0, nullable=False)
    freshness_score = db.Column(db.Float, default=0.0, nullable=False)
    final_ranking_score = db.Column(db.Float, default=0.0, nullable=False)

    tags = db.relationship('Tag', secondary='image_tags', back_populates='images', lazy='dynamic')
    collections = db.relationship('Collection', secondary='collection_images', back_populates='images', lazy='dynamic')
    user = db.relationship('User', back_populates='images')

    logger = logging.getLogger('ImageModel')
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    def __init__(self, prompt, image_url, user_id, public=False, request_id=None):
        self.prompt = prompt
        self.image_url = image_url
        self.user_id = user_id
        self.public = public
        self.request_id = request_id or self.generate_request_id()
        self.logger.info(f"Initialized Image: {self.to_dict()}")

    @staticmethod
    def generate_request_id():
        return f"REQ-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    def increment_views(self):
        self.views += 1
        self.update_scores()

    def increment_upvotes(self):
        self.upvotes += 1
        self.update_scores()

    def increment_shares(self):
        self.shares += 1
        self.update_scores()

    def increment_saves(self):
        self.saves += 1
        self.update_scores()

    def update_scores(self):
        try:
            self.engagement_score = self.calculate_engagement_score()
            self.quality_score = self.calculate_quality_score()
            self.trending_score = self.calculate_trending_score()
            self.freshness_score = self.calculate_freshness_score()
            self.final_ranking_score = self.calculate_final_ranking_score()
            db.session.commit()
            self.logger.info(f"Updated scores for Image ID {self.id}. Engagement: {self.engagement_score}, Quality: {self.quality_score}, Trending: {self.trending_score}, Freshness: {self.freshness_score}, Final: {self.final_ranking_score}")
        except Exception as e:
            self.logger.error(f"Failed to update scores for Image ID {self.id}: {e}")

    def calculate_engagement_score(self):
        view_weight = 1
        upvote_weight = 5
        share_weight = 3
        save_weight = 4

        total_weighted_interactions = (
            self.views * view_weight +
            self.upvotes * upvote_weight +
            self.shares * share_weight +
            self.saves * save_weight
        )

        unique_interaction_types = len([x for x in [self.views, self.upvotes, self.shares, self.saves] if x > 0])
        diversity_bonus = log(unique_interaction_types + 1, 2)

        return total_weighted_interactions * diversity_bonus

    def calculate_quality_score(self):
        upvote_ratio = self.upvotes / max(self.views, 1)
        tag_count = self.tags.count()
        tag_bonus = min(tag_count, 5) * 0.1

        user_reputation = self.user.reputation_score if hasattr(self.user, 'reputation_score') else 1
        reputation_factor = log(user_reputation + 1, 2)

        return (upvote_ratio * 0.6 + tag_bonus) * reputation_factor

    def calculate_trending_score(self):
        now = datetime.utcnow()
        age_in_hours = max((now - self.created_at).total_seconds() / 3600, 1)

        recent_window = timedelta(hours=24)
        recent_upvotes = sum(1 for vote in self.upvotes_log if now - vote.timestamp < recent_window)
        recent_shares = sum(1 for share in self.shares_log if now - share.timestamp < recent_window)

        decay_factor = exp(-age_in_hours / 72)  # Half-life of 3 days
        recent_activity = (recent_upvotes * 2 + recent_shares * 3) * decay_factor

        return recent_activity

    def calculate_freshness_score(self):
        age_in_days = (datetime.utcnow() - self.created_at).days
        return max(0, 1 - (age_in_days / 30))  # Linear decay over 30 days

    def calculate_final_ranking_score(self):
        weights = {
            'engagement': 0.35,
            'quality': 0.25,
            'trending': 0.25,
            'freshness': 0.15
        }

        return (
            self.engagement_score * weights['engagement'] +
            self.quality_score * weights['quality'] +
            self.trending_score * weights['trending'] +
            self.freshness_score * weights['freshness']
        )

    def add_tag(self, tag):
        if tag not in self.tags:
            self.tags.append(tag)
            db.session.commit()
            self.update_scores()
            self.logger.info(f"Added tag '{tag.name}' to Image ID {self.id}.")
        else:
            self.logger.warning(f"Tag '{tag.name}' already associated with Image ID {self.id}.")

    def remove_tag(self, tag):
        if tag in self.tags:
            self.tags.remove(tag)
            db.session.commit()
            self.update_scores()
            self.logger.info(f"Removed tag '{tag.name}' from Image ID {self.id}.")
        else:
            self.logger.warning(f"Tag '{tag.name}' not associated with Image ID {self.id}.")

    def to_dict(self):
        return {
            'id': self.id,
            'prompt': self.prompt,
            'image_url': self.image_url,
            'created_at': self.created_at.isoformat(),
            'user_id': self.user_id,
            'public': self.public,
            'request_id': self.request_id,
            'views': self.views,
            'upvotes': self.upvotes,
            'shares': self.shares,
            'saves': self.saves,
            'engagement_score': self.engagement_score,
            'quality_score': self.quality_score,
            'trending_score': self.trending_score,
            'freshness_score': self.freshness_score,
            'final_ranking_score': self.final_ranking_score,
            'tags': [tag.to_dict() for tag in self.tags.all()],
            'collections': [collection.to_dict() for collection in self.collections.all()]
        }