from django.core.management.base import BaseCommand, CommandError
from relay.models import User, Message
from relay.semantic import model_version, index_pending, SemanticError

class Command(BaseCommand):
    help='Prepare local semantic embeddings for one account without logging message content.'

    def add_arguments(self,parser):
        parser.add_argument('--user-id',type=int,required=True)

    def handle(self,*args,**options):
        if not User.objects.filter(pk=options['user_id']).exists():
            raise CommandError('Account not found.')
        messages=Message.objects.filter(chat__owner_id=options['user_id'])
        try:
            version=model_version()
            remaining=index_pending(messages,version)
            self.stdout.write(f'Messages remaining: {remaining}')
            while remaining:
                remaining=index_pending(messages,version)
                self.stdout.write(f'Messages remaining: {remaining}')
        except SemanticError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS('Semantic index ready.'))
