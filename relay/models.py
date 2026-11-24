import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        user = self.model(email=email.strip().lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **extra)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=80)
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']
    objects = UserManager()

    class Meta:
        constraints = [models.UniqueConstraint(Lower('email'), name='user_email_ci_unique')]


class Chat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='messages')
    timestamp = models.DateTimeField()
    sender = models.CharField(max_length=200, blank=True)
    text = models.TextField()
    system = models.BooleanField(default=False)
    fingerprint = models.CharField(max_length=64)
    source_line = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['chat', 'fingerprint'], name='chat_message_unique')]
        indexes = [models.Index(fields=['chat', 'timestamp'])]


class ImportBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, null=True)
    status = models.CharField(max_length=16, default='preview')
    filename = models.CharField(max_length=160)
    chat_name = models.CharField(max_length=120)
    date_order = models.CharField(max_length=3)
    timezone_name = models.CharField(max_length=40)
    records = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    total = models.PositiveIntegerField()
    inserted = models.PositiveIntegerField(default=0)
    duplicates = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()


class RateBucket(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)

class SemanticDocument(models.Model):
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='semantic_document')
    version = models.CharField(max_length=160, db_index=True)
    content_hash = models.CharField(max_length=64)
    chunks = models.JSONField(default=list)
    indexed_at = models.DateTimeField(auto_now=True)

class MessageTopics(models.Model):
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='topic_index')
    version = models.CharField(max_length=180, db_index=True)
    content_hash = models.CharField(max_length=64)
    labels = models.JSONField(default=list)


class TopicSummary(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    topic = models.CharField(max_length=30)
    fingerprint = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner','topic'], name='owner_topic_summary_unique')]


class TopicCorrections(models.Model):
    owner = models.OneToOneField(User, on_delete=models.CASCADE, related_name='topic_corrections')
    state = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class ProcessingJob(models.Model):
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    progress = models.JSONField(default=dict)
    rerun = models.BooleanField(default=False)
    owner = models.OneToOneField(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default='queued')
    phase = models.CharField(max_length=30, default='topics')
    remaining = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class MessageInsight(models.Model):
    message = models.OneToOneField(Message, on_delete=models.CASCADE)
    content_hash = models.CharField(max_length=64)
    version = models.CharField(max_length=40)
    items = models.JSONField(default=list)


class CorrectionEvent(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    action = models.CharField(max_length=40)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    undone = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
